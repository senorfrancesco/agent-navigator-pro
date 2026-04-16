import os
import sys
import uuid
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import orchestrator.knowledge_base_store as knowledge_base_store_module
import orchestrator.qdrant_knowledge_base_store as qdrant_store_module
from orchestrator.knowledge_base_ingestion import ingest_text_source_sync
from orchestrator.knowledge_base_retrieval import retrieve_merged_chunks
from orchestrator.knowledge_base_store import SQLiteKnowledgeBaseStore
from orchestrator.qdrant_knowledge_base_store import QdrantKnowledgeBaseStore


class _FakeVectorParams:
    def __init__(self, *, size, distance):
        self.size = size
        self.distance = distance


class _FakePointStruct:
    def __init__(self, *, id, vector, payload):
        self.id = id
        self.vector = vector
        self.payload = payload


class _FakeMatchValue:
    def __init__(self, *, value):
        self.value = value


class _FakeFieldCondition:
    def __init__(self, *, key, match=None, range=None):
        self.key = key
        self.match = match
        self.range = range


class _FakeRange:
    def __init__(self, *, gte=None):
        self.gte = gte


class _FakeFilter:
    def __init__(self, *, must):
        self.must = must


class _FakeScoredPoint:
    def __init__(self, *, id, score, payload):
        self.id = id
        self.score = score
        self.payload = payload


class _FakeQueryResponse:
    def __init__(self, *, points):
        self.points = points


class _FakeQdrantClient:
    def __init__(self, url=None):
        self.url = url
        self.collections = {}

    def collection_exists(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name, vectors_config):
        self.collections[collection_name] = {
            "vectors_config": vectors_config,
            "points": {},
        }

    def get_collection(self, collection_name):
        bucket = self.collections.get(collection_name)
        if bucket is None:
            raise KeyError(collection_name)
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=SimpleNamespace(size=getattr(bucket.get("vectors_config"), "size", None))
                )
            )
        )

    def delete_collection(self, collection_name):
        self.collections.pop(collection_name, None)

    def upsert(self, collection_name, points):
        bucket = self.collections.setdefault(collection_name, {"vectors_config": None, "points": {}})
        for point in points:
            if not isinstance(point.id, int):
                uuid.UUID(str(point.id))
            bucket["points"][str(point.id)] = {
                "id": str(point.id),
                "vector": np.asarray(point.vector, dtype=np.float32),
                "payload": dict(point.payload or {}),
            }

    def delete(self, collection_name, points_selector):
        bucket = self.collections.setdefault(collection_name, {"vectors_config": None, "points": {}})
        to_delete = []
        for point_id, point in bucket["points"].items():
            if _matches_filter(point["payload"], points_selector):
                to_delete.append(point_id)
        for point_id in to_delete:
            bucket["points"].pop(point_id, None)

    def count(self, collection_name, count_filter=None, exact=True):
        bucket = self.collections.setdefault(collection_name, {"vectors_config": None, "points": {}})
        total = 0
        for point in bucket["points"].values():
            if count_filter is not None and not _matches_filter(point["payload"], count_filter):
                continue
            total += 1
        return SimpleNamespace(count=total)

    def query_points(self, collection_name, query, query_filter, limit, with_payload):
        bucket = self.collections.setdefault(collection_name, {"vectors_config": None, "points": {}})
        query_vector = np.asarray(query, dtype=np.float32)
        results = []
        for point in bucket["points"].values():
            if not _matches_filter(point["payload"], query_filter):
                continue
            vector = np.asarray(point["vector"], dtype=np.float32)
            score = float(np.dot(query_vector, vector) / (np.linalg.norm(query_vector) * np.linalg.norm(vector)))
            results.append(_FakeScoredPoint(id=point["id"], score=score, payload=point["payload"]))
        results.sort(key=lambda item: item.score, reverse=True)
        return _FakeQueryResponse(points=results[:limit])


def _matches_filter(payload, query_filter):
    for condition in getattr(query_filter, "must", []):
        if getattr(condition, "match", None) is not None and payload.get(condition.key) != condition.match.value:
            return False
        if getattr(condition, "range", None) is not None:
            if payload.get(condition.key) is None or float(payload.get(condition.key)) < float(condition.range.gte):
                return False
    return True


