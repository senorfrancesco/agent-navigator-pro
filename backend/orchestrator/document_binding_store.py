from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol


DEFAULT_DOCUMENT_BINDING_DB_URL = os.getenv(
    "ORCHESTRATOR_DOCUMENT_BINDING_DB_URL",
    "sqlite:///.data/orchestrator_document_bindings.db",
)
SESSION_RAG_TTL_HOURS = int(os.getenv("SESSION_RAG_TTL_HOURS", "72"))
_STORE_LOCK = threading.Lock()
_STORE_SINGLETON: Optional["DocumentBindingStoreProtocol"] = None


@dataclass
class DocumentBindingRecord:
    document_id: str
    version_id: str
    thread_id: Optional[str]
    upload_id: Optional[str]
    file_id: Optional[str]
    display_name: str
    storage_path: Optional[str]
    source_scope: str
    ingestion_status: str
    resolved_identity: str
    resolved_identity_kind: str
    expires_at: Optional[float]
    is_active: bool


class DocumentBindingStoreProtocol(Protocol):
    def register_document_binding_sync(
        self,
        *,
        thread_id: Optional[str],
        binding: Dict[str, Any],
        source_scope: str = "session",
    ) -> DocumentBindingRecord: ...

    def list_thread_bindings_sync(
        self,
        thread_id: str,
        *,
        active_only: bool = True,
    ) -> List[DocumentBindingRecord]: ...

    def list_expired_bindings_sync(
        self,
        *,
        now: Optional[float] = None,
    ) -> List[DocumentBindingRecord]: ...

    def prune_expired_bindings_sync(self, *, now: Optional[float] = None) -> int: ...

    async def register_document_binding(
        self,
        *,
        thread_id: Optional[str],
        binding: Dict[str, Any],
        source_scope: str = "session",
    ) -> DocumentBindingRecord: ...

    async def list_thread_bindings(
        self,
        thread_id: str,
        *,
        active_only: bool = True,
    ) -> List[DocumentBindingRecord]: ...

    async def list_expired_bindings(
        self,
        *,
        now: Optional[float] = None,
    ) -> List[DocumentBindingRecord]: ...

    async def prune_expired_bindings(self, *, now: Optional[float] = None) -> int: ...


def _sqlite_db_path_from_url(db_url: str) -> str:
    if db_url.startswith("sqlite:///"):
        raw_path = db_url.split("sqlite:///", 1)[1]
    elif db_url.startswith("sqlite://"):
        raw_path = db_url.split("sqlite://", 1)[1].lstrip("/")
    else:
        raw_path = DEFAULT_DOCUMENT_BINDING_DB_URL.split("sqlite:///", 1)[1]
    if raw_path.startswith("/"):
        return raw_path
    return os.path.join(os.getcwd(), raw_path)


def _stable_id(prefix: str, value: str) -> str:
    token = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{token}"


def _normalize_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


