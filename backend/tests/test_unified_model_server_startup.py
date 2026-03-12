import os
import sys
import threading
import time
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

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


@pytest.fixture(autouse=True)
def _reset_ums_state(monkeypatch):
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
    ):
        monkeypatch.delenv(env_name, raising=False)
    ums_server.state["processes"].clear()
    ums_server.state["placements"].clear()
    ums_server.state["active_model"] = None
    ums_server.state["tier_config"] = None
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
