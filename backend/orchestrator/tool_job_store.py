from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from orchestrator.state_store import (
    DEFAULT_STATE_DB_URL,
    StateStoreConfigurationError,
    _is_postgres_db_url,
    _json_dump,
    _json_load,
    _sqlite_db_path_from_url,
)

import sqlite3


DEFAULT_TOOL_JOB_DB_URL = os.getenv("ORCHESTRATOR_TOOL_JOB_DB_URL", DEFAULT_STATE_DB_URL)
_STORE_LOCK = threading.Lock()
_STORE_SINGLETON: Optional["ToolJobStateStore"] = None
_INTERRUPTED_JOB_ERROR = "interrupted:process-restart"


def _normalize_route_prefix(route_prefix: Optional[str]) -> str:
    normalized = str(route_prefix or "").strip().strip("/")
    if not normalized:
        return ""
    return f"/{normalized}"


@dataclass
class ToolJobRecord:
    job_id: str
    tool_name: str
    status: str
    route_prefix: str
    submitted_at: str
    current_stage: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    run_id: Optional[str] = None
    state_ref: Optional[str] = None
    error_summary: Optional[str] = None
    result_preview: Optional[str] = None
    request_payload: Optional[Dict[str, Any]] = None
    response: Optional[Dict[str, Any]] = None
    execution_metadata: Optional[Dict[str, Any]] = None
    cancel_requested_at: Optional[str] = None

    @property
    def status_url(self) -> str:
        return f"{self.route_prefix}/tool-jobs/{self.job_id}"

    @property
    def result_ref(self) -> str:
        return f"{self.route_prefix}/tool-jobs/{self.job_id}/result"


class ToolJobStateStore(Protocol):
    def create_job(
        self,
        *,
        tool_name: str,
        route_prefix: Optional[str],
        request_payload: Optional[Dict[str, Any]],
        execution_metadata: Optional[Dict[str, Any]],
        job_id: Optional[str] = None,
    ) -> ToolJobRecord: ...

    def get(self, job_id: str) -> Optional[ToolJobRecord]: ...

    def mark_running(self, job_id: str) -> ToolJobRecord: ...

    def mark_cancelling(self, job_id: str) -> ToolJobRecord: ...

    def finish_completed(self, job_id: str, response: Dict[str, Any]) -> ToolJobRecord: ...

    def finish_failed(self, job_id: str, error_summary: str, *, current_stage: str = "failed") -> ToolJobRecord: ...

    def finish_cancelled(self, job_id: str, *, error_summary: Optional[str] = None) -> ToolJobRecord: ...

    def reconcile_incomplete_jobs(self) -> int: ...


