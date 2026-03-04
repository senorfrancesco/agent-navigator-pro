import os
import sys
import threading
import time
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


def test_start_server_falls_back_to_cpu_for_st_model():
    fake_process = _FakeProcess()
    launch_calls = []

    def fake_launch(cmd, port, health_timeout_s=120.0):
        launch_calls.append(cmd)
        if "--device" in cmd and cmd[cmd.index("--device") + 1] == "cuda":
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
    assert launch_calls[0][launch_calls[0].index("--device") + 1] == "cuda"
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


@pytest.mark.parametrize(
    "ctx_size,parallel_slots,hard_cap,expected",
    [
        (16384, 1, 16000, 16000),
        (16384, 4, 16000, 4096),
        (4096, 8, 16000, 1024),
    ],
)
def test_compute_effective_context_tokens(ctx_size, parallel_slots, hard_cap, expected):
    assert ums_server._compute_effective_context_tokens(ctx_size, parallel_slots, hard_cap) == expected


def test_status_returns_effective_context_tokens_and_runtime():
    original_state = {
        "active_model": ums_server.state.get("active_model"),
        "processes": dict(ums_server.state.get("processes", {})),
        "effective_context_tokens": ums_server.state.get("effective_context_tokens"),
        "model_runtime": dict(ums_server.state.get("model_runtime", {})),
        "tier_config": ums_server.state.get("tier_config"),
    }
    try:
        ums_server.state["active_model"] = "qwen-14b-llm"
        ums_server.state["processes"] = {"qwen-14b-llm": _FakeProcess()}
        ums_server.state["effective_context_tokens"] = 8192
        ums_server.state["model_runtime"] = {
            "qwen-14b-llm": {
                "ctx_size": 16384,
                "parallel_slots": 2,
                "context_hard_cap": 16384,
                "effective_context_tokens": 8192,
            }
        }
        ums_server.state["tier_config"] = None

        import asyncio
        payload = asyncio.run(ums_server.get_status())

        assert payload["effective_context_tokens"] == 8192
        assert payload["runtime"]["parallel_slots"] == 2
        assert payload["runtime"]["effective_context_tokens"] == 8192
    finally:
        ums_server.state["active_model"] = original_state["active_model"]
        ums_server.state["processes"] = original_state["processes"]
        ums_server.state["effective_context_tokens"] = original_state["effective_context_tokens"]
        ums_server.state["model_runtime"] = original_state["model_runtime"]
        ums_server.state["tier_config"] = original_state["tier_config"]
