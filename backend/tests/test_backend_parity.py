import asyncio
import os
import sys
from unittest.mock import patch

import httpx
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.agent_api import OrchestrationRequest, execute_orchestration_api
from orchestrator.knowledge_base_store import SQLiteKnowledgeBaseStore
from services.model_manager import unified_model_server as ums_server
from services.observability import reset_observability_metrics


class _FakeProcess:
    def __init__(self, pid=12345, returncode=None):
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode


class _FakeJSONResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response, *, headers=None):
        self._response = response
        self.headers = headers or {}
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json):
        self.calls.append({"url": url, "json": json, "headers": dict(self.headers)})
        return self._response


async def _api_request(method: str, path: str, json=None) -> httpx.Response:
    transport = httpx.ASGITransport(app=ums_server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, json=json)


def _stub_embed_fn(texts):
    vectors = []
    for text in texts:
        lowered = str(text).lower()
        vec = np.array(
            [
                1.0 if "штраф" in lowered else 0.0,
                1.0 if "договор" in lowered else 0.0,
                1.0 if "уведом" in lowered else 0.0,
            ],
            dtype=np.float32,
        )
        if not np.any(vec):
            vec = np.ones(3, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        vectors.append(vec)
    return np.array(vectors)


@pytest.fixture(autouse=True)
def _reset_ums_state(monkeypatch, tmp_path):
    reset_observability_metrics()
    original_state = {
        key: (value.copy() if isinstance(value, dict) else value)
        for key, value in ums_server.state.items()
    }
    original_locks = dict(ums_server._model_start_locks)
    for env_name in (
        "BACKEND_MODE",
        "VLLM_BASE_URL",
        "VLLM_API_KEY",
        "VLLM_MODEL_ID_QWEN_14B_LLM",
        "UMS_DYNAMIC_MODELS_REGISTRY_PATH",
    ):
        monkeypatch.delenv(env_name, raising=False)

    monkeypatch.setenv("UMS_DYNAMIC_MODELS_REGISTRY_PATH", str(tmp_path / "ums_dynamic_models.json"))
    ums_server.state["processes"].clear()
    ums_server.state["placements"].clear()
    ums_server.state["admission"].clear()
    ums_server.state["runtime_states"].clear()
    ums_server.state["active_model"] = None
    ums_server.state["dynamic_models"] = {}
    ums_server.state["concurrency_policy"] = {}
    ums_server._llm_semaphore = asyncio.Semaphore(1)
    ums_server._embed_semaphore = asyncio.Semaphore(4)
    ums_server._concurrency_controls["llm_limit"] = 1
    ums_server._concurrency_controls["embed_limit"] = 4

    yield

    reset_observability_metrics()
    ums_server.state.clear()
    ums_server.state.update(original_state)
    ums_server._model_start_locks.clear()
    ums_server._model_start_locks.update(original_locks)


@pytest.fixture(params=["llama-server", "vllm"])
def backend_mode(request, monkeypatch):
    mode = str(request.param)
    monkeypatch.setenv("BACKEND_MODE", mode)
    if mode == "vllm":
        monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.local:8000")
        monkeypatch.setenv("VLLM_MODEL_ID_QWEN_14B_LLM", "qwen-remote")
        monkeypatch.setenv("VLLM_API_KEY", "secret-token")
    return mode


def test_backend_parity_status_contract(backend_mode):
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"] = {"qwen-14b-llm": _FakeProcess(pid=111)}
    ums_server.state["placements"] = {
        "qwen-14b-llm": {"placement_mode": "remote-vllm" if backend_mode == "vllm" else "single-gpu", "gpu_indices": [0]},
    }
    ums_server._set_runtime_state(
        "qwen-14b-llm",
        ums_server._RUNTIME_STATE_AVAILABLE,
        reason="ok",
        placement=ums_server.state["placements"]["qwen-14b-llm"],
    )

    response = asyncio.run(_api_request("GET", "/status"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["backend_mode"] == backend_mode
    assert payload["active_heavy_model"] == "qwen-14b-llm"
    assert "qwen-14b-llm" in payload["running"]
    assert isinstance(payload["placements"], dict)
    assert isinstance(payload["runtime_states"], dict)
    assert isinstance(payload["concurrency_policy"], dict)
    assert isinstance(payload["prompt_cache_policy"], dict)


def test_backend_parity_models_contract(backend_mode):
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"] = {"qwen-14b-llm": _FakeProcess(pid=111)}
    ums_server.state["placements"] = {
        "qwen-14b-llm": {"placement_mode": "remote-vllm" if backend_mode == "vllm" else "single-gpu", "gpu_indices": [0]},
    }
    ums_server._set_runtime_state(
        "qwen-14b-llm",
        ums_server._RUNTIME_STATE_AVAILABLE,
        reason="ok",
        placement=ums_server.state["placements"]["qwen-14b-llm"],
    )

    with patch.object(ums_server, "_discover_available_model_ids", return_value=["labse-embedding", "qwen-14b-llm"]):
        response = asyncio.run(_api_request("GET", "/models"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_heavy_model"] == "qwen-14b-llm"
    models = {item["model_id"]: item for item in payload["models"]}
    assert set(models) == {"labse-embedding", "qwen-14b-llm"}
    for model in models.values():
        assert {"model_id", "type", "path", "resolved_path", "backend", "port", "running", "active", "placement", "admission", "runtime_state"} <= set(model)
    assert models["qwen-14b-llm"]["running"] is True
    assert models["qwen-14b-llm"]["active"] is True


@pytest.mark.asyncio
async def test_backend_parity_chat_infer_contract(backend_mode):
    fake_client = _FakeAsyncClient(_FakeJSONResponse({"id": "chatcmpl-1"}), headers={"Authorization": "Bearer secret-token"})

    with patch.object(ums_server, "_start_server", return_value="qwen-14b-llm"), patch(
        "services.model_manager.unified_model_server.httpx.AsyncClient",
        return_value=fake_client,
    ):
        response = await ums_server.infer(
            ums_server.InferRequest(
                model_id="qwen-14b-llm",
                payload={"messages": [{"role": "user", "content": "Привет"}]},
                stream=False,
            )
        )

    assert response["status"] == "success"
    assert response["model"] == "qwen-14b-llm"
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["url"].endswith("/v1/chat/completions")
    assert fake_client.calls[0]["json"]["messages"][0]["content"] == "Привет"
    if backend_mode == "vllm":
        assert fake_client.calls[0]["json"]["model"] == "qwen-remote"


@pytest.mark.asyncio
async def test_backend_parity_streaming_contract(backend_mode):
    captured = {}

    async def fake_proxy(url, payload, sem, slot_pre_acquired=False, headers=None):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers or {}
        yield 'data: {"choices":[{"delta":{"content":"Привет"},"finish_reason":null}]}\n\n'
        yield "data: [DONE]\n\n"

    with patch.object(ums_server, "_start_server", return_value="qwen-14b-llm"), patch.object(
        ums_server,
        "_proxy_sse_stream",
        fake_proxy,
    ):
        response = await _api_request(
            "POST",
            "/infer",
            json={
                "model_id": "qwen-14b-llm",
                "payload": {"messages": [{"role": "user", "content": "Привет"}]},
                "stream": True,
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]" in response.text
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["payload"]["messages"][0]["content"] == "Привет"
    if backend_mode == "vllm":
        assert captured["payload"]["model"] == "qwen-remote"


@pytest.mark.asyncio
async def test_backend_parity_doc_question_contract(backend_mode, monkeypatch, tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/parity_doc_question.db")

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
        thread_id=f"thread-{backend_mode}",
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
    assert response["rag_scope"] == "session_rag"
    assert response["source_scope_summary"] == "session"
    assert "10 процентов" in response["assistant_message"].lower()
