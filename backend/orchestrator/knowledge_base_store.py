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
    projection_id: Optional[str] = None


@dataclass
class KnowledgeBaseChunkMatchRecord:
    chunk: KnowledgeBaseChunkRecord
    raw_score: float
    source_scope: str
    payload: Dict[str, Any]


@dataclass
class KnowledgeBaseProjectionRecord:
    projection_id: str
    collection_id: str
    physical_collection_name: str
    embedding_model_id: str
    embedding_dim: int
    embedding_distance: str
    embedding_revision: Optional[str]
    normalize: bool
    embedding_runtime_id: Optional[str]
    chunking_version: str
    source_mode: str
    status: str
    version: int
    created_at: float
    updated_at: float


class EmbeddingProjectionMismatch(ValueError):
    """Raised when an embedding vector is incompatible with the active KB projection."""


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

    def replace_chunks_sync(
        self,
        *,
        source_id: str,
        chunks: List[Dict[str, Any]],
        projection_id: Optional[str] = None,
    ) -> None: ...

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
        query_embedding_model_id: Optional[str] = None,
    ) -> List[KnowledgeBaseChunkMatchRecord]: ...

    def get_active_projection_sync(self, collection_id: str) -> Optional[KnowledgeBaseProjectionRecord]: ...

    def delete_chunks_sync(
        self,
        *,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int: ...

    async def register_source(self, **kwargs: Any) -> KnowledgeBaseSourceRecord: ...

    async def replace_chunks(
        self,
        *,
        source_id: str,
        chunks: List[Dict[str, Any]],
        projection_id: Optional[str] = None,
    ) -> None: ...

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
        query_embedding_model_id: Optional[str] = None,
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


def _normalize_embedding_model_id(value: Any) -> str:
    return str(value or "").strip()


def _normalize_embedding_distance(value: Any) -> str:
    distance = str(value or "Cosine").strip()
    return distance or "Cosine"


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
            projection_id TEXT,
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
            projection_id TEXT,
            embedding_blob BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding_model_id TEXT NOT NULL,
            FOREIGN KEY(chunk_id) REFERENCES kb_chunks(chunk_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_kb_chunk_embeddings_model
            ON kb_chunk_embeddings(embedding_model_id);

        CREATE TABLE IF NOT EXISTS kb_index_projections (
            projection_id TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL,
            physical_collection_name TEXT NOT NULL,
            embedding_model_id TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding_distance TEXT NOT NULL,
            embedding_revision TEXT,
            normalize INTEGER NOT NULL,
            embedding_runtime_id TEXT,
            chunking_version TEXT NOT NULL,
            source_mode TEXT NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(collection_id, version)
        );

        CREATE INDEX IF NOT EXISTS idx_kb_index_projections_collection
            ON kb_index_projections(collection_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_index_projections_active
            ON kb_index_projections(collection_id)
            WHERE status = 'active';
        """
        with self._connect() as conn:
            conn.executescript(schema)
            self._ensure_column(conn, "kb_chunks", "projection_id", "TEXT")
            self._ensure_column(conn, "kb_chunk_embeddings", "projection_id", "TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kb_chunks_projection_id ON kb_chunks(projection_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kb_chunk_embeddings_projection_id ON kb_chunk_embeddings(projection_id)"
            )
            conn.commit()

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {str(row["name"]) for row in rows}
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

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
            projection_id=str(row["projection_id"]) if "projection_id" in row.keys() and row["projection_id"] is not None else None,
        )

    def _row_to_projection(self, row: sqlite3.Row) -> KnowledgeBaseProjectionRecord:
        return KnowledgeBaseProjectionRecord(
            projection_id=str(row["projection_id"]),
            collection_id=str(row["collection_id"]),
            physical_collection_name=str(row["physical_collection_name"]),
            embedding_model_id=str(row["embedding_model_id"]),
            embedding_dim=int(row["embedding_dim"]),
            embedding_distance=str(row["embedding_distance"]),
            embedding_revision=str(row["embedding_revision"]) if row["embedding_revision"] is not None else None,
            normalize=bool(int(row["normalize"])),
            embedding_runtime_id=str(row["embedding_runtime_id"]) if row["embedding_runtime_id"] is not None else None,
            chunking_version=str(row["chunking_version"]),
            source_mode=str(row["source_mode"]),
            status=str(row["status"]),
            version=int(row["version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _source_record_sync(self, source_id: str) -> KnowledgeBaseSourceRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM kb_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown-source:{source_id}")
        return self._row_to_source(row)

    def _projection_physical_collection_name(self, collection_id: str) -> str:
        return f"sqlite:{collection_id}"

    def _managed_projection_physical_collection_name(self, collection_id: str, version: int) -> str:
        return f"{self._projection_physical_collection_name(collection_id)}__v{int(version)}"

    def _projection_source_mode(self, collection_id: str) -> str:
        return "session" if str(collection_id).startswith("session:") else "managed"

    def get_active_projection_sync(self, collection_id: str) -> Optional[KnowledgeBaseProjectionRecord]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM kb_index_projections
                WHERE collection_id = ? AND status = 'active'
                ORDER BY version DESC
                LIMIT 1
                """,
                (collection_id,),
            ).fetchone()
        return self._row_to_projection(row) if row is not None else None

    def get_projection_sync(self, projection_id: str) -> Optional[KnowledgeBaseProjectionRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM kb_index_projections WHERE projection_id = ?",
                (projection_id,),
            ).fetchone()
        return self._row_to_projection(row) if row is not None else None

    def get_latest_projection_sync(self, collection_id: str) -> Optional[KnowledgeBaseProjectionRecord]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM kb_index_projections
                WHERE collection_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (collection_id,),
            ).fetchone()
        return self._row_to_projection(row) if row is not None else None

    def _next_projection_version_sync(self, collection_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS max_version FROM kb_index_projections WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        return int((row["max_version"] if row is not None else None) or 0) + 1

    def _insert_projection_sync(
        self,
        *,
        collection_id: str,
        physical_collection_name: str,
        embedding_model_id: str,
        embedding_dim: int,
        chunking_version: str,
        embedding_distance: str = "Cosine",
        embedding_revision: Optional[str] = None,
        normalize: bool = True,
        embedding_runtime_id: Optional[str] = None,
        source_mode: Optional[str] = None,
        status: str = "active",
        version: Optional[int] = None,
    ) -> KnowledgeBaseProjectionRecord:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"active", "building", "superseded", "needs_profile"}:
            raise ValueError(f"unsupported projection status: {status}")
        normalized_model = _normalize_embedding_model_id(embedding_model_id)
        if not normalized_model and normalized_status != "needs_profile":
            raise ValueError("embedding_model_id is required for a knowledge base projection")
        normalized_dim = int(embedding_dim)
        if normalized_dim <= 0 and normalized_status != "needs_profile":
            raise ValueError("embedding_dim must be positive for a knowledge base projection")
        normalized_distance = _normalize_embedding_distance(embedding_distance)
        mode = str(source_mode or self._projection_source_mode(collection_id))
        projection_version = int(version or self._next_projection_version_sync(collection_id))
        now = time.time()
        projection_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kb_index_projections (
                    projection_id, collection_id, physical_collection_name, embedding_model_id,
                    embedding_dim, embedding_distance, embedding_revision, normalize,
                    embedding_runtime_id, chunking_version, source_mode, status, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    projection_id,
                    collection_id,
                    physical_collection_name,
                    normalized_model,
                    max(0, normalized_dim),
                    normalized_distance,
                    embedding_revision,
                    1 if normalize else 0,
                    embedding_runtime_id,
                    str(chunking_version or ""),
                    mode,
                    normalized_status,
                    projection_version,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM kb_index_projections WHERE projection_id = ?",
                (projection_id,),
            ).fetchone()
        return self._row_to_projection(row)

    def ensure_projection_sync(
        self,
        *,
        collection_id: str,
        physical_collection_name: str,
        embedding_model_id: str,
        embedding_dim: int,
        chunking_version: str,
        embedding_distance: str = "Cosine",
        embedding_revision: Optional[str] = None,
        normalize: bool = True,
        embedding_runtime_id: Optional[str] = None,
        source_mode: Optional[str] = None,
    ) -> KnowledgeBaseProjectionRecord:
        normalized_model = _normalize_embedding_model_id(embedding_model_id)
        if not normalized_model:
            raise ValueError("embedding_model_id is required for a knowledge base projection")
        normalized_distance = _normalize_embedding_distance(embedding_distance)
        normalized_dim = int(embedding_dim)
        if normalized_dim <= 0:
            raise ValueError("embedding_dim must be positive for a knowledge base projection")

        mode = str(source_mode or self._projection_source_mode(collection_id))
        active = self.get_active_projection_sync(collection_id)
        if active is not None:
            same_projection = (
                active.embedding_model_id == normalized_model
                and active.embedding_dim == normalized_dim
                and active.embedding_distance == normalized_distance
                and active.physical_collection_name == physical_collection_name
            )
            if same_projection:
                return active
            if active.source_mode != "session" and mode != "session":
                raise EmbeddingProjectionMismatch(
                    f"Collection `{collection_id}` expected embedding model {active.embedding_model_id} "
                    f"with dimension {active.embedding_dim}, but received embedding model {normalized_model} "
                    f"with dimension {normalized_dim}."
                )
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE kb_index_projections
                    SET status = 'superseded', updated_at = ?
                    WHERE projection_id = ?
                    """,
                    (time.time(), active.projection_id),
                )
                conn.commit()

        return self._insert_projection_sync(
            collection_id=collection_id,
            physical_collection_name=physical_collection_name,
            embedding_model_id=normalized_model,
            embedding_dim=normalized_dim,
            chunking_version=chunking_version,
            embedding_distance=normalized_distance,
            embedding_revision=embedding_revision,
            normalize=normalize,
            embedding_runtime_id=embedding_runtime_id,
            source_mode=mode,
            status="active",
        )

    def create_building_projection_sync(
        self,
        *,
        collection_id: str,
        embedding_model_id: str,
        embedding_dim: int,
        chunking_version: str,
        embedding_distance: str = "Cosine",
        embedding_revision: Optional[str] = None,
        normalize: bool = True,
        embedding_runtime_id: Optional[str] = None,
        physical_collection_name: Optional[str] = None,
    ) -> KnowledgeBaseProjectionRecord:
        version = self._next_projection_version_sync(collection_id)
        return self._insert_projection_sync(
            collection_id=collection_id,
            physical_collection_name=physical_collection_name
            or self._managed_projection_physical_collection_name(collection_id, version),
            embedding_model_id=embedding_model_id,
            embedding_dim=embedding_dim,
            chunking_version=chunking_version,
            embedding_distance=embedding_distance,
            embedding_revision=embedding_revision,
            normalize=normalize,
            embedding_runtime_id=embedding_runtime_id,
            source_mode="managed",
            status="building",
            version=version,
        )

    def publish_projection_sync(self, projection_id: str) -> KnowledgeBaseProjectionRecord:
        projection = self.get_projection_sync(projection_id)
        if projection is None:
            raise KeyError(f"unknown-projection:{projection_id}")
        if not projection.embedding_model_id or projection.embedding_dim <= 0:
            raise EmbeddingProjectionMismatch(
                f"Projection `{projection_id}` requires an explicit embedding profile before publish."
            )
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE kb_index_projections
                SET status = 'superseded', updated_at = ?
                WHERE collection_id = ? AND status = 'active' AND projection_id <> ?
                """,
                (now, projection.collection_id, projection_id),
            )
            conn.execute(
                """
                UPDATE kb_index_projections
                SET status = 'active', updated_at = ?
                WHERE projection_id = ?
                """,
                (now, projection_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM kb_index_projections WHERE projection_id = ?",
                (projection_id,),
            ).fetchone()
        return self._row_to_projection(row)

    def attach_external_projection_sync(
        self,
        *,
        collection_id: str,
        physical_collection_name: str,
        embedding_model_id: Optional[str] = None,
        embedding_dim: Optional[int] = None,
        chunking_version: str = "external",
        embedding_distance: str = "Cosine",
        embedding_revision: Optional[str] = None,
        normalize: bool = True,
        embedding_runtime_id: Optional[str] = None,
    ) -> KnowledgeBaseProjectionRecord:
        has_profile = bool(_normalize_embedding_model_id(embedding_model_id)) and int(embedding_dim or 0) > 0
        projection = self._insert_projection_sync(
            collection_id=collection_id,
            physical_collection_name=physical_collection_name,
            embedding_model_id=_normalize_embedding_model_id(embedding_model_id) if has_profile else "",
            embedding_dim=int(embedding_dim or 0),
            chunking_version=chunking_version,
            embedding_distance=embedding_distance,
            embedding_revision=embedding_revision,
            normalize=normalize,
            embedding_runtime_id=embedding_runtime_id,
            source_mode="attached",
            status="building" if has_profile else "needs_profile",
        )
        return self.publish_projection_sync(projection.projection_id) if has_profile else projection

    def _projection_inputs_for_chunks(
        self,
        *,
        source: KnowledgeBaseSourceRecord,
        chunks: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        vector_chunks = [chunk for chunk in chunks if chunk.get("embedding") is not None]
        if not vector_chunks:
            return None

        expected_model = _normalize_embedding_model_id(source.embedding_model_id)
        embedding_dim: Optional[int] = None
        for chunk in vector_chunks:
            chunk_model = _normalize_embedding_model_id(chunk.get("embedding_model_id") or expected_model)
            if expected_model and chunk_model != expected_model:
                raise EmbeddingProjectionMismatch(
                    f"Source `{source.source_id}` expected embedding model {expected_model}, "
                    f"but chunk `{chunk.get('chunk_id')}` uses embedding model {chunk_model}."
                )
            array = np.asarray(chunk["embedding"], dtype=np.float32)
            if array.ndim != 1:
                raise ValueError("Chunk embedding must be a 1D vector")
            chunk_dim = int(array.shape[0])
            if embedding_dim is None:
                embedding_dim = chunk_dim
            elif embedding_dim != chunk_dim:
                raise EmbeddingProjectionMismatch(
                    f"Source `{source.source_id}` contains mixed embedding dimensions "
                    f"{embedding_dim} and {chunk_dim}."
                )

        return {
            "collection_id": source.collection_id,
            "physical_collection_name": self._projection_physical_collection_name(source.collection_id),
            "embedding_model_id": expected_model,
            "embedding_dim": int(embedding_dim or 0),
            "chunking_version": source.chunking_version,
            "source_mode": self._projection_source_mode(source.collection_id),
        }

    def _ensure_projection_for_chunks_sync(
        self,
        *,
        source: KnowledgeBaseSourceRecord,
        chunks: List[Dict[str, Any]],
    ) -> Optional[KnowledgeBaseProjectionRecord]:
        projection_inputs = self._projection_inputs_for_chunks(source=source, chunks=chunks)
        if projection_inputs is None:
            return None
        return self.ensure_projection_sync(**projection_inputs)

    def _validate_chunks_for_projection_sync(
        self,
        *,
        projection: KnowledgeBaseProjectionRecord,
        chunks: List[Dict[str, Any]],
    ) -> None:
        if projection.status == "needs_profile":
            raise EmbeddingProjectionMismatch(
                f"Projection `{projection.projection_id}` requires an explicit embedding profile before indexing."
            )
        for chunk in chunks:
            if chunk.get("embedding") is None:
                continue
            chunk_model = _normalize_embedding_model_id(chunk.get("embedding_model_id") or projection.embedding_model_id)
            if chunk_model != projection.embedding_model_id:
                raise EmbeddingProjectionMismatch(
                    f"Projection `{projection.projection_id}` expected embedding model {projection.embedding_model_id}, "
                    f"but chunk `{chunk.get('chunk_id')}` uses embedding model {chunk_model}."
                )
            array = np.asarray(chunk["embedding"], dtype=np.float32)
            if array.ndim != 1:
                raise ValueError("Chunk embedding must be a 1D vector")
            if int(array.shape[0]) != projection.embedding_dim:
                raise EmbeddingProjectionMismatch(
                    f"Projection `{projection.projection_id}` expected embedding dimension {projection.embedding_dim}, "
                    f"but chunk `{chunk.get('chunk_id')}` uses dimension {int(array.shape[0])}."
                )

    def _namespace_chunks_for_projection(
        self,
        chunks: List[Dict[str, Any]],
        projection_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        if not projection_id:
            return chunks
        suffix = f":{projection_id}"
        namespaced_chunks: List[Dict[str, Any]] = []
        for chunk in chunks:
            materialized = dict(chunk)
            chunk_id = str(materialized["chunk_id"])
            if not chunk_id.endswith(suffix):
                materialized["chunk_id"] = f"{chunk_id}{suffix}"
            namespaced_chunks.append(materialized)
        return namespaced_chunks

    def _validate_query_projection_sync(
        self,
        *,
        collection_id: str,
        query_embedding: np.ndarray,
        query_embedding_model_id: Optional[str],
    ) -> Optional[KnowledgeBaseProjectionRecord]:
        projection = self.get_active_projection_sync(collection_id)
        if projection is None:
            latest = self.get_latest_projection_sync(collection_id)
            if latest is not None and latest.status == "needs_profile":
                raise EmbeddingProjectionMismatch(
                    f"Collection `{collection_id}` requires an explicit embedding profile before search."
                )
            return None
        query_vector = np.asarray(query_embedding, dtype=np.float32)
        if query_vector.ndim != 1:
            raise ValueError("query_embedding must be a 1D vector")
        if int(query_vector.shape[0]) != projection.embedding_dim:
            raise EmbeddingProjectionMismatch(
                f"Collection `{collection_id}` expected embedding dimension {projection.embedding_dim}, "
                f"but received dimension {int(query_vector.shape[0])}."
            )
        query_model = _normalize_embedding_model_id(query_embedding_model_id)
        if query_model and query_model != projection.embedding_model_id:
            raise EmbeddingProjectionMismatch(
                f"Collection `{collection_id}` expected embedding model {projection.embedding_model_id}, "
                f"but received embedding model {query_model}."
            )
        return projection

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

    def replace_chunks_sync(
        self,
        *,
        source_id: str,
        chunks: List[Dict[str, Any]],
        projection_id: Optional[str] = None,
    ) -> None:
        source = self._source_record_sync(source_id)
        projection: Optional[KnowledgeBaseProjectionRecord] = None
        if projection_id is not None:
            projection = self.get_projection_sync(projection_id)
            if projection is None:
                raise KeyError(f"unknown-projection:{projection_id}")
            if projection.collection_id != source.collection_id:
                raise EmbeddingProjectionMismatch(
                    f"Projection `{projection_id}` belongs to collection `{projection.collection_id}`, "
                    f"but source `{source_id}` belongs to `{source.collection_id}`."
                )
            self._validate_chunks_for_projection_sync(projection=projection, chunks=chunks)
        else:
            projection = self._ensure_projection_for_chunks_sync(source=source, chunks=chunks)
        target_projection_id = projection.projection_id if projection is not None else None
        delete_legacy_null_projection = bool(projection is not None and projection.status == "active")
        chunks_to_store = self._namespace_chunks_for_projection(chunks, target_projection_id if projection_id else None)
        with self._connect() as conn:
            if target_projection_id is None:
                conn.execute(
                    "DELETE FROM kb_chunk_embeddings WHERE chunk_id IN (SELECT chunk_id FROM kb_chunks WHERE source_id = ?)",
                    (source_id,),
                )
                conn.execute("DELETE FROM kb_chunks WHERE source_id = ?", (source_id,))
            else:
                projection_clause = "projection_id = ?"
                params: tuple[Any, ...] = (source_id, target_projection_id)
                if delete_legacy_null_projection:
                    projection_clause = "(projection_id = ? OR projection_id IS NULL)"
                conn.execute(
                    f"DELETE FROM kb_chunk_embeddings WHERE chunk_id IN (SELECT chunk_id FROM kb_chunks WHERE source_id = ? AND {projection_clause})",
                    params,
                )
                conn.execute(
                    f"DELETE FROM kb_chunks WHERE source_id = ? AND {projection_clause}",
                    params,
                )
            for chunk in chunks_to_store:
                chunk_id = str(chunk["chunk_id"])
                conn.execute(
                    """
                    INSERT INTO kb_chunks (
                        chunk_id, source_id, projection_id, chunk_index, text, metadata_json, source_origin
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        source_id,
                        target_projection_id,
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
                            chunk_id, projection_id, embedding_blob, embedding_dim, embedding_model_id
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            chunk_id,
                            target_projection_id,
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
        return self._list_chunks_sync(
            collection_id=collection_id,
            source_ids=source_ids,
            include_embeddings=include_embeddings,
        )

    def _list_chunks_sync(
        self,
        *,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        include_embeddings: bool = False,
        projection: Optional[KnowledgeBaseProjectionRecord] = None,
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
        if projection is not None:
            if projection.version == 1 and projection.status == "active":
                query += " AND (c.projection_id = ? OR c.projection_id IS NULL)"
            else:
                query += " AND c.projection_id = ?"
            params.append(projection.projection_id)
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
        query_embedding_model_id: Optional[str] = None,
    ) -> List[KnowledgeBaseChunkMatchRecord]:
        query_vector = np.asarray(query_embedding, dtype=np.float32)
        projection = self._validate_query_projection_sync(
            collection_id=collection_id,
            query_embedding=query_vector,
            query_embedding_model_id=query_embedding_model_id,
        )
        if query_vector.ndim != 1:
            raise ValueError("query_embedding must be a 1D vector")

        chunks = self._list_chunks_sync(
            collection_id=collection_id,
            source_ids=source_ids,
            include_embeddings=True,
            projection=projection,
        )
        chunks = [
            chunk
            for chunk in chunks
            if _chunk_matches_filters(chunk, collection_id=collection_id, filters=filters)
        ]
        if not chunks:
            return []

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

    async def replace_chunks(
        self,
        *,
        source_id: str,
        chunks: List[Dict[str, Any]],
        projection_id: Optional[str] = None,
    ) -> None:
        self.replace_chunks_sync(source_id=source_id, chunks=chunks, projection_id=projection_id)

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
        query_embedding_model_id: Optional[str] = None,
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
            query_embedding_model_id=query_embedding_model_id,
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
