import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.execution_runtime import (
    ExecutionDependencies,
    _run_graph,
    execute_orchestration,
)
from orchestrator.doc_question_heuristics import (
    build_doc_question_deterministic_fallback,
    citations_are_valid,
    compute_confidence_v1,
    extract_citation_ids,
    has_sufficient_evidence_v1,
)
from orchestrator.state_store import get_orchestration_state_store


def _build_minimal_deps() -> ExecutionDependencies:
    return ExecutionDependencies(
        infer_assistant_text=AsyncMock(return_value="Привет!"),
        build_prompt=lambda query, history, system_msg="": f"{system_msg}\n{query}",
        get_profile_system_prompt=lambda: "Ты ассистент.",
        has_retrieval_adapter=lambda: False,
        get_retrieval_embed_fn=lambda: None,
        get_knowledge_base_store=lambda: None,
        get_active_doc_ids=lambda: [],
        get_all_docs=lambda: [],
        get_active_docs=lambda: [],
        get_report_docs=lambda: [],
        resolve_target_doc_name=lambda query, docs: None,
        is_report_query=lambda query: False,
        ensure_rag_index_for_doc_ids=AsyncMock(return_value=False),
        get_rag_pipeline=lambda: None,
        build_sources_from_rag_result=lambda rag_result, rag_pipeline, max_sources=5: [],
        reindex_sources=lambda sources: sources,
        build_doc_question_deterministic_fallback=lambda **kwargs: {
            "answer_text": "Недостаточно данных.",
            "sources": [],
            "answer_mode": "insufficient_evidence",
            "fallback_type": kwargs.get("fallback_type", "insufficient_evidence"),
            "fallback_reason": kwargs.get("fallback_reason"),
            "confidence": 0.1,
            "confidence_label": "low",
            "confidence_method": "heuristic_v1",
            "confidence_version": "1",
        },
        render_doc_question_markdown=lambda payload: payload["answer_text"],
        build_doc_question_prompt_with_sources=lambda query, history, sources: query,
        citations_are_valid=lambda answer_text, source_count: True,
        needs_doc_question_regen=lambda answer_text, has_session_docs: False,
        extract_citation_ids=lambda answer_text: [],
        has_sufficient_evidence=lambda **kwargs: False,
        compute_confidence_v1=lambda sources, cited_ids, answer_mode, **kwargs: (0.4, "medium"),
        strip_model_source_sections=lambda answer_text: answer_text,
        to_host_path=lambda path: path,
        active_set_status_line=lambda: "Активный набор: 0 документов",
        attach_and_register_report=AsyncMock(),
    )


def test_execute_orchestration_adds_state_ref_and_pending_action_metadata():
    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Сравни эти два документа",
                "session_id": "session-1",
                "runtime_mode": "specialized_tasks",
                "file_count": 2,
                "has_session_docs": True,
                "session_docs": {
                    "old.pdf": {"text": "Старая версия"},
                    "new.pdf": {"text": "Новая версия"},
                },
                "attachments_meta": [
                    {"name": "old.pdf", "path": "/tmp/old.pdf"},
                    {"name": "new.pdf", "path": "/tmp/new.pdf"},
                ],
                "active_doc_ids": ["old", "new"],
                "classifier_result": {
                    "intent": "general_chat",
                    "confidence": 0.44,
                    "margin": 0.005,
                    "needs_rag": False,
                },
            }
        )
    )

    assert response["route"] == "compare_documents"
    assert response["action_required"]["type"] == "choose_route"
    assert response["pending_action_id"]
    assert response["state_ref"].startswith("run:")
    assert response["run_id"]
    assert response["state_version"] == 2
    assert response["ui_effects"]["set_pending_action"]["type"] == "choose_route"


def test_execute_orchestration_runs_chat_handler_via_backend_dependencies():
    deps = _build_minimal_deps()

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет",
                "session_id": "session-2",
                "runtime_mode": "chat_only",
                "history": [],
            },
            deps=deps,
        )
    )

    assert response["executor"] == "chat"
    assert response["assistant_message"] == "Привет!"
    deps.infer_assistant_text.assert_awaited_once()


