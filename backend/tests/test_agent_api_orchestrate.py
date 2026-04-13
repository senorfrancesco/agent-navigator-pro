import os
import sys
import asyncio
import uuid
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import orchestrator.qdrant_knowledge_base_store as qdrant_store_module
from orchestrator.agent_api import (
    OrchestrationRequest,
    _build_api_execution_dependencies,
    cancel_tool_job_route,
    execute_orchestration_api,
    get_tool_job_result_route,
    get_tool_job_status_route,
    orchestrate,
)
from orchestrator.execution_runtime import execute_orchestration as execute_runtime_orchestration
from orchestrator.knowledge_base_ingestion import ingest_text_source_sync
from orchestrator.knowledge_base_store import SQLiteKnowledgeBaseStore, get_knowledge_base_store
from orchestrator.qdrant_knowledge_base_store import QdrantKnowledgeBaseStore
from orchestrator.tool_job_store import get_tool_job_store
from orchestrator.tool_execution import reconcile_incomplete_tool_jobs
from services.model_manager.model_selection import resolve_execution_plan


def _strip_timing_footer(text: str) -> str:
    return str(text).split("\n\n---\nTiming / Quality", 1)[0]


def _stub_embed_fn(texts):
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


def _build_fake_qdrant_models():
    return SimpleNamespace(
        VectorParams=_FakeVectorParams,
        Distance=SimpleNamespace(COSINE="cosine"),
        PointStruct=_FakePointStruct,
        MatchValue=_FakeMatchValue,
        FieldCondition=_FakeFieldCondition,
        Filter=_FakeFilter,
        Range=_FakeRange,
    )


def _build_qdrant_store(monkeypatch, tmp_path, *, db_name: str) -> QdrantKnowledgeBaseStore:
    monkeypatch.setattr(
        qdrant_store_module,
        "_load_qdrant_dependencies",
        lambda: (_FakeQdrantClient, _build_fake_qdrant_models()),
    )
    return QdrantKnowledgeBaseStore(
        db_url=f"sqlite:///{tmp_path}/{db_name}",
        qdrant_url="http://fake-qdrant",
        collection_name="rag_chunks_v1",
    )


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
async def test_orchestrate_skips_binding_materialization_for_text_only_session_docs():
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
    assert request.document_bindings is None


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
async def test_execute_orchestration_api_forces_explicit_tool_boundary_even_with_chat_only(monkeypatch):
    captured = {}

    async def fake_execute_orchestration(payload, deps=None):
        captured["payload"] = payload
        return {
            "route": payload.get("forced_route"),
            "assistant_message": "ok",
        }

    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute_orchestration)

    request = OrchestrationRequest(
        message="Просто поговори, но вызови быстрый анализ оборудования",
        requested_tool="analyze_equipment_fast",
        routing_mode="explicit",
        runtime_mode="chat_only",
        classifier_result={
            "intent": "general_chat",
            "confidence": 0.99,
            "margin": 0.9,
            "needs_rag": False,
        },
    )

    response = await execute_orchestration_api(request)

    assert response["requested_tool"] == "analyze_equipment_fast"
    assert captured["payload"]["forced_route"] == "equipment_analysis"
    assert captured["payload"]["runtime_mode"] == "specialized_tasks"
    assert captured["payload"]["execution_surface"] == "explicit_tool"


@pytest.mark.asyncio
async def test_execute_orchestration_chat_only_does_not_resolve_classifier(monkeypatch):
    classifier_calls = {"count": 0}

    async def fail_if_called(**kwargs):
        classifier_calls["count"] += 1
        return {
            "intent": "equipment_analysis",
            "confidence": 0.99,
            "margin": 0.9,
            "needs_rag": False,
        }

    monkeypatch.setattr(
        "orchestrator.execution_runtime._resolve_classifier_result_for_request",
        fail_if_called,
    )

    response = await execute_runtime_orchestration(
        {
            "message": "Проверь насос",
            "session_id": "session-chat-only-no-classifier",
            "runtime_mode": "chat_only",
            "history": [],
        }
    )

    assert classifier_calls["count"] == 0
    assert response["executor"] == "chat"
    assert response["reason"] == "chat_only_mode"


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
async def test_execute_orchestration_api_routes_equipment_fast_without_documents(monkeypatch):
    async def fake_infer_with_effective_settings(
        effective_settings,
        prompt,
        *,
        enforced_overrides=None,
        device_mode=None,
        record_model_execution=None,
        **_,
    ):
        return "Краткий анализ оборудования без document-only fallback."

    monkeypatch.setattr("orchestrator.agent_api._infer_with_effective_settings", fake_infer_with_effective_settings)

    request = OrchestrationRequest(
        message="Проверь насос НП-100 и дай краткий вывод",
        requested_tool="analyze_equipment_fast",
        routing_mode="explicit",
        file_count=0,
        has_session_docs=False,
    )

    response = await execute_orchestration_api(request)

    assert response["route"] == "equipment_analysis"
    assert response["requested_tool"] == "analyze_equipment_fast"
    assert _strip_timing_footer(response["assistant_message"]) == "Краткий анализ оборудования без document-only fallback."
    assert "минимум 2 документа" not in response["assistant_message"]


