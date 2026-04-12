from __future__ import annotations

import importlib.util
import json
import os
import pathlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "scripts" / "runtime_preflight.py"
SPEC = importlib.util.spec_from_file_location("runtime_preflight", MODULE_PATH)
runtime_preflight = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runtime_preflight)


@pytest.fixture(autouse=True)
def _clear_runtime_env(monkeypatch):
    for env_name in (
        "BACKEND_MODE",
        "DEVICE_MODE",
        "LLM_DEVICE_MODE",
        "VLM_DEVICE_MODE",
        "INTENT_EMBEDDER_DEVICE_MODE",
        "RETRIEVAL_EMBEDDER_DEVICE_MODE",
        "GPU_LAYERS_MODE",
        "N_GPU_LAYERS_OVERRIDE",
        "UMS_RUNTIME_PROFILE",
        "UMS_LLM_GPU_INDICES",
        "UMS_EMBEDDING_GPU_INDEX",
        "UMS_LLM_MIN_FREE_VRAM_GB",
        "UMS_LLM_MIN_BALANCE_RATIO",
        "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS",
        "UMS_RETRIEVED_CONTEXT_RATIO",
        "UMS_GENERATION_TOKENS_RESERVE",
        "INTENT_CLASSIFIER_EMBEDDER_MODEL",
        "LEGAL_EMBEDDER_MODEL",
    ):
        monkeypatch.delenv(env_name, raising=False)


def test_build_runtime_plan_returns_stable_payload():
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(
            has_gpu=True,
            gpu_count=1,
            gpus=[],
            total_vram_gb=12.0,
            free_vram_gb=10.0,
            platform_name="Linux",
        ),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=2,
            rag_mode="corrective",
            embedding_backend="qwen3",
            embedding_device="cuda",
            llm_ctx_size=16384,
            llm_model_id="qwen-14b-llm",
            llm_quant="Q4_K_M",
            llm_gpu_layers=18,
        ),
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive")

    assert plan["runtime_profile"] == "adaptive"
    assert plan["device_mode"] == "hybrid"
    assert plan["effective_context_tokens"] > 0
    assert plan["retrieved_context_tokens_budget"] > 0
    assert plan["rag_mode"] == "corrective"
    assert plan["rag_mode_label"] == "corrective retrieval"
    assert plan["embedding_backend"] == "qwen3"
    assert plan["embedding_device"] == "cuda"
    assert plan["backend_mode"] == "llama-cpp-python"
    assert plan["llm_model_id"] == "qwen-14b-llm"
    assert plan["llm_quant"] == "Q4_K_M"
    assert plan["llm_gpu_layers"] == 18
    assert plan["gpu_layers_mode"] == "auto"
    assert plan["gpu_layers_source"] == "tier_auto"
    assert plan["hardware"]["has_gpu"] is True
    assert plan["placements"]["llm"]["gpu_layers"] == 18
    assert plan["placements"]["llm"]["device_mode"] == "hybrid"
    assert plan["placements"]["intent_embedder"]["device_mode"] == "gpu"
    assert plan["placements"]["retrieval_embedder"]["device_mode"] == "gpu"


def test_build_runtime_plan_uses_has_gpu_property_for_default_device_mode():
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(has_gpu=True, gpu_count=1, gpus=[]),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=3,
            rag_mode="agentic",
            embedding_backend="pytorch",
            embedding_device="cuda",
            llm_ctx_size=16384,
            llm_gpu_layers=-1,
            llm_model_id="qwen-32b-llm",
            llm_quant="Q4_K_M",
        ),
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive")

    assert plan["device_mode"] == "hybrid"


def test_build_runtime_plan_respects_env_device_mode_override(monkeypatch):
    monkeypatch.setenv("DEVICE_MODE", "gpu")
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(has_gpu=False, gpu_count=0, gpus=[]),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=1,
            rag_mode="simple",
            embedding_backend="onnx",
            embedding_device="cpu",
            llm_ctx_size=4096,
            llm_gpu_layers=0,
            llm_model_id="qwen-7b-llm",
            llm_quant="Q4_K_M",
        ),
    ):
        exit_code = runtime_preflight.main(["plan"])

    assert exit_code == 0


