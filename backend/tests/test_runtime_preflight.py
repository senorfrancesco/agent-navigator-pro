from __future__ import annotations

import importlib.util
import json
import os
import pathlib
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "scripts" / "runtime_preflight.py"
SPEC = importlib.util.spec_from_file_location("runtime_preflight", MODULE_PATH)
runtime_preflight = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runtime_preflight)


def test_build_runtime_plan_returns_stable_payload():
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(has_cuda=True),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(tier=2, rag_mode="corrective", embedding_backend="qwen3", llm_ctx_size=16384),
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive")

    assert plan["runtime_profile"] == "adaptive"
    assert plan["device_mode"] == "hybrid"
    assert plan["effective_context_tokens"] > 0
    assert plan["retrieved_context_tokens_budget"] > 0
    assert plan["rag_mode"] == "corrective"
    assert plan["embedding_backend"] == "qwen3"


def test_manual_profile_respects_explicit_overrides():
    with patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(tier=1, rag_mode="simple", embedding_backend="labse", llm_ctx_size=8192),
    ):
        plan = runtime_preflight.build_runtime_plan(
            runtime_profile="manual",
            manual_effective_context_tokens=4096,
            retrieved_context_ratio=0.55,
            generation_tokens_reserve=768,
            device_mode="cpu",
        )

    assert plan["runtime_profile"] == "manual"
    assert plan["effective_context_tokens"] == 4096
    assert plan["retrieved_context_tokens_budget"] == 2252
    assert plan["generation_tokens_reserve"] == 768
    assert plan["device_mode"] == "cpu"


def test_render_env_runtime_is_deterministic():
    plan = {
        "runtime_profile": "adaptive",
        "effective_context_tokens": 12288,
        "retrieved_context_tokens_budget": 7372,
        "generation_tokens_reserve": 1024,
        "context_budget_ratio": 0.6,
        "device_mode": "hybrid",
        "rag_mode": "corrective",
    }

    content = runtime_preflight.render_env_runtime(plan)

    assert content == (
        "UMS_RUNTIME_PROFILE=adaptive\n"
        "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS=12288\n"
        "UMS_RETRIEVED_CONTEXT_RATIO=0.6\n"
        "UMS_GENERATION_TOKENS_RESERVE=1024\n"
        "DEVICE_MODE=hybrid\n"
        "RAG_MODE_OVERRIDE=corrective\n"
    )


def test_write_env_runtime_writes_file(tmp_path):
    path = tmp_path / ".env.runtime"
    plan = {
        "runtime_profile": "default",
        "effective_context_tokens": 8192,
        "retrieved_context_tokens_budget": 4915,
        "generation_tokens_reserve": 1024,
        "context_budget_ratio": 0.6,
        "device_mode": "hybrid",
        "rag_mode": "simple",
    }

    written = runtime_preflight.write_env_runtime(plan, output_path=path)

    assert written == path
    assert path.read_text(encoding="utf-8").startswith("UMS_RUNTIME_PROFILE=default")


def test_apply_report_only_does_not_write_env_runtime(tmp_path):
    path = tmp_path / ".env.runtime"

    exit_code = runtime_preflight.main(
        [
            "apply",
            "--profile",
            "manual",
            "--manual-effective-context-tokens",
            "4096",
            "--output",
            str(path),
            "--report-only",
        ]
    )

    assert exit_code == 0
    assert not path.exists()