def _build_fake_models():
    return SimpleNamespace(
        VectorParams=_FakeVectorParams,
        Distance=SimpleNamespace(COSINE="cosine"),
        PointStruct=_FakePointStruct,
        MatchValue=_FakeMatchValue,
        FieldCondition=_FakeFieldCondition,
        Filter=_FakeFilter,
        Range=_FakeRange,
    )


def _retrieval_embed_fn(texts):
    vectors = []
    for text in texts:
        lowered = str(text).lower()
        vec = np.array(
            [
                1.0 if "штраф" in lowered or "просроч" in lowered else 0.0,
                1.0 if "сервис" in lowered or "обслуж" in lowered else 0.0,
                1.0 if "гарант" in lowered else 0.0,
            ],
            dtype=np.float32,
        )
        if not np.any(vec):
            vec = np.ones(3, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        vectors.append(vec)
    return np.array(vectors)


def test_qdrant_store_indexes_and_searches_chunks(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    source = store.register_source_sync(
        collection_id="legal",
        display_name="policy.txt",
        content_hash="hash-qdrant",
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
                "source_origin": "knowledge_base",
                "embedding": np.array([0.0, 1.0, 0.0], dtype=np.float32),
                "embedding_model_id": "labse",
            },
            {
                "chunk_id": "chunk-2",
                "chunk_index": 1,
                "text": "Штраф за просрочку поставки 3 процента.",
                "metadata_json": {"section": "1.2"},
                "source_origin": "knowledge_base",
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
    assert matches[0].payload["collection_id"] == "legal"
    assert matches[0].payload["document_id"] == source.source_id


def test_qdrant_store_supports_session_scope_search_and_delete(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    source = store.register_source_sync(
        collection_id="session:thread-42",
        display_name="contract.txt",
        content_hash="ver-1",
        mime_type="text/plain",
        index_version="session_v1",
        embedding_model_id="labse",
        chunking_version="session_v1",
    )
    store.replace_chunks_sync(
        source_id=source.source_id,
        chunks=[
            {
                "chunk_id": "session-chunk-1",
                "chunk_index": 0,
                "text": "Штраф составляет 10 процентов от суммы договора.",
                "metadata_json": {
                    "document_id": "doc-1",
                    "document_version_id": "ver-1",
                    "thread_id": "thread-42",
                    "source_scope": "session",
                    "expires_at": 4102444800.0,
                },
                "source_origin": "session_upload",
                "embedding": np.array([1.0, 0.0, 0.0], dtype=np.float32),
                "embedding_model_id": "labse",
            }
        ],
    )

    matches = store.search_chunks_sync(
        collection_id="session:thread-42",
        query_text="Какой штраф указан в договоре?",
        query_embedding=np.array([1.0, 0.0, 0.0], dtype=np.float32),
        top_k=2,
        filters={
            "source_scope": "session",
            "thread_id": "thread-42",
            "document_ids": ["doc-1"],
            "expires_at_gte": 0.0,
        },
    )

    assert [match.payload["document_id"] for match in matches] == ["doc-1"]

    deleted = store.delete_chunks_sync(
        collection_id="session:thread-42",
        filters={
            "source_scope": "session",
            "thread_id": "thread-42",
            "document_version_id": "ver-1",
        },
    )

    assert deleted == 1
    assert store.search_chunks_sync(
        collection_id="session:thread-42",
        query_text="Какой штраф указан в договоре?",
        query_embedding=np.array([1.0, 0.0, 0.0], dtype=np.float32),
        top_k=2,
        filters={"source_scope": "session", "thread_id": "thread-42"},
    ) == []


def test_qdrant_store_search_respects_multiple_source_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    source_a = store.register_source_sync(
        collection_id="legal",
        display_name="policy-a.txt",
        content_hash="hash-a",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    source_b = store.register_source_sync(
        collection_id="legal",
        display_name="policy-b.txt",
        content_hash="hash-b",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    source_c = store.register_source_sync(
        collection_id="legal",
        display_name="policy-c.txt",
        content_hash="hash-c",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    store.replace_chunks_sync(
        source_id=source_a.source_id,
        chunks=[
            {
                "chunk_id": "chunk-a",
                "chunk_index": 0,
                "text": "Сервисное обслуживание для политики A.",
                "metadata_json": {"section": "a"},
                "source_origin": "knowledge_base",
                "embedding": np.array([1.0, 0.0, 0.0], dtype=np.float32),
                "embedding_model_id": "labse",
            }
        ],
    )
    store.replace_chunks_sync(
        source_id=source_b.source_id,
        chunks=[
            {
                "chunk_id": "chunk-b",
                "chunk_index": 0,
                "text": "Сервисное обслуживание для политики B.",
                "metadata_json": {"section": "b"},
                "source_origin": "knowledge_base",
                "embedding": np.array([0.9, 0.1, 0.0], dtype=np.float32),
                "embedding_model_id": "labse",
            }
        ],
    )
    store.replace_chunks_sync(
        source_id=source_c.source_id,
        chunks=[
            {
                "chunk_id": "chunk-c",
                "chunk_index": 0,
                "text": "Сервисное обслуживание для политики C.",
                "metadata_json": {"section": "c"},
                "source_origin": "knowledge_base",
                "embedding": np.array([0.8, 0.2, 0.0], dtype=np.float32),
                "embedding_model_id": "labse",
            }
        ],
    )

    matches = store.search_chunks_sync(
        collection_id="legal",
        query_text="Где описано сервисное обслуживание?",
        query_embedding=np.array([1.0, 0.0, 0.0], dtype=np.float32),
        top_k=5,
        source_ids=[source_a.source_id, source_b.source_id],
        filters={"source_scope": "knowledge"},
    )

    assert {match.chunk.source_id for match in matches} == {source_a.source_id, source_b.source_id}
    assert all(match.chunk.source_id != source_c.source_id for match in matches)


def test_qdrant_store_delete_respects_multiple_source_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    source_a = store.register_source_sync(
        collection_id="legal",
        display_name="policy-a.txt",
        content_hash="hash-a",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    source_b = store.register_source_sync(
        collection_id="legal",
        display_name="policy-b.txt",
        content_hash="hash-b",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    source_c = store.register_source_sync(
        collection_id="legal",
        display_name="policy-c.txt",
        content_hash="hash-c",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    for source, chunk_id in ((source_a, "chunk-a"), (source_b, "chunk-b"), (source_c, "chunk-c")):
        store.replace_chunks_sync(
            source_id=source.source_id,
            chunks=[
                {
                    "chunk_id": chunk_id,
                    "chunk_index": 0,
                    "text": f"Текст для {chunk_id}.",
                    "metadata_json": {"section": chunk_id},
                    "source_origin": "knowledge_base",
                    "embedding": np.array([1.0, 0.0, 0.0], dtype=np.float32),
                    "embedding_model_id": "labse",
                }
            ],
        )

    deleted = store.delete_chunks_sync(
        collection_id="legal",
        source_ids=[source_a.source_id, source_b.source_id],
        filters={"source_scope": "knowledge"},
    )

    assert deleted == 2
    remaining = store.list_chunks_sync("legal", include_embeddings=False)
    assert [chunk.source_id for chunk in remaining] == [source_c.source_id]
    remaining_point_sources = {
        point["payload"]["source_id"]
        for point in store._client.collections["rag_chunks_v1"]["points"].values()
    }
    assert remaining_point_sources == {source_c.source_id}


def test_get_knowledge_base_store_selects_qdrant_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )
    monkeypatch.setenv("KB_BACKEND", "qdrant")
    monkeypatch.setenv("ORCHESTRATOR_KB_DB_URL", f"sqlite:///{tmp_path}/kb_store.db")
    monkeypatch.setenv("QDRANT_URL", "http://fake-qdrant")
    monkeypatch.setenv("QDRANT_COLLECTION_NAME", "rag_chunks_v1")
    monkeypatch.setattr(knowledge_base_store_module, "_STORE_SINGLETON", None)

    store = knowledge_base_store_module.get_knowledge_base_store()

    assert isinstance(store, QdrantKnowledgeBaseStore)
    assert store.collection_name == "rag_chunks_v1"


def test_qdrant_store_supports_session_retrieval_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    ingest_text_source_sync(
        collection_id="session:thread-42",
        display_name="contract.txt",
        text="Штраф составляет 10 процентов от суммы договора.",
        store=store,
        mime_type="text/plain",
        index_version="session_v1",
        embedding_model_id="labse",
        chunking_version="session_v1",
        embed_fn=lambda texts: np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32),
        content_hash_override="ver-1",
        source_origin="session_upload",
        base_metadata={
            "document_id": "doc-1",
            "document_version_id": "ver-1",
            "thread_id": "thread-42",
            "source_scope": "session",
            "expires_at": 4102444800.0,
        },
    )
    session_docs = {
        "contract.txt": {
            "document_id": "doc-1",
            "version_id": "ver-1",
            "thread_id": "thread-42",
            "text": "Штраф составляет 10 процентов от суммы договора.",
            "path": "/tmp/contract.txt",
            "ingestion_status": "indexed",
        }
    }

    result = retrieve_merged_chunks(
        query="Какой штраф указан в договоре?",
        rag_scope="session_rag",
        knowledge_collection_id=None,
        session_docs=session_docs,
        active_doc_ids=["doc-1"],
        embed_fn=lambda texts: np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32),
        kb_store=store,
        top_k=3,
        candidate_budget_per_scope=3,
    )

    assert result["source_scope_summary"] == "session"
    assert result["chunks"][0]["document_id"] == "doc-1"
    assert result["chunks"][0]["metadata_json"]["thread_id"] == "thread-42"
    assert result["chunks"][0]["source_origin"] == "session_upload"


def test_qdrant_store_without_embeddings_keeps_sqlite_chunks_but_vector_search_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    source = store.register_source_sync(
        collection_id="legal",
        display_name="policy.txt",
        content_hash="hash-no-emb",
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
                "text": "Текст без эмбеддинга.",
                "metadata_json": {"section": "1.1"},
                "source_origin": "knowledge_base",
            }
        ],
    )

    sqlite_chunks = store.list_chunks_sync("legal", include_embeddings=False)
    search_matches = store.search_chunks_sync(
        collection_id="legal",
        query_text="Что в документе?",
        query_embedding=np.array([1.0, 0.0, 0.0], dtype=np.float32),
        top_k=3,
        filters={"source_scope": "knowledge"},
    )

    assert [chunk.chunk_id for chunk in sqlite_chunks] == ["chunk-1"]
    assert search_matches == []
    points = store._client.collections.get("rag_chunks_v1", {}).get("points", {})
    assert points == {}


def test_qdrant_store_supports_knowledge_base_rag_with_session_overlay(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    duplicate_text = "За просрочку поставки применяется штраф 3 процента."
    ingest_text_source_sync(
        collection_id="legal",
        display_name="kb_policy.txt",
        text=duplicate_text,
        store=store,
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
        embed_fn=lambda texts: np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32),
    )
    session_docs = {
        "contract.txt": {
            "document_id": "session-contract",
            "thread_id": "thread-42",
            "text": duplicate_text,
            "path": "/tmp/contract.txt",
        }
    }

    result = retrieve_merged_chunks(
        query="Какой штраф за просрочку поставки?",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
        session_docs=session_docs,
        active_doc_ids=["session-contract"],
        embed_fn=lambda texts: np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32),
        kb_store=store,
        top_k=3,
        candidate_budget_per_scope=3,
    )

    assert result["source_scope_summary"] == "knowledge_base+session_overlay"
    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["source_origin"] == "session"


def test_qdrant_retrieval_matches_sqlite_top_chunk_for_hybrid_and_semantic_modes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    sqlite_store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_sqlite.db")
    qdrant_store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_qdrant.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    for store in (sqlite_store, qdrant_store):
        ingest_text_source_sync(
            collection_id="legal",
            display_name="penalty.txt",
            text="За просрочку поставки применяется штраф 3 процента.",
            store=store,
            mime_type="text/plain",
            index_version="v1",
            embedding_model_id="labse",
            chunking_version="legal_v1",
            embed_fn=_retrieval_embed_fn,
        )
        ingest_text_source_sync(
            collection_id="legal",
            display_name="service.txt",
            text="Сервисное обслуживание осуществляется на площадке заказчика.",
            store=store,
            mime_type="text/plain",
            index_version="v1",
            embedding_model_id="labse",
            chunking_version="legal_v1",
            embed_fn=_retrieval_embed_fn,
        )

    for mode in ("hybrid", "semantic"):
        sqlite_result = retrieve_merged_chunks(
            query="Какой штраф за просрочку поставки?",
            rag_scope="knowledge_base_rag",
            knowledge_collection_id="legal",
            session_docs={},
            active_doc_ids=[],
            embed_fn=_retrieval_embed_fn,
            kb_store=sqlite_store,
            top_k=2,
            candidate_budget_per_scope=2,
            mode=mode,
        )
        qdrant_result = retrieve_merged_chunks(
            query="Какой штраф за просрочку поставки?",
            rag_scope="knowledge_base_rag",
            knowledge_collection_id="legal",
            session_docs={},
            active_doc_ids=[],
            embed_fn=_retrieval_embed_fn,
            kb_store=qdrant_store,
            top_k=2,
            candidate_budget_per_scope=2,
            mode=mode,
        )

        assert sqlite_result["source_scope_summary"] == "knowledge_base"
        assert qdrant_result["source_scope_summary"] == "knowledge_base"
        assert sqlite_result["chunks"][0]["display_name"] == "penalty.txt"
        assert qdrant_result["chunks"][0]["display_name"] == "penalty.txt"


def test_qdrant_retrieval_applies_optional_rerank_hook_to_shortlist(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    ingest_text_source_sync(
        collection_id="legal",
        display_name="penalty.txt",
        text="За просрочку поставки применяется штраф 3 процента.",
        store=store,
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
        embed_fn=_retrieval_embed_fn,
    )
    session_docs = {
        "service.txt": {
            "document_id": "session-service",
            "thread_id": "thread-42",
            "text": "Сервисное обслуживание осуществляется на площадке заказчика.",
            "path": "/tmp/service.txt",
        }
    }

    def rerank_fn(query, chunks):
        return [1.0 if "сервис" in chunk["text"].lower() else 0.0 for chunk in chunks]

    result = retrieve_merged_chunks(
        query="Что сказано про штраф и обслуживание?",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
        session_docs=session_docs,
        active_doc_ids=["session-service"],
        embed_fn=_retrieval_embed_fn,
        kb_store=store,
        top_k=2,
        candidate_budget_per_scope=4,
        rerank_fn=rerank_fn,
    )

    assert result["chunks"][0]["text"].lower().startswith("сервисное обслуживание")
    assert result["chunks"][0]["rerank_score"] == 1.0


def test_qdrant_retrieval_falls_back_to_session_docs_when_store_backed_session_filtered_out(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    ingest_text_source_sync(
        collection_id="session:thread-42",
        display_name="contract.txt",
        text="Просрочка поставки влечёт штраф 10 процентов.",
        store=store,
        mime_type="text/plain",
        index_version="session_v1",
        embedding_model_id="labse",
        chunking_version="session_v1",
        embed_fn=lambda texts: np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32),
        content_hash_override="ver-expired",
        source_origin="session_upload",
        base_metadata={
            "document_id": "doc-1",
            "document_version_id": "ver-expired",
            "thread_id": "thread-42",
            "source_scope": "session",
            "expires_at": 1.0,
        },
    )
    session_docs = {
        "contract.txt": {
            "document_id": "doc-1",
            "thread_id": "thread-42",
            "text": "Просрочка поставки влечёт штраф 10 процентов.",
            "path": "/tmp/contract.txt",
        }
    }

    result = retrieve_merged_chunks(
        query="Какой штраф за просрочку поставки?",
        rag_scope="session_rag",
        knowledge_collection_id=None,
        session_docs=session_docs,
        active_doc_ids=["doc-1"],
        embed_fn=lambda texts: np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32),
        kb_store=store,
        top_k=3,
        candidate_budget_per_scope=3,
    )

    assert result["source_scope_summary"] == "session"
    assert result["chunks"][0]["document_id"] == "doc-1"
    assert result["chunks"][0]["source_origin"] == "session"


def test_get_knowledge_base_store_raises_clear_error_when_qdrant_dependencies_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_ for _ in ()).throw(RuntimeError("qdrant-client is not installed.")),
    )
    monkeypatch.setenv("KB_BACKEND", "qdrant")
    monkeypatch.setenv("ORCHESTRATOR_KB_DB_URL", f"sqlite:///{tmp_path}/kb_store.db")
    monkeypatch.setattr(knowledge_base_store_module, "_STORE_SINGLETON", None)

    with pytest.raises(RuntimeError, match="qdrant-client is not installed"):
        knowledge_base_store_module.get_knowledge_base_store()


def test_qdrant_store_rejects_existing_collection_with_dimension_mismatch_when_knowledge_points_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    store._client.create_collection(
        "rag_chunks_v1",
        _FakeVectorParams(size=3, distance="cosine"),
    )
    store._client.upsert(
        "rag_chunks_v1",
        points=[
            _FakePointStruct(
                id="22222222-2222-2222-2222-222222222222",
                vector=[1.0, 0.0, 0.0],
                payload={
                    "chunk_id": "legacy-knowledge-1",
                    "collection_id": "legal",
                    "source_id": "legacy-knowledge-source",
                    "document_id": "doc-kb-1",
                    "document_version_id": "doc-kb-1",
                    "display_name": "kb.txt",
                    "source_scope": "knowledge",
                },
            )
        ],
    )
    source = store.register_source_sync(
        collection_id="legal",
        display_name="policy.txt",
        content_hash="hash-mismatch",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )

    with pytest.raises(RuntimeError, match="expects vector size 3"):
        store.replace_chunks_sync(
            source_id=source.source_id,
            chunks=[
                {
                    "chunk_id": "chunk-1",
                    "chunk_index": 0,
                    "text": "Текст с новой размерностью.",
                    "metadata_json": {"section": "1.1"},
                    "source_origin": "knowledge_base",
                    "embedding": np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
                    "embedding_model_id": "labse",
                }
            ],
        )


def test_qdrant_store_recreates_session_only_collection_on_dimension_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_models()),
    )

    store = QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/kb_store.db",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )
    store._client.create_collection(
        "rag_chunks_v1",
        _FakeVectorParams(size=3, distance="cosine"),
    )
    store._client.upsert(
        "rag_chunks_v1",
        points=[
            _FakePointStruct(
                id="11111111-1111-1111-1111-111111111111",
                vector=[1.0, 0.0, 0.0],
                payload={
                    "chunk_id": "legacy-session-1",
                    "collection_id": "session:thread-42",
                    "source_id": "legacy-session-source",
                    "document_id": "doc-1",
                    "document_version_id": "ver-1",
                    "display_name": "contract.txt",
                    "source_scope": "session",
                    "thread_id": "thread-42",
                    "expires_at": 4102444800.0,
                },
            )
        ],
    )

    source = store.register_source_sync(
        collection_id="session:thread-42",
        display_name="contract.txt",
        content_hash="ver-2",
        mime_type="text/plain",
        index_version="session_v1",
        embedding_model_id="labse",
        chunking_version="session_v1",
    )
    store.replace_chunks_sync(
        source_id=source.source_id,
        chunks=[
            {
                "chunk_id": "session-chunk-1024",
                "chunk_index": 0,
                "text": "Штраф составляет 17 процентов.",
                "metadata_json": {
                    "document_id": "doc-2",
                    "document_version_id": "ver-2",
                    "thread_id": "thread-42",
                    "source_scope": "session",
                    "expires_at": 4102444800.0,
                },
                "source_origin": "session_upload",
                "embedding": np.ones(1024, dtype=np.float32),
                "embedding_model_id": "labse",
            }
        ],
    )

    bucket = store._client.collections["rag_chunks_v1"]
    assert bucket["vectors_config"].size == 1024
    assert len(bucket["points"]) == 1
    only_payload = next(iter(bucket["points"].values()))["payload"]
    assert only_payload["chunk_id"] == "session-chunk-1024"