def test_manual_profile_respects_explicit_overrides():
    with patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=1,
            rag_mode="simple",
            embedding_backend="labse",
            embedding_device="cpu",
            llm_ctx_size=8192,
            llm_gpu_layers=0,
            llm_model_id="qwen-7b-llm",
            llm_quant="Q4_K_M",
        ),
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


def test_build_runtime_plan_reports_vllm_backend_mode(monkeypatch):
    monkeypatch.setenv("BACKEND_MODE", "vllm")
    with patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=1,
            rag_mode="simple",
            embedding_backend="labse",
            embedding_device="cpu",
            llm_ctx_size=8192,
            llm_gpu_layers=0,
            llm_model_id="qwen-7b-llm",
            llm_quant="Q4_K_M",
        ),
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive")

    assert plan["backend_mode"] == "vllm"


def test_render_env_runtime_is_deterministic():
    plan = {
        "runtime_profile": "adaptive",
        "effective_context_tokens": 12288,
        "retrieved_context_tokens_budget": 7372,
        "generation_tokens_reserve": 1024,
        "context_budget_ratio": 0.6,
        "device_mode": "hybrid",
        "gpu_layers_mode": "max",
        "llm_gpu_layers": -1,
        "rag_mode": "corrective",
        "component_device_modes": {
            "llm": "hybrid",
            "vlm": "hybrid",
            "intent_embedder": "cpu",
            "retrieval_embedder": "cpu",
        },
    }

    content = runtime_preflight.render_env_runtime(plan)

    assert content == (
        "UMS_RUNTIME_PROFILE=adaptive\n"
        "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS=12288\n"
        "UMS_RETRIEVED_CONTEXT_RATIO=0.6\n"
        "UMS_GENERATION_TOKENS_RESERVE=1024\n"
        "DEVICE_MODE=hybrid\n"
        "GPU_LAYERS_MODE=max\n"
        "LLM_DEVICE_MODE=hybrid\n"
        "VLM_DEVICE_MODE=hybrid\n"
        "INTENT_EMBEDDER_DEVICE_MODE=cpu\n"
        "RETRIEVAL_EMBEDDER_DEVICE_MODE=cpu\n"
        "N_GPU_LAYERS_OVERRIDE=-1\n"
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
        "gpu_layers_mode": "auto",
        "llm_gpu_layers": 12,
        "rag_mode": "simple",
    }

    written = runtime_preflight.write_env_runtime(plan, output_path=path)

    assert written == path
    assert path.read_text(encoding="utf-8").startswith("UMS_RUNTIME_PROFILE=default")


def test_build_recommendation_report_contains_gpu_split_and_tunable_fields(monkeypatch):
    monkeypatch.setenv("UMS_LLM_MIN_FREE_VRAM_GB", "1")
    plan = {
        "runtime_profile": "adaptive",
        "effective_context_tokens": 12288,
        "generation_tokens_reserve": 1024,
        "context_budget_ratio": 0.6,
        "device_mode": "hybrid",
        "gpu_layers_mode": "max",
        "component_device_modes": {
            "llm": "hybrid",
            "vlm": "hybrid",
            "intent_embedder": "gpu",
            "retrieval_embedder": "gpu",
        },
        "placements": {
            "llm": {"placement_mode": "multi-gpu", "gpu_indices": [0, 1], "tensor_split": [0.4, 0.6]},
            "intent_embedder": {"gpu_indices": [1]},
            "retrieval_embedder": {"gpu_indices": [1]},
        },
        "hardware": {"gpu_count": 2, "total_vram_gb": 16.0, "free_vram_gb": 14.0, "best_gpu": {"name": "GPU1", "index": 1}},
    }

    report = runtime_preflight.build_recommendation_report(plan)

    assert report["recommended_env"]["UMS_LLM_GPU_INDICES"] == "0,1"
    assert report["recommended_env"]["UMS_EMBEDDING_GPU_INDEX"] == "1"
    assert report["recommended_env"]["UMS_LLM_MIN_FREE_VRAM_GB"] == "1"
    assert any(item["key"] == "UMS_LLM_MIN_BALANCE_RATIO" for item in report["tunable_fields"])
    assert any("tensor_split" in note for note in report["notes"])