@pytest.mark.asyncio
async def test_tool_job_polling_returns_completed_result(monkeypatch):
    monkeypatch.setenv("OPENWEBUI_EXPLICIT_TOOL_JOB_START_DELAY_S", "0")

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


@pytest.mark.asyncio
async def test_tool_job_result_returns_409_before_completion():
    job = get_tool_job_store().create_job(
        tool_name="analyze_document_deep",
        route_prefix=None,
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )

    with pytest.raises(Exception) as exc_info:
        await get_tool_job_result_route(job.job_id)

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", "") == f"job-not-ready:{job.job_id}"


@pytest.mark.asyncio
async def test_tool_job_failed_status_and_result_contract(monkeypatch):
    monkeypatch.setenv("OPENWEBUI_EXPLICIT_TOOL_JOB_START_DELAY_S", "0")

    async def fake_execute_orchestration(payload, deps=None):
        await asyncio.sleep(0)
        raise RuntimeError("deep-failure")

    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute_orchestration)

    request = OrchestrationRequest(
        message="Сделай глубокий анализ документа",
        requested_tool="analyze_document_deep",
        routing_mode="explicit",
        file_count=1,
        has_session_docs=True,
        session_docs={"contract.pdf": {"text": "Штраф 10 процентов"}},
        active_doc_ids=["contract.pdf"],
    )

    accepted = await execute_orchestration_api(request)
    status = None
    for _ in range(5):
        await asyncio.sleep(0)
        status = await get_tool_job_status_route(accepted["job_id"])
        if status["status"] == "failed":
            break

    assert status is not None
    assert status["status"] == "failed"
    assert status["error_summary"] == "deep-failure"

    with pytest.raises(Exception) as exc_info:
        await get_tool_job_result_route(accepted["job_id"])

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", "") == f"job-terminal-without-result:{accepted['job_id']}:failed"


@pytest.mark.asyncio
async def test_async_tool_job_busy_response_is_persisted_as_failed(monkeypatch):
    monkeypatch.setenv("OPENWEBUI_EXPLICIT_TOOL_JOB_START_DELAY_S", "0")

    async def fake_execute_orchestration(payload, deps=None):
        await asyncio.sleep(0)
        return {
            "assistant_message": "Модель занята предыдущим тяжёлым запросом. Дождитесь освобождения слота или остановите активный запуск.",
            "execution_metadata": {"status": "busy"},
        }

    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute_orchestration)

    request = OrchestrationRequest(
        message="Сделай глубокий анализ документа",
        requested_tool="analyze_document_deep",
        routing_mode="explicit",
        file_count=1,
        has_session_docs=True,
        session_docs={"contract.pdf": {"text": "Штраф 10 процентов"}},
        active_doc_ids=["contract.pdf"],
    )

    accepted = await execute_orchestration_api(request)
    status = None
    for _ in range(5):
        await asyncio.sleep(0)
        status = await get_tool_job_status_route(accepted["job_id"])
        if status["status"] == "failed":
            break

    assert status is not None
    assert status["status"] == "failed"
    assert status["current_stage"] == "busy"
    assert "Модель занята предыдущим тяжёлым запросом" in status["error_summary"]

    with pytest.raises(Exception) as exc_info:
        await get_tool_job_result_route(accepted["job_id"])

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", "") == f"job-terminal-without-result:{accepted['job_id']}:failed"


