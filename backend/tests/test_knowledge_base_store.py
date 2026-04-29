import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.knowledge_base_ingestion import ingest_text_source_sync
from orchestrator.knowledge_base_retrieval import retrieve_merged_chunks
from orchestrator.knowledge_base_store import (
    EmbeddingProjectionMismatch,
    KnowledgeBaseChunkRecord,
    KnowledgeBaseChunkMatchRecord,
    KnowledgeBaseSourceRecord,
    KnowledgeBaseStoreProtocol,
    SQLiteKnowledgeBaseStore,
)


def _protocol_embed_fn(texts):
    vectors = []
    for text in texts:
        lowered = text.lower()
        vec = np.array(
            [
                1.0 if "уведом" in lowered or "дней" in lowered else 0.0,
                1.0 if "штраф" in lowered or "просроч" in lowered else 0.0,
                1.0 if "сервис" in lowered or "обслуж" in lowered else 0.0,
            ],
            dtype=np.float32,
        )
        if not np.any(vec):
            vec = np.ones(3, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        vectors.append(vec)
    return np.array(vectors, dtype=np.float32)


class InMemoryKnowledgeBaseStore:
    def __init__(self):
        self._sources = {}
        self._chunks = {}
        self.search_calls = []

    def register_source_sync(
        self,
        *,
        collection_id,
        display_name,
        content_hash,
        mime_type,
        index_version,
        embedding_model_id,
        chunking_version,
        status="indexed",
    ):
        source = next(
            (
                existing
                for existing in self._sources.values()
                if existing.collection_id == collection_id and existing.content_hash == content_hash
            ),
            None,
        )
        if source is None:
            source = KnowledgeBaseSourceRecord(
                source_id=f"source-{len(self._sources) + 1}",
                collection_id=collection_id,
                display_name=display_name,
                content_hash=content_hash,
                mime_type=mime_type,
                status=status,
                index_version=index_version,
                embedding_model_id=embedding_model_id,
                chunking_version=chunking_version,
                created_at=0.0,
                updated_at=0.0,
            )
        else:
            source = KnowledgeBaseSourceRecord(
                source_id=source.source_id,
                collection_id=collection_id,
                display_name=display_name,
                content_hash=content_hash,
                mime_type=mime_type,
                status=status,
                index_version=index_version,
                embedding_model_id=embedding_model_id,
                chunking_version=chunking_version,
                created_at=source.created_at,
                updated_at=0.0,
            )
        self._sources[source.source_id] = source
        return source

    def replace_chunks_sync(self, *, source_id, chunks, projection_id=None):
        source = self._sources[source_id]
        materialized = []
        for chunk in chunks:
            embedding = chunk.get("embedding")
            embedding_array = None if embedding is None else np.asarray(embedding, dtype=np.float32)
            materialized.append(
                KnowledgeBaseChunkRecord(
                    chunk_id=str(chunk["chunk_id"]),
                    source_id=source_id,
                    collection_id=source.collection_id,
                    display_name=source.display_name,
                    chunk_index=int(chunk["chunk_index"]),
                    text=str(chunk["text"]),
                    metadata_json=dict(chunk.get("metadata_json") or {}),
                    source_origin=str(chunk.get("source_origin") or "knowledge_base"),
                    embedding=embedding_array,
                    embedding_dim=None if embedding_array is None else int(embedding_array.shape[0]),
                    embedding_model_id=str(chunk.get("embedding_model_id") or "") or None,
                )
            )
        self._chunks[source_id] = materialized

    def list_sources_sync(self, collection_id):
        return [
            source
            for source in self._sources.values()
            if source.collection_id == collection_id
        ]

    def list_chunks_sync(self, collection_id, source_ids=None, include_embeddings=False):
        rows = []
        requested_source_ids = set(source_ids or [])
        for source_id, chunks in self._chunks.items():
            source = self._sources[source_id]
            if source.collection_id != collection_id:
                continue
            if requested_source_ids and source_id not in requested_source_ids:
                continue
            for chunk in chunks:
                rows.append(
                    chunk
                    if include_embeddings
                    else KnowledgeBaseChunkRecord(
                        chunk_id=chunk.chunk_id,
                        source_id=chunk.source_id,
                        collection_id=chunk.collection_id,
                        display_name=chunk.display_name,
                        chunk_index=chunk.chunk_index,
                        text=chunk.text,
                        metadata_json=dict(chunk.metadata_json or {}),
                        source_origin=chunk.source_origin,
                    )
                )
        return rows

    def search_chunks_sync(
        self,
        *,
        collection_id,
        query_text,
        query_embedding,
        top_k,
        source_ids=None,
        filters=None,
        mode="hybrid",
        embed_fn=None,
        query_embedding_model_id=None,
    ):
        self.search_calls.append(
            {
                "collection_id": collection_id,
                "query_text": query_text,
                "top_k": top_k,
                "mode": mode,
                "filters": dict(filters or {}),
                "query_embedding_model_id": query_embedding_model_id,
            }
        )
        matches = []
        for chunk in self.list_chunks_sync(collection_id, source_ids=source_ids, include_embeddings=True):
            matches.append(
                KnowledgeBaseChunkMatchRecord(
                    chunk=chunk,
                    raw_score=1.0 if "сервис" in chunk.text.lower() else 0.1,
                    source_scope="knowledge",
                    payload={"collection_id": chunk.collection_id},
                )
            )
        matches.sort(key=lambda item: item.raw_score, reverse=True)
        return matches[:top_k]

    def get_active_projection_sync(self, collection_id):
        return None

    def delete_chunks_sync(self, *, collection_id, source_ids=None, filters=None):
        target_source_ids = set(source_ids or [])
        if not target_source_ids:
            for chunk in self.list_chunks_sync(collection_id, include_embeddings=False):
                metadata = dict(chunk.metadata_json or {})
                document_id = str(metadata.get("document_id") or chunk.source_id)
                if filters and filters.get("document_ids"):
                    allowed = {str(item) for item in filters["document_ids"] if item is not None}
                    if allowed and document_id not in allowed:
                        continue
                target_source_ids.add(chunk.source_id)
        for source_id in list(target_source_ids):
            self._chunks.pop(source_id, None)
            self._sources.pop(source_id, None)
        return len(target_source_ids)

    async def register_source(self, **kwargs):
        return self.register_source_sync(**kwargs)

    async def replace_chunks(self, *, source_id, chunks, projection_id=None):
        self.replace_chunks_sync(source_id=source_id, chunks=chunks, projection_id=projection_id)

    async def list_sources(self, collection_id):
        return self.list_sources_sync(collection_id)

    async def list_chunks(self, collection_id, source_ids=None, include_embeddings=False):
        return self.list_chunks_sync(
            collection_id,
            source_ids=source_ids,
            include_embeddings=include_embeddings,
        )

    async def search_chunks(
        self,
        *,
        collection_id,
        query_text,
        query_embedding,
        top_k,
        source_ids=None,
        filters=None,
        mode="hybrid",
        embed_fn=None,
        query_embedding_model_id=None,
    ):
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

    async def delete_chunks(self, *, collection_id, source_ids=None, filters=None):
        return self.delete_chunks_sync(
            collection_id=collection_id,
            source_ids=source_ids,
            filters=filters,
        )


def test_kb_store_registers_source_and_deduplicates_by_content_hash(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_store.db")

    first = store.register_source_sync(
        collection_id="legal",
        display_name="contract_v1.pdf",
        content_hash="hash-1",
        mime_type="application/pdf",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    second = store.register_source_sync(
        collection_id="legal",
        display_name="contract_v1_copy.pdf",
        content_hash="hash-1",
        mime_type="application/pdf",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )

    assert first.source_id == second.source_id
    assert len(store.list_sources_sync("legal")) == 1


def test_kb_store_replaces_chunks_for_source(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_store.db")
    source = store.register_source_sync(
        collection_id="legal",
        display_name="policy.txt",
        content_hash="hash-2",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )

    store.replace_chunks_sync(
        source_id=source.source_id,
        chunks=[
            {
                "chunk_id": "chunk-1",
                "chunk_index": 0,
                "text": "Первый фрагмент",
                "metadata_json": {"section": "1.1"},
            },
            {
                "chunk_id": "chunk-2",
                "chunk_index": 1,
                "text": "Второй фрагмент",
                "metadata_json": {"section": "1.2"},
            },
        ],
    )
    store.replace_chunks_sync(
        source_id=source.source_id,
        chunks=[
            {
                "chunk_id": "chunk-3",
                "chunk_index": 0,
                "text": "Обновлённый фрагмент",
                "metadata_json": {"section": "2.1"},
            }
        ],
    )

    chunks = store.list_chunks_sync(collection_id="legal")

    assert [chunk.chunk_id for chunk in chunks] == ["chunk-3"]
    assert chunks[0].metadata_json["section"] == "2.1"


def test_kb_store_persists_chunk_embeddings_when_requested(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_store.db")
    source = store.register_source_sync(
        collection_id="legal",
        display_name="policy.txt",
        content_hash="hash-emb",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )

    store.replace_chunks_sync(
        source_id=source.source_id,
        chunks=[
            {
                "chunk_id": "chunk-1",
                "chunk_index": 0,
                "text": "Первый фрагмент",
                "metadata_json": {"section": "1.1"},
                "embedding": np.array([0.1, 0.2, 0.3], dtype=np.float32),
                "embedding_model_id": "labse",
            }
        ],
    )

    chunks = store.list_chunks_sync(collection_id="legal", include_embeddings=True)

    assert chunks[0].embedding_model_id == "labse"
    assert chunks[0].embedding_dim == 3
    assert chunks[0].embedding is not None
    assert chunks[0].projection_id == store.get_active_projection_sync("legal").projection_id
    np.testing.assert_allclose(chunks[0].embedding, np.array([0.1, 0.2, 0.3], dtype=np.float32))


def test_kb_store_building_projection_does_not_replace_active_until_publish(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_store.db")
    source = store.register_source_sync(
        collection_id="legal",
        display_name="policy.txt",
        content_hash="hash-active",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    store.replace_chunks_sync(
        source_id=source.source_id,
        chunks=[
            {
                "chunk_id": "active-chunk",
                "chunk_index": 0,
                "text": "Активный фрагмент.",
                "metadata_json": {},
                "embedding": np.array([1.0, 0.0, 0.0], dtype=np.float32),
                "embedding_model_id": "labse",
            }
        ],
    )
    active = store.get_active_projection_sync("legal")

    building = store.create_building_projection_sync(
        collection_id="legal",
        embedding_model_id="qwen3-embedding-0.6b",
        embedding_dim=3,
        chunking_version="legal_v2",
    )
    published = store.publish_projection_sync(building.projection_id)

    assert active is not None
    assert building.status == "building"
    assert store.get_projection_sync(active.projection_id).status == "superseded"
    assert published.status == "active"
    assert store.get_active_projection_sync("legal").projection_id == building.projection_id


def test_kb_store_search_rejects_external_projection_without_profile(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_store.db")
    projection = store.attach_external_projection_sync(
        collection_id="attached",
        physical_collection_name="external_vectors",
    )

    with pytest.raises(EmbeddingProjectionMismatch, match="requires an explicit embedding profile"):
        store.search_chunks_sync(
            collection_id="attached",
            query_text="Что есть в базе?",
            query_embedding=np.array([1.0, 0.0, 0.0], dtype=np.float32),
            top_k=3,
        )

    assert projection.status == "needs_profile"
    assert store.get_active_projection_sync("attached") is None


def test_kb_store_attaches_external_projection_with_explicit_profile(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_store.db")

    projection = store.attach_external_projection_sync(
        collection_id="attached",
        physical_collection_name="external_vectors",
        embedding_model_id="qwen3-embedding-0.6b",
        embedding_dim=3,
        chunking_version="external_v1",
    )

    assert projection.status == "active"
    assert projection.source_mode == "attached"
    assert projection.physical_collection_name == "external_vectors"
    assert store.get_active_projection_sync("attached").projection_id == projection.projection_id


def test_sqlite_store_satisfies_runtime_protocol(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_store.db")

    assert isinstance(store, KnowledgeBaseStoreProtocol)


def test_ingestion_accepts_store_double_via_protocol_boundary():
    store = InMemoryKnowledgeBaseStore()

    record = ingest_text_source_sync(
        collection_id="legal",
        display_name="contract.txt",
        text="Статья 1. Уведомление направляется не позднее чем за 10 календарных дней.",
        store=store,
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )

    assert isinstance(store, KnowledgeBaseStoreProtocol)
    assert record.source.collection_id == "legal"
    assert len(store.list_sources_sync("legal")) == 1
    assert all(chunk.source_origin == "knowledge_base" for chunk in record.chunks)


def test_retrieval_accepts_store_double_via_protocol_boundary():
    store = InMemoryKnowledgeBaseStore()
    ingest_text_source_sync(
        collection_id="legal",
        display_name="kb_policy.txt",
        text="Сервисное обслуживание осуществляется на площадке заказчика.",
        store=store,
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
        embed_fn=_protocol_embed_fn,
    )

    result = retrieve_merged_chunks(
        query="Где описано сервисное обслуживание?",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
        session_docs={},
        active_doc_ids=[],
        embed_fn=_protocol_embed_fn,
        query_embedding_model_id="labse",
        kb_store=store,
        top_k=2,
        candidate_budget_per_scope=2,
    )

    assert isinstance(store, KnowledgeBaseStoreProtocol)
    assert result["source_scope_summary"] == "knowledge_base"
    assert len(result["chunks"]) >= 1
    assert result["chunks"][0]["source_origin"] == "knowledge_base"
    assert store.search_calls[0]["collection_id"] == "legal"
    assert store.search_calls[0]["query_text"] == "Где описано сервисное обслуживание?"
    assert store.search_calls[0]["filters"] == {"source_scope": "knowledge"}
    assert store.search_calls[0]["query_embedding_model_id"] == "labse"


def test_kb_store_search_returns_ranked_matches(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_store.db")
    source = store.register_source_sync(
        collection_id="legal",
        display_name="policy.txt",
        content_hash="hash-search",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    store.replace_chunks_sync(
        source_id=source.source_id,
        chunks=[
            {
                "chunk_id": "chunk-1",
                "chunk_index": 0,
                "text": "Сервисное обслуживание на площадке заказчика.",
                "metadata_json": {"section": "1.1"},
                "embedding": np.array([0.0, 1.0, 0.0], dtype=np.float32),
                "embedding_model_id": "labse",
            },
            {
                "chunk_id": "chunk-2",
                "chunk_index": 1,
                "text": "Штраф за просрочку поставки 3 процента.",
                "metadata_json": {"section": "1.2"},
                "embedding": np.array([1.0, 0.0, 0.0], dtype=np.float32),
                "embedding_model_id": "labse",
            },
        ],
    )

    matches = store.search_chunks_sync(
        collection_id="legal",
        query_text="Где описано сервисное обслуживание?",
        query_embedding=np.array([0.0, 1.0, 0.0], dtype=np.float32),
        top_k=2,
        filters={"source_scope": "knowledge"},
    )

    assert [match.chunk.chunk_id for match in matches] == ["chunk-1", "chunk-2"]
    assert matches[0].source_scope == "knowledge"
    assert matches[0].raw_score >= matches[1].raw_score