def test_main_recommend_outputs_text(capsys):
    exit_code = runtime_preflight.main(["recommend"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Рекомендации для backend/.env" in captured.out
    assert "Рекомендуемый блок для backend/.env:" in captured.out


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


def test_detect_hardware_snapshot_reports_has_cuda_from_has_gpu():
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(
            has_gpu=True,
            gpu_count=2,
            gpus=[
                SimpleNamespace(index=0, name="GPU0", total_vram_gb=8.0, free_vram_gb=7.0, compute_capability=(8, 9)),
                SimpleNamespace(index=1, name="GPU1", total_vram_gb=8.0, free_vram_gb=6.5, compute_capability=(8, 9)),
            ],
            best_gpu=SimpleNamespace(index=0, name="GPU0", total_vram_gb=8.0, free_vram_gb=7.0),
            ram_gb=64,
            cpu_cores=16,
            platform_name="Linux",
        ),
    ):
        snapshot = runtime_preflight.detect_hardware_snapshot()

    assert snapshot["has_cuda"] is True
    assert snapshot["gpu_count"] == 2
    assert snapshot["gpus"][0]["name"] == "GPU0"
    assert snapshot["best_gpu"]["name"] == "GPU0"
    json.dumps({"hardware": snapshot}, ensure_ascii=False)


def test_build_runtime_plan_supports_max_gpu_layers_override():
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(has_gpu=True, gpu_count=1, gpus=[]),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=2,
            rag_mode="corrective",
            embedding_backend="pytorch",
            embedding_device="cuda",
            llm_ctx_size=8192,
            llm_gpu_layers=11,
            llm_model_id="qwen-14b-llm",
            llm_quant="Q4_K_M",
        ),
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive", gpu_layers_mode="max")

    assert plan["llm_gpu_layers"] == -1
    assert plan["gpu_layers_mode"] == "max"
    assert plan["gpu_layers_source"] == "max_override"
    assert plan["placements"]["llm"]["gpu_layers"] == -1


def test_build_runtime_plan_moves_embedders_to_cpu_for_single_gpu_8gb_profile():
    fake_profile = SimpleNamespace(
        has_gpu=True,
        gpu_count=1,
        gpus=[
            SimpleNamespace(
                index=0,
                name="GTX 1070",
                total_vram_gb=8.0,
                free_vram_gb=7.5,
                compute_capability=(6, 1),
                driver_version="560",
            )
        ],
        best_gpu=SimpleNamespace(index=0, name="GTX 1070", total_vram_gb=8.0, free_vram_gb=7.5),
        total_vram_gb=8.0,
        free_vram_gb=7.5,
        ram_gb=16,
        cpu_cores=8,
        platform_name="Linux",
    )
    fake_tier = SimpleNamespace(
        tier=2,
        rag_mode="corrective",
        embedding_backend="pytorch",
        embedding_device="cuda",
        llm_ctx_size=8192,
        llm_gpu_layers=-1,
        llm_model_id="qwen-14b-llm",
        llm_quant="Q4_K_M",
    )

    with patch("services.hardware.HardwareProfiler.detect", return_value=fake_profile), patch(
        "services.hardware.TierSelector.select",
        return_value=fake_tier,
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive")

    assert plan["component_device_modes"]["intent_embedder"] == "cpu"
    assert plan["component_device_modes"]["retrieval_embedder"] == "cpu"
    assert plan["component_device_mode_sources"]["intent_embedder"] == "weak_pc_policy"
    assert plan["component_device_mode_sources"]["retrieval_embedder"] == "weak_pc_policy"
    assert "weak-pc policy moved embedders to cpu" in " ".join(plan["warnings"])


def test_build_runtime_plan_exposes_component_level_placement_summary():
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(has_gpu=True, gpu_count=1, gpus=[]),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=2,
            rag_mode="corrective",
            embedding_backend="onnx",
            embedding_device="cpu",
            llm_ctx_size=8192,
            llm_gpu_layers=7,
            llm_model_id="qwen-14b-llm",
            llm_quant="Q4_K_M",
        ),
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive", device_mode="gpu")

    assert plan["placements"]["llm"]["model_id"] == "qwen-14b-llm"
    assert plan["placements"]["llm"]["device_mode"] == "gpu"
    assert plan["placements"]["llm"]["gpu_layers"] == 7
    assert plan["placements"]["llm"]["gpu_layers_mode"] == "auto"
    assert plan["placements"]["llm"]["gpu_layers_source"] == "tier_auto"
    assert plan["placements"]["llm"]["quant"] == "Q4_K_M"
    assert plan["placements"]["llm"]["ctx_size"] == 8192
    assert plan["placements"]["llm"]["requested_device"] == "gpu"
    assert plan["placements"]["llm"]["resolved_device"] == "hybrid"
    assert plan["placements"]["llm"]["admission"] == "degraded_candidate"
    assert plan["placements"]["vlm"]["device_mode"] == "gpu"
    assert plan["placements"]["intent_embedder"]["device_mode"] == "cpu"
    assert plan["placements"]["retrieval_embedder"]["device_mode"] == "cpu"


