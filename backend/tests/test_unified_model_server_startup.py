import os
import sys
import threading
import time
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.model_manager import unified_model_server as ums_server
from services.observability import render_metrics_text, reset_observability_metrics


class _FakeProcess:
    def __init__(self, pid=12345, returncode=None):
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
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


class _FakeHTTPClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url):
        self.calls.append(url)
        if not self._responses:
            raise AssertionError(f"Unexpected GET {url}")
        return self._responses.pop(0)


class _FakePostAsyncClient:
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


class _FakeVLLMAsyncClient:
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


@pytest.fixture(autouse=True)
def _reset_ums_state(monkeypatch, tmp_path):
    reset_observability_metrics()
    original_state = {
        key: (value.copy() if isinstance(value, dict) else value)
        for key, value in ums_server.state.items()
    }
    original_locks = dict(ums_server._model_start_locks)
    for env_name in (
        "MODEL_PATH_LLM",
        "MODEL_PATH_VLM",
        "MODEL_PATH_EMBEDDING_INTENT",
        "MODEL_PATH_EMBEDDING_RETRIEVAL",
        "BACKEND_MODE",
        "UMS_LLAMA_CACHE_PROMPT",
        "UMS_LLM_GPU_INDICES",
        "UMS_LLM_MIN_FREE_VRAM_GB",
        "UMS_LLM_MIN_BALANCE_RATIO",
        "UMS_EMBEDDING_GPU_INDEX",
        "UMS_DYNAMIC_MODELS_REGISTRY_PATH",
        "UMS_LLM_MAX_CONCURRENCY",
        "UMS_EMBED_MAX_CONCURRENCY",
        "UMS_LLM_CONCURRENCY",
        "UMS_EMBED_CONCURRENCY",
        "UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S",
        "UMS_FAIL_FAST_ON_SATURATION",
        "VLLM_BASE_URL",
        "VLLM_API_KEY",
        "VLLM_MODEL_ID_QWEN_14B_LLM",
        "MODEL_PATH_QWEN14B",
        "MODEL_PATH_QWENVL",
        "MODEL_PATH_LABSE",
        "MODEL_PATH_QWEN3_EMBEDDING_06B",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("UMS_DYNAMIC_MODELS_REGISTRY_PATH", str(tmp_path / "ums_dynamic_models.json"))
    ums_server.state["processes"].clear()
    ums_server.state["placements"].clear()
    ums_server.state["active_model"] = None
    ums_server.state["tier_config"] = None
    ums_server.state["dynamic_models"] = {}
    ums_server.state["discovered_model_ports"] = {}
    ums_server.state["dynamic_ports"] = 8100
    ums_server.state["reserved_ports"] = set()
    ums_server.state["port_owners"] = {}
    ums_server.state["released_dynamic_ports"] = []
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


def test_start_server_falls_back_to_cpu_for_st_model():
    fake_process = _FakeProcess()
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append(cmd)
        if "--device" in cmd and cmd[cmd.index("--device") + 1].startswith("cuda"):
            raise RuntimeError("CUDA out of memory")
        return fake_process

    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "st", "path": "./models/st/LaBSE", "port": 8093},
    ), patch.object(
        ums_server,
        "_get_gpu_info",
        return_value=[{"index": 0, "free_gb": 1.0, "total_gb": 8.0}],
    ), patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server.state["processes"].clear()
        ums_server._start_server("labse-embedding", ums_server.DeviceMode.HYBRID)

    assert len(launch_calls) == 2
    assert launch_calls[0][launch_calls[0].index("--device") + 1] == "cuda:0"
    assert launch_calls[1][launch_calls[1].index("--device") + 1] == "cpu"
    assert ums_server.state["processes"]["labse-embedding"] is fake_process


def test_start_server_keeps_cpu_for_st_model_when_requested():
    fake_process = _FakeProcess()
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append(cmd)
        return fake_process

    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "st", "path": "./models/st/LaBSE", "port": 8093},
    ), patch.object(
        ums_server,
        "_get_gpu_info",
        return_value=[{"index": 0, "free_gb": 1.0, "total_gb": 8.0}],
    ), patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server.state["processes"].clear()
        ums_server._start_server("labse-embedding", ums_server.DeviceMode.CPU)

    assert len(launch_calls) == 1
    assert launch_calls[0][launch_calls[0].index("--device") + 1] == "cpu"


def test_start_server_records_cpu_placement_after_st_fallback():
    fake_process = _FakeProcess()
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append(cmd)
        if "--device" in cmd and cmd[cmd.index("--device") + 1] == "cuda:0":
            raise RuntimeError("CUDA out of memory")
        return fake_process

    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "st", "path": "./models/st/LaBSE", "port": 8093},
    ), patch.object(
        ums_server,
        "_get_gpu_info",
        return_value=[{"index": 0, "free_gb": 1.0, "total_gb": 8.0}],
    ), patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server._start_server("labse-embedding", ums_server.DeviceMode.HYBRID)

    assert len(launch_calls) == 2
    assert ums_server.state["placements"]["labse-embedding"]["placement_mode"] == "cpu"
    assert ums_server.state["placements"]["labse-embedding"]["gpu_indices"] == []
    assert ums_server.state["placements"]["labse-embedding"]["device_arg"] == "cpu"
    metrics = render_metrics_text()
    assert "agent_nav_fallback_events_total" in metrics
    assert 'component="ums"' in metrics
    assert 'fallback="st_start_cpu_retry"' in metrics


def test_start_server_uses_weighted_tensor_split_for_multi_gpu_gguf(monkeypatch):
    fake_process = _FakeProcess()
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append(cmd)
        return fake_process

    monkeypatch.setenv("UMS_LLM_MIN_BALANCE_RATIO", "0.1")
    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "gguf", "path": "./models/gguf/qwen.gguf", "ctx_size": 8192, "gpu_layers": -1, "port": 8091},
    ), patch.object(
        ums_server,
        "_get_gpu_info",
        return_value=[
            {"index": 0, "free_gb": 24.0, "total_gb": 24.0},
            {"index": 1, "free_gb": 12.0, "total_gb": 12.0},
        ],
    ), patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server._start_server("qwen-14b-llm", ums_server.DeviceMode.HYBRID)

    assert len(launch_calls) == 1
    cmd = launch_calls[0]
    assert "--tensor-split" in cmd
    assert cmd[cmd.index("--tensor-split") + 1] == "0.6667,0.3333"
    assert ums_server.state["placements"]["qwen-14b-llm"]["placement_mode"] == "multi-gpu"
    assert ums_server.state["placements"]["qwen-14b-llm"]["gpu_indices"] == [0, 1]


def test_start_server_keeps_cpu_path_for_gguf_when_cpu_requested():
    fake_process = _FakeProcess()
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append(cmd)
        return fake_process

    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "gguf", "path": "./models/gguf/qwen.gguf", "ctx_size": 8192, "gpu_layers": -1, "port": 8091},
    ), patch.object(
        ums_server,
        "_get_gpu_info",
        return_value=[{"index": 0, "free_gb": 24.0, "total_gb": 24.0}],
    ), patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server._start_server("qwen-14b-llm", ums_server.DeviceMode.CPU)

    assert len(launch_calls) == 1
    cmd = launch_calls[0]
    assert cmd[cmd.index("-ngl") + 1] == "0"
    assert "--tensor-split" not in cmd
    assert ums_server.state["placements"]["qwen-14b-llm"]["placement_mode"] == "cpu"


