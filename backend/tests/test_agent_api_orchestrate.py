import os
import sys
import asyncio

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.agent_api import (
    OrchestrationRequest,
    _build_api_execution_dependencies,
    execute_orchestration_api,
    get_tool_job_result_route,
    get_tool_job_status_route,
    orchestrate,
)
from orchestrator.knowledge_base_ingestion import ingest_text_source_sync
from orchestrator.knowledge_base_store import get_knowledge_base_store


def _strip_timing_footer(text: str) -> str:
    return str(text).split("\n\n---\nTiming / Quality", 1)[0]


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


@pytest.mark.asyncio
async def test_orchestrate_prefers_requested_tool_over_classifier_route():
    request = OrchestrationRequest(
        message="Что написано про штраф?",
        requested_tool="compare_documents_fast",
        routing_mode="explicit",
        file_count=2,
        has_session_docs=True,
        session_docs={
            "old.pdf": {"text": "v1"},
            "new.pdf": {"text": "v2"},
        },
        active_doc_ids=["old", "new"],
        classifier_result={
            "intent": "document_question",
            "confidence": 0.98,
            "margin": 0.8,
            "needs_rag": True,
        },
    )

    response = await orchestrate(request)

    assert response["route"] == "compare_documents"
    assert response["reason"] == "forced_route"
    assert response["requested_tool"] == "compare_documents_fast"
    assert response["routing_mode"] == "explicit"


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
    assert response["effective_settings"]["resolved_retrieval_embedder_model_id"] == "qwen3-embedding-0.6b"
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
async def test_execute_orchestration_api_maps_requested_tool_to_legacy_forced_route(monkeypatch):
    captured = {}

    async def fake_execute_orchestration(payload, deps=None):
        captured["payload"] = payload
        return {
            "route": payload.get("forced_route"),
            "assistant_message": "ok",
        }

    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute_orchestration)

    request = OrchestrationRequest(
        message="Что написано про штраф?",
        requested_tool="ask_document",
        routing_mode="explicit",
        file_count=1,
        has_session_docs=True,
        session_docs={
            "contract.pdf": {"text": "Штраф 10 процентов"},
        },
        active_doc_ids=["contract.pdf"],
    )

    response = await execute_orchestration_api(request)

    assert response["route"] == "document_question"
    assert captured["payload"]["requested_tool"] == "ask_document"
    assert captured["payload"]["routing_mode"] == "explicit"
    assert captured["payload"]["forced_route"] == "document_question"
    assert captured["payload"]["runtime_mode"] == "specialized_tasks"
    assert captured["payload"]["rag_scope"] == "session_rag"


@pytest.mark.asyncio
async def test_execute_orchestration_api_returns_accepted_job_for_deep_tool(monkeypatch):
    async def fake_execute_orchestration(payload, deps=None):
        await asyncio.sleep(0)
        return {
            "route": payload.get("forced_route"),
            "assistant_message": "deep completed",
            "run_id": "run-deep-1",
            "state_ref": "run:run-deep-1",
        }

    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute_orchestration)

    request = OrchestrationRequest(
        message="Сделай глубокий анализ документа",
        requested_tool="analyze_document_deep",
        routing_mode="explicit",
        file_count=1,
        has_session_docs=True,
        session_docs={
            "contract.pdf": {"text": "Штраф 10 процентов"},
        },
        active_doc_ids=["contract.pdf"],
    )

    response = await execute_orchestration_api(request)

    assert response["status"] == "accepted"
    assert response["tool_name"] == "analyze_document_deep"
    assert response["job_id"]
    assert response["status_url"].endswith(response["job_id"])
    assert response["execution_metadata"]["execution_mode"] == "async"


@pytest.mark.asyncio
async def test_tool_job_polling_returns_completed_result(monkeypatch):
    async def fake_execute_orchestration(payload, deps=None):
        await asyncio.sleep(0)
        return {
            "route": payload.get("forced_route"),
            "assistant_message": "deep completed",
            "run_id": "run-deep-2",
            "state_ref": "run:run-deep-2",
        }

    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute_orchestration)

    request = OrchestrationRequest(
        message="Сделай глубокий анализ документа",
        requested_tool="analyze_document_deep",
        routing_mode="explicit",
        file_count=1,
        has_session_docs=True,
        session_docs={
            "contract.pdf": {"text": "Штраф 10 процентов"},
        },
        active_doc_ids=["contract.pdf"],
    )

    accepted = await execute_orchestration_api(request)
    status = None
    for _ in range(5):
        await asyncio.sleep(0)
        status = await get_tool_job_status_route(accepted["job_id"])
        if status["status"] == "completed":
            break

    assert status is not None
    assert status["status"] == "completed"
    result = await get_tool_job_result_route(accepted["job_id"])

    assert status["result_ref"].endswith("/result")
    assert result["assistant_message"] == "deep completed"
    assert result["run_id"] == "run-deep-2"