def test_build_runtime_plan_warns_when_auto_chooses_cpu_only_with_gpu():
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(has_gpu=True, gpu_count=1, gpus=[]),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=2,
            rag_mode="corrective",
            embedding_backend="onnx",
            embedding_device="cpu",
            llm_ctx_size=8192,
            llm_gpu_layers=0,
            llm_model_id="qwen-14b-llm",
            llm_quant="Q4_K_M",
        ),
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive")

    assert any("cpu-only placement" in warning for warning in plan["warnings"])


def test_build_runtime_plan_supports_component_device_mode_overrides(monkeypatch):
    monkeypatch.setenv("INTENT_CLASSIFIER_EMBEDDER_MODEL", "qwen3-embedding-0.6b")
    monkeypatch.setenv("LEGAL_EMBEDDER_MODEL", "labse-embedding")
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(has_gpu=True, gpu_count=1, gpus=[]),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=2,
            rag_mode="corrective",
            embedding_backend="onnx",
            embedding_device="cpu",
            llm_ctx_size=8192,
            llm_gpu_layers=7,
            llm_model_id="qwen-14b-llm",
            llm_quant="Q4_K_M",
        ),
    ):
        plan = runtime_preflight.build_runtime_plan(
            runtime_profile="adaptive",
            llm_device_mode="gpu",
            vlm_device_mode="cpu",
            intent_embedder_device_mode="gpu",
            retrieval_embedder_device_mode="hybrid",
        )

    assert plan["placements"]["llm"]["device_mode"] == "gpu"
    assert plan["placements"]["vlm"]["device_mode"] == "cpu"
    assert plan["placements"]["intent_embedder"]["device_mode"] == "gpu"
    assert plan["placements"]["retrieval_embedder"]["device_mode"] == "hybrid"
    assert plan["component_device_mode_sources"]["llm"] == "cli_override"
    assert plan["component_device_mode_sources"]["vlm"] == "cli_override"
    assert plan["component_device_mode_sources"]["intent_embedder"] == "cli_override"
    assert plan["component_device_mode_sources"]["retrieval_embedder"] == "cli_override"


