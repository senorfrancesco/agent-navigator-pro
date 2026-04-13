import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.knowledge_base_ingestion import ingest_text_source_sync
from orchestrator.knowledge_base_retrieval import retrieve_merged_chunks
from orchestrator.knowledge_base_store import SQLiteKnowledgeBaseStore


def _stub_embed_fn(texts):
    vectors = []
    for text in texts:
        lowered = text.lower()
        vec = np.array(
            [
                1.0 if "уведом" in lowered or "дней" in lowered else 0.0,
                1.0 if "штраф" in lowered or "просроч" in lowered else 0.0,
                1.0 if "сервис" in lowered or "обслуж" in lowered else 0.0,
                1.0 if "гарант" in lowered or "24 месяц" in lowered else 0.0,
            ],
            dtype=np.float32,
        )
        if not np.any(vec):
            vec = np.ones(4, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        vectors.append(vec)
    return np.array(vectors)


class CountingEmbedFn:
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return _stub_embed_fn(texts)


def test_retrieve_merged_chunks_combines_kb_and_session_with_provenance(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_retrieval.db")
    ingest_text_source_sync(
        collection_id="legal",
        display_name="kb_policy.txt",
        text="Сервисное обслуживание осуществляется на площадке заказчика.",
        store=store,
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    session_docs = {
        "contract.txt": {
            "document_id": "session-contract",
            "text": "За просрочку поставки применяется штраф 3 процента.",
            "path": "/tmp/contract.txt",
        }
    }

    result = retrieve_merged_chunks(
        query="Какая ответственность за просрочку и где описано сервисное обслуживание?",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
        session_docs=session_docs,
        active_doc_ids=["session-contract"],
        embed_fn=_stub_embed_fn,
        kb_store=store,
        top_k=4,
        candidate_budget_per_scope=4,
    )

    assert result["source_scope_summary"] == "knowledge_base+session_overlay"
    origins = {chunk["source_origin"] for chunk in result["chunks"]}
    assert origins == {"knowledge_base", "session"}


def test_retrieve_merged_chunks_session_rag_only_uses_active_session_docs(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_retrieval.db")
    ingest_text_source_sync(
        collection_id="legal",
        display_name="kb_policy.txt",
        text="Гарантийный срок оборудования составляет 24 месяца.",
        store=store,
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    session_docs = {
        "contract.txt": {
            "document_id": "session-contract",
            "text": "Уведомление направляется не позднее чем за 10 календарных дней.",
            "path": "/tmp/contract.txt",
        }
    }

    result = retrieve_merged_chunks(
        query="За сколько дней направляется уведомление?",
        rag_scope="session_rag",
        knowledge_collection_id="legal",
        session_docs=session_docs,
        active_doc_ids=["session-contract"],
        embed_fn=_stub_embed_fn,
        kb_store=store,
        top_k=3,
        candidate_budget_per_scope=3,
    )

    assert result["source_scope_summary"] == "session"
    assert all(chunk["source_origin"] == "session" for chunk in result["chunks"])


def test_retrieve_merged_chunks_prefers_store_backed_session_scope_when_indexed(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_retrieval.db")
    ingest_text_source_sync(
        collection_id="session:thread-42",
        display_name="contract.txt",
        text="Штраф составляет 10 процентов от суммы договора.",
        store=store,
        mime_type="text/plain",
        index_version="session_v1",
        embedding_model_id="labse",
        chunking_version="session_v1",
        embed_fn=_stub_embed_fn,
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
        embed_fn=_stub_embed_fn,
        kb_store=store,
        top_k=3,
        candidate_budget_per_scope=3,
    )

    assert result["source_scope_summary"] == "session"
    assert result["chunks"][0]["document_id"] == "doc-1"
    assert result["chunks"][0]["metadata_json"]["thread_id"] == "thread-42"
    assert result["chunks"][0]["source_origin"] == "session_upload"


def test_retrieve_merged_chunks_prefers_session_overlay_for_duplicate_text(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_retrieval.db")
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
    )
    session_docs = {
        "contract.txt": {
            "document_id": "session-contract",
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
        embed_fn=_stub_embed_fn,
        kb_store=store,
        top_k=3,
        candidate_budget_per_scope=3,
    )

    assert result["source_scope_summary"] == "knowledge_base+session_overlay"
    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["source_origin"] == "session"


def test_retrieve_merged_chunks_uses_persisted_kb_embeddings_without_reembedding_docs(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_retrieval.db")
    embed_fn = CountingEmbedFn()
    ingest_text_source_sync(
        collection_id="legal",
        display_name="kb_policy.txt",
        text="Гарантийный срок оборудования составляет 24 месяца.",
        store=store,
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
        embed_fn=embed_fn,
    )
    embed_fn.calls.clear()

    result = retrieve_merged_chunks(
        query="Какой гарантийный срок оборудования?",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
        session_docs={},
        active_doc_ids=[],
        embed_fn=embed_fn,
        kb_store=store,
        top_k=2,
        candidate_budget_per_scope=2,
    )

    assert result["source_scope_summary"] == "knowledge_base"
    assert len(result["chunks"]) >= 1
    assert embed_fn.calls == [["Какой гарантийный срок оборудования?"]]


def test_retrieve_merged_chunks_applies_optional_rerank_hook_to_shortlist(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_retrieval.db")
    ingest_text_source_sync(
        collection_id="legal",
        display_name="kb_policy.txt",
        text="За просрочку поставки применяется штраф 3 процента.",
        store=store,
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
        embed_fn=_stub_embed_fn,
    )
    session_docs = {
        "service.txt": {
            "document_id": "session-service",
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
        embed_fn=_stub_embed_fn,
        kb_store=store,
        top_k=2,
        candidate_budget_per_scope=4,
        rerank_fn=rerank_fn,
    )

    assert result["chunks"][0]["text"].lower().startswith("сервисное обслуживание")
    assert result["chunks"][0]["rerank_score"] == 1.0
