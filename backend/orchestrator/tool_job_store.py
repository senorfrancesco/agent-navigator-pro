from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

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


def _build_status_payload(
    *,
    status: str,
    status_text: str,
    progress: Optional[Dict[str, Any]] = None,
    status_history: Optional[List[Dict[str, Any]]] = None,
    embeds: Optional[List[Dict[str, Any]]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    result_preview: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "status_text": status_text,
        "status_history": list(status_history or []),
        "artifacts": list(artifacts or []),
        "sources": list(sources or []),
        "embeds": list(embeds or []),
    }
    if progress is not None:
        payload["progress"] = dict(progress)
    if result_preview is not None:
        payload["result_preview"] = result_preview
    return payload


def _merge_status_payload(
    existing: Optional[Dict[str, Any]],
    *,
    status_text: Optional[str] = None,
    progress: Optional[Dict[str, Any]] = None,
    status_history: Optional[List[Dict[str, Any]]] = None,
    embeds: Optional[List[Dict[str, Any]]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    result_preview: Optional[str] = None,
) -> Dict[str, Any]:
    payload = dict(existing or {})
    if status_text is not None:
        payload["status_text"] = status_text
    if progress is not None:
        payload["progress"] = dict(progress)
    if status_history is not None:
        payload["status_history"] = list(status_history)
    else:
        payload.setdefault("status_history", list(payload.get("status_history") or []))
    if embeds is not None:
        payload["embeds"] = list(embeds)
    else:
        payload.setdefault("embeds", list(payload.get("embeds") or []))
    if artifacts is not None:
        payload["artifacts"] = list(artifacts)
    else:
        payload.setdefault("artifacts", list(payload.get("artifacts") or []))
    if sources is not None:
        payload["sources"] = list(sources)
    else:
        payload.setdefault("sources", list(payload.get("sources") or []))
    if result_preview is not None:
        payload["result_preview"] = result_preview
    return payload


def _append_terminal_history(existing_history: List[Dict[str, Any]], *, status: str, status_text: str) -> List[Dict[str, Any]]:
    history = list(existing_history or [])
    terminal_key = f"terminal:{status}"
    if any(str(item.get("key") or "") == terminal_key for item in history if isinstance(item, dict)):
        return history
    history.append(
        {
            "key": terminal_key,
            "title": "Завершение",
            "content": status_text,
            "status": status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
        }
    )
    return history


def _build_terminal_status_payload(
    existing: Optional[Dict[str, Any]],
    *,
    status: str,
    status_text: str,
    result_payload: Optional[Dict[str, Any]] = None,
    result_preview: Optional[str] = None,
) -> Dict[str, Any]:
    payload = _merge_status_payload(
        existing,
        status_text=status_text,
        embeds=(result_payload or {}).get("embeds"),
        artifacts=(result_payload or {}).get("artifacts"),
        sources=(result_payload or {}).get("sources"),
        result_preview=result_preview,
    )
    payload["status"] = status
    payload["status_history"] = _append_terminal_history(
        list(payload.get("status_history") or []),
        status=status,
        status_text=status_text,
    )
    return payload


def _normalize_result_payload(
    payload: Dict[str, Any],
    *,
    status: str,
    reason: Optional[str] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    embeds: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    normalized = dict(payload or {})
    if "assistant_message" in normalized:
        normalized["assistant_message"] = _strip_telemetry_footer(str(normalized.get("assistant_message") or ""))
    execution_metadata = dict(normalized.get("execution_metadata") or {})
    execution_metadata["status"] = status
    if reason:
        execution_metadata.setdefault("reason", reason)
    normalized["execution_metadata"] = execution_metadata
    if sources is not None:
        normalized.setdefault("sources", list(sources))
    if artifacts is not None:
        normalized.setdefault("artifacts", list(artifacts))
    if embeds is not None:
        normalized.setdefault("embeds", list(embeds))
    return normalized


def _strip_telemetry_footer(text: str) -> str:
    marker = "\n\n---\nTiming / Quality"
    value = str(text or "")
    if marker not in value:
        return value
    return value.split(marker, 1)[0].rstrip()


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
    status_payload: Optional[Dict[str, Any]] = None
    result_payload: Optional[Dict[str, Any]] = None
    execution_metadata: Optional[Dict[str, Any]] = None
    cancel_requested_at: Optional[str] = None
    result_message_id: Optional[str] = None
    terminal_emitted_at: Optional[str] = None

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

    def find_latest_active_by_chat_id(self, chat_id: str) -> Optional[ToolJobRecord]: ...

    def mark_running(self, job_id: str) -> ToolJobRecord: ...

    def mark_cancelling(self, job_id: str) -> ToolJobRecord: ...

    def update_job_status(
        self,
        job_id: str,
        *,
        current_stage: Optional[str] = None,
        status_text: Optional[str] = None,
        progress: Optional[Dict[str, Any]] = None,
        status_history: Optional[List[Dict[str, Any]]] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        artifacts: Optional[List[Dict[str, Any]]] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        result_preview: Optional[str] = None,
    ) -> ToolJobRecord: ...

    def finish_completed(self, job_id: str, response: Dict[str, Any]) -> ToolJobRecord: ...

    def finish_failed(self, job_id: str, error_summary: str, *, current_stage: str = "failed") -> ToolJobRecord: ...

    def finish_cancelled(self, job_id: str, *, error_summary: Optional[str] = None) -> ToolJobRecord: ...

    def record_terminal_delivery(
        self,
        job_id: str,
        *,
        result_message_id: str,
        terminal_emitted_at: Optional[str] = None,
    ) -> ToolJobRecord: ...

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
            status_payload_blob TEXT,
            result_payload_blob TEXT,
            execution_metadata_blob TEXT,
            cancel_requested_at TEXT,
            result_message_id TEXT,
            terminal_emitted_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tool_jobs_status
            ON tool_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_tool_jobs_submitted_at
            ON tool_jobs(submitted_at DESC);
        """
        with self._connect() as conn:
            conn.executescript(schema)
            self._ensure_sqlite_column(conn, "tool_jobs", "status_payload_blob", "TEXT")
            self._ensure_sqlite_column(conn, "tool_jobs", "result_payload_blob", "TEXT")
            self._ensure_sqlite_column(conn, "tool_jobs", "result_message_id", "TEXT")
            self._ensure_sqlite_column(conn, "tool_jobs", "terminal_emitted_at", "TEXT")
            conn.commit()

    @staticmethod
    def _ensure_sqlite_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
        existing_columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in existing_columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

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
            status_payload=_json_load(row["status_payload_blob"]),
            result_payload=_json_load(row["result_payload_blob"]),
            execution_metadata=_json_load(row["execution_metadata_blob"]),
            cancel_requested_at=row["cancel_requested_at"],
            result_message_id=row["result_message_id"],
            terminal_emitted_at=row["terminal_emitted_at"],
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
                    request_payload_blob, status_payload_blob, execution_metadata_blob
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effective_job_id,
                    tool_name,
                    "queued",
                    normalized_prefix,
                    now,
                    "queued",
                    _json_dump(request_payload),
                    _json_dump(_build_status_payload(status="queued", status_text="Задача поставлена в очередь.")),
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

    def find_latest_active_by_chat_id(self, chat_id: str) -> Optional[ToolJobRecord]:
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            return None
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tool_jobs
                WHERE status IN ('queued', 'running', 'cancelling')
                ORDER BY submitted_at DESC
                """
            ).fetchall()
        for row in rows:
            record = self._row_to_record(row)
            request_payload = record.request_payload or {}
            if str(request_payload.get("chat_id") or "").strip() == normalized_chat_id:
                return record
        return None

    def mark_running(self, job_id: str) -> ToolJobRecord:
        return self._update_status(
            job_id,
            status="running",
            current_stage="running",
            started_at=self._utc_now(),
            status_payload_blob=_build_status_payload(status="running", status_text="deep-job выполняется."),
        )

    def mark_cancelling(self, job_id: str) -> ToolJobRecord:
        return self._update_status(
            job_id,
            status="cancelling",
            current_stage="cancelling",
            cancel_requested_at=self._utc_now(),
            status_payload_blob=_build_status_payload(
                status="cancelling",
                status_text="deep-job готовится к отмене.",
            ),
        )

    def update_job_status(
        self,
        job_id: str,
        *,
        current_stage: Optional[str] = None,
        status_text: Optional[str] = None,
        progress: Optional[Dict[str, Any]] = None,
        status_history: Optional[List[Dict[str, Any]]] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        artifacts: Optional[List[Dict[str, Any]]] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        result_preview: Optional[str] = None,
    ) -> ToolJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        merged_status_payload = _merge_status_payload(
            current.status_payload,
            status_text=status_text,
            progress=progress,
            status_history=status_history,
            embeds=embeds,
            artifacts=artifacts,
            sources=sources,
            result_preview=result_preview,
        )
        return self._update_status(
            job_id,
            status=current.status,
            current_stage=current_stage,
            result_preview=result_preview,
            status_payload_blob=merged_status_payload,
        )

    def finish_completed(self, job_id: str, response: Dict[str, Any]) -> ToolJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        result_payload = _normalize_result_payload(response, status="completed")
        assistant_message = str(result_payload.get("assistant_message") or "").strip()
        return self._update_status(
            job_id,
            status="completed",
            current_stage="completed",
            completed_at=self._utc_now(),
            response_blob=result_payload,
            result_payload_blob=result_payload,
            run_id=response.get("run_id"),
            state_ref=response.get("state_ref"),
            result_preview=assistant_message[:280] or None,
            error_summary=None,
            status_payload_blob=_build_terminal_status_payload(
                current.status_payload,
                status="completed",
                status_text="deep-job завершён.",
                result_payload=result_payload,
                result_preview=assistant_message[:280] or None,
            ),
        )

    def finish_failed(self, job_id: str, error_summary: str, *, current_stage: str = "failed") -> ToolJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        error_summary = _strip_telemetry_footer(error_summary)
        result_payload = _normalize_result_payload(
            {"assistant_message": f"deep-job завершён со статусом failed.\nerror: {error_summary}"},
            status="failed",
            reason=error_summary,
            sources=(current.status_payload or {}).get("sources"),
            artifacts=(current.status_payload or {}).get("artifacts"),
            embeds=(current.status_payload or {}).get("embeds"),
        )
        return self._update_status(
            job_id,
            status="failed",
            current_stage=current_stage,
            completed_at=self._utc_now(),
            error_summary=error_summary,
            response_blob=result_payload,
            result_payload_blob=result_payload,
            status_payload_blob=_build_terminal_status_payload(
                current.status_payload,
                status="failed",
                status_text="deep-job завершён со статусом failed.",
            ),
        )

    def finish_cancelled(self, job_id: str, *, error_summary: Optional[str] = None) -> ToolJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        cancel_reason = _strip_telemetry_footer(error_summary or "cancelled-by-request")
        result_payload = _normalize_result_payload(
            {
                "assistant_message": (
                    "deep-job отменён."
                    if cancel_reason == "cancelled-by-request"
                    else f"deep-job отменён.\nerror: {cancel_reason}"
                )
            },
            status="cancelled",
            reason=cancel_reason,
            sources=(current.status_payload or {}).get("sources"),
            artifacts=(current.status_payload or {}).get("artifacts"),
            embeds=(current.status_payload or {}).get("embeds"),
        )
        return self._update_status(
            job_id,
            status="cancelled",
            current_stage="cancelled",
            completed_at=self._utc_now(),
            error_summary=cancel_reason,
            response_blob=result_payload,
            result_payload_blob=result_payload,
            status_payload_blob=_build_terminal_status_payload(
                current.status_payload,
                status="cancelled",
                status_text="deep-job отменён.",
            ),
        )

    def record_terminal_delivery(
        self,
        job_id: str,
        *,
        result_message_id: str,
        terminal_emitted_at: Optional[str] = None,
    ) -> ToolJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        if current.status not in {"completed", "failed", "cancelled"}:
            raise RuntimeError(f"job-not-terminal:{job_id}")
        existing_result_message_id = str(current.result_message_id or "").strip()
        emitted_at = str(terminal_emitted_at or self._utc_now()).strip() or self._utc_now()
        if existing_result_message_id:
            if current.terminal_emitted_at:
                return current
            return self._update_status(
                job_id,
                status=current.status,
                current_stage=current.current_stage,
                error_summary=current.error_summary,
                result_message_id=existing_result_message_id,
                terminal_emitted_at=emitted_at,
            )
        return self._update_status(
            job_id,
            status=current.status,
            current_stage=current.current_stage,
            error_summary=current.error_summary,
            result_message_id=result_message_id,
            terminal_emitted_at=emitted_at,
        )

    def reconcile_incomplete_jobs(self) -> int:
        with self._connect() as conn:
            rows = conn.execute("SELECT job_id FROM tool_jobs WHERE status IN ('queued', 'running', 'cancelling')").fetchall()
        for row in rows:
            self.finish_failed(str(row["job_id"]), _INTERRUPTED_JOB_ERROR, current_stage="interrupted")
        return len(rows)

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
        status_payload_blob: Optional[Dict[str, Any]] = None,
        result_payload_blob: Optional[Dict[str, Any]] = None,
        cancel_requested_at: Optional[str] = None,
        result_message_id: Optional[str] = None,
        terminal_emitted_at: Optional[str] = None,
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
                    status_payload_blob = COALESCE(?, status_payload_blob),
                    result_payload_blob = COALESCE(?, result_payload_blob),
                    cancel_requested_at = COALESCE(?, cancel_requested_at),
                    result_message_id = COALESCE(?, result_message_id),
                    terminal_emitted_at = COALESCE(?, terminal_emitted_at)
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
                    _json_dump(status_payload_blob) if status_payload_blob is not None else None,
                    _json_dump(result_payload_blob) if result_payload_blob is not None else None,
                    cancel_requested_at,
                    result_message_id,
                    terminal_emitted_at,
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
            status_payload_blob JSONB,
            result_payload_blob JSONB,
            execution_metadata_blob JSONB,
            cancel_requested_at TEXT,
            result_message_id TEXT,
            terminal_emitted_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tool_jobs_status
            ON tool_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_tool_jobs_submitted_at
            ON tool_jobs(submitted_at DESC);
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(schema)
                cur.execute("ALTER TABLE tool_jobs ADD COLUMN IF NOT EXISTS status_payload_blob JSONB")
                cur.execute("ALTER TABLE tool_jobs ADD COLUMN IF NOT EXISTS result_payload_blob JSONB")
                cur.execute("ALTER TABLE tool_jobs ADD COLUMN IF NOT EXISTS result_message_id TEXT")
                cur.execute("ALTER TABLE tool_jobs ADD COLUMN IF NOT EXISTS terminal_emitted_at TEXT")
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
            status_payload=row.get("status_payload_blob"),
            result_payload=row.get("result_payload_blob"),
            execution_metadata=row.get("execution_metadata_blob"),
            cancel_requested_at=row.get("cancel_requested_at"),
            result_message_id=row.get("result_message_id"),
            terminal_emitted_at=row.get("terminal_emitted_at"),
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
                        request_payload_blob, status_payload_blob, execution_metadata_blob
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        _build_status_payload(status="queued", status_text="Задача поставлена в очередь."),
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

    def find_latest_active_by_chat_id(self, chat_id: str) -> Optional[ToolJobRecord]:
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM tool_jobs
                    WHERE status IN ('queued', 'running', 'cancelling')
                    ORDER BY submitted_at DESC
                    """
                )
                rows = cur.fetchall()
        for row in rows:
            record = self._row_to_record(row)
            request_payload = record.request_payload or {}
            if str(request_payload.get("chat_id") or "").strip() == normalized_chat_id:
                return record
        return None

    def mark_running(self, job_id: str) -> ToolJobRecord:
        return self._update_status(
            job_id,
            status="running",
            current_stage="running",
            started_at=SQLiteToolJobStore._utc_now(),
            status_payload_blob=_build_status_payload(status="running", status_text="deep-job выполняется."),
        )

    def mark_cancelling(self, job_id: str) -> ToolJobRecord:
        return self._update_status(
            job_id,
            status="cancelling",
            current_stage="cancelling",
            cancel_requested_at=SQLiteToolJobStore._utc_now(),
            status_payload_blob=_build_status_payload(
                status="cancelling",
                status_text="deep-job готовится к отмене.",
            ),
        )

    def update_job_status(
        self,
        job_id: str,
        *,
        current_stage: Optional[str] = None,
        status_text: Optional[str] = None,
        progress: Optional[Dict[str, Any]] = None,
        status_history: Optional[List[Dict[str, Any]]] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        artifacts: Optional[List[Dict[str, Any]]] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        result_preview: Optional[str] = None,
    ) -> ToolJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        merged_status_payload = _merge_status_payload(
            current.status_payload,
            status_text=status_text,
            progress=progress,
            status_history=status_history,
            embeds=embeds,
            artifacts=artifacts,
            sources=sources,
            result_preview=result_preview,
        )
        return self._update_status(
            job_id,
            status=current.status,
            current_stage=current_stage,
            result_preview=result_preview,
            status_payload_blob=merged_status_payload,
        )

    def finish_completed(self, job_id: str, response: Dict[str, Any]) -> ToolJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        result_payload = _normalize_result_payload(response, status="completed")
        assistant_message = str(result_payload.get("assistant_message") or "").strip()
        return self._update_status(
            job_id,
            status="completed",
            current_stage="completed",
            completed_at=SQLiteToolJobStore._utc_now(),
            response_blob=result_payload,
            result_payload_blob=result_payload,
            run_id=response.get("run_id"),
            state_ref=response.get("state_ref"),
            result_preview=assistant_message[:280] or None,
            error_summary=None,
            status_payload_blob=_build_terminal_status_payload(
                current.status_payload,
                status="completed",
                status_text="deep-job завершён.",
                result_payload=result_payload,
                result_preview=assistant_message[:280] or None,
            ),
        )

    def finish_failed(self, job_id: str, error_summary: str, *, current_stage: str = "failed") -> ToolJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        error_summary = _strip_telemetry_footer(error_summary)
        result_payload = _normalize_result_payload(
            {"assistant_message": f"deep-job завершён со статусом failed.\nerror: {error_summary}"},
            status="failed",
            reason=error_summary,
            sources=(current.status_payload or {}).get("sources"),
            artifacts=(current.status_payload or {}).get("artifacts"),
            embeds=(current.status_payload or {}).get("embeds"),
        )
        return self._update_status(
            job_id,
            status="failed",
            current_stage=current_stage,
            completed_at=SQLiteToolJobStore._utc_now(),
            error_summary=error_summary,
            response_blob=result_payload,
            result_payload_blob=result_payload,
            status_payload_blob=_build_terminal_status_payload(
                current.status_payload,
                status="failed",
                status_text="deep-job завершён со статусом failed.",
            ),
        )

    def finish_cancelled(self, job_id: str, *, error_summary: Optional[str] = None) -> ToolJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        cancel_reason = _strip_telemetry_footer(error_summary or "cancelled-by-request")
        result_payload = _normalize_result_payload(
            {
                "assistant_message": (
                    "deep-job отменён."
                    if cancel_reason == "cancelled-by-request"
                    else f"deep-job отменён.\nerror: {cancel_reason}"
                )
            },
            status="cancelled",
            reason=cancel_reason,
            sources=(current.status_payload or {}).get("sources"),
            artifacts=(current.status_payload or {}).get("artifacts"),
            embeds=(current.status_payload or {}).get("embeds"),
        )
        return self._update_status(
            job_id,
            status="cancelled",
            current_stage="cancelled",
            completed_at=SQLiteToolJobStore._utc_now(),
            error_summary=cancel_reason,
            response_blob=result_payload,
            result_payload_blob=result_payload,
            status_payload_blob=_build_terminal_status_payload(
                current.status_payload,
                status="cancelled",
                status_text="deep-job отменён.",
            ),
        )

    def record_terminal_delivery(
        self,
        job_id: str,
        *,
        result_message_id: str,
        terminal_emitted_at: Optional[str] = None,
    ) -> ToolJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        if current.status not in {"completed", "failed", "cancelled"}:
            raise RuntimeError(f"job-not-terminal:{job_id}")
        existing_result_message_id = str(current.result_message_id or "").strip()
        emitted_at = str(terminal_emitted_at or SQLiteToolJobStore._utc_now()).strip() or SQLiteToolJobStore._utc_now()
        if existing_result_message_id:
            if current.terminal_emitted_at:
                return current
            return self._update_status(
                job_id,
                status=current.status,
                current_stage=current.current_stage,
                error_summary=current.error_summary,
                result_message_id=existing_result_message_id,
                terminal_emitted_at=emitted_at,
            )
        return self._update_status(
            job_id,
            status=current.status,
            current_stage=current.current_stage,
            error_summary=current.error_summary,
            result_message_id=result_message_id,
            terminal_emitted_at=emitted_at,
        )

    def reconcile_incomplete_jobs(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT job_id FROM tool_jobs WHERE status IN ('queued', 'running', 'cancelling')")
                rows = cur.fetchall() or []
        for row in rows:
            job_id = str(row[0] if not isinstance(row, dict) else row["job_id"])
            self.finish_failed(job_id, _INTERRUPTED_JOB_ERROR, current_stage="interrupted")
        return len(rows)

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
        status_payload_blob: Optional[Dict[str, Any]] = None,
        result_payload_blob: Optional[Dict[str, Any]] = None,
        cancel_requested_at: Optional[str] = None,
        result_message_id: Optional[str] = None,
        terminal_emitted_at: Optional[str] = None,
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
                        status_payload_blob = COALESCE(%s, status_payload_blob),
                        result_payload_blob = COALESCE(%s, result_payload_blob),
                        cancel_requested_at = COALESCE(%s, cancel_requested_at),
                        result_message_id = COALESCE(%s, result_message_id),
                        terminal_emitted_at = COALESCE(%s, terminal_emitted_at)
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
                        status_payload_blob,
                        result_payload_blob,
                        cancel_requested_at,
                        result_message_id,
                        terminal_emitted_at,
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
