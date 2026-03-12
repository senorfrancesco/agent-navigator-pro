import asyncio
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.agent_api import OrchestrationRequest, execute_orchestration_api, orchestrate


def test_orchestrate_request_accepts_specialized_tasks_runtime_mode():
    request = OrchestrationRequest(
        message="Сравни эти два документа",
        runtime_mode="specialized_tasks",
        file_count=2,
        has_session_docs=True,
        session_docs={
            "old.pdf": {"text": "v1"},
            "new.pdf": {"text": "v2"},
        },
        active_doc_ids=["old", "new"],
        classifier_result={
            "intent": "compare_documents",
            "confidence": 0.9,
            "margin": 0.7,
            "needs_rag": False,
        },
    )

    response = asyncio.run(orchestrate(request))

    assert response["mode"] == "specialized_tasks"


def test_orchestration_request_rejects_unknown_runtime_mode():
    with pytest.raises(ValidationError):
        OrchestrationRequest(
            message="Привет",
            runtime_mode="broken_mode",
        )


def test_orchestrate_response_includes_effective_settings():
    request = OrchestrationRequest(
        message="Что написано в базе знаний про штрафы?",
        assistant_mode="rag_qa",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
        model_profile="analyst",
        prompt_profile="strict-grounded-doc-qa",
        generation_overrides={"temperature": 0.15, "top_p": 0.5, "max_tokens": 800},
        custom_system_prompt="Отвечай с явными ссылками на источники.",
        tool_scope="document_qa",
        file_count=0,
        has_session_docs=False,
    )

    response = asyncio.run(orchestrate(request))

    assert response["effective_settings"]["assistant_mode"] == "rag_qa"
    assert response["effective_settings"]["rag_scope"] == "knowledge_base_rag"
    assert response["effective_settings"]["knowledge_collection_id"] == "legal"
    assert response["effective_settings"]["model_profile"] == "analyst"
    assert response["effective_settings"]["prompt_profile"] == "strict-grounded-doc-qa"
    assert response["effective_settings"]["custom_system_prompt"] == "Отвечай с явными ссылками на источники."
    assert response["effective_settings"]["tool_scope"] == "document_qa"
    assert response["effective_settings"]["resolved_model_id"] == "qwen-14b-llm"
    assert response["effective_settings"]["generation"] == {
        "temperature": 0.15,
        "top_p": 0.5,
        "max_tokens": 800,
    }


def test_orchestration_request_rejects_unknown_assistant_mode():
    with pytest.raises(ValidationError):
        OrchestrationRequest(
            message="Привет",
            assistant_mode="broken_mode",
        )


def test_execute_orchestration_api_returns_execution_metadata():
    request = OrchestrationRequest(
        message="Сравни эти два документа",
        session_id="session-api",
        runtime_mode="specialized_tasks",
        file_count=2,
        has_session_docs=True,
        session_docs={
            "old.pdf": {"text": "old"},
            "new.pdf": {"text": "new"},
        },
        attachments_meta=[
            {"name": "old.pdf", "path": "/tmp/old.pdf"},
            {"name": "new.pdf", "path": "/tmp/new.pdf"},
        ],
        active_doc_ids=["old", "new"],
        classifier_result={
            "intent": "general_chat",
            "confidence": 0.44,
            "margin": 0.005,
            "needs_rag": False,
        },
    )

    response = asyncio.run(execute_orchestration_api(request))

    assert response["state_ref"] == "session:session-api"
    assert response["pending_action_id"]
    assert response["action_required"]["type"] == "choose_route"
    assert response["ui_effects"]["set_pending_action"]["type"] == "choose_route"
