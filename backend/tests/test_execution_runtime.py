import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.execution_runtime import ExecutionDependencies, execute_orchestration


def _build_minimal_deps() -> ExecutionDependencies:
    return ExecutionDependencies(
        infer_assistant_text=AsyncMock(return_value="Привет!"),
        build_prompt=lambda query, history, system_msg="": f"{system_msg}\n{query}",
        get_profile_system_prompt=lambda: "Ты ассистент.",
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
    assert response["state_ref"] == "session:session-1"
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