def test_start_server_honors_tier_cpu_preference_for_embeddings():
    fake_process = _FakeProcess()
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append(cmd)
        return fake_process

    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "st", "path": "./models/st/LaBSE", "port": 8093},
    ), patch.object(
        ums_server,
        "_get_gpu_info",
        return_value=[{"index": 0, "free_gb": 16.0, "total_gb": 24.0}],
    ), patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server.state["tier_config"] = SimpleNamespace(embedding_device="cpu")
        ums_server._start_server("labse-embedding", ums_server.DeviceMode.HYBRID)

    assert len(launch_calls) == 1
    assert launch_calls[0][launch_calls[0].index("--device") + 1] == "cpu"
    assert ums_server.state["placements"]["labse-embedding"]["placement_mode"] == "cpu"


def test_start_server_places_embeddings_on_non_llm_gpu_when_available():
    fake_process = _FakeProcess()
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append(cmd)
        return fake_process

    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "st", "path": "./models/st/LaBSE", "port": 8093},
    ), patch.object(
        ums_server,
        "_get_gpu_info",
        return_value=[
            {"index": 0, "free_gb": 20.0, "total_gb": 24.0},
            {"index": 1, "free_gb": 12.0, "total_gb": 24.0},
        ],
    ), patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server.state["active_model"] = "qwen-14b-llm"
        ums_server.state["placements"]["qwen-14b-llm"] = {
            "placement_mode": "single-gpu",
            "gpu_indices": [0],
        }
        ums_server._start_server("labse-embedding", ums_server.DeviceMode.HYBRID)

    assert len(launch_calls) == 1
    assert launch_calls[0][launch_calls[0].index("--device") + 1] == "cuda:1"
    assert ums_server.state["placements"]["labse-embedding"]["gpu_indices"] == [1]


def test_start_server_serializes_concurrent_model_startup():
    fake_process = _FakeProcess()
    launch_calls = []
    ready = threading.Event()

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append(cmd)
        ready.wait(timeout=1.0)
        return fake_process

    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "st", "path": "./models/st/LaBSE", "port": 8093},
    ), patch.object(
        ums_server,
        "_get_gpu_info",
        return_value=[{"index": 0, "free_gb": 1.0, "total_gb": 8.0}],
    ), patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server.state["processes"].clear()

        t1 = threading.Thread(
            target=ums_server._start_server,
            args=("labse-embedding", ums_server.DeviceMode.HYBRID),
        )
        t2 = threading.Thread(
            target=ums_server._start_server,
            args=("labse-embedding", ums_server.DeviceMode.HYBRID),
        )

        t1.start()
        time.sleep(0.05)
        t2.start()
        ready.set()
        t1.join(timeout=1.0)
        t2.join(timeout=1.0)

    assert not t1.is_alive()
    assert not t2.is_alive()
    assert len(launch_calls) == 1
    assert ums_server.state["processes"]["labse-embedding"] is fake_process


def test_start_server_reaps_stale_listener_before_launch():
    fake_process = _FakeProcess(pid=22222)
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append((cmd, port))
        return fake_process

    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "st", "path": "./models/st/LaBSE", "port": 8093},
    ), patch.object(
        ums_server,
        "_get_gpu_info",
        return_value=[{"index": 0, "free_gb": 1.0, "total_gb": 8.0}],
    ), patch.object(
        ums_server,
        "_find_listener_pids",
        side_effect=lambda port: [11111] if int(port) == 8093 else [],
    ), patch.object(
        ums_server,
        "_kill_process_tree",
    ) as mock_kill, patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server.state["processes"].clear()
        ums_server._start_server("labse-embedding", ums_server.DeviceMode.HYBRID)

    mock_kill.assert_not_called()
    assert len(launch_calls) == 1
    assert launch_calls[0][1] != 8093
    assert ums_server.state["processes"]["labse-embedding"] is fake_process


def test_status_exposes_adaptive_runtime_budget(monkeypatch):
    monkeypatch.setenv("UMS_RUNTIME_PROFILE", "adaptive")
    monkeypatch.setenv("UMS_RETRIEVED_CONTEXT_RATIO", "0.6")
    monkeypatch.setenv("UMS_GENERATION_TOKENS_RESERVE", "1024")
    monkeypatch.setenv("UMS_ADAPTIVE_CONTEXT_UTILIZATION", "0.75")

    previous_active = ums_server.state.get("active_model")
    previous_tier = ums_server.state.get("tier_config")
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["tier_config"] = SimpleNamespace(tier=2, rag_mode="corrective", embedding_backend="labse")
    try:
        payload = asyncio.run(ums_server.get_status())
    finally:
        ums_server.state["active_model"] = previous_active
        ums_server.state["tier_config"] = previous_tier

    assert payload["runtime_profile"] == "adaptive"
    assert payload["effective_context_tokens"] == 12288
    assert payload["retrieved_context_tokens_budget"] == 7372
    assert payload["generation_tokens_reserve"] == 1024
    assert payload["context_budget_ratio"] == 0.6
    assert payload["tier"]["rag_mode_label"] == "corrective retrieval"


def test_status_respects_manual_runtime_budget(monkeypatch):
    monkeypatch.setenv("UMS_RUNTIME_PROFILE", "manual")
    monkeypatch.setenv("UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS", "6144")
    monkeypatch.setenv("UMS_RETRIEVED_CONTEXT_RATIO", "0.9")
    monkeypatch.setenv("UMS_GENERATION_TOKENS_RESERVE", "4096")

    previous_active = ums_server.state.get("active_model")
    ums_server.state["active_model"] = "qwen-14b-llm"
    try:
        payload = asyncio.run(ums_server.get_status())
    finally:
        ums_server.state["active_model"] = previous_active

    assert payload["runtime_profile"] == "manual"
    assert payload["effective_context_tokens"] == 6144
    assert payload["context_budget_ratio"] == 0.65
    assert payload["generation_tokens_reserve"] == 3072
    assert payload["retrieved_context_tokens_budget"] == 3072


def test_health_returns_trace_header():
    response = asyncio.run(_api_request("GET", "/health"))

    assert response.status_code == 200
    assert response.headers.get("X-Trace-Id")


def test_metrics_endpoint_exposes_http_and_runtime_metrics():
    asyncio.run(_api_request("GET", "/health"))
    response = asyncio.run(_api_request("GET", "/metrics"))

    assert response.status_code == 200
    payload = response.text
    assert "agent_nav_http_requests_total" in payload
    assert 'service="ums"' in payload
    assert "agent_nav_ums_running_models" in payload
    assert 'path="/metrics"' not in payload


def test_status_exposes_current_placements():
    previous_active = ums_server.state.get("active_model")
    previous_processes = dict(ums_server.state.get("processes") or {})
    previous_placements = dict(ums_server.state.get("placements") or {})
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"] = {
        "qwen-14b-llm": _FakeProcess(pid=111),
        "labse-embedding": _FakeProcess(pid=222),
    }
    ums_server.state["placements"] = {
        "qwen-14b-llm": {
            "placement_mode": "multi-gpu",
            "gpu_indices": [0, 1],
            "tensor_split": [0.6667, 0.3333],
        },
        "labse-embedding": {
            "placement_mode": "single-gpu",
            "gpu_indices": [2],
            "device_arg": "cuda:2",
        },
    }
    try:
        payload = asyncio.run(ums_server.get_status())
    finally:
        ums_server.state["active_model"] = previous_active
        ums_server.state["processes"] = previous_processes
        ums_server.state["placements"] = previous_placements

    assert payload["placements"]["qwen-14b-llm"]["tensor_split"] == [0.6667, 0.3333]
    assert payload["placements"]["labse-embedding"]["device_arg"] == "cuda:2"


