import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "scripts" / "benchmark.py"
SPEC = importlib.util.spec_from_file_location("benchmark_runner", MODULE_PATH)
benchmark = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(benchmark)


def test_build_runtime_metadata_prefers_ums_status_backend_mode(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "llama-server")

    metadata = benchmark._build_runtime_metadata(
        {
            "backend_mode": "vllm",
            "runtime_profile": "adaptive",
            "active_heavy_model": "qwen-14b-llm",
            "running": ["qwen-14b-llm"],
            "effective_context_tokens": 8192,
            "retrieved_context_tokens_budget": 4096,
            "generation_tokens_reserve": 1024,
            "placements": {"qwen-14b-llm": {"placement_mode": "remote-vllm"}},
        }
    )

    assert metadata["backend_mode"] == "vllm"
    assert metadata["runtime_profile"] == "adaptive"
    assert metadata["running_models"] == ["qwen-14b-llm"]
    assert metadata["placements"]["qwen-14b-llm"]["placement_mode"] == "remote-vllm"


def test_run_benchmark_includes_backend_mode_and_runtime_metadata(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "vllm")

    class _FakeResponse:
        def __init__(self, payload, status_code=200, content=b"{}"):
            self._payload = payload
            self.status_code = status_code
            self.content = content

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.calls = []

        def get(self, url, timeout=None):
            self.calls.append(("GET", url))
            if url.endswith("/status"):
                return _FakeResponse(
                    {
                        "backend_mode": "vllm",
                        "runtime_profile": "adaptive",
                        "active_heavy_model": "qwen-14b-llm",
                        "running": ["qwen-14b-llm"],
                        "effective_context_tokens": 8192,
                        "retrieved_context_tokens_budget": 4096,
                        "generation_tokens_reserve": 1024,
                        "placements": {"qwen-14b-llm": {"placement_mode": "remote-vllm"}},
                    }
                )
            return _FakeResponse({}, status_code=200)

        def close(self):
            return None

    fake_result = benchmark.BenchmarkResult(
        scenario="health",
        description="health",
        status="ok",
        elapsed_sec=0.25,
    )

    with patch.object(benchmark.httpx, "Client", return_value=_FakeClient()), patch.dict(
        benchmark.SCENARIOS, {"health": lambda client: fake_result}, clear=True
    ):
        report = benchmark.run_benchmark(["health"], repeats=1, api_url="http://localhost:8000")

    assert report.backend_mode == "vllm"
    assert report.runtime_metadata["backend_mode"] == "vllm"
    assert report.runtime_metadata["runtime_profile"] == "adaptive"
    assert report.summary["ok"] == 1


def test_build_runtime_metadata_carries_prompt_cache_policy():
    metadata = benchmark._build_runtime_metadata(
        {
            "backend_mode": "llama-cpp-python",
            "runtime_profile": "adaptive",
            "active_heavy_model": "qwen-14b-llm",
            "running": ["qwen-14b-llm"],
            "effective_context_tokens": 8192,
            "retrieved_context_tokens_budget": 4096,
            "generation_tokens_reserve": 1024,
            "prompt_cache_policy": {"enabled": True, "backend_mode": "llama-cpp-python"},
            "placements": {"qwen-14b-llm": {"placement_mode": "single-gpu"}},
        }
    )

    assert metadata["prompt_cache_policy"] == {"enabled": True, "backend_mode": "llama-cpp-python"}


def test_prompt_cache_probe_reports_cold_and_warm_delta():
    class _FakeResponse:
        def __init__(self, payload, status_code=200, content=b"{}"):
            self._payload = payload
            self.status_code = status_code
            self.content = content
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self):
            self._status_calls = 0
            self._infer_calls = 0

        def get(self, url, timeout=None):
            self._status_calls += 1
            return _FakeResponse(
                {
                    "backend_mode": "llama-cpp-python",
                    "active_heavy_model": "qwen-14b-llm",
                    "prompt_cache_policy": {"enabled": True, "backend_mode": "llama-cpp-python"},
                }
            )

        def post(self, url, json, timeout=None):
            self._infer_calls += 1
            return _FakeResponse({"status": "success"}, content=b'{"status":"success"}')

    fake_client = _FakeClient()
    monotonic_values = iter([10.0, 11.2, 20.0, 20.5])

    with patch.object(benchmark.time, "monotonic", side_effect=lambda: next(monotonic_values)):
        result = benchmark.scenario_prompt_cache_probe(fake_client)

    assert result.status == "ok"
    assert result.details["cold_elapsed_sec"] == 1.2
    assert result.details["warm_elapsed_sec"] == 0.5
    assert result.details["speedup"] == 2.4
    assert result.details["status_before"]["prompt_cache_policy"]["enabled"] is True
    assert result.details["status_after"]["active_heavy_model"] == "qwen-14b-llm"
