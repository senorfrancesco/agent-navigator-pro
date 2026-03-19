from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


DEFAULT_STATE_DB_URL = os.getenv("ORCHESTRATOR_STATE_DB_URL", "sqlite:///.data/orchestrator_state.db")
_STORE_LOCK = threading.Lock()
_STORE_SINGLETON: Optional["OrchestrationStateStore"] = None


class StateStoreConfigurationError(RuntimeError):
    pass


@dataclass
class OrchestrationRunRecord:
    run_id: str
    thread_id: Optional[str]
    session_id: Optional[str]
    workflow_type: str
    status: str
    state_ref: str
    pending_action_id: Optional[str]
    resume_state_blob: Optional[Dict[str, Any]]
    checkpoint_blob: Optional[Dict[str, Any]]
    version: int
    created_at: float
    updated_at: float
    last_error: Optional[str]
    idempotency_key: Optional[str]


class OrchestrationStateStore(Protocol):
    async def get_or_create_run(
        self,
        *,
        thread_id: Optional[str],
        session_id: Optional[str],
        workflow_type: str,
        idempotency_key: Optional[str] = None,
    ) -> OrchestrationRunRecord: ...

    async def load_run(
        self,
        *,
        run_id: Optional[str] = None,
        state_ref: Optional[str] = None,
        thread_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[OrchestrationRunRecord]: ...

    async def save_run(
        self,
        *,
        run_id: str,
        status: str,
        pending_action_id: Optional[str],
        resume_state_blob: Optional[Dict[str, Any]],
        checkpoint_blob: Optional[Dict[str, Any]],
        last_error: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> OrchestrationRunRecord: ...


def _sqlite_db_path_from_url(db_url: str) -> str:
    if db_url.startswith("sqlite:///"):
        raw_path = db_url.split("sqlite:///", 1)[1]
    elif db_url.startswith("sqlite://"):
        raw_path = db_url.split("sqlite://", 1)[1].lstrip("/")
    else:
        fallback = DEFAULT_STATE_DB_URL.split("sqlite:///", 1)[1]
        raw_path = fallback
    if raw_path.startswith("/"):
        return raw_path
    return os.path.join(os.getcwd(), raw_path)


def _is_postgres_db_url(db_url: str) -> bool:
    return db_url.startswith("postgres://") or db_url.startswith("postgresql://")


def _json_dump(value: Optional[Dict[str, Any]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load(value: Optional[str]) -> Optional[Dict[str, Any]]:
    if not value:
        return None
    return json.loads(value)


class SQLiteOrchestrationStateStore:
    def __init__(self, db_url: str = DEFAULT_STATE_DB_URL):
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
        CREATE TABLE IF NOT EXISTS orchestrator_runs (
            run_id TEXT PRIMARY KEY,
            thread_id TEXT,
            session_id TEXT,
            workflow_type TEXT NOT NULL,
            status TEXT NOT NULL,
            state_ref TEXT NOT NULL UNIQUE,
            pending_action_id TEXT,
            resume_state_blob TEXT,
            checkpoint_blob TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_error TEXT,
            idempotency_key TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_orchestrator_runs_thread_id
            ON orchestrator_runs(thread_id);
        CREATE INDEX IF NOT EXISTS idx_orchestrator_runs_session_id
            ON orchestrator_runs(session_id);
        CREATE INDEX IF NOT EXISTS idx_orchestrator_runs_updated_at
            ON orchestrator_runs(updated_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_orchestrator_runs_idempotency
            ON orchestrator_runs(workflow_type, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
        """
        with self._connect() as conn:
            conn.executescript(schema)
            conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> OrchestrationRunRecord:
        return OrchestrationRunRecord(
            run_id=str(row["run_id"]),
            thread_id=row["thread_id"],
            session_id=row["session_id"],
            workflow_type=str(row["workflow_type"]),
            status=str(row["status"]),
            state_ref=str(row["state_ref"]),
            pending_action_id=row["pending_action_id"],
            resume_state_blob=_json_load(row["resume_state_blob"]),
            checkpoint_blob=_json_load(row["checkpoint_blob"]),
            version=int(row["version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            last_error=row["last_error"],
            idempotency_key=row["idempotency_key"],
        )

    def _load_run_sync(
        self,
        *,
        run_id: Optional[str] = None,
        state_ref: Optional[str] = None,
        thread_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[OrchestrationRunRecord]:
        query = None
        params: tuple[Any, ...] = ()
        if run_id:
            query = "SELECT * FROM orchestrator_runs WHERE run_id = ?"
            params = (run_id,)
        elif state_ref:
            query = "SELECT * FROM orchestrator_runs WHERE state_ref = ?"
            params = (state_ref,)
        elif thread_id:
            query = "SELECT * FROM orchestrator_runs WHERE thread_id = ? ORDER BY updated_at DESC LIMIT 1"
            params = (thread_id,)
        elif session_id:
            query = "SELECT * FROM orchestrator_runs WHERE session_id = ? ORDER BY updated_at DESC LIMIT 1"
            params = (session_id,)
        else:
            return None
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return self._row_to_record(row) if row else None

    def _load_by_idempotency_sync(self, *, workflow_type: str, idempotency_key: str) -> Optional[OrchestrationRunRecord]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM orchestrator_runs
                WHERE workflow_type = ? AND idempotency_key = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (workflow_type, idempotency_key),
            ).fetchone()
        return self._row_to_record(row) if row else None

    async def load_run(
        self,
        *,
        run_id: Optional[str] = None,
        state_ref: Optional[str] = None,
        thread_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[OrchestrationRunRecord]:
        return self._load_run_sync(
            run_id=run_id,
            state_ref=state_ref,
            thread_id=thread_id,
            session_id=session_id,
        )

    def _get_or_create_run_sync(
        self,
        *,
        thread_id: Optional[str],
        session_id: Optional[str],
        workflow_type: str,
        idempotency_key: Optional[str] = None,
    ) -> OrchestrationRunRecord:
        if idempotency_key:
            existing = self._load_by_idempotency_sync(workflow_type=workflow_type, idempotency_key=idempotency_key)
            if existing is not None:
                return existing
        existing = self._load_run_sync(thread_id=thread_id) if thread_id else None
        if existing is None and session_id:
            existing = self._load_run_sync(session_id=session_id)
        if existing is not None:
            return existing

        now = time.time()
        run_id = str(uuid.uuid4())
        state_ref = f"run:{run_id}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO orchestrator_runs (
                    run_id, thread_id, session_id, workflow_type, status, state_ref,
                    pending_action_id, resume_state_blob, checkpoint_blob, version,
                    created_at, updated_at, last_error, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    thread_id,
                    session_id,
                    workflow_type,
                    "initialized",
                    state_ref,
                    None,
                    None,
                    None,
                    1,
                    now,
                    now,
                    None,
                    idempotency_key,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM orchestrator_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RuntimeError("Failed to persist orchestration run")
        return self._row_to_record(row)

    async def get_or_create_run(
        self,
        *,
        thread_id: Optional[str],
        session_id: Optional[str],
        workflow_type: str,
        idempotency_key: Optional[str] = None,
    ) -> OrchestrationRunRecord:
        return self._get_or_create_run_sync(
            thread_id=thread_id,
            session_id=session_id,
            workflow_type=workflow_type,
            idempotency_key=idempotency_key,
        )

    def _save_run_sync(
        self,
        *,
        run_id: str,
        status: str,
        pending_action_id: Optional[str],
        resume_state_blob: Optional[Dict[str, Any]],
        checkpoint_blob: Optional[Dict[str, Any]],
        last_error: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> OrchestrationRunRecord:
        current = self._load_run_sync(run_id=run_id)
        if current is None:
            raise KeyError(f"Unknown run_id={run_id}")
        required_version = expected_version if expected_version is not None else current.version
        if expected_version is not None and current.version != expected_version:
            raise RuntimeError(
                f"Version mismatch for run_id={run_id}: expected {expected_version}, got {current.version}"
            )
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE orchestrator_runs
                SET status = ?, pending_action_id = ?, resume_state_blob = ?, checkpoint_blob = ?,
                    version = version + 1, updated_at = ?, last_error = ?
                WHERE run_id = ? AND version = ?
                """,
                (
                    status,
                    pending_action_id,
                    _json_dump(resume_state_blob),
                    _json_dump(checkpoint_blob),
                    now,
                    last_error,
                    run_id,
                    required_version,
                ),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                row = conn.execute("SELECT version FROM orchestrator_runs WHERE run_id = ?", (run_id,)).fetchone()
                actual_version = row["version"] if row is not None else "missing"
                raise RuntimeError(
                    f"Version mismatch for run_id={run_id}: expected {required_version}, got {actual_version}"
                )
            conn.commit()
            row = conn.execute("SELECT * FROM orchestrator_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"Failed to update run_id={run_id}")
        return self._row_to_record(row)

    async def save_run(
        self,
        *,
        run_id: str,
        status: str,
        pending_action_id: Optional[str],
        resume_state_blob: Optional[Dict[str, Any]],
        checkpoint_blob: Optional[Dict[str, Any]],
        last_error: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> OrchestrationRunRecord:
        return self._save_run_sync(
            run_id=run_id,
            status=status,
            pending_action_id=pending_action_id,
            resume_state_blob=resume_state_blob,
            checkpoint_blob=checkpoint_blob,
            last_error=last_error,
            expected_version=expected_version,
        )


class PostgresOrchestrationStateStore:
    def __init__(self, db_url: str):
        self.db_url = db_url
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise StateStoreConfigurationError(
                "Postgres state store requires psycopg. Install psycopg[binary] or use sqlite://"
            ) from exc
        self._psycopg = psycopg
        self._bootstrap()

    def _connect(self):
        conn = self._psycopg.connect(self.db_url)
        conn.autocommit = False
        return conn

    def _bootstrap(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS orchestrator_runs (
            run_id TEXT PRIMARY KEY,
            thread_id TEXT,
            session_id TEXT,
            workflow_type TEXT NOT NULL,
            status TEXT NOT NULL,
            state_ref TEXT NOT NULL UNIQUE,
            pending_action_id TEXT,
            resume_state_blob JSONB,
            checkpoint_blob JSONB,
            version INTEGER NOT NULL DEFAULT 1,
            created_at DOUBLE PRECISION NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL,
            last_error TEXT,
            idempotency_key TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_orchestrator_runs_thread_id
            ON orchestrator_runs(thread_id);
        CREATE INDEX IF NOT EXISTS idx_orchestrator_runs_session_id
            ON orchestrator_runs(session_id);
        CREATE INDEX IF NOT EXISTS idx_orchestrator_runs_updated_at
            ON orchestrator_runs(updated_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_orchestrator_runs_idempotency
            ON orchestrator_runs(workflow_type, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
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

    def _row_to_record(self, row: Dict[str, Any]) -> OrchestrationRunRecord:
        return OrchestrationRunRecord(
            run_id=str(row["run_id"]),
            thread_id=row["thread_id"],
            session_id=row["session_id"],
            workflow_type=str(row["workflow_type"]),
            status=str(row["status"]),
            state_ref=str(row["state_ref"]),
            pending_action_id=row["pending_action_id"],
            resume_state_blob=row["resume_state_blob"],
            checkpoint_blob=row["checkpoint_blob"],
            version=int(row["version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            last_error=row["last_error"],
            idempotency_key=row["idempotency_key"],
        )

    async def load_run(
        self,
        *,
        run_id: Optional[str] = None,
        state_ref: Optional[str] = None,
        thread_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[OrchestrationRunRecord]:
        query = None
        params: tuple[Any, ...] = ()
        if run_id:
            query = "SELECT * FROM orchestrator_runs WHERE run_id = %s"
            params = (run_id,)
        elif state_ref:
            query = "SELECT * FROM orchestrator_runs WHERE state_ref = %s"
            params = (state_ref,)
        elif thread_id:
            query = "SELECT * FROM orchestrator_runs WHERE thread_id = %s ORDER BY updated_at DESC LIMIT 1"
            params = (thread_id,)
        elif session_id:
            query = "SELECT * FROM orchestrator_runs WHERE session_id = %s ORDER BY updated_at DESC LIMIT 1"
            params = (session_id,)
        else:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = self._fetchone(cur)
        return self._row_to_record(row) if row else None

    async def get_or_create_run(
        self,
        *,
        thread_id: Optional[str],
        session_id: Optional[str],
        workflow_type: str,
        idempotency_key: Optional[str] = None,
    ) -> OrchestrationRunRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if idempotency_key:
                    cur.execute(
                        """
                        SELECT * FROM orchestrator_runs
                        WHERE workflow_type = %s AND idempotency_key = %s
                        ORDER BY updated_at DESC LIMIT 1
                        """,
                        (workflow_type, idempotency_key),
                    )
                    row = self._fetchone(cur)
                    if row is not None:
                        return self._row_to_record(row)
                if thread_id:
                    cur.execute(
                        "SELECT * FROM orchestrator_runs WHERE thread_id = %s ORDER BY updated_at DESC LIMIT 1",
                        (thread_id,),
                    )
                    row = self._fetchone(cur)
                    if row is not None:
                        return self._row_to_record(row)
                if session_id:
                    cur.execute(
                        "SELECT * FROM orchestrator_runs WHERE session_id = %s ORDER BY updated_at DESC LIMIT 1",
                        (session_id,),
                    )
                    row = self._fetchone(cur)
                    if row is not None:
                        return self._row_to_record(row)

                now = time.time()
                run_id = str(uuid.uuid4())
                state_ref = f"run:{run_id}"
                cur.execute(
                    """
                    INSERT INTO orchestrator_runs (
                        run_id, thread_id, session_id, workflow_type, status, state_ref,
                        pending_action_id, resume_state_blob, checkpoint_blob, version,
                        created_at, updated_at, last_error, idempotency_key
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        run_id,
                        thread_id,
                        session_id,
                        workflow_type,
                        "initialized",
                        state_ref,
                        None,
                        self._psycopg.types.json.Jsonb(None),
                        self._psycopg.types.json.Jsonb(None),
                        1,
                        now,
                        now,
                        None,
                        idempotency_key,
                    ),
                )
                row = self._fetchone(cur)
            conn.commit()
        if row is None:
            raise RuntimeError("Failed to persist orchestration run")
        return self._row_to_record(row)

    async def save_run(
        self,
        *,
        run_id: str,
        status: str,
        pending_action_id: Optional[str],
        resume_state_blob: Optional[Dict[str, Any]],
        checkpoint_blob: Optional[Dict[str, Any]],
        last_error: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> OrchestrationRunRecord:
        current = await self.load_run(run_id=run_id)
        if current is None:
            raise KeyError(f"Unknown run_id={run_id}")
        required_version = expected_version if expected_version is not None else current.version
        if expected_version is not None and current.version != expected_version:
            raise RuntimeError(
                f"Version mismatch for run_id={run_id}: expected {expected_version}, got {current.version}"
            )

        now = time.time()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orchestrator_runs
                    SET status = %s, pending_action_id = %s, resume_state_blob = %s, checkpoint_blob = %s,
                        version = version + 1, updated_at = %s, last_error = %s
                    WHERE run_id = %s AND version = %s
                    RETURNING *
                    """,
                    (
                        status,
                        pending_action_id,
                        self._psycopg.types.json.Jsonb(resume_state_blob),
                        self._psycopg.types.json.Jsonb(checkpoint_blob),
                        now,
                        last_error,
                        run_id,
                        required_version,
                    ),
                )
                row = self._fetchone(cur)
            if row is None:
                conn.rollback()
                actual = await self.load_run(run_id=run_id)
                actual_version = actual.version if actual is not None else "missing"
                raise RuntimeError(
                    f"Version mismatch for run_id={run_id}: expected {required_version}, got {actual_version}"
                )
            conn.commit()
        return self._row_to_record(row)


def get_orchestration_state_store() -> OrchestrationStateStore:
    global _STORE_SINGLETON
    if _STORE_SINGLETON is not None:
        return _STORE_SINGLETON

    with _STORE_LOCK:
        if _STORE_SINGLETON is None:
            db_url = os.getenv("ORCHESTRATOR_STATE_DB_URL", DEFAULT_STATE_DB_URL)
            if db_url.startswith("sqlite://"):
                _STORE_SINGLETON = SQLiteOrchestrationStateStore(db_url)
            elif _is_postgres_db_url(db_url):
                _STORE_SINGLETON = PostgresOrchestrationStateStore(db_url)
            else:
                raise StateStoreConfigurationError(
                    f"Unsupported ORCHESTRATOR_STATE_DB_URL scheme: {db_url}"
                )
    return _STORE_SINGLETON