def test_execute_orchestration_adds_top_level_control_plane_fields():
    deps = _build_minimal_deps()

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что по загруженному документу?",
                "session_id": "session-3",
                "assistant_mode": "rag_qa",
                "rag_scope": "knowledge_base_rag",
                "knowledge_collection_id": "legal",
                "session_docs": {
                    "doc.txt": {"text": "штраф 10%"},
                },
                "active_doc_ids": ["doc.txt"],
                "history": [],
            },
            deps=deps,
        )
    )

    assert response["rag_scope"] == "knowledge_base_rag"
    assert response["knowledge_collection_id"] == "legal"
    assert response["source_scope_summary"] == "knowledge_base+session_overlay"


def test_execute_orchestration_exposes_top_level_control_plane_fields():
    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что указано в базе знаний?",
                "assistant_mode": "rag_qa",
                "rag_scope": "knowledge_base_rag",
                "knowledge_collection_id": "legal",
                "session_docs": {
                    "session-note.txt": {"text": "overlay"},
                },
                "has_session_docs": True,
                "active_doc_ids": ["session-note.txt"],
                "classifier_result": {
                    "intent": "general_chat",
                    "confidence": 0.2,
                    "margin": 0.1,
                    "needs_rag": True,
                },
            }
        )
    )

    assert response["rag_scope"] == "knowledge_base_rag"
    assert response["knowledge_collection_id"] == "legal"
    assert response["source_scope_summary"] == "knowledge_base+session_overlay"
    assert isinstance(response.get("model_profile"), (str, type(None)))


def test_execute_orchestration_marks_fresh_attachments_as_session_overlay():
    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что в свежем документе?",
                "assistant_mode": "rag_qa",
                "rag_scope": "knowledge_base_rag",
                "knowledge_collection_id": "legal",
                "attachments_meta": [{"name": "fresh.pdf", "path": "/tmp/fresh.pdf"}],
            }
        )
    )

    assert response["rag_scope"] == "knowledge_base_rag"
    assert response["source_scope_summary"] == "knowledge_base+session_overlay"


def test_execute_orchestration_doc_question_multihop_single_citation_falls_back(monkeypatch):
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {"document_id": "doc-a", "display_name": "a.pdf", "text": "Уведомление за 10 дней"},
        {"document_id": "doc-b", "display_name": "b.pdf", "text": "Штраф 10 процентов"},
    ]
    deps.get_active_docs = deps.get_all_docs
    deps.resolve_target_doc_name = lambda query, docs: None
    deps.get_rag_pipeline = lambda: SimpleNamespace(
        _indexed=True,
        top_k=5,
        retrieve=lambda query, top_k=None: SimpleNamespace(chunks=["chunk-a", "chunk-b"], metadata={"mode": "simple"}),
    )
    deps.build_sources_from_rag_result = lambda rag_result, rag_pipeline, max_sources=20: [
        {
            "source_id": 1,
            "document_id": "doc-a",
            "display_name": "a.pdf",
            "chunk_id": 0,
            "char_span": {"start_char": 0, "end_char": 100},
            "page": None,
            "quote": "Уведомление за 10 дней",
            "raw_score": 0.72,
            "normalized_score": 0.95,
            "grade": "excellent",
            "z_score": 0.8,
        },
        {
            "source_id": 2,
            "document_id": "doc-b",
            "display_name": "b.pdf",
            "chunk_id": 1,
            "char_span": {"start_char": 101, "end_char": 200},
            "page": None,
            "quote": "Штраф 10 процентов",
            "raw_score": 0.68,
            "normalized_score": 0.91,
            "grade": "excellent",
            "z_score": 0.7,
        },
    ]
    deps.infer_assistant_text = AsyncMock(return_value="Ответ [1]")
    deps.citations_are_valid = citations_are_valid
    deps.extract_citation_ids = extract_citation_ids
    deps.has_sufficient_evidence = has_sufficient_evidence_v1
    deps.compute_confidence_v1 = compute_confidence_v1
    deps.build_doc_question_deterministic_fallback = build_doc_question_deterministic_fallback
    deps.render_doc_question_markdown = lambda payload: payload["answer_text"]
    deps.build_doc_question_prompt_with_sources = lambda query, history, sources: "prompt"

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.execution_runtime.asyncio.to_thread", fake_to_thread)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Сравни условия уведомления и штрафа между документами",
                "runtime_mode": "specialized_tasks",
                "assistant_mode": "rag_qa",
                "rag_scope": "session_rag",
                "has_session_docs": True,
                "session_docs": {
                    "a.pdf": {"document_id": "doc-a", "text": "Уведомление за 10 дней"},
                    "b.pdf": {"document_id": "doc-b", "text": "Штраф 10 процентов"},
                },
                "active_doc_ids": ["doc-a", "doc-b"],
                "classifier_result": {
                    "intent": "document_question",
                    "confidence": 0.95,
                    "margin": 0.5,
                    "needs_rag": True,
                },
            },
            deps=deps,
        )
    )

    assert response["route"] == "document_question"
    assert "данных недостаточно" in response["assistant_message"].lower()