@pytest.mark.asyncio
async def test_cancel_tool_job_route_cancels_running_job(monkeypatch):
    async def fake_execute_orchestration(payload, deps=None):
        await asyncio.sleep(10)
        return {"assistant_message": "should-not-complete"}

    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute_orchestration)

    request = OrchestrationRequest(
        message="Сделай глубокий анализ документа",
        requested_tool="analyze_document_deep",
        routing_mode="explicit",
        file_count=1,
        has_session_docs=True,
        session_docs={"contract.pdf": {"text": "Штраф 10 процентов"}},
        active_doc_ids=["contract.pdf"],
    )

    accepted = await execute_orchestration_api(request)
    await asyncio.sleep(0)
    cancel_response = await cancel_tool_job_route(accepted["job_id"])
    assert cancel_response["status"] == "cancelling"

    final_status = None
    for _ in range(10):
        await asyncio.sleep(0)
        final_status = await get_tool_job_status_route(accepted["job_id"])
        if final_status["status"] == "cancelled":
            break

    assert final_status is not None
    assert final_status["status"] == "cancelled"
    assert final_status["error_summary"] == "cancelled-by-request"


@pytest.mark.asyncio
async def test_execute_orchestration_api_defers_explicit_async_tool_job_start(monkeypatch):
    monkeypatch.setenv("OPENWEBUI_EXPLICIT_TOOL_JOB_START_DELAY_S", "30")
    started = {"count": 0}

    async def fake_execute_orchestration(payload, deps=None):
        started["count"] += 1
        return {"assistant_message": "should-not-run-yet"}

    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute_orchestration)

    request = OrchestrationRequest(
        message="Сделай глубокий анализ документа",
        requested_tool="analyze_document_deep",
        routing_mode="explicit",
        file_count=1,
        has_session_docs=True,
        session_docs={"contract.pdf": {"text": "Штраф 10 процентов"}},
        active_doc_ids=["contract.pdf"],
    )

    accepted = await execute_orchestration_api(request)
    await asyncio.sleep(0)
    status = await get_tool_job_status_route(accepted["job_id"])

    assert status["status"] == "queued"
    assert started["count"] == 0


@pytest.mark.asyncio
async def test_cancel_tool_job_route_cancels_queued_job_before_runner_starts(monkeypatch):
    monkeypatch.setenv("OPENWEBUI_EXPLICIT_TOOL_JOB_START_DELAY_S", "30")
    started = {"count": 0}

    async def fake_execute_orchestration(payload, deps=None):
        started["count"] += 1
        await asyncio.sleep(10)
        return {"assistant_message": "should-not-complete"}

    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute_orchestration)

    request = OrchestrationRequest(
        message="Сделай глубокий анализ документа",
        requested_tool="analyze_document_deep",
        routing_mode="explicit",
        file_count=1,
        has_session_docs=True,
        session_docs={"contract.pdf": {"text": "Штраф 10 процентов"}},
        active_doc_ids=["contract.pdf"],
    )

    accepted = await execute_orchestration_api(request)
    await asyncio.sleep(0)
    cancel_response = await cancel_tool_job_route(accepted["job_id"])
    assert cancel_response["status"] == "cancelling"

    final_status = None
    for _ in range(10):
        await asyncio.sleep(0)
        final_status = await get_tool_job_status_route(accepted["job_id"])
        if final_status["status"] == "cancelled":
            break

    assert final_status is not None
    assert final_status["status"] == "cancelled"
    assert final_status["error_summary"] == "cancelled-by-request"
    assert started["count"] == 0


