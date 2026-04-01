from __future__ import annotations

import importlib.util
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BUNDLE_ROOT / "scripts" / "preflight_runtime.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("offline_preflight_runtime", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_recommended_runtime_plan_prefers_gpu_when_detected() -> None:
    module = _load_module()

    current = {
        "UMS_RUNTIME_PROFILE": "adaptive",
        "RAG_MODE_OVERRIDE": "auto",
    }
    snapshot = {
        "gpu_count": "2",
    }

    recommended = module.build_recommended_runtime_plan(snapshot, current)

    assert recommended["DEVICE_MODE"] == "gpu"
    assert recommended["GPU_LAYERS_MODE"] == "max"
    assert recommended["UMS_SELECTED_GPU_LAYERS"] == "-1"
    assert recommended["LLM_DEVICE_MODE"] == "gpu"
    assert recommended["VLM_DEVICE_MODE"] == "gpu"
    assert recommended["INTENT_EMBEDDER_DEVICE_MODE"] == "cpu"
    assert recommended["RETRIEVAL_EMBEDDER_DEVICE_MODE"] == "cpu"
    assert recommended["N_GPU_LAYERS_OVERRIDE"] == "-1"


def test_build_recommended_runtime_plan_falls_back_to_cpu_without_gpu() -> None:
    module = _load_module()

    current = {
        "UMS_RUNTIME_PROFILE": "adaptive",
        "RAG_MODE_OVERRIDE": "auto",
    }
    snapshot = {
        "gpu_count": "0",
    }

    recommended = module.build_recommended_runtime_plan(snapshot, current)

    assert recommended["DEVICE_MODE"] == "cpu"
    assert recommended["GPU_LAYERS_MODE"] == "auto"
    assert recommended["UMS_SELECTED_GPU_LAYERS"] == "0"
    assert recommended["LLM_DEVICE_MODE"] == "cpu"
    assert recommended["VLM_DEVICE_MODE"] == "cpu"
    assert recommended["N_GPU_LAYERS_OVERRIDE"] == ""
