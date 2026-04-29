from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from orchestrator.knowledge_base_store import (
    DEFAULT_KB_DB_URL,
    EmbeddingProjectionMismatch,
    KnowledgeBaseProjectionRecord,
    KnowledgeBaseChunkMatchRecord,
    KnowledgeBaseChunkRecord,
    SQLiteKnowledgeBaseStore,
    _normalize_source_scope,
)


DEFAULT_QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
DEFAULT_QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "rag_chunks_v1")
logger = logging.getLogger(__name__)


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

    def _projection_physical_collection_name(self, collection_id: str) -> str:
        return self.collection_name

    def _managed_projection_physical_collection_name(self, collection_id: str, version: int) -> str:
        safe_collection_id = "".join(
            char if char.isalnum() or char in {"_", "-"} else "_"
            for char in str(collection_id)
        ).strip("_")
        if not safe_collection_id:
            safe_collection_id = "kb"
        return f"{self.collection_name}__{safe_collection_id}__v{int(version)}"

    def _existing_collection_vector_size(self, *, collection_name: Optional[str] = None) -> Optional[int]:
        target_collection = collection_name or self.collection_name
        get_collection = getattr(self._client, "get_collection", None)
        if get_collection is None:
            return None
        collection_info = get_collection(target_collection)
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

    def _count_collection_points(
        self,
        *,
        collection_name: Optional[str] = None,
        point_filter: Optional[Any] = None,
    ) -> int:
        target_collection = collection_name or self.collection_name
        count_method = getattr(self._client, "count", None)
        if callable(count_method):
            response = count_method(
                collection_name=target_collection,
                count_filter=point_filter,
                exact=True,
            )
            return int(getattr(response, "count", 0) or 0)
        return 0

    def _count_points_by_scope(self, source_scope: str, *, collection_name: Optional[str] = None) -> int:
        scope_filter = self._models.Filter(
            must=[
                self._models.FieldCondition(
                    key="source_scope",
                    match=self._models.MatchValue(value=str(source_scope)),
                )
            ]
        )
        return self._count_collection_points(collection_name=collection_name, point_filter=scope_filter)

    def _can_recreate_collection_for_dimension_mismatch(self, *, collection_name: Optional[str] = None) -> bool:
        target_collection = collection_name or self.collection_name
        total_points = self._count_collection_points(collection_name=target_collection)
        if total_points == 0:
            return True
        knowledge_points = self._count_points_by_scope("knowledge", collection_name=target_collection)
        session_points = self._count_points_by_scope("session", collection_name=target_collection)
        return knowledge_points == 0 and total_points == session_points

    def _recreate_collection(self, embedding_dim: int, *, collection_name: Optional[str] = None) -> None:
        target_collection = collection_name or self.collection_name
        delete_collection = getattr(self._client, "delete_collection", None)
        if not callable(delete_collection):
            raise RuntimeError(
                f"Qdrant collection `{target_collection}` requires recreation, but delete_collection is unavailable."
            )
        delete_collection(target_collection)
        self._client.create_collection(
            target_collection,
            vectors_config=self._models.VectorParams(
                size=int(embedding_dim),
                distance=self._models.Distance.COSINE,
            ),
        )

    def _ensure_collection(self, embedding_dim: int, *, collection_name: Optional[str] = None) -> None:
        target_collection = collection_name or self.collection_name
        if self._client.collection_exists(target_collection):
            existing_size = self._existing_collection_vector_size(collection_name=target_collection)
            if existing_size is not None and existing_size != int(embedding_dim):
                if self._can_recreate_collection_for_dimension_mismatch(collection_name=target_collection):
                    logger.warning(
                        "Recreating Qdrant collection %s due to session-only vector size mismatch %s -> %s",
                        target_collection,
                        existing_size,
                        int(embedding_dim),
                    )
                    self._recreate_collection(int(embedding_dim), collection_name=target_collection)
                    return
                raise RuntimeError(
                    f"Qdrant collection `{target_collection}` expects vector size {existing_size}, "
                    f"but received {int(embedding_dim)}."
                )
            return
        self._client.create_collection(
            target_collection,
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

    def replace_chunks_sync(
        self,
        *,
        source_id: str,
        chunks: List[Dict[str, Any]],
        projection_id: Optional[str] = None,
    ) -> None:
        source = self._source_record_sync(source_id)
        vector_chunks = [chunk for chunk in chunks if chunk.get("embedding") is not None]
        projection: Optional[KnowledgeBaseProjectionRecord] = None
        if vector_chunks:
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
                self._ensure_collection(
                    int(projection.embedding_dim),
                    collection_name=projection.physical_collection_name,
                )
            else:
                projection_inputs = self._projection_inputs_for_chunks(source=source, chunks=chunks)
                if projection_inputs is None:
                    return
                active_projection = self.get_active_projection_sync(source.collection_id)
                if active_projection is None:
                    self._ensure_collection(
                        int(projection_inputs["embedding_dim"]),
                        collection_name=str(projection_inputs["physical_collection_name"]),
                    )
                    projection = self.ensure_projection_sync(**projection_inputs)
                else:
                    projection = self.ensure_projection_sync(**projection_inputs)
                    self._ensure_collection(
                        int(projection.embedding_dim),
                        collection_name=projection.physical_collection_name,
                    )

        target_projection_id = projection.projection_id if projection is not None else projection_id
        chunks_to_store = self._namespace_chunks_for_projection(chunks, target_projection_id if projection_id else None)

        super().replace_chunks_sync(
            source_id=source_id,
            chunks=chunks_to_store,
            projection_id=projection_id,
        )
        if projection is None and projection_id is not None:
            projection = self.get_projection_sync(projection_id)
        if projection is None:
            projection = self.get_active_projection_sync(source.collection_id)
        target_collection = projection.physical_collection_name if projection is not None else self.collection_name
        self._client.delete(
            collection_name=target_collection,
            points_selector=self._models.Filter(
                must=[
                    self._models.FieldCondition(
                        key="source_id",
                        match=self._models.MatchValue(value=str(source_id)),
                    )
                ]
            ),
        )
        vector_chunks = [chunk for chunk in chunks_to_store if chunk.get("embedding") is not None]
        if not vector_chunks:
            return
        points = []
        for chunk in vector_chunks:
            embedding_model_id = str(chunk.get("embedding_model_id") or source.embedding_model_id)
            payload = {
                "chunk_id": str(chunk["chunk_id"]),
                "collection_id": source.collection_id,
                "source_id": source.source_id,
                "document_id": source.source_id,
                "document_version_id": source.source_id,
                "display_name": source.display_name,
                "source_scope": "knowledge",
                "source_origin": str(chunk.get("source_origin") or "knowledge_base"),
                "embedding_model_id": embedding_model_id,
                "embedding_projection_id": projection.projection_id if projection is not None else None,
                "embedding_dim": projection.embedding_dim
                if projection is not None
                else int(np.asarray(chunk["embedding"], dtype=np.float32).shape[0]),
                "embedding_distance": projection.embedding_distance if projection is not None else "Cosine",
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
        self._client.upsert(collection_name=target_collection, points=points)

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
        query_embedding_model_id: Optional[str] = None,
    ) -> List[KnowledgeBaseChunkMatchRecord]:
        source_scope = _normalize_source_scope((filters or {}).get("source_scope"), collection_id=collection_id)
        normalized_source_ids = [str(item) for item in (source_ids or []) if item is not None]
        query_array = np.asarray(query_embedding, dtype=np.float32)
        projection = self._validate_query_projection_sync(
            collection_id=collection_id,
            query_embedding=query_array,
            query_embedding_model_id=query_embedding_model_id,
        )
        target_collection = projection.physical_collection_name if projection is not None else self.collection_name
        if query_array.ndim != 1:
            raise ValueError("query_embedding must be a 1D vector")
        query_vector = query_array.tolist()
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
                collection_name=target_collection,
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
        projection = self.get_active_projection_sync(collection_id)
        target_collection = projection.physical_collection_name if projection is not None else self.collection_name
        normalized_source_ids = [str(item) for item in (source_ids or []) if item is not None]
        source_id_batches: List[Optional[List[str]]] = [None]
        if len(normalized_source_ids) == 1:
            source_id_batches = [normalized_source_ids]
        elif len(normalized_source_ids) > 1:
            source_id_batches = [[source_id] for source_id in normalized_source_ids]

        for source_id_batch in source_id_batches:
            self._client.delete(
                collection_name=target_collection,
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