class SQLiteDocumentBindingStore:
    def __init__(self, db_url: str = DEFAULT_DOCUMENT_BINDING_DB_URL):
        self.db_url = db_url
        self.db_path = _sqlite_db_path_from_url(db_url)
        self._bootstrap()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _bootstrap(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        schema = """
        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            origin TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS document_versions (
            version_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            upload_id TEXT,
            file_id TEXT,
            storage_path TEXT,
            content_hash TEXT,
            mime_type TEXT,
            ingestion_status TEXT NOT NULL,
            source_scope TEXT NOT NULL,
            resolved_identity TEXT NOT NULL,
            resolved_identity_kind TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS thread_document_bindings (
            binding_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            expires_at REAL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(version_id) REFERENCES document_versions(version_id) ON DELETE CASCADE,
            UNIQUE(thread_id, version_id)
        );

        CREATE INDEX IF NOT EXISTS idx_document_versions_document_id
            ON document_versions(document_id);
        CREATE INDEX IF NOT EXISTS idx_document_versions_resolved_identity
            ON document_versions(resolved_identity);
        CREATE INDEX IF NOT EXISTS idx_thread_document_bindings_thread_id
            ON thread_document_bindings(thread_id);
        CREATE INDEX IF NOT EXISTS idx_thread_document_bindings_expires_at
            ON thread_document_bindings(expires_at);
        """
        with self._connect() as conn:
            conn.executescript(schema)
            conn.commit()

    def _resolve_payload(self, binding: Dict[str, Any], source_scope: str) -> Dict[str, Any]:
        document_id = _normalize_text(binding.get("document_id"))
        upload_id = _normalize_text(binding.get("upload_id"))
        file_id = _normalize_text(binding.get("file_id"))
        version_id = _normalize_text(binding.get("version_id"))
        session_file_ref = _normalize_text(binding.get("session_file_ref"))
        file_path = _normalize_text(binding.get("file_path"))
        label = _normalize_text(binding.get("label") or binding.get("display_name"))

        resolved_identity = (
            document_id
            or upload_id
            or file_id
            or version_id
            or session_file_ref
            or file_path
        )
        if not resolved_identity:
            raise ValueError("document binding requires at least one identity field")

        if document_id is None:
            if upload_id is not None:
                document_id = f"upload:{upload_id}"
            elif file_id is not None:
                document_id = f"file:{file_id}"
            else:
                document_id = _stable_id("doc", resolved_identity)

        if version_id is None:
            version_seed = "|".join(
                [
                    document_id,
                    upload_id or "",
                    file_id or "",
                    file_path or "",
                    session_file_ref or "",
                    source_scope or "session",
                ]
            )
            version_id = _stable_id("ver", version_seed)

        resolved_identity_kind = (
            "document_id"
            if _normalize_text(binding.get("document_id"))
            else "upload_id"
            if upload_id
            else "file_id"
            if file_id
            else "version_id"
            if _normalize_text(binding.get("version_id"))
            else "session_file_ref"
            if session_file_ref
            else "file_path"
        )
        display_name = label or (os.path.basename(file_path) if file_path else None) or document_id
        return {
            "document_id": document_id,
            "version_id": version_id,
            "upload_id": upload_id,
            "file_id": file_id,
            "display_name": display_name,
            "storage_path": file_path,
            "source_scope": str(source_scope or "session"),
            "ingestion_status": _normalize_text(binding.get("ingestion_status")) or "registered",
            "resolved_identity": resolved_identity,
            "resolved_identity_kind": resolved_identity_kind,
            "origin": resolved_identity_kind,
        }

    def _row_to_binding(self, row: sqlite3.Row) -> DocumentBindingRecord:
        return DocumentBindingRecord(
            document_id=str(row["document_id"]),
            version_id=str(row["version_id"]),
            thread_id=row["thread_id"],
            upload_id=row["upload_id"],
            file_id=row["file_id"],
            display_name=str(row["display_name"]),
            storage_path=row["storage_path"],
            source_scope=str(row["source_scope"]),
            ingestion_status=str(row["ingestion_status"]),
            resolved_identity=str(row["resolved_identity"]),
            resolved_identity_kind=str(row["resolved_identity_kind"]),
            expires_at=float(row["expires_at"]) if row["expires_at"] is not None else None,
            is_active=bool(row["is_active"]) if row["is_active"] is not None else False,
        )

    def _load_binding_sync(
        self,
        *,
        version_id: str,
        thread_id: Optional[str],
    ) -> DocumentBindingRecord:
        query = """
            SELECT
                d.document_id,
                d.display_name,
                v.version_id,
                v.upload_id,
                v.file_id,
                v.storage_path,
                v.source_scope,
                v.ingestion_status,
                v.resolved_identity,
                v.resolved_identity_kind,
                b.thread_id,
                b.expires_at,
                b.is_active
            FROM document_versions v
            JOIN documents d ON d.document_id = v.document_id
            LEFT JOIN thread_document_bindings b
                ON b.version_id = v.version_id AND (? IS NOT NULL AND b.thread_id = ?)
            WHERE v.version_id = ?
            ORDER BY b.updated_at DESC
            LIMIT 1
        """
        with self._connect() as conn:
            row = conn.execute(query, (thread_id, thread_id, version_id)).fetchone()
        if row is None:
            raise KeyError(f"unknown-document-version:{version_id}")
        return self._row_to_binding(row)

    def register_document_binding_sync(
        self,
        *,
        thread_id: Optional[str],
        binding: Dict[str, Any],
        source_scope: str = "session",
    ) -> DocumentBindingRecord:
        now = time.time()
        normalized = self._resolve_payload(binding, source_scope)
        expires_at = None
        if normalized["source_scope"] == "session" and thread_id:
            expires_at = now + max(1, SESSION_RAG_TTL_HOURS) * 3600

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (document_id, display_name, origin, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    origin = excluded.origin,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized["document_id"],
                    normalized["display_name"],
                    normalized["origin"],
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO document_versions (
                    version_id, document_id, upload_id, file_id, storage_path, content_hash, mime_type,
                    ingestion_status, source_scope, resolved_identity, resolved_identity_kind, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(version_id) DO UPDATE SET
                    upload_id = excluded.upload_id,
                    file_id = excluded.file_id,
                    storage_path = excluded.storage_path,
                    ingestion_status = excluded.ingestion_status,
                    source_scope = excluded.source_scope,
                    resolved_identity = excluded.resolved_identity,
                    resolved_identity_kind = excluded.resolved_identity_kind,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized["version_id"],
                    normalized["document_id"],
                    normalized["upload_id"],
                    normalized["file_id"],
                    normalized["storage_path"],
                    None,
                    None,
                    normalized["ingestion_status"],
                    normalized["source_scope"],
                    normalized["resolved_identity"],
                    normalized["resolved_identity_kind"],
                    now,
                    now,
                ),
            )
            if thread_id:
                conn.execute(
                    """
                    INSERT INTO thread_document_bindings (
                        binding_id, thread_id, version_id, expires_at, is_active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id, version_id) DO UPDATE SET
                        expires_at = excluded.expires_at,
                        is_active = excluded.is_active,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(uuid.uuid4()),
                        thread_id,
                        normalized["version_id"],
                        expires_at,
                        1,
                        now,
                        now,
                    ),
                )
            conn.commit()
        return self._load_binding_sync(version_id=normalized["version_id"], thread_id=thread_id)

    def list_thread_bindings_sync(
        self,
        thread_id: str,
        *,
        active_only: bool = True,
    ) -> List[DocumentBindingRecord]:
        query = """
            SELECT
                d.document_id,
                d.display_name,
                v.version_id,
                v.upload_id,
                v.file_id,
                v.storage_path,
                v.source_scope,
                v.ingestion_status,
                v.resolved_identity,
                v.resolved_identity_kind,
                b.thread_id,
                b.expires_at,
                b.is_active
            FROM thread_document_bindings b
            JOIN document_versions v ON v.version_id = b.version_id
            JOIN documents d ON d.document_id = v.document_id
            WHERE b.thread_id = ?
        """
        params: List[Any] = [thread_id]
        if active_only:
            query += " AND b.is_active = 1 AND (b.expires_at IS NULL OR b.expires_at >= ?)"
            params.append(time.time())
        query += " ORDER BY b.updated_at DESC, d.display_name ASC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_binding(row) for row in rows]

    def list_expired_bindings_sync(self, *, now: Optional[float] = None) -> List[DocumentBindingRecord]:
        timestamp = now if now is not None else time.time()
        query = """
            SELECT
                d.document_id,
                d.display_name,
                v.version_id,
                v.upload_id,
                v.file_id,
                v.storage_path,
                v.source_scope,
                v.ingestion_status,
                v.resolved_identity,
                v.resolved_identity_kind,
                b.thread_id,
                b.expires_at,
                b.is_active
            FROM thread_document_bindings b
            JOIN document_versions v ON v.version_id = b.version_id
            JOIN documents d ON d.document_id = v.document_id
            WHERE b.is_active = 1 AND b.expires_at IS NOT NULL AND b.expires_at < ?
            ORDER BY b.expires_at ASC, b.updated_at ASC
        """
        with self._connect() as conn:
            rows = conn.execute(query, (timestamp,)).fetchall()
        return [self._row_to_binding(row) for row in rows]

    def prune_expired_bindings_sync(self, *, now: Optional[float] = None) -> int:
        timestamp = now if now is not None else time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE thread_document_bindings
                SET is_active = 0, updated_at = ?
                WHERE is_active = 1 AND expires_at IS NOT NULL AND expires_at < ?
                """,
                (timestamp, timestamp),
            )
            conn.commit()
        return int(cursor.rowcount or 0)

    async def register_document_binding(
        self,
        *,
        thread_id: Optional[str],
        binding: Dict[str, Any],
        source_scope: str = "session",
    ) -> DocumentBindingRecord:
        return self.register_document_binding_sync(
            thread_id=thread_id,
            binding=binding,
            source_scope=source_scope,
        )

    async def list_thread_bindings(
        self,
        thread_id: str,
        *,
        active_only: bool = True,
    ) -> List[DocumentBindingRecord]:
        return self.list_thread_bindings_sync(thread_id, active_only=active_only)

    async def list_expired_bindings(self, *, now: Optional[float] = None) -> List[DocumentBindingRecord]:
        return self.list_expired_bindings_sync(now=now)

    async def prune_expired_bindings(self, *, now: Optional[float] = None) -> int:
        return self.prune_expired_bindings_sync(now=now)


def get_document_binding_store() -> DocumentBindingStoreProtocol:
    global _STORE_SINGLETON
    with _STORE_LOCK:
        if _STORE_SINGLETON is None:
            _STORE_SINGLETON = SQLiteDocumentBindingStore()
    return _STORE_SINGLETON