def test_status_exposes_admission_contract_for_llm_and_embeddings(monkeypatch):
    monkeypatch.setenv("LLM_DEVICE_MODE", "gpu")
    monkeypatch.setenv("INTENT_EMBEDDER_DEVICE_MODE", "gpu")
    monkeypatch.setenv("RETRIEVAL_EMBEDDER_DEVICE_MODE", "cpu")

    previous_active = ums_server.state.get("active_model")
    previous_processes = dict(ums_server.state.get("processes") or {})
    previous_placements = dict(ums_server.state.get("placements") or {})
    previous_tier = ums_server.state.get("tier_config")
    previous_admission = dict(ums_server.state.get("admission") or {})
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"] = {
        "qwen-14b-llm": _FakeProcess(pid=111),
        "labse-embedding": _FakeProcess(pid=222),
        "qwen3-embedding-0.6b": _FakeProcess(pid=333),
    }
    ums_server.state["placements"] = {
        "qwen-14b-llm": {
            "placement_mode": "single-gpu",
            "gpu_indices": [0],
            "requested_device": "gpu",
            "resolved_device": "hybrid",
            "admission": "requires_degraded",
        },
        "labse-embedding": {
            "placement_mode": "cpu",
            "gpu_indices": [],
            "requested_device": "cpu",
            "resolved_device": "cpu",
            "admission": "ok",
        },
        "qwen3-embedding-0.6b": {
            "placement_mode": "single-gpu",
            "gpu_indices": [0],
            "device_arg": "cuda:0",
            "requested_device": "gpu",
            "resolved_device": "gpu",
            "admission": "ok",
        },
    }
    ums_server.state["tier_config"] = SimpleNamespace(tier=3, rag_mode="agentic", embedding_backend="pytorch")
    ums_server.state["admission"] = {
        "qwen-14b-llm": {
            "component": "llm",
            "requested_device": "gpu",
            "resolved_device": "hybrid",
            "admission": "requires_degraded",
            "estimated_vram_gb": 14.5,
            "available_vram_gb": 10.2,
            "effective_token_budget": 8192,
            "warnings": ["manual gpu request downgraded to hybrid"],
        },
        "labse-embedding": {
            "component": "retrieval_embedder",
            "requested_device": "cpu",
            "resolved_device": "cpu",
            "admission": "ok",
            "fallback_applied": False,
            "warnings": [],
        },
        "qwen3-embedding-0.6b": {
            "component": "intent_embedder",
            "requested_device": "gpu",
            "resolved_device": "gpu",
            "admission": "ok",
            "fallback_applied": False,
            "warnings": [],
        },
    }
    try:
        payload = asyncio.run(ums_server.get_status())
    finally:
        ums_server.state["active_model"] = previous_active
        ums_server.state["processes"] = previous_processes
        ums_server.state["placements"] = previous_placements
        ums_server.state["tier_config"] = previous_tier
        ums_server.state["admission"] = previous_admission

    assert payload["admission"]["qwen-14b-llm"]["requested_device"] == "gpu"
    assert payload["admission"]["qwen-14b-llm"]["resolved_device"] == "hybrid"
    assert payload["admission"]["qwen-14b-llm"]["admission"] == "requires_degraded"
    assert payload["admission"]["qwen-14b-llm"]["effective_token_budget"] == 8192
    assert payload["admission"]["labse-embedding"]["component"] == "retrieval_embedder"
    assert payload["admission"]["qwen3-embedding-0.6b"]["component"] == "intent_embedder"


def test_status_prunes_dead_local_process_and_clears_active_model():
    previous_active = ums_server.state.get("active_model")
    previous_processes = dict(ums_server.state.get("processes") or {})
    previous_placements = dict(ums_server.state.get("placements") or {})
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"] = {"qwen-14b-llm": _FakeProcess(pid=111, returncode=1)}
    ums_server.state["placements"] = {
        "qwen-14b-llm": {"placement_mode": "single-gpu", "gpu_indices": [0]},
    }
    try:
        payload = asyncio.run(ums_server.get_status())
    finally:
        ums_server.state["active_model"] = previous_active
        ums_server.state["processes"] = previous_processes
        ums_server.state["placements"] = previous_placements

    assert payload["active_heavy_model"] is None
    assert payload["running"] == []
    assert payload["placements"] == {}


def test_status_keeps_remote_process_registered():
    previous_active = ums_server.state.get("active_model")
    previous_processes = dict(ums_server.state.get("processes") or {})
    previous_placements = dict(ums_server.state.get("placements") or {})
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"] = {"qwen-14b-llm": ums_server._RemoteProcess()}
    ums_server.state["placements"] = {
        "qwen-14b-llm": {"placement_mode": "remote-vllm", "backend": "vllm"},
    }
    try:
        payload = asyncio.run(ums_server.get_status())
    finally:
        ums_server.state["active_model"] = previous_active
        ums_server.state["processes"] = previous_processes
        ums_server.state["placements"] = previous_placements

    assert payload["active_heavy_model"] == "qwen-14b-llm"
    assert payload["running"] == ["qwen-14b-llm"]
    assert payload["placements"]["qwen-14b-llm"]["placement_mode"] == "remote-vllm"


def test_status_exposes_concurrency_policy(monkeypatch):
    monkeypatch.setenv("UMS_LLM_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("UMS_EMBED_MAX_CONCURRENCY", "6")
    monkeypatch.setenv("UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S", "1.5")
    ums_server._refresh_concurrency_controls()

    payload = asyncio.run(ums_server.get_status())

    assert payload["concurrency_policy"]["llm_max_concurrency"] == 2
    assert payload["concurrency_policy"]["embed_max_concurrency"] == 6
    assert payload["concurrency_policy"]["acquire_timeout_s"] == 1.5
    assert payload["concurrency_policy"]["fail_fast_on_saturation"] is False
    assert payload["concurrency_policy"]["llm_inflight"] == 0
    assert payload["concurrency_policy"]["embedding_inflight"] == 0


def test_lifespan_preloads_llm_then_retrieval_then_intent(monkeypatch):
    previous_device_mode = ums_server.state.get("device_mode")
    call_order = []

    with patch("services.hardware.HardwareProfiler.detect", return_value=SimpleNamespace(has_gpu=True, gpu_count=1)), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=3,
            rag_mode="agentic",
            embedding_backend="pytorch",
            embedding_device="cuda",
            llm_ctx_size=16384,
            llm_gpu_layers=-1,
        ),
    ), patch.object(
        ums_server,
        "_start_server",
        side_effect=lambda model_id, device_mode: call_order.append(model_id),
    ), patch.object(
        ums_server,
        "_stop_all_servers",
    ):
        asyncio.run(ums_server.lifespan(ums_server.app).__aenter__())

    try:
        assert call_order[:3] == ["qwen-14b-llm", "labse-embedding", "qwen3-embedding-0.6b"]
    finally:
        ums_server.state["device_mode"] = previous_device_mode


def test_lifespan_honors_device_mode_override_for_cpu_tier(monkeypatch):
    monkeypatch.setenv("DEVICE_MODE", "gpu")
    previous_device_mode = ums_server.state.get("device_mode")
    previous_ctx = ums_server.STATIC_MODELS_CONFIG["qwen-14b-llm"]["ctx_size"]
    previous_gpu_layers = ums_server.STATIC_MODELS_CONFIG["qwen-14b-llm"]["gpu_layers"]

    class _DummyContext:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("services.hardware.HardwareProfiler.detect", return_value=SimpleNamespace(has_gpu=False, gpu_count=0)), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(tier=1, rag_mode="simple", embedding_backend="onnx", llm_ctx_size=4096, llm_gpu_layers=0),
    ), patch.object(
        ums_server,
        "_start_server",
    ), patch.object(
        ums_server,
        "_stop_all_servers",
    ):
        asyncio.run(ums_server.lifespan(ums_server.app).__aenter__())

    try:
        assert ums_server.state["device_mode"] == ums_server.DeviceMode.GPU
        assert ums_server.STATIC_MODELS_CONFIG["qwen-14b-llm"]["gpu_layers"] == -1
    finally:
        ums_server.state["device_mode"] = previous_device_mode
        ums_server.STATIC_MODELS_CONFIG["qwen-14b-llm"]["ctx_size"] = previous_ctx
        ums_server.STATIC_MODELS_CONFIG["qwen-14b-llm"]["gpu_layers"] = previous_gpu_layers


