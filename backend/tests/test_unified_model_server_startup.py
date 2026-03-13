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


class _FakeProcess:
    def __init__(self, pid=12345, returncode=None):
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


async def _api_request(method: str, path: str, json=None) -> httpx.Response:
    transport = httpx.ASGITransport(app=ums_server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, json=json)


@pytest.fixture(autouse=True)
def _reset_ums_state(monkeypatch, tmp_path):
    original_state = {
        key: (value.copy() if isinstance(value, dict) else value)
        for key, value in ums_server.state.items()
    }
    original_locks = dict(ums_server._model_start_locks)
    for env_name in (
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
        return_value=[11111],
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

    mock_kill.assert_called_once_with(11111)
    assert len(launch_calls) == 1
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