def test_build_api_execution_dependencies_collects_model_execution_events():
    request = OrchestrationRequest(
        message="Привет",
        session_id="session-model-execution",
        runtime_mode="chat_only",
        history=[],
    )
    effective_settings = {
        "runtime_mode": "chat_only",
        "assistant_mode": "general_chat",
        "resolved_model_id": "qwen-14b-llm",
        "resolved_retrieval_embedder_model_id": "qwen3-embedding-0.6b",
        "resolved_intent_embedder_model_id": "qwen3-embedding-0.6b",
        "device_mode": "prefer-gpu",
    }

    deps = _build_api_execution_dependencies(request, effective_settings)
    deps.record_model_execution(
        {
            "role_key": "llm.default_chat",
            "primary_model_id": "qwen-14b-llm",
            "fallback_model_id": "qwen-14b-llm",
            "used_model_id": "qwen-14b-llm",
            "fallback_used": False,
            "attempt_count": 1,
            "status": "completed",
        }
    )

    events = deps.get_model_execution_events()
    assert len(events) == 1
    assert events[0]["used_model_id"] == "qwen-14b-llm"


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
    assert response["telemetry"]["elapsed_ms"] >= 0
    assert response["telemetry"]["quality_summary"]


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


def test_api_execution_dependencies_use_shared_doc_question_heuristic():
    request = OrchestrationRequest(message="Сравни штраф и уведомление между документами")
    effective_settings = {
        "resolved_retrieval_embedder_model_id": "labse-embedding",
        "prompt_profile": "strict-grounded-doc-qa",
    }
    deps = _build_api_execution_dependencies(request, effective_settings)
    sources = [
        {
            "source_id": 1,
            "document_id": "a.pdf",
            "display_name": "a.pdf",
            "raw_score": 0.7,
            "normalized_score": 0.95,
            "quote": "Уведомление за 10 дней",
        },
        {
            "source_id": 2,
            "document_id": "b.pdf",
            "display_name": "b.pdf",
            "raw_score": 0.68,
            "normalized_score": 0.91,
            "quote": "Штраф 10 процентов",
        },
    ]

    assert (
        deps.has_sufficient_evidence(
            sources=sources,
            cited_ids=[1],
            mode="simple",
            query=request.message,
            citations_valid=True,
        )
        is False
    )
    confidence, _ = deps.compute_confidence_v1(sources, [1, 2], "grounded_answer")
    lower_confidence, _ = deps.compute_confidence_v1(sources, [1], "grounded_answer")
    assert confidence > lower_confidence


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
    monkeypatch.setattr(
        "orchestrator.agent_api._create_failover_embed_fn",
        lambda selection, *, record_model_execution=None: _stub_embed_fn,
    )
    monkeypatch.setattr(
        "orchestrator.agent_api.ums_client.infer",
        lambda model_id, payload, device_mode="hybrid": {"content": "Штраф составляет 3 процента [1]"},
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
    assert _strip_timing_footer(response["assistant_message"]) == "Штраф составляет 3 процента [1]"
    assert response["sources"][0]["source_origin"] == "knowledge_base"
    assert response["sources"][0]["collection_id"] == "legal"


@pytest.mark.asyncio
async def test_infer_with_effective_settings_calls_ums_client(monkeypatch):
    called = {}

    def fake_infer(model_id, payload, device_mode="hybrid"):
        called["model_id"] = model_id
        called["payload"] = payload
        called["device_mode"] = device_mode
        return {"content": "test response"}

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.agent_api.ums_client.infer", fake_infer)
    monkeypatch.setattr("orchestrator.agent_api.asyncio.to_thread", fake_to_thread)

    from orchestrator.agent_api import _infer_with_effective_settings

    result = await _infer_with_effective_settings(
        {"resolved_model_id": "test-model", "generation": {"temperature": 0.5}, "device_mode": "low-vram"},
        "test prompt",
    )

    assert called["model_id"] == "test-model"
    assert called["payload"]["temperature"] == 0.5
    assert called["device_mode"] == "cpu"
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

    def fake_create_failover_embed_fn(selection, *, record_model_execution=None):
        called["model_id"] = getattr(selection, "resolved_model_id", None)
        return _stub_embed_fn

    monkeypatch.setattr("orchestrator.agent_api._create_failover_embed_fn", fake_create_failover_embed_fn)

    request = OrchestrationRequest(message="Что написано про штраф?", has_session_docs=False)
    effective = resolve_effective_settings({"model_profile": "low-vram"})

    deps = _build_api_execution_dependencies(request, effective)

    assert deps.get_retrieval_embed_fn() is _stub_embed_fn
