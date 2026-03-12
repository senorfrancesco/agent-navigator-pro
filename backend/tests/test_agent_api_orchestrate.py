import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.agent_api import OrchestrationRequest, execute_orchestration_api, orchestrate
from orchestrator.knowledge_base_ingestion import ingest_text_source_sync
from orchestrator.knowledge_base_store import get_knowledge_base_store


def _stub_embed_fn(texts):
    import numpy as np

    vectors = []
    for text in texts:
        lowered = text.lower()
        vec = np.array(
            [
                1.0 if "штраф" in lowered or "просроч" in lowered else 0.0,
                1.0 if "уведом" in lowered or "дней" in lowered else 0.0,
                1.0 if "сервис" in lowered or "обслуж" in lowered else 0.0,
            ],
            dtype=np.float32,
        )
        if not np.any(vec):
            vec = np.ones(3, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        vectors.append(vec)
    return np.array(vectors)


@pytest.mark.asyncio
async def test_orchestrate_request_accepts_specialized_tasks_runtime_mode():
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

    response = await orchestrate(request)

    assert response["mode"] == "specialized_tasks"


def test_orchestration_request_rejects_unknown_runtime_mode():
    with pytest.raises(ValidationError):
        OrchestrationRequest(
            message="Привет",
            runtime_mode="broken_mode",
        )


@pytest.mark.asyncio
async def test_orchestrate_response_includes_effective_settings():
    request = OrchestrationRequest(
        message="Что написано в базе знаний про штрафы?",
        assistant_mode="rag_qa",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
        model_profile="legal-compare",
        prompt_profile="strict-grounded-doc-qa",
        generation_overrides={"temperature": 0.15, "top_p": 0.5, "max_tokens": 800},
        custom_system_prompt="Отвечай с явными ссылками на источники.",
        tool_scope="document_qa",
        file_count=0,
        has_session_docs=False,
    )

    response = await orchestrate(request)

    assert response["effective_settings"]["assistant_mode"] == "rag_qa"
    assert response["effective_settings"]["rag_scope"] == "knowledge_base_rag"
    assert response["effective_settings"]["knowledge_collection_id"] == "legal"
    assert response["effective_settings"]["model_profile"] == "legal-compare"
    assert response["effective_settings"]["device_mode"] == "prefer-gpu"
    assert response["effective_settings"]["context_budget_profile"] == "legal-compare"
    assert response["effective_settings"]["prompt_profile"] == "strict-grounded-doc-qa"
    assert response["effective_settings"]["custom_system_prompt"] == "Отвечай с явными ссылками на источники."
    assert response["effective_settings"]["tool_scope"] == "document_qa"
    assert response["effective_settings"]["resolved_model_id"] == "qwen-14b-llm"
    assert response["effective_settings"]["resolved_intent_embedder_model_id"] == "qwen3-embedding-0.6b"
    assert response["effective_settings"]["resolved_retrieval_embedder_model_id"] == "labse-embedding"
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


@pytest.mark.asyncio
async def test_execute_orchestration_api_returns_execution_metadata():
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

    response = await execute_orchestration_api(request)

    assert response["state_ref"].startswith("run:")
    assert response["run_id"]
    assert response["state_version"] == 2
    assert response["pending_action_id"]
    assert response["action_required"]["type"] == "choose_route"
    assert response["ui_effects"]["set_pending_action"]["type"] == "choose_route"


@pytest.mark.asyncio
async def test_execute_orchestration_api_returns_top_level_control_plane_fields():
    request = OrchestrationRequest(
        message="Что в базе знаний про штрафы?",
        assistant_mode="rag_qa",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
        session_docs={
            "session-note.txt": {"text": "штраф составляет 10 процентов"},
        },
        has_session_docs=True,
        active_doc_ids=["session-note.txt"],
        classifier_result={
            "intent": "document_question",
            "confidence": 0.95,
            "margin": 0.5,
            "needs_rag": True,
        },
    )

    response = await execute_orchestration_api(request)

    assert response["rag_scope"] == "knowledge_base_rag"
    assert response["knowledge_collection_id"] == "legal"
    assert response["source_scope_summary"] == "knowledge_base+session_overlay"


@pytest.mark.asyncio
async def test_orchestrate_uses_effective_runtime_mode_from_assistant_mode():
    request = OrchestrationRequest(
        message="Сравни эти два документа",
        assistant_mode="specific_tasks",
        file_count=2,
        has_session_docs=True,
        session_docs={
            "old.pdf": {"text": "v1"},
            "new.pdf": {"text": "v2"},
        },
        attachments_meta=[
            {"name": "old.pdf", "path": "/tmp/old.pdf"},
            {"name": "new.pdf", "path": "/tmp/new.pdf"},
        ],
        active_doc_ids=["old", "new"],
        classifier_result={
            "intent": "compare_documents",
            "confidence": 0.91,
            "margin": 0.55,
            "needs_rag": True,
        },
    )

    response = await orchestrate(request)

    assert response["mode"] == "specialized_tasks"
    assert response["route"] == "compare_documents"
    assert response["rag_scope"] == "session_rag"
    assert response["source_scope_summary"] == "session"


@pytest.mark.asyncio
async def test_execute_orchestration_returns_top_level_control_plane_fields(monkeypatch):
    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "orchestrator.agent_api.ums_client.infer",
        lambda model_id, payload: {"content": "stubbed response"},
    )
    monkeypatch.setattr("orchestrator.agent_api.asyncio.to_thread", fake_to_thread)

    request = OrchestrationRequest(
        message="Что написано в базе знаний про штрафы?",
        assistant_mode="rag_qa",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
        model_profile="legal-compare",
        file_count=0,
        has_session_docs=False,
    )

    response = await execute_orchestration_api(request)

    assert response["mode"] == "specialized_tasks"
    assert response["rag_scope"] == "knowledge_base_rag"
    assert response["knowledge_collection_id"] == "legal"
    assert response["source_scope_summary"] == "knowledge_base"
    assert response["model_profile"] == "legal-compare"