def test_component_device_override_resolves_for_heavy_and_embedding_models(monkeypatch):
    monkeypatch.setenv("LLM_DEVICE_MODE", "gpu")
    monkeypatch.setenv("VLM_DEVICE_MODE", "cpu")
    monkeypatch.setenv("INTENT_EMBEDDER_DEVICE_MODE", "gpu")
    monkeypatch.setenv("RETRIEVAL_EMBEDDER_DEVICE_MODE", "cpu")

    assert ums_server._resolve_component_device_mode("qwen-14b-llm", ums_server.DeviceMode.HYBRID) == ums_server.DeviceMode.GPU
    assert ums_server._resolve_component_device_mode("qwen-vl-8b", ums_server.DeviceMode.HYBRID) == ums_server.DeviceMode.CPU
    assert ums_server._resolve_component_device_mode("qwen3-embedding-0.6b", ums_server.DeviceMode.HYBRID) == ums_server.DeviceMode.GPU
    assert ums_server._resolve_component_device_mode("labse-embedding", ums_server.DeviceMode.HYBRID) == ums_server.DeviceMode.CPU


def test_explicit_embedding_component_override_beats_cpu_tier_preference(monkeypatch):
    monkeypatch.setenv("INTENT_EMBEDDER_DEVICE_MODE", "gpu")
    ums_server.state["tier_config"] = SimpleNamespace(embedding_device="cpu")

    placement = ums_server._build_model_placement_plan(
        model_id="qwen3-embedding-0.6b",
        config={"type": "st"},
        device_mode=ums_server.DeviceMode.HYBRID,
        available_gpus=[{"index": 0, "free_gb": 8.0}],
    )

    assert placement["placement_mode"] == "single-gpu"
    assert placement["device_arg"] == "cuda:0"


def test_explicit_intent_embedder_gpu_override_allows_single_gpu_colocation(monkeypatch):
    monkeypatch.setenv("INTENT_EMBEDDER_DEVICE_MODE", "gpu")
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["placements"]["qwen-14b-llm"] = {
        "placement_mode": "single-gpu",
        "gpu_indices": [0],
    }

    placement = ums_server._build_model_placement_plan(
        model_id="qwen3-embedding-0.6b",
        config={"type": "st"},
        device_mode=ums_server.DeviceMode.HYBRID,
        available_gpus=[{"index": 0, "free_gb": 8.0}],
    )

    assert placement["placement_mode"] == "single-gpu"
    assert placement["device_arg"] == "cuda:0"
    assert placement["gpu_indices"] == [0]


def test_status_exposes_backend_mode_for_vllm(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "vllm")
    ums_server.state["processes"]["qwen-14b-llm"] = ums_server._RemoteProcess()
    ums_server.state["placements"]["qwen-14b-llm"] = {
        "placement_mode": "remote-vllm",
        "backend": "vllm",
        "upstream_url": "http://vllm.local:8000",
        "served_model_id": "qwen-remote",
        "gpu_indices": [],
        "device_arg": "remote",
    }
    ums_server.state["active_model"] = "qwen-14b-llm"

    payload = asyncio.run(ums_server.get_status())

    assert payload["backend_mode"] == "vllm"
    assert payload["running"] == ["qwen-14b-llm"]
    assert payload["placements"]["qwen-14b-llm"]["placement_mode"] == "remote-vllm"
    assert payload["prompt_cache_policy"] == {"enabled": False, "backend_mode": "vllm"}


def test_status_exposes_prompt_cache_policy_for_local_llama():
    payload = asyncio.run(ums_server.get_status())

    assert payload["backend_mode"] == "llama-cpp-python"
    assert payload["prompt_cache_policy"] == {"enabled": True, "backend_mode": "llama-cpp-python"}