@pytest.mark.asyncio
async def test_cancel_tool_job_route_returns_terminal_job_state_without_conflict():
    job = get_tool_job_store().create_job(
        tool_name="analyze_document_deep",
        route_prefix=None,
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    get_tool_job_store().finish_completed(job.job_id, {"assistant_message": "done"})

    response = await cancel_tool_job_route(job.job_id)

    assert response["job_id"] == job.job_id
    assert response["status"] == "completed"


def test_reconcile_incomplete_tool_jobs_marks_orphaned_jobs_failed():
    store = get_tool_job_store()
    queued = store.create_job(
        tool_name="analyze_document_deep",
        route_prefix=None,
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    running = store.create_job(
        tool_name="analyze_equipment_deep",
        route_prefix=None,
        request_payload={"requested_tool": "analyze_equipment_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    store.mark_running(running.job_id)

    updated_count = reconcile_incomplete_tool_jobs()

    assert updated_count == 2
    assert store.get(queued.job_id).status == "failed"
    assert store.get(queued.job_id).error_summary == "interrupted:process-restart"
    assert store.get(running.job_id).status == "failed"
    assert store.get(running.job_id).current_stage == "interrupted"


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


def test_api_execution_dependencies_expose_ums_retrieval_adapter(monkeypatch):
    request = OrchestrationRequest(message="Что написано в документе про штраф?")
    effective_settings = {
        "resolved_retrieval_embedder_model_id": "qwen3-embedding-0.6b",
        "resolved_retrieval_embedder_resolution": resolve_execution_plan(
            requested_model_id="qwen3-embedding-0.6b"
        ),
        "prompt_profile": "strict-grounded-doc-qa",
    }

    monkeypatch.setattr(
        "orchestrator.agent_api.create_ums_embed_fn",
        lambda base_url=None, *, model_id=None: _stub_embed_fn,
    )

    deps = _build_api_execution_dependencies(request, effective_settings)
    embed_fn = deps.get_retrieval_embed_fn()

    assert deps.has_retrieval_adapter() is True
    assert callable(embed_fn)
    assert getattr(embed_fn(["probe"]), "shape", None) == (1, 3)


@pytest.mark.asyncio
async def test_execute_orchestration_api_doc_question_indexes_session_doc_for_api_adapter(monkeypatch, tmp_path):
    store = get_knowledge_base_store().__class__(db_url=f"sqlite:///{tmp_path}/api_session_rag.db")

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.agent_api.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr(
        "orchestrator.agent_api._create_failover_embed_fn",
        lambda selection, *, record_model_execution=None: _stub_embed_fn,
    )
    monkeypatch.setattr("orchestrator.agent_api.get_knowledge_base_store", lambda: store)
    monkeypatch.setattr(
        "orchestrator.agent_api.ums_client.infer",
        lambda model_id, payload, device_mode="hybrid": {"content": "Штраф составляет 10 процентов [1]"},
    )

    request = OrchestrationRequest(
        message="Что написано в документе про штраф?",
        thread_id="thread-42",
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
    assert "10 процентов" in response["assistant_message"].lower()
    assert len(store.list_sources_sync("session:thread-42")) == 1


@pytest.mark.asyncio
async def test_execute_orchestration_api_doc_question_indexes_session_doc_for_api_adapter_with_qdrant(
    monkeypatch, tmp_path
):
    store = _build_qdrant_store(monkeypatch, tmp_path, db_name="api_session_rag_qdrant.db")

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.agent_api.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr(
        "orchestrator.agent_api._create_failover_embed_fn",
        lambda selection, *, record_model_execution=None: _stub_embed_fn,
    )
    monkeypatch.setattr("orchestrator.agent_api.get_knowledge_base_store", lambda: store)
    monkeypatch.setattr(
        "orchestrator.agent_api.ums_client.infer",
        lambda model_id, payload, device_mode="hybrid": {"content": "Штраф составляет 10 процентов [1]"},
    )

    request = OrchestrationRequest(
        message="Что написано в документе про штраф?",
        thread_id="thread-42",
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
    assert "10 процентов" in response["assistant_message"].lower()
    assert len(store.list_sources_sync("session:thread-42")) == 1
    session_matches = store.search_chunks_sync(
        collection_id="session:thread-42",
        query_text="Какой штраф указан в договоре?",
        query_embedding=_stub_embed_fn(["Какой штраф указан в договоре?"])[0],
        top_k=3,
        filters={"source_scope": "session", "thread_id": "thread-42"},
    )
    assert session_matches


@pytest.mark.asyncio
async def test_execute_orchestration_api_reads_knowledge_base_via_unified_core(monkeypatch, tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/api_kb_sqlite.db")
    ingest_text_source_sync(
        collection_id="legal",
        display_name="kb_policy.txt",
        text="За просрочку поставки применяется штраф 3 процента.",
        store=store,
        embedding_model_id="labse-embedding",
        embed_fn=_stub_embed_fn,
    )

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.agent_api.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("orchestrator.agent_api.get_knowledge_base_store", lambda: store)
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
async def test_execute_orchestration_api_reads_knowledge_base_via_unified_core_with_qdrant(monkeypatch, tmp_path):
    store = _build_qdrant_store(monkeypatch, tmp_path, db_name="api_kb_qdrant.db")
    ingest_text_source_sync(
        collection_id="legal",
        display_name="kb_policy.txt",
        text="За просрочку поставки применяется штраф 3 процента.",
        store=store,
        embedding_model_id="labse-embedding",
        embed_fn=_stub_embed_fn,
    )

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.agent_api.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("orchestrator.agent_api.get_knowledge_base_store", lambda: store)
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
    kb_matches = store.search_chunks_sync(
        collection_id="legal",
        query_text="Какой штраф за просрочку поставки?",
        query_embedding=_stub_embed_fn(["Какой штраф за просрочку поставки?"])[0],
        top_k=3,
        filters={"source_scope": "knowledge"},
    )
    assert kb_matches


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
