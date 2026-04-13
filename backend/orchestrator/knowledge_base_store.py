from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

import numpy as np

from orchestrator.rag.retriever import HybridRetriever


DEFAULT_KB_DB_URL = os.getenv("ORCHESTRATOR_KB_DB_URL", "sqlite:///.data/orchestrator_kb.db")
_STORE_LOCK = threading.Lock()
_STORE_SINGLETON: Optional["KnowledgeBaseStoreProtocol"] = None


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
    embedding: Optional[np.ndarray] = None
    embedding_dim: Optional[int] = None
    embedding_model_id: Optional[str] = None


@dataclass
class KnowledgeBaseChunkMatchRecord:
    chunk: KnowledgeBaseChunkRecord
    raw_score: float
    source_scope: str
    payload: Dict[str, Any]


@runtime_checkable
class KnowledgeBaseStoreProtocol(Protocol):
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
    ) -> KnowledgeBaseSourceRecord: ...

    def replace_chunks_sync(self, *, source_id: str, chunks: List[Dict[str, Any]]) -> None: ...

    def list_sources_sync(self, collection_id: str) -> List[KnowledgeBaseSourceRecord]: ...

    def list_chunks_sync(
        self,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        include_embeddings: bool = False,
    ) -> List[KnowledgeBaseChunkRecord]: ...

    def search_chunks_sync(
        self,
        *,
        collection_id: str,
        query_text: str,
        query_embedding: np.ndarray,
        top_k: int,
        source_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        mode: str = "hybrid",
        embed_fn: Optional[Callable] = None,
    ) -> List[KnowledgeBaseChunkMatchRecord]: ...

    def delete_chunks_sync(
        self,
        *,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int: ...

    async def register_source(self, **kwargs: Any) -> KnowledgeBaseSourceRecord: ...

    async def replace_chunks(self, *, source_id: str, chunks: List[Dict[str, Any]]) -> None: ...

    async def list_sources(self, collection_id: str) -> List[KnowledgeBaseSourceRecord]: ...

    async def list_chunks(
        self,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        include_embeddings: bool = False,
    ) -> List[KnowledgeBaseChunkRecord]: ...

    async def search_chunks(
        self,
        *,
        collection_id: str,
        query_text: str,
        query_embedding: np.ndarray,
        top_k: int,
        source_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        mode: str = "hybrid",
        embed_fn: Optional[Callable] = None,
    ) -> List[KnowledgeBaseChunkMatchRecord]: ...

    async def delete_chunks(
        self,
        *,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int: ...


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


def _serialize_embedding(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    array = np.asarray(value, dtype=np.float32)
    if array.ndim != 1:
        raise ValueError("Chunk embedding must be a 1D vector")
    return array.tobytes()


def _deserialize_embedding(blob: Optional[bytes], dim: Optional[int]) -> Optional[np.ndarray]:
    if blob is None or dim is None:
        return None
    array = np.frombuffer(blob, dtype=np.float32)
    if array.size != dim:
        return None
    return array.copy()


def _normalize_source_scope(value: Any, *, collection_id: Optional[str] = None) -> str:
    scope = str(value or "").strip().lower()
    if scope in {"knowledge", "knowledge_base"}:
        return "knowledge"
    if scope == "session":
        return "session"
    if collection_id and str(collection_id).startswith("session:"):
        return "session"
    return "knowledge"


def _chunk_matches_filters(
    chunk: KnowledgeBaseChunkRecord,
    *,
    collection_id: str,
    filters: Optional[Dict[str, Any]] = None,
) -> bool:
    if not filters:
        return True

    metadata = dict(chunk.metadata_json or {})
    expected_scope = _normalize_source_scope(filters.get("source_scope"), collection_id=collection_id)
    actual_scope = _normalize_source_scope(metadata.get("source_scope"), collection_id=collection_id)
    if expected_scope != actual_scope:
        return False

    actual_document_id = str(metadata.get("document_id") or chunk.source_id)
    expected_document_id = filters.get("document_id")
    if expected_document_id is not None and actual_document_id != str(expected_document_id):
        return False

    expected_document_ids = filters.get("document_ids")
    if expected_document_ids:
        allowed_ids = {str(item) for item in expected_document_ids if item is not None}
        if allowed_ids and actual_document_id not in allowed_ids:
            return False

    expected_version_id = filters.get("document_version_id")
    if expected_version_id is not None and str(metadata.get("document_version_id") or "") != str(expected_version_id):
        return False

    expected_thread_id = filters.get("thread_id")
    if expected_thread_id is not None and str(metadata.get("thread_id") or "") != str(expected_thread_id):
        return False

    expires_at_gte = filters.get("expires_at_gte")
    if expires_at_gte is not None:
        try:
            expires_at = metadata.get("expires_at")
            if expires_at is None or float(expires_at) < float(expires_at_gte):
                return False
        except (TypeError, ValueError):
            return False

    return True


class SQLiteKnowledgeBaseStore:
    def __init__(self, db_url: str = DEFAULT_KB_DB_URL):
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

        CREATE TABLE IF NOT EXISTS kb_chunk_embeddings (
            chunk_id TEXT PRIMARY KEY,
            embedding_blob BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding_model_id TEXT NOT NULL,
            FOREIGN KEY(chunk_id) REFERENCES kb_chunks(chunk_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_kb_chunk_embeddings_model
            ON kb_chunk_embeddings(embedding_model_id);
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
            embedding=_deserialize_embedding(row["embedding_blob"], row["embedding_dim"])
            if "embedding_blob" in row.keys()
            else None,
            embedding_dim=int(row["embedding_dim"]) if "embedding_dim" in row.keys() and row["embedding_dim"] is not None else None,
            embedding_model_id=str(row["embedding_model_id"]) if "embedding_model_id" in row.keys() and row["embedding_model_id"] is not None else None,
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
            conn.execute(
                "DELETE FROM kb_chunk_embeddings WHERE chunk_id IN (SELECT chunk_id FROM kb_chunks WHERE source_id = ?)",
                (source_id,),
            )
            conn.execute("DELETE FROM kb_chunks WHERE source_id = ?", (source_id,))
            for chunk in chunks:
                chunk_id = str(chunk["chunk_id"])
                conn.execute(
                    """
                    INSERT INTO kb_chunks (
                        chunk_id, source_id, chunk_index, text, metadata_json, source_origin
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        source_id,
                        int(chunk["chunk_index"]),
                        str(chunk["text"]),
                        _json_dump(dict(chunk.get("metadata_json") or {})),
                        str(chunk.get("source_origin") or "knowledge_base"),
                    ),
                )
                embedding_blob = _serialize_embedding(chunk.get("embedding"))
                if embedding_blob is not None:
                    conn.execute(
                        """
                        INSERT INTO kb_chunk_embeddings (
                            chunk_id, embedding_blob, embedding_dim, embedding_model_id
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            chunk_id,
                            embedding_blob,
                            int(np.asarray(chunk["embedding"], dtype=np.float32).shape[0]),
                            str(chunk.get("embedding_model_id") or ""),
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
        include_embeddings: bool = False,
    ) -> List[KnowledgeBaseChunkRecord]:
        select_columns = "c.*, s.collection_id, s.display_name"
        join_clause = ""
        if include_embeddings:
            select_columns += ", e.embedding_blob, e.embedding_dim, e.embedding_model_id"
            join_clause = " LEFT JOIN kb_chunk_embeddings e ON e.chunk_id = c.chunk_id"
        query = f"""
            SELECT {select_columns}
            FROM kb_chunks c
            JOIN kb_sources s ON s.source_id = c.source_id
            {join_clause}
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

    def search_chunks_sync(
        self,
        *,
        collection_id: str,
        query_text: str,
        query_embedding: np.ndarray,
        top_k: int,
        source_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        mode: str = "hybrid",
        embed_fn: Optional[Callable] = None,
    ) -> List[KnowledgeBaseChunkMatchRecord]:
        chunks = self.list_chunks_sync(
            collection_id=collection_id,
            source_ids=source_ids,
            include_embeddings=True,
        )
        chunks = [
            chunk
            for chunk in chunks
            if _chunk_matches_filters(chunk, collection_id=collection_id, filters=filters)
        ]
        if not chunks:
            return []

        query_vector = np.asarray(query_embedding, dtype=np.float32)
        if query_vector.ndim != 1:
            raise ValueError("query_embedding must be a 1D vector")

        def _search_embed_fn(texts: List[str]) -> np.ndarray:
            if len(texts) == 1 and str(texts[0]) == query_text:
                return np.asarray([query_vector], dtype=np.float32)
            if embed_fn is None:
                raise ValueError("search_chunks_sync requires embed_fn when KB chunk embeddings are unavailable")
            return np.asarray(embed_fn(texts), dtype=np.float32)

        retriever = HybridRetriever(embed_fn=_search_embed_fn, use_bm25=True)
        documents = [chunk.text for chunk in chunks]
        if all(chunk.embedding is not None for chunk in chunks):
            embeddings = np.vstack([np.asarray(chunk.embedding, dtype=np.float32) for chunk in chunks])
            retriever.index_with_embeddings(documents, embeddings)
        else:
            retriever.index(documents)

        results = retriever.search(query_text, top_k=min(top_k, len(chunks)), mode=mode)
        matches: List[KnowledgeBaseChunkMatchRecord] = []
        source_scope = _normalize_source_scope((filters or {}).get("source_scope"), collection_id=collection_id)
        for result in results:
            chunk = chunks[result.index]
            metadata = dict(chunk.metadata_json or {})
            matches.append(
                KnowledgeBaseChunkMatchRecord(
                    chunk=chunk,
                    raw_score=float(result.score),
                    source_scope=source_scope,
                    payload={
                        "collection_id": chunk.collection_id,
                        "document_id": metadata.get("document_id") or chunk.source_id,
                        "document_version_id": metadata.get("document_version_id"),
                        "thread_id": metadata.get("thread_id"),
                        "expires_at": metadata.get("expires_at"),
                        "source_scope": source_scope,
                        "source_origin": chunk.source_origin,
                        "display_name": chunk.display_name,
                        "embedding_model_id": chunk.embedding_model_id,
                    },
                )
            )
        return matches

    def delete_chunks_sync(
        self,
        *,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int:
        candidate_source_ids = set(str(item) for item in (source_ids or []) if item is not None)
        if not candidate_source_ids:
            for chunk in self.list_chunks_sync(collection_id=collection_id, include_embeddings=False):
                if _chunk_matches_filters(chunk, collection_id=collection_id, filters=filters):
                    candidate_source_ids.add(str(chunk.source_id))
        if not candidate_source_ids:
            return 0

        placeholders = ",".join("?" for _ in candidate_source_ids)
        ordered_source_ids = tuple(candidate_source_ids)
        with self._connect() as conn:
            conn.execute(
                f"DELETE FROM kb_chunk_embeddings WHERE chunk_id IN (SELECT chunk_id FROM kb_chunks WHERE source_id IN ({placeholders}))",
                ordered_source_ids,
            )
            conn.execute(
                f"DELETE FROM kb_chunks WHERE source_id IN ({placeholders})",
                ordered_source_ids,
            )
            conn.execute(
                f"DELETE FROM kb_sources WHERE collection_id = ? AND source_id IN ({placeholders})",
                (collection_id, *ordered_source_ids),
            )
            conn.commit()
        return len(candidate_source_ids)

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
        include_embeddings: bool = False,
    ) -> List[KnowledgeBaseChunkRecord]:
        return self.list_chunks_sync(collection_id, source_ids=source_ids, include_embeddings=include_embeddings)

    async def search_chunks(
        self,
        *,
        collection_id: str,
        query_text: str,
        query_embedding: np.ndarray,
        top_k: int,
        source_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        mode: str = "hybrid",
        embed_fn: Optional[Callable] = None,
    ) -> List[KnowledgeBaseChunkMatchRecord]:
        return self.search_chunks_sync(
            collection_id=collection_id,
            query_text=query_text,
            query_embedding=query_embedding,
            top_k=top_k,
            source_ids=source_ids,
            filters=filters,
            mode=mode,
            embed_fn=embed_fn,
        )

    async def delete_chunks(
        self,
        *,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int:
        return self.delete_chunks_sync(
            collection_id=collection_id,
            source_ids=source_ids,
            filters=filters,
        )


def get_knowledge_base_store() -> KnowledgeBaseStoreProtocol:
    global _STORE_SINGLETON
    with _STORE_LOCK:
        if _STORE_SINGLETON is None:
            backend = str(os.getenv("KB_BACKEND", "sqlite") or "sqlite").strip().lower()
            if backend == "qdrant":
                from orchestrator.qdrant_knowledge_base_store import QdrantKnowledgeBaseStore

                _STORE_SINGLETON = QdrantKnowledgeBaseStore()
            else:
                _STORE_SINGLETON = SQLiteKnowledgeBaseStore()
    return _STORE_SINGLETON
