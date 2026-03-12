import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.orchestration_runtime import (
    decide_orchestration,
    resolve_pending_action_selection,
)


def test_social_query_with_active_docs_preserves_context():
    response = decide_orchestration(
        query="Спасибо",
        trace_id="trace123",
        runtime_mode="auto",
        file_count=0,
        has_session_docs=True,
        session_docs={"doc.pdf": {"text": "test"}},
        active_doc_ids=["doc-1"],
        classifier_result={"intent": "greeting", "confidence": 0.9, "margin": 0.8, "needs_rag": False},
    )

    assert response["route"] == "greeting"
    assert response["executor"] == "chat"
    assert response["reason"] == "social_guard"
    assert response["ui_hints"]["preserve_active_docs"] is True
    assert response["session_state_patch"]["preserve_active_mode"] is True


def test_missing_documents_returns_upload_required():
    response = decide_orchestration(
        query="Что написано в документе про штрафы?",
        trace_id="trace456",
        runtime_mode="auto",
        file_count=0,
        has_session_docs=False,
        session_docs={},
        active_doc_ids=[],
        classifier_result={"intent": "document_question", "confidence": 0.8, "margin": 0.3, "needs_rag": True},
    )

    assert response["route"] == "document_question"
    assert response["executor"] is None
    assert response["reason"] == "missing_documents"
    assert response["action_required"]["type"] == "upload_required"


def test_two_docs_low_confidence_returns_choose_route():
    response = decide_orchestration(
        query="Сравни эти два документа",
        trace_id="trace789",
        runtime_mode="auto",
        file_count=2,
        has_session_docs=True,
        session_docs={"old.pdf": {"text": "v1"}, "new.pdf": {"text": "v2"}},
        active_doc_ids=["old", "new"],
        new_files=[{"name": "old.pdf"}, {"name": "new.pdf"}],
        classifier_result={"intent": "general_chat", "confidence": 0.44, "margin": 0.005, "needs_rag": False},
    )

    assert response["route"] == "compare_documents"
    assert response["action_required"]["type"] == "choose_route"
    assert response["session_state_patch"]["pending_action"]["recommended_route"] == "compare_documents"
    assert resolve_pending_action_selection("1", response["action_required"]) == "compare_documents"


def test_chat_only_mode_forces_chat_executor():
    response = decide_orchestration(
        query="Сравни эти два документа",
        trace_id="trace000",
        runtime_mode="chat_only",
        file_count=2,
        has_session_docs=True,
        session_docs={"old.pdf": {"text": "v1"}, "new.pdf": {"text": "v2"}},
        active_doc_ids=["old", "new"],
        classifier_result={"intent": "compare_documents", "confidence": 0.91, "margin": 0.7, "needs_rag": True},
    )

    assert response["route"] == "general_chat"
    assert response["executor"] == "chat"
    assert response["reason"] == "chat_only_mode"


def test_specialized_tasks_social_query_preserves_context():
    response = decide_orchestration(
        query="Спасибо",
        trace_id="trace111",
        runtime_mode="specialized_tasks",
        file_count=0,
        has_session_docs=True,
        session_docs={"doc.pdf": {"text": "test"}},
        active_doc_ids=["doc-1"],
        classifier_result={"intent": "greeting", "confidence": 0.93, "margin": 0.88, "needs_rag": False},
    )

    assert response["route"] == "greeting"
    assert response["executor"] == "chat"
    assert response["mode"] == "specialized_tasks"
    assert response["ui_hints"]["preserve_active_docs"] is True
    assert response["session_state_patch"]["preserve_active_mode"] is True


def test_specialized_tasks_clear_compare_intent_routes_to_task_executor():
    response = decide_orchestration(
        query="Сравни эти два документа",
        trace_id="trace222",
        runtime_mode="specialized_tasks",
        file_count=2,
        has_session_docs=True,
        session_docs={"old.pdf": {"text": "v1"}, "new.pdf": {"text": "v2"}},
        active_doc_ids=["old", "new"],
        classifier_result={"intent": "compare_documents", "confidence": 0.91, "margin": 0.71, "needs_rag": False},
    )

    assert response["route"] == "compare_documents"
    assert response["executor"] == "compare_documents"
    assert response["mode"] == "specialized_tasks"