def test_run_graph_merges_errors_from_multiple_nodes():
    class _Workflow:
        async def astream(self, initial_state):
            yield {"node_a": {"errors": ["first"], "value": 1}}
            yield {"node_b": {"errors": ["second"], "other": 2}}

    final_state = asyncio.run(_run_graph(_Workflow(), {"errors": []}))

    assert final_state["errors"] == ["first", "second"]
    assert final_state["value"] == 1
    assert final_state["other"] == 2


def test_execute_orchestration_doc_question_handles_rag_result_without_metadata_and_filters_by_display_name(monkeypatch):
    deps = _build_minimal_deps()
    deps.has_retrieval_adapter = lambda: True
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.pdf",
            "text": "Штраф 10 процентов",
        }
    ]
    deps.get_active_docs = deps.get_all_docs
    deps.resolve_target_doc_name = lambda query, docs: "contract.pdf"
    deps.ensure_rag_index_for_doc_ids = AsyncMock(return_value=True)
    deps.get_rag_pipeline = lambda: SimpleNamespace(
        _indexed=True,
        top_k=5,
        retrieve=lambda query, top_k=None: SimpleNamespace(chunks=["chunk-1"]),
    )
    deps.build_sources_from_rag_result = lambda rag_result, rag_pipeline, max_sources=20: [
        {"document_id": "doc-1", "display_name": "contract.pdf", "source_id": 1},
        {"document_id": "doc-2", "display_name": "other.pdf", "source_id": 2},
    ]
    deps.reindex_sources = lambda sources: [
        {**source, "source_id": idx + 1} for idx, source in enumerate(sources)
    ]
    deps.build_doc_question_prompt_with_sources = lambda query, history, sources: "prompt with sources"
    deps.infer_assistant_text = AsyncMock(return_value="Ответ [1]")
    deps.extract_citation_ids = lambda answer_text: [1]
    deps.has_sufficient_evidence = lambda **kwargs: True
    deps.compute_confidence_v1 = lambda sources, cited_ids, answer_mode, **kwargs: (0.8, "high")
    deps.render_doc_question_markdown = lambda payload: payload["answer_text"]

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.execution_runtime.asyncio.to_thread", fake_to_thread)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что написано в contract.pdf про штраф?",
                "runtime_mode": "specialized_tasks",
                "session_docs": {
                    "contract.pdf": {
                        "document_id": "doc-1",
                        "text": "Штраф 10 процентов",
                        "path": "/tmp/contract.pdf",
                    }
                },
                "has_session_docs": True,
                "active_doc_ids": ["doc-1"],
                "classifier_result": {
                    "intent": "document_question",
                    "confidence": 0.95,
                    "margin": 0.7,
                    "needs_rag": True,
                },
            },
            deps=deps,
        )
    )

    assert response["assistant_message"] == "Ответ [1]"
    assert response["sources"] == [
        {"document_id": "doc-1", "display_name": "contract.pdf", "source_id": 1}
    ]


def test_execute_orchestration_reuses_same_run_for_same_thread_and_persists_resume_snapshot():
    deps = _build_minimal_deps()

    first = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет",
                "thread_id": "thread-42",
                "history": [],
                "ui_state": {"control_plane_state": {"assistant_mode": "general_chat"}},
            },
            deps=deps,
        )
    )
    second = asyncio.run(
        execute_orchestration(
            {
                "message": "Еще вопрос",
                "thread_id": "thread-42",
                "history": [],
                "ui_state": {"control_plane_state": {"assistant_mode": "general_chat"}},
            },
            deps=deps,
        )
    )

    assert first["run_id"] == second["run_id"]
    assert first["state_ref"] == second["state_ref"]
    assert second["state_version"] > first["state_version"]

    stored = asyncio.run(get_orchestration_state_store().load_run(run_id=second["run_id"]))
    assert stored is not None
    assert "document_refs" not in (stored.resume_state_blob or {})


