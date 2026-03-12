from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


DEFAULT_KB_DB_URL = os.getenv("ORCHESTRATOR_KB_DB_URL", "sqlite:///.data/orchestrator_kb.db")
_STORE_LOCK = threading.Lock()
_STORE_SINGLETON: Optional["SQLiteKnowledgeBaseStore"] = None


@dataclass
class KnowledgeBaseSourceRecord:
    source_id: str
    collection_id: str
    display_name: str
    content_hash: str
    mime_type: str
    status: str
    index_version: str
    embedding_model_id: str
    chunking_version: str
    created_at: float
    updated_at: float


@dataclass
class KnowledgeBaseChunkRecord:
    chunk_id: str
    source_id: str
    collection_id: str
    display_name: str
    chunk_index: int
    text: str
    metadata_json: Dict[str, Any]
    source_origin: str


def _sqlite_db_path_from_url(db_url: str) -> str:
    if db_url.startswith("sqlite:///"):
        raw_path = db_url.split("sqlite:///", 1)[1]
    elif db_url.startswith("sqlite://"):
        raw_path = db_url.split("sqlite://", 1)[1].lstrip("/")
    else:
        raw_path = DEFAULT_KB_DB_URL.split("sqlite:///", 1)[1]
    if raw_path.startswith("/"):
        return raw_path
    return os.path.join(os.getcwd(), raw_path)


def _json_dump(value: Dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load(value: str) -> Dict[str, Any]:
    return json.loads(value) if value else {}


class SQLiteKnowledgeBaseStore:
    def __init__(self, db_url: str = DEFAULT_KB_DB_URL):
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
        CREATE TABLE IF NOT EXISTS kb_sources (
            source_id TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            status TEXT NOT NULL,
            index_version TEXT NOT NULL,
            embedding_model_id TEXT NOT NULL,
            chunking_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(collection_id, content_hash)
        );

        CREATE TABLE IF NOT EXISTS kb_chunks (
            chunk_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            source_origin TEXT NOT NULL,
            FOREIGN KEY(source_id) REFERENCES kb_sources(source_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_kb_sources_collection_id
            ON kb_sources(collection_id);
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_source_id
            ON kb_chunks(source_id);
        """
        with self._connect() as conn:
            conn.executescript(schema)
            conn.commit()

    def _row_to_source(self, row: sqlite3.Row) -> KnowledgeBaseSourceRecord:
        return KnowledgeBaseSourceRecord(
            source_id=str(row["source_id"]),
            collection_id=str(row["collection_id"]),
            display_name=str(row["display_name"]),
            content_hash=str(row["content_hash"]),
            mime_type=str(row["mime_type"]),
            status=str(row["status"]),
            index_version=str(row["index_version"]),
            embedding_model_id=str(row["embedding_model_id"]),
            chunking_version=str(row["chunking_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _row_to_chunk(self, row: sqlite3.Row) -> KnowledgeBaseChunkRecord:
        return KnowledgeBaseChunkRecord(
            chunk_id=str(row["chunk_id"]),
            source_id=str(row["source_id"]),
            collection_id=str(row["collection_id"]),
            display_name=str(row["display_name"]),
            chunk_index=int(row["chunk_index"]),
            text=str(row["text"]),
            metadata_json=_json_load(str(row["metadata_json"])),
            source_origin=str(row["source_origin"]),
        )

    def register_source_sync(
        self,
        *,
        collection_id: str,
        display_name: str,
        content_hash: str,
        mime_type: str,
        index_version: str,
        embedding_model_id: str,
        chunking_version: str,
        status: str = "indexed",
    ) -> KnowledgeBaseSourceRecord:
        now = time.time()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM kb_sources WHERE collection_id = ? AND content_hash = ?",
                (collection_id, content_hash),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    """
                    UPDATE kb_sources
                    SET updated_at = ?, status = ?, display_name = ?, mime_type = ?,
                        index_version = ?, embedding_model_id = ?, chunking_version = ?
                    WHERE source_id = ?
                    """,
                    (
                        now,
                        status,
                        display_name,
                        mime_type,
                        index_version,
                        embedding_model_id,
                        chunking_version,
                        existing["source_id"],
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM kb_sources WHERE source_id = ?",
                    (existing["source_id"],),
                ).fetchone()
                return self._row_to_source(row)

            source_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO kb_sources (
                    source_id, collection_id, display_name, content_hash, mime_type, status,
                    index_version, embedding_model_id, chunking_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    collection_id,
                    display_name,
                    content_hash,
                    mime_type,
                    status,
                    index_version,
                    embedding_model_id,
                    chunking_version,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM kb_sources WHERE source_id = ?", (source_id,)).fetchone()
        return self._row_to_source(row)

    def replace_chunks_sync(self, *, source_id: str, chunks: List[Dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM kb_chunks WHERE source_id = ?", (source_id,))
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO kb_chunks (
                        chunk_id, source_id, chunk_index, text, metadata_json, source_origin
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(chunk["chunk_id"]),
                        source_id,
                        int(chunk["chunk_index"]),
                        str(chunk["text"]),
                        _json_dump(dict(chunk.get("metadata_json") or {})),
                        str(chunk.get("source_origin") or "knowledge_base"),
                    ),
                )
            conn.commit()

    def list_sources_sync(self, collection_id: str) -> List[KnowledgeBaseSourceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM kb_sources WHERE collection_id = ? ORDER BY updated_at DESC",
                (collection_id,),
            ).fetchall()
        return [self._row_to_source(row) for row in rows]

    def list_chunks_sync(
        self,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
    ) -> List[KnowledgeBaseChunkRecord]:
        query = """
            SELECT c.*, s.collection_id, s.display_name
            FROM kb_chunks c
            JOIN kb_sources s ON s.source_id = c.source_id
            WHERE s.collection_id = ?
        """
        params: List[Any] = [collection_id]
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            query += f" AND c.source_id IN ({placeholders})"
            params.extend(source_ids)
        query += " ORDER BY s.display_name, c.chunk_index"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    async def register_source(self, **kwargs: Any) -> KnowledgeBaseSourceRecord:
        return self.register_source_sync(**kwargs)

    async def replace_chunks(self, *, source_id: str, chunks: List[Dict[str, Any]]) -> None:
        self.replace_chunks_sync(source_id=source_id, chunks=chunks)

    async def list_sources(self, collection_id: str) -> List[KnowledgeBaseSourceRecord]:
        return self.list_sources_sync(collection_id)

    async def list_chunks(
        self,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
    ) -> List[KnowledgeBaseChunkRecord]:
        return self.list_chunks_sync(collection_id, source_ids=source_ids)


def get_knowledge_base_store() -> SQLiteKnowledgeBaseStore:
    global _STORE_SINGLETON
    with _STORE_LOCK:
        if _STORE_SINGLETON is None:
            _STORE_SINGLETON = SQLiteKnowledgeBaseStore()
    return _STORE_SINGLETON
