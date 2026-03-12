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


def _build_minimal_deps() -> ExecutionDependencies:
    return ExecutionDependencies(
        infer_assistant_text=AsyncMock(return_value="Привет!"),
        build_prompt=lambda query, history, system_msg="": f"{system_msg}\n{query}",
        get_profile_system_prompt=lambda: "Ты ассистент.",
        has_retrieval_adapter=lambda: False,
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
        compute_confidence_v1=lambda sources, cited_ids, answer_mode: (0.4, "medium"),
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
    deps.compute_confidence_v1 = lambda sources, cited_ids, answer_mode: (0.8, "high")
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
