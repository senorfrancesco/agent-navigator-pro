from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from orchestrator.knowledge_base_store import (
    DEFAULT_KB_DB_URL,
    KnowledgeBaseChunkMatchRecord,
    KnowledgeBaseChunkRecord,
    SQLiteKnowledgeBaseStore,
    _normalize_source_scope,
)


DEFAULT_QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
DEFAULT_QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "rag_chunks_v1")


def _load_qdrant_dependencies() -> Tuple[Any, Any]:
    try:
        from qdrant_client import QdrantClient, models
    except ImportError as exc:
        raise RuntimeError(
            "qdrant-client is not installed. Install backend dependencies with qdrant-client support."
        ) from exc
    return QdrantClient, models


class QdrantKnowledgeBaseStore(SQLiteKnowledgeBaseStore):
    def __init__(
        self,
        db_url: str = DEFAULT_KB_DB_URL,
        *,
        qdrant_url: str = DEFAULT_QDRANT_URL,
        collection_name: str = DEFAULT_QDRANT_COLLECTION_NAME,
    ):
        super().__init__(db_url=db_url)
        client_class, models = _load_qdrant_dependencies()
        self._models = models
        self._client = client_class(url=qdrant_url)
        self.collection_name = collection_name

    def _existing_collection_vector_size(self) -> Optional[int]:
        get_collection = getattr(self._client, "get_collection", None)
        if get_collection is None:
            return None
        collection_info = get_collection(self.collection_name)
        current: Any = collection_info
        for attr in ("config", "params", "vectors"):
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(attr)
            else:
                current = getattr(current, attr, None)
        if current is None:
            return None
        if isinstance(current, dict):
            size = current.get("size")
        else:
            size = getattr(current, "size", None)
        return int(size) if size is not None else None

    def _ensure_collection(self, embedding_dim: int) -> None:
        if self._client.collection_exists(self.collection_name):
            existing_size = self._existing_collection_vector_size()
            if existing_size is not None and existing_size != int(embedding_dim):
                raise RuntimeError(
                    f"Qdrant collection `{self.collection_name}` expects vector size {existing_size}, "
                    f"but received {int(embedding_dim)}."
                )
            return
        self._client.create_collection(
            self.collection_name,
            vectors_config=self._models.VectorParams(
                size=int(embedding_dim),
                distance=self._models.Distance.COSINE,
            ),
        )

    def _point_id_for_chunk_id(self, chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, str(chunk_id)))

    def _build_filter(
        self,
        *,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Any:
        source_scope = _normalize_source_scope((filters or {}).get("source_scope"), collection_id=collection_id)
        must = [
            self._models.FieldCondition(
                key="collection_id",
                match=self._models.MatchValue(value=collection_id),
            ),
            self._models.FieldCondition(
                key="source_scope",
                match=self._models.MatchValue(value=source_scope),
            ),
        ]
        thread_id = (filters or {}).get("thread_id")
        if thread_id is not None:
            must.append(
                self._models.FieldCondition(
                    key="thread_id",
                    match=self._models.MatchValue(value=str(thread_id)),
                )
            )
        document_version_id = (filters or {}).get("document_version_id")
        if document_version_id is not None:
            must.append(
                self._models.FieldCondition(
                    key="document_version_id",
                    match=self._models.MatchValue(value=str(document_version_id)),
                )
            )
        normalized_source_ids = [str(item) for item in (source_ids or []) if item is not None]
        if len(normalized_source_ids) == 1:
            must.append(
                self._models.FieldCondition(
                    key="source_id",
                    match=self._models.MatchValue(value=normalized_source_ids[0]),
                )
            )
        document_id = (filters or {}).get("document_id")
        if document_id is not None:
            must.append(
                self._models.FieldCondition(
                    key="document_id",
                    match=self._models.MatchValue(value=str(document_id)),
                )
            )
        document_ids = (filters or {}).get("document_ids") or []
        if len(document_ids) == 1:
            must.append(
                self._models.FieldCondition(
                    key="document_id",
                    match=self._models.MatchValue(value=str(document_ids[0])),
                )
            )
        expires_at_gte = (filters or {}).get("expires_at_gte")
        if expires_at_gte is not None and hasattr(self._models, "Range"):
            must.append(
                self._models.FieldCondition(
                    key="expires_at",
                    range=self._models.Range(gte=float(expires_at_gte)),
                )
            )
        return self._models.Filter(must=must)

    def _point_matches_filters(self, payload: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> bool:
        if not filters:
            return True
        document_ids = filters.get("document_ids")
        if document_ids:
            allowed_ids = {str(item) for item in document_ids if item is not None}
            if allowed_ids and str(payload.get("document_id") or "") not in allowed_ids:
                return False
        expires_at_gte = filters.get("expires_at_gte")
        if expires_at_gte is not None:
            try:
                if payload.get("expires_at") is None or float(payload["expires_at"]) < float(expires_at_gte):
                    return False
            except (TypeError, ValueError):
                return False
        return True

    def _point_matches_source_ids(self, payload: Dict[str, Any], source_ids: Optional[List[str]]) -> bool:
        if not source_ids:
            return True
        allowed_ids = {str(item) for item in source_ids if item is not None}
        if not allowed_ids:
            return True
        return str(payload.get("source_id") or "") in allowed_ids

    def _load_chunks_by_ids(self, *, collection_id: str, chunk_ids: List[str]) -> List[KnowledgeBaseChunkRecord]:
        if not chunk_ids:
            return []
        select_columns = "c.*, s.collection_id, s.display_name, e.embedding_blob, e.embedding_dim, e.embedding_model_id"
        placeholders = ",".join("?" for _ in chunk_ids)
        query = f"""
            SELECT {select_columns}
            FROM kb_chunks c
            JOIN kb_sources s ON s.source_id = c.source_id
            LEFT JOIN kb_chunk_embeddings e ON e.chunk_id = c.chunk_id
            WHERE s.collection_id = ? AND c.chunk_id IN ({placeholders})
        """
        params: List[Any] = [collection_id, *chunk_ids]
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        chunks_by_id = {chunk.chunk_id: chunk for chunk in (self._row_to_chunk(row) for row in rows)}
        return [chunks_by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in chunks_by_id]

    def replace_chunks_sync(self, *, source_id: str, chunks: List[Dict[str, Any]]) -> None:
        super().replace_chunks_sync(source_id=source_id, chunks=chunks)
        sources = {source.source_id: source for source in self.list_sources_sync(collection_id=self._source_collection_id(source_id))}
        source = sources.get(source_id)
        if source is None:
            return

        vector_chunks = [chunk for chunk in chunks if chunk.get("embedding") is not None]
        if vector_chunks:
            embedding_dim = int(np.asarray(vector_chunks[0]["embedding"], dtype=np.float32).shape[0])
            self._ensure_collection(embedding_dim)
        self._client.delete(
            collection_name=self.collection_name,
            points_selector=self._models.Filter(
                must=[
                    self._models.FieldCondition(
                        key="source_id",
                        match=self._models.MatchValue(value=str(source_id)),
                    )
                ]
            ),
        )
        if not vector_chunks:
            return
        points = []
        for chunk in vector_chunks:
            payload = {
                "chunk_id": str(chunk["chunk_id"]),
                "collection_id": source.collection_id,
                "source_id": source.source_id,
                "document_id": source.source_id,
                "document_version_id": source.source_id,
                "display_name": source.display_name,
                "source_scope": "knowledge",
                "source_origin": str(chunk.get("source_origin") or "knowledge_base"),
                "embedding_model_id": str(chunk.get("embedding_model_id") or source.embedding_model_id),
                "chunk_index": int(chunk.get("chunk_index") or 0),
            }
            payload.update(dict(chunk.get("metadata_json") or {}))
            points.append(
                self._models.PointStruct(
                    id=self._point_id_for_chunk_id(str(chunk["chunk_id"])),
                    vector=np.asarray(chunk["embedding"], dtype=np.float32).tolist(),
                    payload=payload,
                )
            )
        self._client.upsert(collection_name=self.collection_name, points=points)

    def _source_collection_id(self, source_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT collection_id FROM kb_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown-source:{source_id}")
        return str(row["collection_id"])

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
        embed_fn: Optional[Any] = None,
    ) -> List[KnowledgeBaseChunkMatchRecord]:
        source_scope = _normalize_source_scope((filters or {}).get("source_scope"), collection_id=collection_id)
        normalized_source_ids = [str(item) for item in (source_ids or []) if item is not None]
        query_vector = np.asarray(query_embedding, dtype=np.float32).tolist()
        source_id_batches: List[Optional[List[str]]] = [None]
        if len(normalized_source_ids) == 1:
            source_id_batches = [normalized_source_ids]
        elif len(normalized_source_ids) > 1:
            source_id_batches = [[source_id] for source_id in normalized_source_ids]

        points_by_chunk_id: Dict[str, Any] = {}
        for source_id_batch in source_id_batches:
            filter_obj = self._build_filter(
                collection_id=collection_id,
                source_ids=source_id_batch,
                filters=filters,
            )
            results = self._client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=filter_obj,
                limit=int(top_k),
                with_payload=True,
            )
            for result in list(getattr(results, "points", []) or []):
                payload = dict(getattr(result, "payload", {}) or {})
                if not self._point_matches_filters(payload, filters):
                    continue
                if not self._point_matches_source_ids(payload, normalized_source_ids):
                    continue
                chunk_id = str(payload.get("chunk_id") or result.id)
                existing = points_by_chunk_id.get(chunk_id)
                if existing is None or float(result.score) > float(existing.score):
                    points_by_chunk_id[chunk_id] = result

        points = list(points_by_chunk_id.values())
        points.sort(key=lambda item: float(item.score), reverse=True)
        points = points[: int(top_k)]
        chunk_ids = [str(getattr(result, "payload", {}).get("chunk_id") or result.id) for result in points]
        chunks = {chunk.chunk_id: chunk for chunk in self._load_chunks_by_ids(collection_id=collection_id, chunk_ids=chunk_ids)}
        matches: List[KnowledgeBaseChunkMatchRecord] = []
        for result in points:
            payload = dict(getattr(result, "payload", {}) or {})
            chunk = chunks.get(str(payload.get("chunk_id") or result.id))
            if chunk is None:
                continue
            matches.append(
                KnowledgeBaseChunkMatchRecord(
                    chunk=chunk,
                    raw_score=float(result.score),
                    source_scope=source_scope,
                    payload=payload,
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
        normalized_source_ids = [str(item) for item in (source_ids or []) if item is not None]
        source_id_batches: List[Optional[List[str]]] = [None]
        if len(normalized_source_ids) == 1:
            source_id_batches = [normalized_source_ids]
        elif len(normalized_source_ids) > 1:
            source_id_batches = [[source_id] for source_id in normalized_source_ids]

        for source_id_batch in source_id_batches:
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=self._build_filter(
                    collection_id=collection_id,
                    source_ids=source_id_batch,
                    filters=filters,
                ),
            )
        return super().delete_chunks_sync(
            collection_id=collection_id,
            source_ids=source_ids,
            filters=filters,
        )