def test_status_reports_inflight_concurrency(monkeypatch):
    monkeypatch.setenv("UMS_LLM_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("UMS_EMBED_MAX_CONCURRENCY", "6")
    ums_server._concurrency_controls["llm_limit"] = 2
    ums_server._concurrency_controls["embed_limit"] = 6
    ums_server._llm_semaphore = asyncio.Semaphore(1)
    ums_server._embed_semaphore = asyncio.Semaphore(4)

    payload = asyncio.run(ums_server.get_status())

    assert payload["concurrency_policy"]["llm_inflight"] == 1
    assert payload["concurrency_policy"]["embedding_inflight"] == 2


def test_status_does_not_recreate_live_semaphores(monkeypatch):
    original_llm_sem = asyncio.Semaphore(1)
    original_embed_sem = asyncio.Semaphore(3)
    ums_server._llm_semaphore = original_llm_sem
    ums_server._embed_semaphore = original_embed_sem
    ums_server._concurrency_controls["llm_limit"] = 2
    ums_server._concurrency_controls["embed_limit"] = 5
    monkeypatch.setenv("UMS_LLM_MAX_CONCURRENCY", "7")
    monkeypatch.setenv("UMS_EMBED_MAX_CONCURRENCY", "9")

    payload = asyncio.run(ums_server.get_status())

    assert ums_server._llm_semaphore is original_llm_sem
    assert ums_server._embed_semaphore is original_embed_sem
    assert payload["concurrency_policy"]["llm_max_concurrency"] == 2
    assert payload["concurrency_policy"]["embed_max_concurrency"] == 5


def test_models_api_lists_available_models_and_active_state():
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"] = {"qwen-14b-llm": _FakeProcess(pid=111)}
    ums_server.state["placements"] = {
        "qwen-14b-llm": {"placement_mode": "single-gpu", "gpu_indices": [0]},
    }

    with patch.object(
        ums_server,
        "_discover_available_model_ids",
        return_value=["labse-embedding", "qwen-14b-llm"],
    ):
        response = asyncio.run(_api_request("GET", "/models"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_heavy_model"] == "qwen-14b-llm"
    models = {item["model_id"]: item for item in payload["models"]}
    assert set(models) == {"labse-embedding", "qwen-14b-llm"}
    assert models["qwen-14b-llm"]["running"] is True
    assert models["qwen-14b-llm"]["active"] is True
    assert models["qwen-14b-llm"]["resolved_path"] == models["qwen-14b-llm"]["path"]
    assert models["labse-embedding"]["running"] is False
    assert models["labse-embedding"]["active"] is False


def test_get_model_config_prefers_canonical_runtime_path_env(monkeypatch):
    monkeypatch.setenv("MODEL_PATH_LLM", "/models/runtime/llm.gguf")
    monkeypatch.setenv("MODEL_PATH_VLM", "/models/runtime/vlm.gguf")
    monkeypatch.setenv("MODEL_PATH_EMBEDDING_INTENT", "/models/runtime/intent")
    monkeypatch.setenv("MODEL_PATH_EMBEDDING_RETRIEVAL", "/models/runtime/retrieval")
    monkeypatch.setenv("MODEL_PATH_QWEN14B", "/legacy/llm.gguf")
    monkeypatch.setenv("MODEL_PATH_QWENVL", "/legacy/vlm.gguf")
    monkeypatch.setenv("MODEL_PATH_QWEN3_EMBEDDING_06B", "/legacy/intent")
    monkeypatch.setenv("MODEL_PATH_LABSE", "/legacy/retrieval")

    assert ums_server.get_model_config("qwen-14b-llm")["path"] == "/models/runtime/llm.gguf"
    assert ums_server.get_model_config("qwen-vl-8b")["path"] == "/models/runtime/vlm.gguf"
    assert ums_server.get_model_config("qwen3-embedding-0.6b")["path"] == "/models/runtime/intent"
    assert ums_server.get_model_config("labse-embedding")["path"] == "/models/runtime/retrieval"


def test_get_model_config_falls_back_to_legacy_runtime_path_aliases(monkeypatch):
    monkeypatch.setenv("MODEL_PATH_QWEN14B", "/legacy/llm.gguf")
    monkeypatch.setenv("MODEL_PATH_QWENVL", "/legacy/vlm.gguf")
    monkeypatch.setenv("MODEL_PATH_QWEN3_EMBEDDING_06B", "/legacy/intent")
    monkeypatch.setenv("MODEL_PATH_LABSE", "/legacy/retrieval")

    assert ums_server.get_model_config("qwen-14b-llm")["path"] == "/legacy/llm.gguf"
    assert ums_server.get_model_config("qwen-vl-8b")["path"] == "/legacy/vlm.gguf"
    assert ums_server.get_model_config("qwen3-embedding-0.6b")["path"] == "/legacy/intent"
    assert ums_server.get_model_config("labse-embedding")["path"] == "/legacy/retrieval"


def test_models_running_api_lists_running_models_and_placements():
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"] = {
        "qwen-14b-llm": _FakeProcess(pid=111),
        "labse-embedding": _FakeProcess(pid=222),
    }
    ums_server.state["placements"] = {
        "qwen-14b-llm": {
            "placement_mode": "multi-gpu",
            "gpu_indices": [0, 1],
            "tensor_split": [0.6667, 0.3333],
        },
        "labse-embedding": {
            "placement_mode": "single-gpu",
            "gpu_indices": [2],
            "device_arg": "cuda:2",
        },
    }

    response = asyncio.run(_api_request("GET", "/models/running"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_heavy_model"] == "qwen-14b-llm"
    assert payload["running_model_ids"] == ["qwen-14b-llm", "labse-embedding"]
    running_models = {item["model_id"]: item for item in payload["running_models"]}
    assert set(running_models) == {"qwen-14b-llm", "labse-embedding"}
    assert payload["placements"]["qwen-14b-llm"]["tensor_split"] == [0.6667, 0.3333]
    assert payload["placements"]["labse-embedding"]["device_arg"] == "cuda:2"


def test_models_running_omits_dead_local_processes():
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"] = {
        "qwen-14b-llm": _FakeProcess(pid=111, returncode=1),
        "labse-embedding": _FakeProcess(pid=222),
    }
    ums_server.state["placements"] = {
        "qwen-14b-llm": {"placement_mode": "single-gpu", "gpu_indices": [0]},
        "labse-embedding": {"placement_mode": "single-gpu", "gpu_indices": [2]},
    }

    response = asyncio.run(_api_request("GET", "/models/running"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_heavy_model"] is None
    assert payload["running_model_ids"] == ["labse-embedding"]
    assert set(payload["placements"]) == {"labse-embedding"}


def test_preload_endpoint_starts_model_with_requested_device_mode():
    async def _run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch.object(ums_server.asyncio, "to_thread", side_effect=_run_inline), patch.object(ums_server, "_start_server") as mock_start:
        response = asyncio.run(
            _api_request(
                "POST",
                "/models/labse-embedding/preload",
                json={"device_mode": "cpu"},
            )
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["action"] == "preload"
    assert payload["model"]["model_id"] == "labse-embedding"
    mock_start.assert_called_once_with("labse-embedding", ums_server.DeviceMode.CPU)


def test_activate_endpoint_starts_model_and_returns_active_view():
    async def _run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    def _fake_start(model_id, device_mode):
        assert model_id == "qwen-14b-llm"
        assert device_mode == ums_server.DeviceMode.HYBRID
        ums_server.state["active_model"] = model_id
        ums_server.state["processes"][model_id] = _FakeProcess(pid=111)
        ums_server.state["placements"][model_id] = {
            "placement_mode": "single-gpu",
            "gpu_indices": [0],
        }

    with patch.object(ums_server.asyncio, "to_thread", side_effect=_run_inline), patch.object(ums_server, "_start_server", side_effect=_fake_start) as mock_start:
        response = asyncio.run(
            _api_request(
                "POST",
                "/models/qwen-14b-llm/activate",
                json={},
            )
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["action"] == "activate"
    assert payload["model"]["model_id"] == "qwen-14b-llm"
    assert payload["model"]["running"] is True
    assert payload["model"]["active"] is True
    mock_start.assert_called_once_with("qwen-14b-llm", ums_server.DeviceMode.HYBRID)


def test_activate_endpoint_uses_remote_vllm_backend(monkeypatch):
    async def _run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setenv("BACKEND_MODE", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.local:8000")
    monkeypatch.setenv("VLLM_MODEL_ID_QWEN_14B_LLM", "qwen-remote")

    with patch.object(ums_server.asyncio, "to_thread", side_effect=_run_inline), patch.object(
        ums_server, "_ensure_vllm_backend"
    ) as mock_probe, patch.object(
        ums_server, "_launch_server_process", side_effect=AssertionError("local process launch must not happen for vLLM")
    ):
        response = asyncio.run(
            _api_request(
                "POST",
                "/models/qwen-14b-llm/activate",
                json={},
            )
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["model"]["backend"] == "vllm"
    assert payload["model"]["running"] is True
    assert payload["model"]["active"] is True
    assert payload["model"]["placement"]["placement_mode"] == "remote-vllm"
    assert payload["model"]["placement"]["served_model_id"] == "qwen-remote"
    assert payload["model"]["placement"]["upstream_url"] == "http://vllm.local:8000"
    assert isinstance(ums_server.state["processes"]["qwen-14b-llm"], ums_server._RemoteProcess)
    mock_probe.assert_called_once_with("qwen-14b-llm")


def test_stop_endpoint_stops_model_and_returns_stopped_view():
    async def _run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"]["qwen-14b-llm"] = _FakeProcess(pid=111)
    ums_server.state["placements"]["qwen-14b-llm"] = {
        "placement_mode": "single-gpu",
        "gpu_indices": [0],
    }

    def _fake_stop(model_id):
        assert model_id == "qwen-14b-llm"
        ums_server.state["processes"].pop(model_id, None)
        ums_server.state["placements"].pop(model_id, None)
        ums_server.state["active_model"] = None

    with patch.object(ums_server.asyncio, "to_thread", side_effect=_run_inline), patch.object(ums_server, "_stop_model", side_effect=_fake_stop) as mock_stop:
        response = asyncio.run(_api_request("POST", "/models/qwen-14b-llm/stop"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["action"] == "stop"
    assert payload["model"]["model_id"] == "qwen-14b-llm"
    assert payload["model"]["running"] is False
    assert payload["model"]["active"] is False
    mock_stop.assert_called_once_with("qwen-14b-llm")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/models/missing-model/preload"),
        ("POST", "/models/missing-model/activate"),
        ("POST", "/models/missing-model/stop"),
    ],
)
def test_model_control_endpoints_return_404_for_unknown_model(method, path):
    response = asyncio.run(_api_request(method, path, json={}))

    assert response.status_code == 404
    assert response.json()["detail"] == "Model missing-model not found"


def test_register_dynamic_model_endpoint_persists_and_lists_model(tmp_path, monkeypatch):
    registry_path = tmp_path / "ums_dynamic_models.json"
    monkeypatch.setenv("UMS_DYNAMIC_MODELS_REGISTRY_PATH", str(registry_path))
    model_path = tmp_path / "dynamic.gguf"
    model_path.write_text("stub", encoding="utf-8")

    response = asyncio.run(
        _api_request(
            "POST",
            "/models/register",
            json={
                "model_id": "dynamic-qwen",
                "type": "gguf",
                "path": str(model_path),
                "ctx_size": 12288,
                "gpu_layers": 33,
                "port": 8115,
            },
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["action"] == "register"
    assert payload["model"]["model_id"] == "dynamic-qwen"
    assert payload["model"]["port"] == 8115
    assert payload["model"]["resolved_path"] == str(model_path.resolve())
    models_response = asyncio.run(_api_request("GET", "/models"))
    assert models_response.status_code == 200
    models = {item["model_id"]: item for item in models_response.json()["models"]}
    assert "dynamic-qwen" in models
    assert models["dynamic-qwen"]["port"] == 8115

    persisted = json.loads(registry_path.read_text())
    assert persisted["models"]["dynamic-qwen"]["path"] == str(model_path.resolve())


def test_register_dynamic_model_allocates_port_and_takes_config_precedence(tmp_path, monkeypatch):
    registry_path = tmp_path / "ums_dynamic_models.json"
    monkeypatch.setenv("UMS_DYNAMIC_MODELS_REGISTRY_PATH", str(registry_path))
    discovered_path = tmp_path / "discovered" / "dynamic-qwen.gguf"
    discovered_path.parent.mkdir(parents=True, exist_ok=True)
    discovered_path.write_text("stub", encoding="utf-8")
    registered_path = tmp_path / "registered.gguf"
    registered_path.write_text("stub", encoding="utf-8")

    with patch.object(ums_server, "MODELS_DIR", discovered_path.parent):
        response = asyncio.run(
            _api_request(
                "POST",
                "/models/register",
                json={
                    "model_id": "dynamic-qwen",
                    "type": "gguf",
                    "path": str(registered_path),
                },
            )
        )

        assert response.status_code == 200
        assert response.json()["model"]["port"] == 8100
        config = ums_server.get_model_config("dynamic-qwen")

    assert config["path"] == str(registered_path.resolve())
    assert config["port"] == 8100
    assert ums_server.state["port_owners"][8100] == "dynamic-qwen"
    assert 8100 in ums_server.state["reserved_ports"]


def test_unregister_dynamic_model_stops_running_process_and_removes_registration(tmp_path, monkeypatch):
    registry_path = tmp_path / "ums_dynamic_models.json"
    monkeypatch.setenv("UMS_DYNAMIC_MODELS_REGISTRY_PATH", str(registry_path))
    model_path = tmp_path / "dynamic.gguf"
    model_path.write_text("stub", encoding="utf-8")
    ums_server.state["dynamic_models"]["dynamic-qwen"] = {
        "type": "gguf",
        "path": str(model_path.resolve()),
        "port": 8110,
    }
    ums_server._save_dynamic_models_registry()
    ums_server.state["processes"]["dynamic-qwen"] = _FakeProcess(pid=111)

    with patch.object(ums_server, "_stop_model") as mock_stop:
        response = asyncio.run(_api_request("DELETE", "/models/dynamic-qwen/registration"))

    assert response.status_code == 200
    assert response.json()["action"] == "unregister"
    assert "dynamic-qwen" not in ums_server.state["dynamic_models"]
    assert 8110 not in ums_server.state["reserved_ports"]
    assert ums_server.state["released_dynamic_ports"] == [8110]
    persisted = json.loads(registry_path.read_text())
    assert persisted["models"] == {}
    mock_stop.assert_called_once_with("dynamic-qwen")


def test_register_rejects_duplicate_dynamic_model_without_replace(tmp_path):
    model_path = tmp_path / "dynamic.gguf"
    model_path.write_text("stub", encoding="utf-8")
    ums_server.state["dynamic_models"]["dynamic-qwen"] = {
        "type": "gguf",
        "path": str(model_path.resolve()),
        "port": 8110,
    }

    response = asyncio.run(
        _api_request(
            "POST",
            "/models/register",
            json={
                "model_id": "dynamic-qwen",
                "type": "gguf",
                "path": str(model_path),
            },
        )
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Model dynamic-qwen is already registered"


def test_register_rejects_explicitly_reserved_port(tmp_path, monkeypatch):
    registry_path = tmp_path / "ums_dynamic_models.json"
    monkeypatch.setenv("UMS_DYNAMIC_MODELS_REGISTRY_PATH", str(registry_path))
    first_model = tmp_path / "first.gguf"
    first_model.write_text("stub", encoding="utf-8")
    second_model = tmp_path / "second.gguf"
    second_model.write_text("stub", encoding="utf-8")

    first = asyncio.run(
        _api_request(
            "POST",
            "/models/register",
            json={
                "model_id": "dynamic-a",
                "type": "gguf",
                "path": str(first_model),
                "port": 8111,
            },
        )
    )
    assert first.status_code == 200

    second = asyncio.run(
        _api_request(
            "POST",
            "/models/register",
            json={
                "model_id": "dynamic-b",
                "type": "gguf",
                "path": str(second_model),
                "port": 8111,
            },
        )
    )

    assert second.status_code == 409
    assert second.json()["detail"] == "Port 8111 is already reserved"


def test_unregister_rejects_static_model():
    response = asyncio.run(_api_request("DELETE", "/models/qwen-14b-llm/registration"))

    assert response.status_code == 400
    assert response.json()["detail"] == "Static models cannot be unregistered"


def test_register_reuses_released_dynamic_port(tmp_path, monkeypatch):
    registry_path = tmp_path / "ums_dynamic_models.json"
    monkeypatch.setenv("UMS_DYNAMIC_MODELS_REGISTRY_PATH", str(registry_path))
    first_model = tmp_path / "first.gguf"
    first_model.write_text("stub", encoding="utf-8")
    second_model = tmp_path / "second.gguf"
    second_model.write_text("stub", encoding="utf-8")

    first = asyncio.run(
        _api_request(
            "POST",
            "/models/register",
            json={
                "model_id": "dynamic-a",
                "type": "gguf",
                "path": str(first_model),
            },
        )
    )
    assert first.status_code == 200
    assert first.json()["model"]["port"] == 8100

    unregister = asyncio.run(_api_request("DELETE", "/models/dynamic-a/registration"))
    assert unregister.status_code == 200
    assert ums_server.state["released_dynamic_ports"] == [8100]

    second = asyncio.run(
        _api_request(
            "POST",
            "/models/register",
            json={
                "model_id": "dynamic-b",
                "type": "gguf",
                "path": str(second_model),
            },
        )
    )
    assert second.status_code == 200
    assert second.json()["model"]["port"] == 8100
    assert ums_server.state["port_owners"][8100] == "dynamic-b"


def test_register_skips_released_dynamic_port_when_os_listener_is_present(tmp_path, monkeypatch):
    registry_path = tmp_path / "ums_dynamic_models.json"
    monkeypatch.setenv("UMS_DYNAMIC_MODELS_REGISTRY_PATH", str(registry_path))
    model_path = tmp_path / "dynamic.gguf"
    model_path.write_text("stub", encoding="utf-8")
    ums_server.state["released_dynamic_ports"] = [8100]

    with patch.object(ums_server, "_find_listener_pids", side_effect=lambda port: [4321] if int(port) == 8100 else []):
        response = asyncio.run(
            _api_request(
                "POST",
                "/models/register",
                json={
                    "model_id": "dynamic-c",
                    "type": "gguf",
                    "path": str(model_path),
                },
            )
        )

    assert response.status_code == 200
    assert response.json()["model"]["port"] == 8101
    assert ums_server.state["port_owners"][8101] == "dynamic-c"


def test_discovered_model_gets_stable_reserved_port_mapping(tmp_path):
    discovered_path = tmp_path / "discovered" / "dynamic-qwen.gguf"
    discovered_path.parent.mkdir(parents=True, exist_ok=True)
    discovered_path.write_text("stub", encoding="utf-8")

    with patch.object(ums_server, "MODELS_DIR", discovered_path.parent):
        first = ums_server.get_model_config("dynamic-qwen")
        second = ums_server.get_model_config("dynamic-qwen")

    assert first["port"] == 8100
    assert second["port"] == 8100
    assert ums_server.state["discovered_model_ports"]["dynamic-qwen"] == 8100
    assert ums_server.state["port_owners"][8100] == "dynamic-qwen"


def test_start_server_falls_back_when_static_requested_port_is_occupied(monkeypatch):
    fake_process = _FakeProcess()
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append((cmd, port))
        return fake_process

    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "gguf", "path": "./models/gguf/qwen.gguf", "ctx_size": 8192, "gpu_layers": 0, "port": 8091},
    ), patch.object(
        ums_server,
        "_find_listener_pids",
        side_effect=lambda port: [9999] if int(port) == 8091 else [],
    ), patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server._start_server("qwen-14b-llm", ums_server.DeviceMode.CPU)

    assert len(launch_calls) == 1
    cmd, assigned_port = launch_calls[0]
    assert assigned_port == 8100
    assert cmd[cmd.index("--port") + 1] == "8100"
    assert ums_server.state["placements"]["qwen-14b-llm"]["port"] == 8100
    assert ums_server.state["port_owners"][8100] == "qwen-14b-llm"


def test_start_server_retries_on_bind_failure_with_fallback_port():
    fake_process = _FakeProcess()
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append((list(cmd), port))
        if int(port) == 8091:
            raise RuntimeError("Server exited with code 1: couldn't bind HTTP server socket")
        return fake_process

    with patch.object(
        ums_server,
        "get_model_config",
        return_value={"type": "gguf", "path": "./models/gguf/qwen.gguf", "ctx_size": 8192, "gpu_layers": 0, "port": 8091},
    ), patch.object(
        ums_server,
        "_find_listener_pids",
        return_value=[],
    ), patch.object(
        ums_server,
        "_launch_server_process",
        side_effect=fake_launch,
    ):
        ums_server._start_server("qwen-14b-llm", ums_server.DeviceMode.CPU)

    assert [port for _, port in launch_calls] == [8091, 8100]
    assert ums_server.state["placements"]["qwen-14b-llm"]["port"] == 8100
    assert ums_server.state["port_owners"][8100] == "qwen-14b-llm"


@pytest.mark.asyncio
async def test_infer_returns_429_when_llm_concurrency_is_saturated(monkeypatch):
    monkeypatch.setenv("UMS_FAIL_FAST_ON_SATURATION", "true")
    monkeypatch.setenv("UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S", "0.01")
    ums_server._llm_semaphore = asyncio.Semaphore(0)

    with patch.object(ums_server, "_start_server") as mock_start:
        with pytest.raises(ums_server.HTTPException) as exc_info:
            await ums_server.infer(
                ums_server.InferRequest(
                    model_id="qwen-14b-llm",
                    payload={"prompt": "test"},
                    stream=False,
                )
            )

    mock_start.assert_not_called()
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "llm concurrency saturated"


@pytest.mark.asyncio
async def test_openai_embeddings_returns_429_when_embedding_concurrency_is_saturated(monkeypatch):
    monkeypatch.setenv("UMS_FAIL_FAST_ON_SATURATION", "true")
    monkeypatch.setenv("UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S", "0.01")
    ums_server._embed_semaphore = asyncio.Semaphore(0)

    with patch.object(ums_server, "_start_server") as mock_start:
        with pytest.raises(ums_server.HTTPException) as exc_info:
            await ums_server.openai_embeddings(
                ums_server.EmbeddingRequest(
                    input="test embedding",
                    model="labse-embedding",
                )
            )

    mock_start.assert_not_called()
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "embedding concurrency saturated"


@pytest.mark.asyncio
async def test_fail_fast_rejects_without_waiting(monkeypatch):
    monkeypatch.setenv("UMS_FAIL_FAST_ON_SATURATION", "true")
    sem = asyncio.Semaphore(0)

    async def unexpected_wait_for(*args, **kwargs):
        raise AssertionError("wait_for should not be used in fail-fast mode")

    with patch.object(ums_server.asyncio, "wait_for", side_effect=unexpected_wait_for):
        with pytest.raises(ums_server.HTTPException) as exc_info:
            async with ums_server._acquire_runtime_slot(sem, "llm"):
                pytest.fail("slot acquisition should fail before entering context")

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "llm concurrency saturated"


@pytest.mark.asyncio
async def test_stream_infer_returns_429_before_opening_stream(monkeypatch):
    monkeypatch.setenv("UMS_FAIL_FAST_ON_SATURATION", "true")
    monkeypatch.setenv("UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S", "0.01")
    ums_server._llm_semaphore = asyncio.Semaphore(0)

    with patch.object(ums_server, "_start_server") as mock_start:
        response = await _api_request(
            "POST",
            "/infer",
            json={
                "model_id": "qwen-14b-llm",
                "payload": {"prompt": "test"},
                "stream": True,
            },
        )

    mock_start.assert_not_called()
    assert response.status_code == 429
    assert response.json()["detail"] == "stream concurrency saturated"


@pytest.mark.asyncio
async def test_stream_infer_releases_slot_when_startup_fails(monkeypatch):
    monkeypatch.setenv("UMS_FAIL_FAST_ON_SATURATION", "true")
    monkeypatch.setenv("UMS_LLM_MAX_CONCURRENCY", "1")
    ums_server._llm_semaphore = asyncio.Semaphore(1)

    with patch.object(ums_server, "_start_server", side_effect=RuntimeError("boom")):
        with pytest.raises(ums_server.HTTPException) as exc_info:
            await ums_server.infer(
                ums_server.InferRequest(
                    model_id="qwen-14b-llm",
                    payload={"prompt": "test"},
                    stream=True,
                )
            )

    assert exc_info.value.status_code == 500
    assert getattr(ums_server._llm_semaphore, "_value", None) == 1


@pytest.mark.asyncio
async def test_non_stream_infer_proxies_to_vllm_with_auth_headers(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.local:8000")
    monkeypatch.setenv("VLLM_API_KEY", "secret-token")
    monkeypatch.setenv("VLLM_MODEL_ID_QWEN_14B_LLM", "qwen-remote")
    fake_client = _FakeVLLMAsyncClient(_FakeJSONResponse({"id": "cmpl-1"}), headers={"Authorization": "Bearer secret-token"})

    with patch.object(ums_server, "_start_server") as mock_start, patch(
        "services.model_manager.unified_model_server.httpx.AsyncClient",
        return_value=fake_client,
    ):
        response = await ums_server.infer(
            ums_server.InferRequest(
                model_id="qwen-14b-llm",
                payload={"prompt": "hello"},
                stream=False,
            )
        )

    mock_start.assert_called_once()
    assert response["status"] == "success"
    assert response["result"] == {"id": "cmpl-1"}
    assert fake_client.calls == [
        {
            "url": "http://vllm.local:8000/v1/completions",
            "json": {"prompt": "hello", "model": "qwen-remote"},
            "headers": {"Authorization": "Bearer secret-token"},
        }
    ]


@pytest.mark.asyncio
async def test_non_stream_local_llama_infer_enables_cache_prompt_by_default():
    fake_client = _FakePostAsyncClient(_FakeJSONResponse({"id": "cmpl-1"}))

    with patch.object(ums_server, "_start_server") as mock_start, patch(
        "services.model_manager.unified_model_server.httpx.AsyncClient",
        return_value=fake_client,
    ):
        response = await ums_server.infer(
            ums_server.InferRequest(
                model_id="qwen-14b-llm",
                payload={"prompt": "hello"},
                stream=False,
            )
        )

    mock_start.assert_called_once()
    assert response["status"] == "success"
    assert fake_client.calls == [
        {
            "url": "http://localhost:8091/v1/completions",
            "json": {"prompt": "hello", "cache_prompt": True},
            "headers": {},
        }
    ]


@pytest.mark.asyncio
async def test_non_stream_local_llama_infer_can_disable_cache_prompt(monkeypatch):
    monkeypatch.setenv("UMS_LLAMA_CACHE_PROMPT", "false")
    fake_client = _FakePostAsyncClient(_FakeJSONResponse({"id": "cmpl-1"}))

    with patch.object(ums_server, "_start_server") as mock_start, patch(
        "services.model_manager.unified_model_server.httpx.AsyncClient",
        return_value=fake_client,
    ):
        response = await ums_server.infer(
            ums_server.InferRequest(
                model_id="qwen-14b-llm",
                payload={"prompt": "hello"},
                stream=False,
            )
        )

    mock_start.assert_called_once()
    assert response["status"] == "success"
    assert fake_client.calls[0]["json"]["cache_prompt"] is False


@pytest.mark.asyncio
async def test_non_stream_chat_infer_uses_vllm_chat_completions(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.local:8000")
    monkeypatch.setenv("VLLM_MODEL_ID_QWEN_14B_LLM", "qwen-remote")
    fake_client = _FakeVLLMAsyncClient(_FakeJSONResponse({"id": "chatcmpl-1"}))

    with patch.object(ums_server, "_start_server") as mock_start, patch(
        "services.model_manager.unified_model_server.httpx.AsyncClient",
        return_value=fake_client,
    ):
        response = await ums_server.infer(
            ums_server.InferRequest(
                model_id="qwen-14b-llm",
                payload={"messages": [{"role": "user", "content": "hello"}]},
                stream=False,
            )
        )

    mock_start.assert_called_once()
    assert response["status"] == "success"
    assert fake_client.calls[0]["url"] == "http://vllm.local:8000/v1/chat/completions"
    assert fake_client.calls[0]["json"]["model"] == "qwen-remote"


@pytest.mark.asyncio
async def test_non_stream_infer_does_not_mask_vllm_upstream_http_error(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.local:8000")
    fake_client = _FakeVLLMAsyncClient(_FakeJSONResponse({"error": "unauthorized"}, status_code=401))

    with patch.object(ums_server, "_start_server") as mock_start, patch(
        "services.model_manager.unified_model_server.httpx.AsyncClient",
        return_value=fake_client,
    ):
        with pytest.raises(ums_server.HTTPException) as exc_info:
            await ums_server.infer(
                ums_server.InferRequest(
                    model_id="qwen-14b-llm",
                    payload={"prompt": "hello"},
                    stream=False,
                )
            )

    mock_start.assert_called_once()
    assert exc_info.value.status_code == 500
    assert "status=401" in exc_info.value.detail


def test_ensure_vllm_backend_rejects_missing_served_model(monkeypatch):
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.local:8000")
    monkeypatch.setenv("VLLM_MODEL_ID_QWEN_14B_LLM", "qwen-remote")
    fake_client = _FakeHTTPClient(
        [
            _FakeJSONResponse({"ok": True}, status_code=200),
            _FakeJSONResponse({"data": [{"id": "other-model"}]}, status_code=200),
        ]
    )

    with patch("services.model_manager.unified_model_server.httpx.Client", return_value=fake_client):
        with pytest.raises(RuntimeError, match="qwen-remote"):
            ums_server._ensure_vllm_backend("qwen-14b-llm")

    assert fake_client.calls == [
        "http://vllm.local:8000/health",
        "http://vllm.local:8000/v1/models",
    ]


def test_stop_model_detaches_remote_vllm_without_killing_process(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "vllm")
    ums_server.state["active_model"] = "qwen-14b-llm"
    ums_server.state["processes"]["qwen-14b-llm"] = ums_server._RemoteProcess()
    ums_server.state["placements"]["qwen-14b-llm"] = {
        "placement_mode": "remote-vllm",
        "backend": "vllm",
    }

    with patch.object(ums_server.os, "killpg", side_effect=AssertionError("remote stop must not kill local pg")):
        ums_server._stop_model("qwen-14b-llm")

    assert "qwen-14b-llm" not in ums_server.state["processes"]
    assert "qwen-14b-llm" not in ums_server.state["placements"]
    assert ums_server.state["active_model"] is None


def test_register_endpoint_rejects_reserved_port_conflict():
    first_path = Path(os.environ["UMS_DYNAMIC_MODELS_REGISTRY_PATH"]).with_name("first.gguf")
    second_path = Path(os.environ["UMS_DYNAMIC_MODELS_REGISTRY_PATH"]).with_name("second.gguf")
    first_path.write_text("first")
    second_path.write_text("second")

    first = asyncio.run(
        _api_request(
            "POST",
            "/models/register",
            json={
                "model_id": "custom-first",
                "type": "gguf",
                "path": str(first_path),
                "port": 8110,
            },
        )
    )
    second = asyncio.run(
        _api_request(
            "POST",
            "/models/register",
            json={
                "model_id": "custom-second",
                "type": "gguf",
                "path": str(second_path),
                "port": 8110,
            },
        )
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "Port 8110 is already reserved"


def test_unregister_releases_port_for_future_dynamic_registration():
    first_path = Path(os.environ["UMS_DYNAMIC_MODELS_REGISTRY_PATH"]).with_name("first-reuse.gguf")
    second_path = Path(os.environ["UMS_DYNAMIC_MODELS_REGISTRY_PATH"]).with_name("second-reuse.gguf")
    first_path.write_text("first")
    second_path.write_text("second")

    first = asyncio.run(
        _api_request(
            "POST",
            "/models/register",
            json={"model_id": "custom-first", "type": "gguf", "path": str(first_path)},
        )
    )
    assert first.status_code == 200
    first_port = first.json()["model"]["port"]

    asyncio.run(_api_request("DELETE", "/models/custom-first/registration"))

    second = asyncio.run(
        _api_request(
            "POST",
            "/models/register",
            json={"model_id": "custom-second", "type": "gguf", "path": str(second_path)},
        )
    )

    assert second.status_code == 200
    assert second.json()["model"]["port"] == first_port


def test_discovered_model_reserves_stable_port_and_owner_mapping(tmp_path):
    model_root = tmp_path / "models"
    model_root.mkdir()
    gguf_path = model_root / "runtime-model.gguf"
    gguf_path.write_text("stub")

    with patch.object(ums_server, "MODELS_DIR", model_root):
        first = ums_server.get_model_config("runtime-model")
        second = ums_server.get_model_config("runtime-model")

    assert first["port"] == second["port"]
    assert ums_server.state["discovered_model_ports"]["runtime-model"] == first["port"]
    assert ums_server.state["port_owners"][first["port"]] == "runtime-model"


def test_stop_model_releases_discovered_port_reservation():
    ums_server.state["processes"]["runtime-model"] = _FakeProcess(pid=123)
    ums_server.state["discovered_model_ports"]["runtime-model"] = 8115
    ums_server.state["reserved_ports"] = {8115}
    ums_server.state["port_owners"] = {8115: "runtime-model"}

    with patch.object(ums_server.os, "killpg"), patch.object(ums_server.os, "getpgid", return_value=123):
        ums_server._stop_model("runtime-model")

    assert "runtime-model" not in ums_server.state["discovered_model_ports"]
    assert 8115 not in ums_server.state["reserved_ports"]
    assert 8115 not in ums_server.state["port_owners"]