def test_specialized_tasks_missing_documents_returns_upload_required():
    response = decide_orchestration(
        query="Сравни эти два документа",
        trace_id="trace333",
        runtime_mode="specialized_tasks",
        file_count=0,
        has_session_docs=False,
        session_docs={},
        active_doc_ids=[],
        classifier_result={"intent": "compare_documents", "confidence": 0.84, "margin": 0.4, "needs_rag": True},
    )

    assert response["route"] == "compare_documents"
    assert response["executor"] is None
    assert response["mode"] == "specialized_tasks"
    assert response["action_required"]["type"] == "upload_required"


def test_specialized_tasks_ambiguous_two_doc_query_requires_choice():
    response = decide_orchestration(
        query="Нужно понять, что делать с этими документами",
        trace_id="trace444",
        runtime_mode="specialized_tasks",
        file_count=2,
        has_session_docs=True,
        session_docs={"old.pdf": {"text": "v1"}, "new.pdf": {"text": "v2"}},
        active_doc_ids=["old", "new"],
        new_files=[{"name": "old.pdf"}, {"name": "new.pdf"}],
        classifier_result={"intent": "general_chat", "confidence": 0.43, "margin": 0.01, "needs_rag": False},
    )

    assert response["mode"] == "specialized_tasks"
    assert response["action_required"]["type"] == "choose_route"
    assert response["executor"] is None


def test_specialized_tasks_non_task_query_falls_back_to_chat_reason():
    response = decide_orchestration(
        query="Объясни простыми словами, что такое облака на небе",
        trace_id="trace555",
        runtime_mode="specialized_tasks",
        file_count=0,
        has_session_docs=False,
        session_docs={},
        active_doc_ids=[],
        classifier_result={"intent": "general_chat", "confidence": 0.89, "margin": 0.62, "needs_rag": False},
    )

    assert response["route"] == "general_chat"
    assert response["executor"] == "chat"
    assert response["mode"] == "specialized_tasks"
    assert response["reason"] == "specialized_fallback_chat"


def test_two_fresh_uploads_detect_equipment_mode_without_session_docs():
    response = decide_orchestration(
        query="Проверь соответствие сметы и ТЗ",
        trace_id="trace666",
        runtime_mode="auto",
        file_count=2,
        has_session_docs=False,
        session_docs={},
        active_doc_ids=[],
        new_files=[
            {"name": "tz_requirements.pdf", "path": "/tmp/tz_requirements.pdf"},
            {"name": "offer_smeta.xlsx", "path": "/tmp/offer_smeta.xlsx"},
        ],
        classifier_result={"intent": "general_chat", "confidence": 0.41, "margin": 0.01, "needs_rag": False},
    )

    assert response["route"] == "equipment_analysis"
    assert response["reason"] == "tz_vs_smeta_ambiguous"
    assert response["action_required"]["type"] == "choose_route"


def test_invalid_forced_route_falls_back_to_normal_routing():
    response = decide_orchestration(
        query="Привет",
        trace_id="trace777",
        runtime_mode="auto",
        file_count=0,
        has_session_docs=False,
        session_docs={},
        active_doc_ids=[],
        classifier_result={"intent": "general_chat", "confidence": 0.9, "margin": 0.8, "needs_rag": False},
        forced_route="broken_route",
    )

    assert response["route"] == "general_chat"
    assert response["executor"] == "chat"
    assert response["reason"] == "semantic_router"


def test_unsure_classifier_two_docs_still_requires_choice():
    response = decide_orchestration(
        query="Нужно понять, что делать с этими двумя файлами",
        trace_id="trace888",
        runtime_mode="auto",
        file_count=2,
        has_session_docs=True,
        session_docs={"old.pdf": {"text": "v1"}, "new.pdf": {"text": "v2"}},
        active_doc_ids=["old", "new"],
        new_files=[{"name": "old.pdf"}, {"name": "new.pdf"}],
        classifier_result={
            "intent": "__unsure__",
            "predicted_intent": "compare_documents",
            "confidence": 0.41,
            "margin": 0.02,
            "needs_rag": False,
            "abstained": True,
        },
    )

    assert response["action_required"]["type"] == "choose_route"
    assert response["reason"] == "two_docs_low_confidence"


def test_unsure_classifier_without_docs_falls_back_to_safe_chat_path():
    response = decide_orchestration(
        query="Объясни простыми словами, как работает система",
        trace_id="trace889",
        runtime_mode="auto",
        file_count=0,
        has_session_docs=False,
        session_docs={},
        active_doc_ids=[],
        classifier_result={
            "intent": "__unsure__",
            "predicted_intent": "document_question",
            "confidence": 0.38,
            "margin": 0.01,
            "needs_rag": True,
            "abstained": True,
        },
    )

    assert response["route"] == "general_chat"
    assert response["executor"] == "chat"