class SQLiteToolJobStore:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.db_path = _sqlite_db_path_from_url(db_url)
        self._bootstrap()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _bootstrap(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        schema = """
        CREATE TABLE IF NOT EXISTS tool_jobs (
            job_id TEXT PRIMARY KEY,
            tool_name TEXT NOT NULL,
            status TEXT NOT NULL,
            route_prefix TEXT NOT NULL DEFAULT '',
            submitted_at TEXT NOT NULL,
            current_stage TEXT,
            started_at TEXT,
            completed_at TEXT,
            run_id TEXT,
            state_ref TEXT,
            error_summary TEXT,
            result_preview TEXT,
            request_payload_blob TEXT,
            response_blob TEXT,
            execution_metadata_blob TEXT,
            cancel_requested_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tool_jobs_status
            ON tool_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_tool_jobs_submitted_at
            ON tool_jobs(submitted_at DESC);
        """
        with self._connect() as conn:
            conn.executescript(schema)
            conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> ToolJobRecord:
        return ToolJobRecord(
            job_id=str(row["job_id"]),
            tool_name=str(row["tool_name"]),
            status=str(row["status"]),
            route_prefix=str(row["route_prefix"] or ""),
            submitted_at=str(row["submitted_at"]),
            current_stage=row["current_stage"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            run_id=row["run_id"],
            state_ref=row["state_ref"],
            error_summary=row["error_summary"],
            result_preview=row["result_preview"],
            request_payload=_json_load(row["request_payload_blob"]),
            response=_json_load(row["response_blob"]),
            execution_metadata=_json_load(row["execution_metadata_blob"]),
            cancel_requested_at=row["cancel_requested_at"],
        )

    def create_job(
        self,
        *,
        tool_name: str,
        route_prefix: Optional[str],
        request_payload: Optional[Dict[str, Any]],
        execution_metadata: Optional[Dict[str, Any]],
        job_id: Optional[str] = None,
    ) -> ToolJobRecord:
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
        effective_job_id = job_id or str(uuid.uuid4())
        normalized_prefix = _normalize_route_prefix(route_prefix)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_jobs (
                    job_id, tool_name, status, route_prefix, submitted_at, current_stage,
                    request_payload_blob, execution_metadata_blob
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effective_job_id,
                    tool_name,
                    "queued",
                    normalized_prefix,
                    now,
                    "queued",
                    _json_dump(request_payload),
                    _json_dump(execution_metadata),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM tool_jobs WHERE job_id = ?", (effective_job_id,)).fetchone()
        if row is None:
            raise RuntimeError("Failed to persist tool job")
        return self._row_to_record(row)

    def get(self, job_id: str) -> Optional[ToolJobRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tool_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def mark_running(self, job_id: str) -> ToolJobRecord:
        return self._update_status(job_id, status="running", current_stage="running", started_at=self._utc_now())

    def mark_cancelling(self, job_id: str) -> ToolJobRecord:
        return self._update_status(
            job_id,
            status="cancelling",
            current_stage="cancelling",
            cancel_requested_at=self._utc_now(),
        )

    def finish_completed(self, job_id: str, response: Dict[str, Any]) -> ToolJobRecord:
        assistant_message = str(response.get("assistant_message") or "").strip()
        return self._update_status(
            job_id,
            status="completed",
            current_stage="completed",
            completed_at=self._utc_now(),
            response_blob=response,
            run_id=response.get("run_id"),
            state_ref=response.get("state_ref"),
            result_preview=assistant_message[:280] or None,
            error_summary=None,
        )

    def finish_failed(self, job_id: str, error_summary: str, *, current_stage: str = "failed") -> ToolJobRecord:
        return self._update_status(
            job_id,
            status="failed",
            current_stage=current_stage,
            completed_at=self._utc_now(),
            error_summary=error_summary,
        )

    def finish_cancelled(self, job_id: str, *, error_summary: Optional[str] = None) -> ToolJobRecord:
        return self._update_status(
            job_id,
            status="cancelled",
            current_stage="cancelled",
            completed_at=self._utc_now(),
            error_summary=error_summary or "cancelled-by-request",
        )

    def reconcile_incomplete_jobs(self) -> int:
        now = self._utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE tool_jobs
                SET status = ?, current_stage = ?, completed_at = ?, error_summary = ?
                WHERE status IN ('queued', 'running', 'cancelling')
                """,
                ("failed", "interrupted", now, _INTERRUPTED_JOB_ERROR),
            )
            conn.commit()
            return int(cursor.rowcount or 0)

    def _update_status(
        self,
        job_id: str,
        *,
        status: str,
        current_stage: Optional[str] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        run_id: Optional[str] = None,
        state_ref: Optional[str] = None,
        error_summary: Optional[str] = None,
        result_preview: Optional[str] = None,
        response_blob: Optional[Dict[str, Any]] = None,
        cancel_requested_at: Optional[str] = None,
    ) -> ToolJobRecord:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE tool_jobs
                SET status = ?,
                    current_stage = COALESCE(?, current_stage),
                    started_at = COALESCE(?, started_at),
                    completed_at = COALESCE(?, completed_at),
                    run_id = COALESCE(?, run_id),
                    state_ref = COALESCE(?, state_ref),
                    error_summary = ?,
                    result_preview = COALESCE(?, result_preview),
                    response_blob = COALESCE(?, response_blob),
                    cancel_requested_at = COALESCE(?, cancel_requested_at)
                WHERE job_id = ?
                """,
                (
                    status,
                    current_stage,
                    started_at,
                    completed_at,
                    run_id,
                    state_ref,
                    error_summary,
                    result_preview,
                    _json_dump(response_blob) if response_blob is not None else None,
                    cancel_requested_at,
                    job_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(job_id)
            conn.commit()
            row = conn.execute("SELECT * FROM tool_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"Failed to reload tool job {job_id}")
        return self._row_to_record(row)

    @staticmethod
    def _utc_now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


class PostgresToolJobStore:
    def __init__(self, db_url: str):
        self.db_url = db_url
        try:
            import psycopg  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise StateStoreConfigurationError(
                "Postgres tool job store requires psycopg. Install psycopg[binary] or use sqlite://"
            ) from exc
        self._psycopg = psycopg
        self._bootstrap()

    def _connect(self):
        conn = self._psycopg.connect(self.db_url)
        conn.autocommit = False
        return conn

    def _bootstrap(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS tool_jobs (
            job_id TEXT PRIMARY KEY,
            tool_name TEXT NOT NULL,
            status TEXT NOT NULL,
            route_prefix TEXT NOT NULL DEFAULT '',
            submitted_at TEXT NOT NULL,
            current_stage TEXT,
            started_at TEXT,
            completed_at TEXT,
            run_id TEXT,
            state_ref TEXT,
            error_summary TEXT,
            result_preview TEXT,
            request_payload_blob JSONB,
            response_blob JSONB,
            execution_metadata_blob JSONB,
            cancel_requested_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tool_jobs_status
            ON tool_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_tool_jobs_submitted_at
            ON tool_jobs(submitted_at DESC);
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(schema)
            conn.commit()

    def _fetchone(self, cur: Any) -> Optional[Dict[str, Any]]:
        row = cur.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return row
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))

    def _row_to_record(self, row: Dict[str, Any]) -> ToolJobRecord:
        return ToolJobRecord(
            job_id=str(row["job_id"]),
            tool_name=str(row["tool_name"]),
            status=str(row["status"]),
            route_prefix=str(row.get("route_prefix") or ""),
            submitted_at=str(row["submitted_at"]),
            current_stage=row.get("current_stage"),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            run_id=row.get("run_id"),
            state_ref=row.get("state_ref"),
            error_summary=row.get("error_summary"),
            result_preview=row.get("result_preview"),
            request_payload=row.get("request_payload_blob"),
            response=row.get("response_blob"),
            execution_metadata=row.get("execution_metadata_blob"),
            cancel_requested_at=row.get("cancel_requested_at"),
        )

    def create_job(
        self,
        *,
        tool_name: str,
        route_prefix: Optional[str],
        request_payload: Optional[Dict[str, Any]],
        execution_metadata: Optional[Dict[str, Any]],
        job_id: Optional[str] = None,
    ) -> ToolJobRecord:
        now = SQLiteToolJobStore._utc_now()
        effective_job_id = job_id or str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tool_jobs (
                        job_id, tool_name, status, route_prefix, submitted_at, current_stage,
                        request_payload_blob, execution_metadata_blob
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        effective_job_id,
                        tool_name,
                        "queued",
                        _normalize_route_prefix(route_prefix),
                        now,
                        "queued",
                        request_payload,
                        execution_metadata,
                    ),
                )
                row = self._fetchone(cur)
            conn.commit()
        if row is None:
            raise RuntimeError("Failed to persist tool job")
        return self._row_to_record(row)

    def get(self, job_id: str) -> Optional[ToolJobRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM tool_jobs WHERE job_id = %s", (job_id,))
                row = self._fetchone(cur)
        return self._row_to_record(row) if row else None

    def mark_running(self, job_id: str) -> ToolJobRecord:
        return self._update_status(job_id, status="running", current_stage="running", started_at=SQLiteToolJobStore._utc_now())

    def mark_cancelling(self, job_id: str) -> ToolJobRecord:
        return self._update_status(
            job_id,
            status="cancelling",
            current_stage="cancelling",
            cancel_requested_at=SQLiteToolJobStore._utc_now(),
        )

    def finish_completed(self, job_id: str, response: Dict[str, Any]) -> ToolJobRecord:
        assistant_message = str(response.get("assistant_message") or "").strip()
        return self._update_status(
            job_id,
            status="completed",
            current_stage="completed",
            completed_at=SQLiteToolJobStore._utc_now(),
            response_blob=response,
            run_id=response.get("run_id"),
            state_ref=response.get("state_ref"),
            result_preview=assistant_message[:280] or None,
            error_summary=None,
        )

    def finish_failed(self, job_id: str, error_summary: str, *, current_stage: str = "failed") -> ToolJobRecord:
        return self._update_status(
            job_id,
            status="failed",
            current_stage=current_stage,
            completed_at=SQLiteToolJobStore._utc_now(),
            error_summary=error_summary,
        )

    def finish_cancelled(self, job_id: str, *, error_summary: Optional[str] = None) -> ToolJobRecord:
        return self._update_status(
            job_id,
            status="cancelled",
            current_stage="cancelled",
            completed_at=SQLiteToolJobStore._utc_now(),
            error_summary=error_summary or "cancelled-by-request",
        )

    def reconcile_incomplete_jobs(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tool_jobs
                    SET status = %s, current_stage = %s, completed_at = %s, error_summary = %s
                    WHERE status IN ('queued', 'running', 'cancelling')
                    """,
                    ("failed", "interrupted", SQLiteToolJobStore._utc_now(), _INTERRUPTED_JOB_ERROR),
                )
                rowcount = int(cur.rowcount or 0)
            conn.commit()
        return rowcount

    def _update_status(
        self,
        job_id: str,
        *,
        status: str,
        current_stage: Optional[str] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        run_id: Optional[str] = None,
        state_ref: Optional[str] = None,
        error_summary: Optional[str] = None,
        result_preview: Optional[str] = None,
        response_blob: Optional[Dict[str, Any]] = None,
        cancel_requested_at: Optional[str] = None,
    ) -> ToolJobRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tool_jobs
                    SET status = %s,
                        current_stage = COALESCE(%s, current_stage),
                        started_at = COALESCE(%s, started_at),
                        completed_at = COALESCE(%s, completed_at),
                        run_id = COALESCE(%s, run_id),
                        state_ref = COALESCE(%s, state_ref),
                        error_summary = %s,
                        result_preview = COALESCE(%s, result_preview),
                        response_blob = COALESCE(%s, response_blob),
                        cancel_requested_at = COALESCE(%s, cancel_requested_at)
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (
                        status,
                        current_stage,
                        started_at,
                        completed_at,
                        run_id,
                        state_ref,
                        error_summary,
                        result_preview,
                        response_blob,
                        cancel_requested_at,
                        job_id,
                    ),
                )
                row = self._fetchone(cur)
            conn.commit()
        if row is None:
            raise KeyError(job_id)
        return self._row_to_record(row)


def _resolve_tool_job_db_url() -> str:
    return (
        str(os.getenv("ORCHESTRATOR_TOOL_JOB_DB_URL") or "").strip()
        or str(os.getenv("ORCHESTRATOR_STATE_DB_URL") or "").strip()
        or DEFAULT_TOOL_JOB_DB_URL
    )


def create_tool_job_store(db_url: Optional[str] = None) -> ToolJobStateStore:
    effective_db_url = str(db_url or _resolve_tool_job_db_url()).strip()
    if _is_postgres_db_url(effective_db_url):
        return PostgresToolJobStore(effective_db_url)
    if effective_db_url.startswith("sqlite://"):
        return SQLiteToolJobStore(effective_db_url)
    raise StateStoreConfigurationError(f"Unsupported tool job DB URL: {effective_db_url}")


def get_tool_job_store() -> ToolJobStateStore:
    global _STORE_SINGLETON
    if _STORE_SINGLETON is None:
        with _STORE_LOCK:
            if _STORE_SINGLETON is None:
                _STORE_SINGLETON = create_tool_job_store()
    return _STORE_SINGLETON