@pytest.mark.asyncio
async def test_execute_orchestration_api_doc_question_reports_missing_rag_adapter_honestly():
    request = OrchestrationRequest(
        message="Что указано в документе про штраф?",
        assistant_mode="specific_tasks",
        session_docs={
            "doc.txt": {"text": "штраф 10 процентов"},
        },
        has_session_docs=True,
        active_doc_ids=["doc.txt"],
        classifier_result={
            "intent": "document_question",
            "confidence": 0.95,
            "margin": 0.5,
            "needs_rag": True,
        },
    )

    response = await execute_orchestration_api(request)

    assert response["route"] == "document_question"
    assert "retrieval adapter" in response["assistant_message"].lower()
    assert "не реализован" in response["assistant_message"].lower()
    assert response["sources"] == []


@pytest.mark.asyncio
async def test_execute_orchestration_api_doc_question_reports_retrieval_unavailable_for_api_adapter():
    request = OrchestrationRequest(
        message="Что написано в документе про штраф?",
        assistant_mode="specific_tasks",
        rag_scope="session_rag",
        file_count=1,
        has_session_docs=True,
        session_docs={
            "contract.pdf": {
                "document_id": "contract.pdf",
                "text": "Штраф составляет 10 процентов от суммы договора.",
                "path": "/tmp/contract.pdf",
            }
        },
        active_doc_ids=["contract.pdf"],
        classifier_result={
            "intent": "document_question",
            "confidence": 0.92,
            "margin": 0.51,
            "needs_rag": True,
        },
    )

    response = await execute_orchestration_api(request)

    assert response["route"] == "document_question"
    assert "retrieval" in response["assistant_message"].lower()
    assert "adapter" in response["assistant_message"].lower()


@pytest.mark.asyncio
async def test_execute_orchestration_api_reads_knowledge_base_via_unified_core(monkeypatch):
    ingest_text_source_sync(
        collection_id="legal",
        display_name="kb_policy.txt",
        text="За просрочку поставки применяется штраф 3 процента.",
        store=get_knowledge_base_store(),
        embedding_model_id="labse-embedding",
    )

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.agent_api.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("services.model_manager.ums_client.create_ums_embed_fn", lambda **kwargs: _stub_embed_fn)
    monkeypatch.setattr(
        "orchestrator.agent_api.ums_client.infer",
        lambda model_id, payload: {"content": "Штраф составляет 3 процента [1]"},
    )

    request = OrchestrationRequest(
        message="Какой штраф за просрочку поставки?",
        assistant_mode="rag_qa",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
        history=[],
    )

    response = await execute_orchestration_api(request)

    assert response["route"] == "document_question"
    assert response["assistant_message"] == "Штраф составляет 3 процента [1]"
    assert response["sources"][0]["source_origin"] == "knowledge_base"
    assert response["sources"][0]["collection_id"] == "legal"


@pytest.mark.asyncio
async def test_infer_with_effective_settings_calls_ums_client(monkeypatch):
    called = {}

    def fake_infer(model_id, payload):
        called["model_id"] = model_id
        called["payload"] = payload
        return {"content": "test response"}

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.agent_api.ums_client.infer", fake_infer)
    monkeypatch.setattr("orchestrator.agent_api.asyncio.to_thread", fake_to_thread)

    from orchestrator.agent_api import _infer_with_effective_settings

    result = await _infer_with_effective_settings(
        {"resolved_model_id": "test-model", "generation": {"temperature": 0.5}},
        "test prompt",
    )

    assert called["model_id"] == "test-model"
    assert called["payload"]["temperature"] == 0.5
    assert result == "test response"


def test_build_api_execution_dependencies_returns_session_docs_via_shared_shape():
    from orchestrator.agent_api import _build_api_execution_dependencies
    from orchestrator.ui_control_plane import resolve_effective_settings

    request = OrchestrationRequest(
        message="Сводка по документам",
        session_docs={
            "contract.pdf": {
                "document_id": "doc-1",
                "path": "/tmp/contract.pdf",
                "text": "Штраф 10 процентов",
                "report_generated": True,
            }
        },
        has_session_docs=True,
    )
    deps = _build_api_execution_dependencies(request, resolve_effective_settings({}))

    assert deps.get_all_docs() == [
        {
            "document_id": "doc-1",
            "display_name": "contract.pdf",
            "path": "/tmp/contract.pdf",
            "text": "Штраф 10 процентов",
            "report_generated": True,
            "order_index": 1,
        }
    ]


def test_build_api_execution_dependencies_uses_resolved_retrieval_embedder(monkeypatch):
    from orchestrator.agent_api import _build_api_execution_dependencies
    from orchestrator.ui_control_plane import resolve_effective_settings

    called = {}

    def fake_create_ums_embed_fn(**kwargs):
        called["model_id"] = kwargs.get("model_id")
        return _stub_embed_fn

    monkeypatch.setattr("services.model_manager.ums_client.create_ums_embed_fn", fake_create_ums_embed_fn)

    request = OrchestrationRequest(message="Что написано про штраф?", has_session_docs=False)
    effective = resolve_effective_settings({"model_profile": "low-vram"})

    deps = _build_api_execution_dependencies(request, effective)

    assert deps.get_retrieval_embed_fn() is _stub_embed_fn
    assert called["model_id"] == effective["resolved_retrieval_embedder_model_id"]