def test_build_runtime_plan_preserves_explicit_env_embedder_cpu_overrides(monkeypatch):
    monkeypatch.setenv("INTENT_EMBEDDER_DEVICE_MODE", "cpu")
    monkeypatch.setenv("RETRIEVAL_EMBEDDER_DEVICE_MODE", "cpu")
    fake_profile = SimpleNamespace(
        has_gpu=True,
        gpu_count=1,
        gpus=[
            SimpleNamespace(
                index=0,
                name="RTX 2070",
                total_vram_gb=8.0,
                free_vram_gb=7.2,
                compute_capability=(7, 5),
            )
        ],
        best_gpu=SimpleNamespace(index=0, name="RTX 2070", total_vram_gb=8.0, free_vram_gb=7.2),
        total_vram_gb=8.0,
        free_vram_gb=7.2,
        ram_gb=32,
        cpu_cores=16,
        platform_name="Linux",
    )
    fake_tier = SimpleNamespace(
        tier=3,
        rag_mode="agentic",
        embedding_backend="pytorch",
        embedding_device="cuda",
        llm_ctx_size=16384,
        llm_gpu_layers=-1,
        llm_model_id="qwen-14b-llm",
        llm_quant="Q4_K_M",
    )

    with patch("services.hardware.HardwareProfiler.detect", return_value=fake_profile), patch(
        "services.hardware.TierSelector.select",
        return_value=fake_tier,
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive")

    assert plan["component_device_modes"]["intent_embedder"] == "cpu"
    assert plan["component_device_modes"]["retrieval_embedder"] == "cpu"
    assert plan["component_device_mode_sources"]["intent_embedder"] == "env_override"
    assert plan["component_device_mode_sources"]["retrieval_embedder"] == "env_override"
    rendered = runtime_preflight.render_env_runtime(plan)
    assert "INTENT_EMBEDDER_DEVICE_MODE=cpu" in rendered
    assert "RETRIEVAL_EMBEDDER_DEVICE_MODE=cpu" in rendered


def test_build_runtime_plan_exposes_llm_admission_and_hybrid_resolution():
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(
            has_gpu=True,
            gpu_count=1,
            gpus=[],
            total_vram_gb=12.0,
            free_vram_gb=8.5,
            platform_name="Linux",
        ),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=2,
            rag_mode="corrective",
            embedding_backend="pytorch",
            embedding_device="cuda",
            llm_ctx_size=16384,
            llm_gpu_layers=-1,
            llm_model_id="qwen-14b-llm",
            llm_quant="Q4_K_M",
        ),
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive", device_mode="gpu")

    assert plan["placements"]["llm"]["requested_device"] == "gpu"
    assert plan["placements"]["llm"]["resolved_device"] == "hybrid"
    assert plan["placements"]["llm"]["admission"] in {"degraded_candidate", "requires_degraded"}
    assert plan["admission"]["qwen-14b-llm"]["requested_device"] == "gpu"
    assert plan["admission"]["qwen-14b-llm"]["resolved_device"] == "hybrid"
    assert plan["admission"]["qwen-14b-llm"]["estimated_vram_gb"] > 0


def test_build_runtime_plan_warns_when_manual_gpu_request_requires_degraded():
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(
            has_gpu=True,
            gpu_count=1,
            gpus=[],
            total_vram_gb=8.0,
            free_vram_gb=1.5,
            platform_name="Linux",
        ),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=1,
            rag_mode="simple",
            embedding_backend="onnx",
            embedding_device="cpu",
            llm_ctx_size=4096,
            llm_gpu_layers=0,
            llm_model_id="qwen-14b-llm",
            llm_quant="Q4_K_M",
        ),
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive", device_mode="gpu")

    assert plan["placements"]["llm"]["admission"] == "requires_degraded"
    assert any("requires_degraded" in warning for warning in plan["warnings"])


def test_build_runtime_plan_aggregates_dual_gpu_snapshot_for_llm_admission():
    with patch(
        "services.hardware.HardwareProfiler.detect",
        return_value=SimpleNamespace(
            has_gpu=True,
            gpu_count=2,
            gpus=[
                SimpleNamespace(index=0, name="GPU0", total_vram_gb=8.0, free_vram_gb=6.9, compute_capability=(7, 5)),
                SimpleNamespace(index=1, name="GPU1", total_vram_gb=8.0, free_vram_gb=7.6, compute_capability=(7, 5)),
            ],
            total_vram_gb=16.0,
            free_vram_gb=14.5,
            platform_name="Linux",
        ),
    ), patch(
        "services.hardware.TierSelector.select",
        return_value=SimpleNamespace(
            tier=3,
            rag_mode="agentic",
            embedding_backend="pytorch",
            embedding_device="cuda",
            llm_ctx_size=16384,
            llm_gpu_layers=-1,
            llm_model_id="qwen-14b-llm",
            llm_quant="Q4_K_M",
        ),
    ):
        plan = runtime_preflight.build_runtime_plan(runtime_profile="adaptive", device_mode="hybrid")

    assert plan["hardware"]["gpu_count"] == 2
    assert plan["admission"]["qwen-14b-llm"]["available_vram_gb"] > 14.0
    assert plan["placements"]["llm"]["placement_mode"] == "multi-gpu"
    assert plan["placements"]["llm"]["gpu_indices"] == [0, 1]
    assert plan["admission"]["qwen-14b-llm"]["resolved_device"] == "hybrid"
    assert plan["admission"]["qwen-14b-llm"]["admission"] in {"ok", "degraded_candidate"}