def test_execute_orchestration_reuses_same_run_for_same_idempotency_key():
    deps = _build_minimal_deps()

    first = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет",
                "session_id": "session-idem-1",
                "idempotency_key": "same-key",
                "history": [],
                "ui_state": {"control_plane_state": {"assistant_mode": "general_chat"}},
            },
            deps=deps,
        )
    )
    second = asyncio.run(
        execute_orchestration(
            {
                "message": "Повтор",
                "session_id": "session-idem-2",
                "idempotency_key": "same-key",
                "history": [],
                "ui_state": {"control_plane_state": {"assistant_mode": "general_chat"}},
            },
            deps=deps,
        )
    )

    assert first["run_id"] == second["run_id"]
    stored = asyncio.run(get_orchestration_state_store().load_run(run_id=second["run_id"]))
    assert stored is not None
    assert stored.idempotency_key == "same-key"


def test_execute_orchestration_persists_document_refs_not_inline_documents():
    deps = _build_minimal_deps()

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что в документе?",
                "thread_id": "thread-doc-refs",
                "runtime_mode": "chat_only",
                "history": [],
                "ui_state": {
                    "documents_by_id": {
                        "doc-1": {
                            "document_id": "doc-1",
                            "display_name": "contract.pdf",
                            "version": 1,
                            "path": "/tmp/contract.pdf",
                            "text": "Штраф 10 процентов",
                            "uploaded_at": 1.0,
                            "source_message_id": None,
                        }
                    },
                    "active_doc_ids": ["doc-1"],
                    "control_plane_state": {"assistant_mode": "specific_tasks"},
                },
                "session_docs": {
                    "contract.pdf": {
                        "document_id": "doc-1",
                        "display_name": "contract.pdf",
                        "path": "/tmp/contract.pdf",
                        "text": "Штраф 10 процентов",
                        "version": 1,
                    }
                },
                "active_doc_ids": ["doc-1"],
            },
            deps=deps,
        )
    )

    stored = asyncio.run(get_orchestration_state_store().load_run(run_id=response["run_id"]))

    assert stored is not None
    assert "documents_by_id" not in (stored.resume_state_blob or {})
    assert "session_docs" not in (stored.resume_state_blob or {})
    assert stored.resume_state_blob["document_refs"] == [
        {
            "document_id": "doc-1",
            "display_name": "contract.pdf",
            "version": 1,
            "path": "/tmp/contract.pdf",
            "uploaded_at": 1.0,
            "source_message_id": None,
            "source_origin": None,
            "collection_id": None,
        }
    ]


def test_execute_orchestration_routes_kb_doc_question_through_unified_backend_core(monkeypatch):
    deps = _build_minimal_deps()
    deps.has_retrieval_adapter = lambda: True
    deps.get_retrieval_embed_fn = lambda: object()
    deps.get_knowledge_base_store = lambda: object()
    deps.infer_assistant_text = AsyncMock(return_value="Ответ по базе [1]")
    deps.citations_are_valid = lambda answer_text, source_count: True
    deps.extract_citation_ids = lambda answer_text: [1]
    deps.has_sufficient_evidence = lambda **kwargs: True
    deps.compute_confidence_v1 = lambda sources, cited_ids, answer_mode, **kwargs: (0.9, "high")
    deps.render_doc_question_markdown = lambda payload: payload["answer_text"]

    monkeypatch.setattr(
        "orchestrator.execution_runtime.retrieve_merged_chunks",
        lambda **kwargs: {
            "chunks": [
                {
                    "chunk_id": "kb-doc:0",
                    "document_id": "kb-doc",
                    "display_name": "kb_policy.txt",
                    "collection_id": "legal",
                    "source_origin": "knowledge_base",
                    "text": "Штраф за просрочку составляет 3 процента.",
                    "metadata_json": {"start_char": 0, "end_char": 42},
                    "raw_score": 0.91,
                    "normalized_score": 0.88,
                }
            ],
            "source_scope_summary": "knowledge_base",
        },
    )

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Какой штраф за просрочку?",
                "assistant_mode": "rag_qa",
                "runtime_mode": "specialized_tasks",
                "rag_scope": "knowledge_base_rag",
                "knowledge_collection_id": "legal",
                "history": [],
            },
            deps=deps,
        )
    )

    assert response["route"] == "document_question"
    assert response["executor"] == "document_question"
    assert response["assistant_message"] == "Ответ по базе [1]"
    assert response["sources"][0]["source_origin"] == "knowledge_base"
    assert response["sources"][0]["collection_id"] == "legal"
