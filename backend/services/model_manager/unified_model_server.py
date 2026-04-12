"""
Unified Model Server (UMS) - Управляет жизненным циклом LLM и Embedding моделей.
Поддерживает динамическое переключение моделей на основе файловой системы.
"""

import os
import sys
import json
import time
import copy
import signal
import subprocess
import asyncio
import logging
import psutil
import warnings
import threading
from collections import deque
from contextlib import suppress
from contextlib import asynccontextmanager as async_cm
from enum import Enum
from typing import Dict, List, Optional, Any, AsyncGenerator
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Подавление предупреждений pynvml
warnings.filterwarnings("ignore", category=FutureWarning, module="pynvml")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='[UMS] %(levelname)s: %(message)s'
)
logger = logging.getLogger("UMS")

# Определяем корень бэкенда
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = BACKEND_ROOT / ".env"

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
    logger.info(f"Loaded .env from {ENV_PATH}")
else:
    load_dotenv()

# Безопасный импорт pynvml
try:
    import pynvml
    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
import httpx
import uvicorn
from services.observability import (
    inc_metric_counter,
    ObservabilityMiddleware,
    render_metrics_text,
    set_ums_runtime_metrics,
)
from services.model_manager.model_registry import get_runtime_config
from services.model_manager.model_registry import get_preferred_role_for_model_id
from services.model_manager.model_selection import resolve_model_selection
from services.model_manager.models_config import (
    get_all_models as get_registered_models,
    get_model_config as get_registry_model_config,
    get_model_path_env_contract,
    resolve_model_path as resolve_runtime_model_path,
)

# === Configuration ===

class DeviceMode(str, Enum):
    CPU = "cpu"
    GPU = "gpu"
    HYBRID = "hybrid"


class _RemoteProcess:
    def __init__(self, pid: int = 0) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

MODELS_DIR = BACKEND_ROOT / "models" / "gguf"

def _build_static_model_entry(model_id: str) -> Optional[Dict[str, Any]]:
    try:
        config = get_registry_model_config(model_id)
    except Exception:
        return None
    runtime_type = str(config.get("runtime_type") or "")
    if runtime_type not in {"gguf", "gguf-vl", "st"} or not config.get("port"):
        return None
    entry: Dict[str, Any] = {
        "type": runtime_type,
        "path": config.get("path"),
        "port": config.get("port"),
    }
    if runtime_type in {"gguf", "gguf-vl"}:
        entry["ctx_size"] = int(config.get("ctx_size") or 0)
        entry["gpu_layers"] = int(config.get("gpu_layers") or 0)
    if runtime_type == "gguf-vl":
        entry["mmproj"] = config.get("mmproj_path")
    return entry


def _build_static_models_config() -> Dict[str, Dict[str, Any]]:
    payload: Dict[str, Dict[str, Any]] = {}
    for model_id in get_registered_models().keys():
        entry = _build_static_model_entry(model_id)
        if entry is not None:
            payload[model_id] = entry
    return payload


def _default_heavy_model_id() -> str:
    runtime_config = get_runtime_config()
    role_key = str(runtime_config.get("default_active_heavy_role") or "llm.default_chat")
    return resolve_model_selection(role_key).resolved_model_id


def _preload_sequence() -> List[str]:
    runtime_config = get_runtime_config()
    payload: List[str] = []
    seen: set[str] = set()
    for item in list(runtime_config.get("preload_sequence") or []):
        candidate = str(item or "").strip()
        if not candidate:
            continue
        if candidate in STATIC_MODELS_CONFIG:
            if candidate not in seen:
                payload.append(candidate)
                seen.add(candidate)
            continue
        try:
            resolved_model_id = resolve_model_selection(candidate).resolved_model_id
            if resolved_model_id not in seen:
                payload.append(resolved_model_id)
                seen.add(resolved_model_id)
        except Exception:
            if candidate not in seen:
                payload.append(candidate)
                seen.add(candidate)
    return payload


STATIC_MODELS_CONFIG = _build_static_models_config()

state = {
    "active_model": None, # Последняя запрошенная "тяжелая" модель
    "processes": {},      # model_id -> process
    "placements": {},     # model_id -> placement metadata
    "admission": {},      # model_id -> admission metadata
    "runtime_states": {}, # model_id -> effective runtime state metadata
    "last_fallback_event": None,
    "device_mode": DeviceMode.HYBRID,
    "runtime_budget": {},
    "dynamic_ports": 8100, # Начальный порт для динамических моделей
    "dynamic_models": {},
    "discovered_model_ports": {},
    "reserved_ports": set(),
    "port_owners": {},
    "released_dynamic_ports": [],
    "concurrency_policy": {},
}
_model_start_locks: Dict[str, threading.Lock] = {}
_model_start_locks_guard = threading.Lock()
_heavy_model_lifecycle_lock = threading.RLock()
_process_log_buffers: Dict[int, deque[str]] = {}

_LLM_LAYER_GUESSES = {
    "qwen-7b-llm": 28,
    "qwen-14b-llm": 40,
    "qwen-32b-llm": 64,
    "qwen-72b-llm": 80,
}
_LLM_BASE_VRAM_GB = {
    ("qwen-7b-llm", "Q4_K_M"): 5.0,
    ("qwen-14b-llm", "Q4_K_M"): 9.8,
    ("qwen-14b-llm", "Q8_0"): 16.0,
    ("qwen-32b-llm", "Q4_K_M"): 20.0,
    ("qwen-72b-llm", "Q4_K_M"): 42.0,
}

_RUNTIME_STATE_AVAILABLE = "available"
_RUNTIME_STATE_LOADING = "loading"
_RUNTIME_STATE_UNAVAILABLE = "unavailable"
_RUNTIME_STATE_DEGRADED_CPU = "degraded_cpu"
_RUNTIME_STATE_ERROR_GPU = "error_gpu"
_HEAVY_GPU_RUNTIME_FAILURE_MARKERS = (
    "ggml_cuda_init: failed",
    "failed to initialize cuda",
    "no usable gpu found",
    "--gpu-layers option will be ignored",
    "tensor split has no effect",
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _allow_heavy_cpu_degrade_after_gpu_failure() -> bool:
    return _env_flag("UMS_ALLOW_HEAVY_CPU_DEGRADE_AFTER_GPU_FAILURE", False)


def _set_runtime_state(
    model_id: str,
    runtime_state: str,
    *,
    reason: str = "ok",
    placement: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "runtime_state": runtime_state,
        "reason": reason,
        "updated_at": int(time.time()),
    }
    effective_placement = dict(placement or {})
    if effective_placement:
        payload["placement"] = effective_placement
        payload["placement_mode"] = effective_placement.get("placement_mode")
        payload["gpu_indices"] = list(effective_placement.get("gpu_indices") or [])
        payload["port"] = effective_placement.get("port")
        if effective_placement.get("requested_device") is not None:
            payload["requested_device"] = effective_placement.get("requested_device")
        if effective_placement.get("resolved_device") is not None:
            payload["resolved_device"] = effective_placement.get("resolved_device")
    state.setdefault("runtime_states", {})[model_id] = payload
    return payload


def _get_runtime_state(model_id: str) -> Optional[Dict[str, Any]]:
    payload = (state.get("runtime_states") or {}).get(model_id)
    return copy.deepcopy(payload) if payload is not None else None


def _build_runtime_unavailable_payload(
    *,
    requested_model_id: str,
    runtime_state: str,
    reason: str,
    ready_model_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _build_infer_readiness_payload(
        requested_model_id=requested_model_id,
        infer_ready=(runtime_state == _RUNTIME_STATE_DEGRADED_CPU),
        ready_model_id=ready_model_id,
        reason=reason,
        runtime_state=runtime_state,
    )

# === Resource Helpers ===

def _get_gpu_info() -> List[Dict[str, Any]]:
    if not PYNVML_AVAILABLE: return []
    try:
        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()
        gpus = []
        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            gpus.append({
                "index": i, "name": name,
                "free_gb": info.free / (1024 ** 3),
                "total_gb": info.total / (1024 ** 3)
            })
        return gpus
    except: return []

def _get_available_vram() -> float:
    return sum(gpu["free_gb"] for gpu in _get_gpu_info())


def _component_kind(model_id: str, config: Optional[Dict[str, Any]] = None) -> str:
    runtime_config = get_runtime_config()
    component_kinds = dict(runtime_config.get("component_kinds") or {})
    if model_id in component_kinds:
        return str(component_kinds[model_id])
    cfg = config or get_model_config(model_id) or {}
    cfg_type = str(cfg.get("type") or "")
    if model_id == "qwen3-embedding-0.6b":
        return "intent_embedder"
    if model_id == "labse-embedding":
        return "retrieval_embedder"
    if cfg_type in {"gguf", "gguf-vl"}:
        return "llm"
    return "component"


def _estimate_llm_vram_gb(model_id: str, quant: str, ctx_size: int) -> float:
    base = _LLM_BASE_VRAM_GB.get((model_id, quant))
    if base is None:
        if "72b" in model_id:
            base = 42.0
        elif "32b" in model_id:
            base = 20.0
        elif "14b" in model_id:
            base = 9.8
        else:
            base = 5.0
    kv_cache = max(0.5, (max(1024, int(ctx_size or 4096)) / 4096.0) * 0.75)
    activation = max(0.25, base * 0.08)
    safety_margin = max(0.5, base * 0.05)
    return round(base + kv_cache + activation + safety_margin, 3)


def _estimate_hybrid_gpu_layers(model_id: str, available_vram_gb: float, estimated_vram_gb: float) -> int:
    layer_guess = _LLM_LAYER_GUESSES.get(model_id, 40)
    if estimated_vram_gb <= 0:
        return max(1, layer_guess // 3)
    ratio = max(0.0, min(1.0, available_vram_gb / estimated_vram_gb))
    if ratio < 0.30:
        return 0
    return max(1, min(layer_guess - 1, int(round(layer_guess * ratio))))


def _resolve_llm_admission(
    *,
    model_id: str,
    config: Dict[str, Any],
    requested_device: DeviceMode,
    available_gpus: List[Dict[str, Any]],
    token_budget: Optional[int] = None,
) -> Dict[str, Any]:
    component = _component_kind(model_id, config)
    quant = str(config.get("quant") or config.get("llm_quant") or "Q4_K_M")
    ctx_size = int(config.get("ctx_size") or _get_runtime_ctx_size())
    estimated_vram_gb = _estimate_llm_vram_gb(model_id, quant, ctx_size)
    candidate_gpus = _select_llm_gpus(available_gpus)
    available_vram_gb = sum(float(gpu.get("free_gb", 0.0)) for gpu in candidate_gpus)
    warnings_list: List[str] = []

    if requested_device == DeviceMode.CPU or not candidate_gpus:
        return {
            "component": component,
            "requested_device": requested_device.value,
            "resolved_device": DeviceMode.CPU.value,
            "admission": "ok",
            "estimated_vram_gb": estimated_vram_gb,
            "available_vram_gb": available_vram_gb,
            "effective_token_budget": token_budget or max(1024, ctx_size // 2),
            "effective_gpu_layers": 0,
            "warnings": warnings_list,
        }

    configured_gpu_layers = int(config.get("gpu_layers", -1))
    if configured_gpu_layers == 0:
        warnings_list.append("llm requested non-cpu placement but configured gpu_layers=0")
        return {
            "component": component,
            "requested_device": requested_device.value,
            "resolved_device": requested_device.value,
            "admission": "requires_degraded",
            "estimated_vram_gb": estimated_vram_gb,
            "available_vram_gb": available_vram_gb,
            "effective_token_budget": token_budget or max(1024, ctx_size // 2),
            "effective_gpu_layers": 0,
            "warnings": warnings_list,
        }

    full_fit = configured_gpu_layers == -1 and available_vram_gb >= estimated_vram_gb
    estimated_hybrid_layers = configured_gpu_layers if configured_gpu_layers > 0 else _estimate_hybrid_gpu_layers(
        model_id, available_vram_gb, estimated_vram_gb
    )

    if requested_device == DeviceMode.GPU:
        if full_fit:
            resolved_device = DeviceMode.GPU.value
            admission = "ok"
            effective_gpu_layers = configured_gpu_layers
        elif estimated_hybrid_layers > 0:
            resolved_device = DeviceMode.HYBRID.value
            admission = "degraded_candidate"
            effective_gpu_layers = estimated_hybrid_layers
            warnings_list.append("manual gpu request downgraded to hybrid due to partial VRAM fit")
        else:
            resolved_device = DeviceMode.GPU.value
            admission = "requires_degraded"
            effective_gpu_layers = 0
            warnings_list.append("manual gpu request requires_degraded due to insufficient VRAM")
    else:
        if estimated_hybrid_layers > 0:
            resolved_device = DeviceMode.HYBRID.value
            admission = "ok" if configured_gpu_layers > 0 or full_fit else "degraded_candidate"
            effective_gpu_layers = estimated_hybrid_layers
        else:
            resolved_device = DeviceMode.HYBRID.value
            admission = "requires_degraded"
            effective_gpu_layers = 0
            warnings_list.append("hybrid request requires_degraded because useful GPU layer placement is unavailable")

    return {
        "component": component,
        "requested_device": requested_device.value,
        "resolved_device": resolved_device,
        "admission": admission,
        "estimated_vram_gb": estimated_vram_gb,
        "available_vram_gb": available_vram_gb,
        "effective_token_budget": token_budget or max(1024, ctx_size // 2),
        "effective_gpu_layers": effective_gpu_layers,
        "warnings": warnings_list,
    }


def _resolve_embedding_admission(
    *,
    model_id: str,
    requested_device: DeviceMode,
    resolved_device: str,
    available_gpus: List[Dict[str, Any]],
    fallback_applied: bool = False,
) -> Dict[str, Any]:
    warnings_list: List[str] = []
    if fallback_applied:
        warnings_list.append("embedding component fell back to cpu")
    admission = "ok" if requested_device.value == resolved_device else "degraded_candidate"
    return {
        "component": _component_kind(model_id),
        "requested_device": requested_device.value,
        "resolved_device": resolved_device,
        "admission": admission,
        "estimated_vram_gb": None,
        "available_vram_gb": sum(float(gpu.get("free_gb", 0.0)) for gpu in available_gpus),
        "effective_token_budget": None,
        "fallback_applied": fallback_applied,
        "warnings": warnings_list,
    }


def _parse_gpu_indices_env(name: str, available_gpus: List[Dict[str, Any]]) -> Optional[List[int]]:
    raw = os.getenv(name)
    if not raw:
        return None
    available = {int(gpu["index"]) for gpu in available_gpus}
    selected: List[int] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            idx = int(token)
        except ValueError:
            continue
        if idx in available and idx not in selected:
            selected.append(idx)
    return selected or None


def _normalize_tensor_split(weights: List[float]) -> List[float]:
    total = sum(max(weight, 0.0) for weight in weights)
    if total <= 0:
        if not weights:
            return []
        return [round(1.0 / len(weights), 4) for _ in weights]
    return [round(max(weight, 0.0) / total, 4) for weight in weights]


def _select_llm_gpus(available_gpus: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    occupied_embedding_gpu_indices = _get_embedding_occupied_gpu_indices()
    selected_gpu_indices = _parse_gpu_indices_env("UMS_LLM_GPU_INDICES", available_gpus)
    selected = [
        gpu for gpu in available_gpus
        if selected_gpu_indices is None or int(gpu["index"]) in set(selected_gpu_indices)
    ]
    selected = [
        gpu for gpu in selected
        if int(gpu["index"]) not in occupied_embedding_gpu_indices
    ]
    min_free_gb = _read_runtime_float("UMS_LLM_MIN_FREE_VRAM_GB", 0.0)
    selected = [gpu for gpu in selected if float(gpu.get("free_gb", 0.0)) >= min_free_gb]
    if len(selected) <= 1:
        return selected
    max_free = max(float(gpu.get("free_gb", 0.0)) for gpu in selected)
    min_balance_ratio = _clamp_float(_read_runtime_float("UMS_LLM_MIN_BALANCE_RATIO", 0.5), min_value=0.1, max_value=1.0)
    balanced = [
        gpu for gpu in selected
        if max_free <= 0 or float(gpu.get("free_gb", 0.0)) / max_free >= min_balance_ratio
    ]
    if not balanced:
        return [max(selected, key=lambda gpu: float(gpu.get("free_gb", 0.0)))]
    if len(balanced) == 1:
        return balanced
    return balanced


def _resolve_embedding_tier_preference() -> str:
    tier_config = state.get("tier_config")
    preferred = str(getattr(tier_config, "embedding_device", "cuda") or "cuda").strip().lower()
    return preferred if preferred in {"cpu", "cuda"} else "cuda"


def _resolve_runtime_device_mode() -> DeviceMode:
    raw = str(os.getenv("DEVICE_MODE", "")).strip().lower()
    if raw == "gpu":
        return DeviceMode.GPU
    if raw == "cpu":
        return DeviceMode.CPU
    if raw == "hybrid":
        return DeviceMode.HYBRID
    return state.get("device_mode", DeviceMode.HYBRID)


def _normalize_device_mode_value(raw: Optional[str]) -> Optional[DeviceMode]:
    normalized = str(raw or "").strip().lower()
    if normalized == "gpu":
        return DeviceMode.GPU
    if normalized == "cpu":
        return DeviceMode.CPU
    if normalized == "hybrid":
        return DeviceMode.HYBRID
    return None


def _get_component_device_override(model_id: str) -> Optional[DeviceMode]:
    if model_id == "qwen-vl-8b":
        return _normalize_device_mode_value(os.getenv("VLM_DEVICE_MODE")) or _normalize_device_mode_value(os.getenv("LLM_DEVICE_MODE"))
    if model_id in {"qwen-14b-llm", "qwen-7b-llm", "qwen-32b-llm", "qwen-72b-llm"}:
        return _normalize_device_mode_value(os.getenv("LLM_DEVICE_MODE"))
    if model_id == "qwen3-embedding-0.6b":
        return _normalize_device_mode_value(os.getenv("INTENT_EMBEDDER_DEVICE_MODE"))
    if model_id == "labse-embedding":
        return _normalize_device_mode_value(os.getenv("RETRIEVAL_EMBEDDER_DEVICE_MODE"))
    return None


def _resolve_component_device_mode(model_id: str, fallback: DeviceMode) -> DeviceMode:
    return _get_component_device_override(model_id) or fallback


def _is_weak_pc_single_gpu_profile(available_gpus: List[Dict[str, Any]]) -> bool:
    if len(available_gpus) != 1:
        return False
    total_gb = float((available_gpus[0] or {}).get("total_gb", 0.0) or 0.0)
    return 0.0 < total_gb <= 8.5


def _get_embedding_occupied_gpu_indices(*, exclude_model_id: Optional[str] = None) -> set[int]:
    occupied: set[int] = set()
    for running_model_id, placement in (state.get("placements") or {}).items():
        if running_model_id == exclude_model_id:
            continue
        config = get_model_config(running_model_id) or {}
        if str(config.get("type") or "") != "st":
            continue
        for gpu_index in list((placement or {}).get("gpu_indices") or []):
            occupied.add(int(gpu_index))
    return occupied


def _build_model_placement_plan(
    *,
    model_id: str,
    config: Dict[str, Any],
    device_mode: DeviceMode,
    available_gpus: List[Dict[str, Any]],
) -> Dict[str, Any]:
    device_mode = _resolve_component_device_mode(model_id, device_mode)
    if config["type"] == "st":
        explicit_override = _get_component_device_override(model_id)
        tier_prefers_cpu = _resolve_embedding_tier_preference() == "cpu"
        weak_pc_prefers_cpu = _is_weak_pc_single_gpu_profile(available_gpus) and explicit_override is None
        if weak_pc_prefers_cpu:
            tier_prefers_cpu = True
        if device_mode == DeviceMode.CPU or not available_gpus or (tier_prefers_cpu and explicit_override is None):
            return {
                "placement_mode": "cpu",
                "device_arg": "cpu",
                "gpu_indices": [],
                "requested_device": device_mode.value,
                "resolved_device": DeviceMode.CPU.value,
                "admission": "ok" if device_mode == DeviceMode.CPU else "degraded_candidate",
            }
        selected_gpu = _parse_gpu_indices_env("UMS_EMBEDDING_GPU_INDEX", available_gpus)
        if selected_gpu:
            gpu_index = selected_gpu[0]
        else:
            llm_gpu_indices = set()
            occupied_embedding_gpu_indices = _get_embedding_occupied_gpu_indices(exclude_model_id=model_id)
            active_heavy_model = state.get("active_model")
            if active_heavy_model:
                llm_gpu_indices = set((state.get("placements", {}).get(active_heavy_model) or {}).get("gpu_indices") or [])
            candidate_gpus = [
                gpu
                for gpu in available_gpus
                if int(gpu["index"]) not in llm_gpu_indices
                and int(gpu["index"]) not in occupied_embedding_gpu_indices
            ]
            if not candidate_gpus and llm_gpu_indices and explicit_override is None:
                return {
                    "placement_mode": "cpu",
                    "device_arg": "cpu",
                    "gpu_indices": [],
                    "requested_device": device_mode.value,
                    "resolved_device": DeviceMode.CPU.value,
                    "admission": "degraded_candidate",
                }
            if not candidate_gpus:
                candidate_gpus = [
                    gpu
                    for gpu in available_gpus
                    if int(gpu["index"]) not in occupied_embedding_gpu_indices
                ]
            if not candidate_gpus:
                return {
                    "placement_mode": "cpu",
                    "device_arg": "cpu",
                    "gpu_indices": [],
                    "requested_device": device_mode.value,
                    "resolved_device": DeviceMode.CPU.value,
                    "admission": "degraded_candidate",
                }
            gpu_index = max(candidate_gpus, key=lambda gpu: float(gpu.get("free_gb", 0.0)))["index"]
        return {
            "placement_mode": "single-gpu",
            "device_arg": f"cuda:{gpu_index}",
            "gpu_indices": [int(gpu_index)],
            "requested_device": device_mode.value,
            "resolved_device": DeviceMode.GPU.value,
            "admission": "ok",
        }

    if device_mode == DeviceMode.CPU or not available_gpus:
        return {
            "placement_mode": "cpu",
            "gpu_indices": [],
            "tensor_split": [],
            "requested_device": device_mode.value,
            "resolved_device": DeviceMode.CPU.value,
            "admission": "ok",
        }

    selected_gpus = _select_llm_gpus(available_gpus)
    if not selected_gpus:
        return {
            "placement_mode": "cpu",
            "gpu_indices": [],
            "tensor_split": [],
            "requested_device": device_mode.value,
            "resolved_device": DeviceMode.CPU.value,
            "admission": "requires_degraded",
        }
    if len(selected_gpus) == 1:
        return {
            "placement_mode": "single-gpu",
            "gpu_indices": [int(selected_gpus[0]["index"])],
            "tensor_split": [1.0],
            "requested_device": device_mode.value,
            "resolved_device": device_mode.value,
            "admission": "ok",
        }
    tensor_split = _normalize_tensor_split([float(gpu.get("free_gb", 0.0)) for gpu in selected_gpus])
    return {
        "placement_mode": "multi-gpu",
        "gpu_indices": [int(gpu["index"]) for gpu in selected_gpus],
        "tensor_split": tensor_split,
        "requested_device": device_mode.value,
        "resolved_device": device_mode.value,
        "admission": "ok",
    }


def _placement_with_device_arg(placement: Dict[str, Any], device_arg: str) -> Dict[str, Any]:
    resolved = dict(placement)
    resolved["device_arg"] = device_arg
    if device_arg == "cpu":
        resolved["placement_mode"] = "cpu"
        resolved["gpu_indices"] = []
    return resolved


def _clamp_float(value: float, *, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _resolve_runtime_profile() -> str:
    raw = str(os.getenv("UMS_RUNTIME_PROFILE", "adaptive")).strip().lower()
    if raw in {"default", "adaptive", "manual"}:
        return raw
    return "adaptive"


def _read_runtime_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _read_runtime_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _read_runtime_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _resolve_backend_mode() -> str:
    raw = str(os.getenv("BACKEND_MODE", "llama-cpp-python")).strip().lower()
    if raw in {"llama-cpp-python", "llama-server", "vllm"}:
        return raw
    return "llama-cpp-python"


def _resolve_infer_timeout_s() -> float:
    return float(os.getenv("UMS_INFER_TIMEOUT_S", "300.0"))


def _should_use_vllm_backend(config: Dict[str, Any]) -> bool:
    return _resolve_backend_mode() == "vllm" and str(config.get("type")) == "gguf"


def _resolve_prompt_cache_policy(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    backend_mode = _resolve_backend_mode()
    model_type = str((config or {}).get("type") or "")
    enabled = backend_mode != "vllm" and model_type in {"gguf", "gguf-vl"} and _read_runtime_bool(
        "UMS_LLAMA_CACHE_PROMPT", True
    )
    return {
        "enabled": enabled,
        "backend_mode": backend_mode,
    }


def _get_vllm_base_url() -> str:
    return str(os.getenv("VLLM_BASE_URL", "http://localhost:8101")).rstrip("/")


def _sanitize_env_model_suffix(model_id: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in model_id.upper())


def _get_vllm_served_model_id(model_id: str) -> str:
    env_name = f"VLLM_MODEL_ID_{_sanitize_env_model_suffix(model_id)}"
    return str(os.getenv(env_name, model_id)).strip() or model_id


def _build_vllm_headers() -> Dict[str, str]:
    api_key = str(os.getenv("VLLM_API_KEY", "")).strip()
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def _build_upstream_headers(config: Dict[str, Any]) -> Dict[str, str]:
    if _should_use_vllm_backend(config):
        return _build_vllm_headers()
    return {}


def _build_vllm_url(*parts: str) -> str:
    suffix = "/".join(part.strip("/") for part in parts if part)
    return f"{_get_vllm_base_url()}/{suffix}" if suffix else _get_vllm_base_url()


def _ensure_vllm_backend(model_id: str) -> None:
    headers = _build_vllm_headers()
    served_model_id = _get_vllm_served_model_id(model_id)
    with httpx.Client(timeout=5.0, headers=headers) as client:
        health = client.get(_build_vllm_url("health"))
        if health.status_code != 200:
            raise RuntimeError(f"vLLM health probe failed: status={health.status_code}")
        models = client.get(_build_vllm_url("v1", "models"))
        if models.status_code != 200:
            raise RuntimeError(f"vLLM models probe failed: status={models.status_code}")
        payload = models.json()
        available_ids = {
            str(item.get("id"))
            for item in (payload.get("data") or [])
            if isinstance(item, dict) and item.get("id") is not None
        }
        if served_model_id not in available_ids:
            raise RuntimeError(f"vLLM model {served_model_id} not exposed by upstream")


def _build_vllm_placement(model_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "placement_mode": "remote-vllm",
        "backend": "vllm",
        "upstream_url": _get_vllm_base_url(),
        "served_model_id": _get_vllm_served_model_id(model_id),
        "gpu_indices": [],
        "device_arg": "remote",
        "model_type": str(config.get("type") or ""),
    }


def _build_infer_url(config: Dict[str, Any], *, is_chat: bool) -> str:
    if _should_use_vllm_backend(config):
        return _build_vllm_url("v1", "chat/completions" if is_chat else "completions")
    url_suffix = "v1/embeddings" if config["type"] == "st" else f"v1/{'chat/' if is_chat else ''}completions"
    return f"http://localhost:{config['port']}/{url_suffix}"


def _build_infer_payload(model_id: str, config: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    enriched = payload.copy()
    if _should_use_vllm_backend(config):
        enriched.setdefault("model", _get_vllm_served_model_id(model_id))
        return enriched
    prompt_cache_policy = _resolve_prompt_cache_policy(config)
    if prompt_cache_policy["enabled"]:
        enriched["cache_prompt"] = True
    elif str(config.get("type") or "") in {"gguf", "gguf-vl"}:
        enriched["cache_prompt"] = False
    return enriched


def _get_runtime_ctx_size() -> int:
    active_model = state.get("active_model") or "qwen-14b-llm"
    config = get_model_config(active_model) or STATIC_MODELS_CONFIG.get("qwen-14b-llm", {})
    ctx_size = int(config.get("ctx_size") or STATIC_MODELS_CONFIG["qwen-14b-llm"]["ctx_size"])
    return max(ctx_size, 1024)


def resolve_runtime_budget(
    *,
    ctx_size: Optional[int] = None,
    runtime_profile: Optional[str] = None,
    default_effective_context_tokens: Optional[int] = None,
    manual_effective_context_tokens: Optional[int] = None,
    context_budget_ratio: Optional[float] = None,
    generation_tokens_reserve: Optional[int] = None,
    adaptive_min_context_tokens: Optional[int] = None,
    adaptive_context_utilization: Optional[float] = None,
) -> Dict[str, Any]:
    profile = runtime_profile or _resolve_runtime_profile()
    if profile not in {"default", "adaptive", "manual"}:
        profile = "adaptive"
    llm_ctx_size = int(ctx_size or _get_runtime_ctx_size())
    default_ctx = (
        int(default_effective_context_tokens)
        if default_effective_context_tokens is not None
        else _read_runtime_int("UMS_DEFAULT_EFFECTIVE_CONTEXT_TOKENS", min(llm_ctx_size, 8192))
    )
    adaptive_floor = (
        int(adaptive_min_context_tokens)
        if adaptive_min_context_tokens is not None
        else _read_runtime_int("UMS_ADAPTIVE_MIN_CONTEXT_TOKENS", 4096)
    )
    adaptive_util = _clamp_float(
        float(adaptive_context_utilization)
        if adaptive_context_utilization is not None
        else _read_runtime_float("UMS_ADAPTIVE_CONTEXT_UTILIZATION", 0.85),
        min_value=0.50,
        max_value=1.0,
    )
    manual_ctx = (
        int(manual_effective_context_tokens)
        if manual_effective_context_tokens is not None
        else _read_runtime_int("UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS", default_ctx)
    )
    context_budget_ratio = _clamp_float(
        float(context_budget_ratio)
        if context_budget_ratio is not None
        else _read_runtime_float("UMS_RETRIEVED_CONTEXT_RATIO", 0.60),
        min_value=0.55,
        max_value=0.65,
    )
    generation_reserve = max(
        256,
        int(generation_tokens_reserve)
        if generation_tokens_reserve is not None
        else _read_runtime_int("UMS_GENERATION_TOKENS_RESERVE", 1024),
    )

    if profile == "manual":
        effective_context_tokens = manual_ctx
    elif profile == "default":
        effective_context_tokens = default_ctx
    else:
        effective_context_tokens = max(adaptive_floor, int(llm_ctx_size * adaptive_util))

    effective_context_tokens = max(1024, min(effective_context_tokens, llm_ctx_size))
    generation_tokens_reserve = min(generation_reserve, max(256, effective_context_tokens // 2))
    retrieved_context_tokens_budget = min(
        max(512, int(effective_context_tokens * context_budget_ratio)),
        max(512, effective_context_tokens - generation_tokens_reserve),
    )

    return {
        "runtime_profile": profile,
        "llm_ctx_size": llm_ctx_size,
        "effective_context_tokens": effective_context_tokens,
        "retrieved_context_tokens_budget": retrieved_context_tokens_budget,
        "generation_tokens_reserve": generation_tokens_reserve,
        "context_budget_ratio": context_budget_ratio,
    }

def resolve_model_path(path_str: str) -> str:
    path = Path(os.path.expanduser(path_str))
    if not path.is_absolute():
        path = BACKEND_ROOT / path_str
    return str(path.resolve())


def _require_configured_model_path(model_id: str, config: Dict[str, Any], *, field_name: str = "path") -> str:
    raw_value = str(config.get(field_name) or "").strip()
    if raw_value:
        return raw_value

    contract = get_model_path_env_contract(model_id)
    canonical_env = str(contract.get("canonical_env") or "").strip()
    legacy_envs = [str(name).strip() for name in (contract.get("legacy_envs") or []) if str(name).strip()]
    expected_envs = [name for name in [canonical_env, *legacy_envs] if name]
    expected_text = ", ".join(expected_envs) if expected_envs else "configured env path"
    raise HTTPException(
        status_code=422,
        detail=f"Model path is not configured for {model_id}. Set {expected_text}.",
    )


def _dynamic_models_registry_path() -> Path:
    return Path(
        os.getenv("UMS_DYNAMIC_MODELS_REGISTRY_PATH", str(BACKEND_ROOT / ".data" / "ums_dynamic_models.json"))
    )


def _get_model_start_lock(model_id: str) -> threading.Lock:
    with _model_start_locks_guard:
        lock = _model_start_locks.get(model_id)
        if lock is None:
            lock = threading.Lock()
            _model_start_locks[model_id] = lock
        return lock


def _normalize_port_value(port: Any) -> Optional[int]:
    try:
        if port is None:
            return None
        return int(port)
    except (TypeError, ValueError):
        return None


def _register_port_owner(model_id: str, port: int) -> None:
    state.setdefault("reserved_ports", set()).add(int(port))
    state.setdefault("port_owners", {})[int(port)] = model_id


def _port_has_foreign_listener(port: int, *, allowed_pid: Optional[int] = None) -> bool:
    listener_pids = _find_listener_pids(int(port))
    if allowed_pid is None:
        return bool(listener_pids)
    return any(int(pid) != int(allowed_pid) for pid in listener_pids)


def _release_port(model_id: str, *, reusable: bool = True) -> Optional[int]:
    port_owners = state.setdefault("port_owners", {})
    released_port: Optional[int] = None
    for port, owner in list(port_owners.items()):
        if owner != model_id:
            continue
        released_port = int(port)
        port_owners.pop(port, None)
        state.setdefault("reserved_ports", set()).discard(int(port))
        if reusable:
            released_dynamic_ports = state.setdefault("released_dynamic_ports", [])
            if int(port) not in released_dynamic_ports:
                released_dynamic_ports.append(int(port))
                released_dynamic_ports.sort()
    if released_port is None:
        config = (state.get("dynamic_models") or {}).get(model_id)
        if config is not None:
            released_port = _normalize_port_value(config.get("port"))
        if released_port is None:
            released_port = _normalize_port_value((state.get("discovered_model_ports") or {}).pop(model_id, None))
        if released_port is not None:
            state.setdefault("reserved_ports", set()).discard(int(released_port))
            if reusable:
                released_dynamic_ports = state.setdefault("released_dynamic_ports", [])
                if int(released_port) not in released_dynamic_ports:
                    released_dynamic_ports.append(int(released_port))
                    released_dynamic_ports.sort()
    return released_port


def _sync_port_registry() -> None:
    reserved_ports = set()
    port_owners: Dict[int, str] = {}
    for model_id, config in STATIC_MODELS_CONFIG.items():
        port = _normalize_port_value(config.get("port"))
        if port is None:
            continue
        reserved_ports.add(port)
        port_owners[port] = model_id
    for model_id, config in (state.get("dynamic_models") or {}).items():
        port = _normalize_port_value(config.get("port"))
        if port is None:
            continue
        reserved_ports.add(port)
        port_owners[port] = model_id
    for model_id, port in (state.get("discovered_model_ports") or {}).items():
        normalized = _normalize_port_value(port)
        if normalized is None:
            continue
        reserved_ports.add(normalized)
        port_owners[normalized] = model_id
    state["reserved_ports"] = reserved_ports
    state["port_owners"] = port_owners
    released_dynamic_ports = [
        int(port)
        for port in (state.get("released_dynamic_ports") or [])
        if int(port) not in reserved_ports
    ]
    released_dynamic_ports.sort()
    state["released_dynamic_ports"] = released_dynamic_ports


def _reserve_port(model_id: str, requested_port: Optional[int] = None) -> int:
    normalized_requested = _normalize_port_value(requested_port)
    port_owners = state.setdefault("port_owners", {})
    existing_port = next((int(port) for port, owner in port_owners.items() if owner == model_id), None)
    if existing_port is not None and normalized_requested is None:
        return existing_port

    if normalized_requested is not None:
        owner = port_owners.get(normalized_requested)
        if owner is not None and owner != model_id:
            raise HTTPException(status_code=409, detail=f"Port {normalized_requested} is already reserved")
        if _port_has_foreign_listener(normalized_requested):
            logger.warning(
                "Requested port %s for %s is already occupied by a foreign listener; allocating fallback dynamic port",
                normalized_requested,
                model_id,
            )
            normalized_requested = None
        else:
            _register_port_owner(model_id, normalized_requested)
            return normalized_requested

    released_dynamic_ports = state.setdefault("released_dynamic_ports", [])
    while released_dynamic_ports:
        candidate = int(released_dynamic_ports.pop(0))
        owner = port_owners.get(candidate)
        if owner is not None and owner != model_id:
            continue
        if _port_has_foreign_listener(candidate):
            continue
        _register_port_owner(model_id, candidate)
        return candidate

    port = _allocate_dynamic_port()
    _register_port_owner(model_id, port)
    return port


def _reassign_model_port(model_id: str, current_port: int) -> int:
    _release_port(model_id, reusable=False)
    next_port = _reserve_port(model_id, None)
    logger.warning("Reassigned %s from occupied port %s to fallback port %s", model_id, current_port, next_port)
    return next_port


def _find_listener_pids(port: int) -> List[int]:
    pids = set()
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN:
                continue
            if not conn.laddr:
                continue
            if conn.laddr.port != port:
                continue
            if conn.pid:
                pids.add(conn.pid)
    except Exception as e:
        logger.warning(f"Failed to inspect listeners on port {port}: {e}")
    return sorted(pids)


def _kill_process_tree(pid: int) -> None:
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return

    children = proc.children(recursive=True)
    for child in children:
        with suppress(psutil.Error):
            child.terminate()
    with suppress(psutil.Error):
        proc.terminate()

    gone, alive = psutil.wait_procs(children + [proc], timeout=3)
    for survivor in alive:
        with suppress(psutil.Error):
            survivor.kill()


def _reap_stale_listener_on_port(port: int, tracked_proc: Optional[subprocess.Popen] = None) -> None:
    tracked_pid = tracked_proc.pid if tracked_proc is not None else None
    for pid in _find_listener_pids(port):
        if tracked_pid and pid == tracked_pid:
            continue
        logger.warning(f"Reaping stale listener on port {port}: pid={pid}")
        _kill_process_tree(pid)


def _is_managed_process_alive(proc: Any) -> bool:
    if isinstance(proc, _RemoteProcess):
        return True
    poll = getattr(proc, "poll", None)
    if not callable(poll):
        return False
    try:
        return poll() is None
    except Exception:
        return False


def _prune_dead_processes() -> List[str]:
    removed: List[str] = []
    processes = state.get("processes") or {}
    for model_id, proc in list(processes.items()):
        if _is_managed_process_alive(proc):
            continue
        logger.warning(f"Pruning dead managed process for {model_id}")
        processes.pop(model_id, None)
        _drop_process_log_buffer(getattr(proc, "pid", None))
        state.setdefault("placements", {}).pop(model_id, None)
        state.setdefault("admission", {}).pop(model_id, None)
        _set_runtime_state(model_id, _RUNTIME_STATE_UNAVAILABLE, reason="process_exited")
        if state.get("active_model") == model_id:
            state["active_model"] = None
        removed.append(model_id)
    return removed


def _cleanup_failed_start_state(model_id: str) -> None:
    proc = state.setdefault("processes", {}).pop(model_id, None)
    _drop_process_log_buffer(getattr(proc, "pid", None) if proc is not None else None)
    state.setdefault("placements", {}).pop(model_id, None)
    state.setdefault("admission", {}).pop(model_id, None)
    if state.get("active_model") == model_id:
        state["active_model"] = None
    _release_port(model_id, reusable=False)


def _resolve_server_model_failover_plan(requested_model_id: str) -> Dict[str, Any]:
    role_key = get_preferred_role_for_model_id(requested_model_id)
    if not role_key:
        return {
            "requested_model_id": requested_model_id,
            "role_key": None,
            "role_label": None,
            "primary_model_id": requested_model_id,
            "fallback_model_id": requested_model_id,
            "fallback_available": False,
            "source": "model_id_only",
            "warning": None,
        }

    try:
        selection = resolve_model_selection(role_key)
    except Exception as exc:
        logger.warning("Failed to resolve model failover plan for %s: %s", requested_model_id, exc)
        return {
            "requested_model_id": requested_model_id,
            "role_key": role_key,
            "role_label": role_key,
            "primary_model_id": requested_model_id,
            "fallback_model_id": requested_model_id,
            "fallback_available": False,
            "source": "resolution_failed",
            "warning": str(exc),
        }

    primary_model_id = str(selection.resolved_model_id or requested_model_id)
    fallback_model_id = str(selection.fallback_model_id or primary_model_id)
    allow_failover = bool(
        selection.fallback_available
        and primary_model_id == requested_model_id
        and fallback_model_id
        and fallback_model_id != requested_model_id
    )
    return {
        "requested_model_id": requested_model_id,
        "role_key": selection.role_key,
        "role_label": selection.role_label,
        "primary_model_id": requested_model_id,
        "registry_primary_model_id": primary_model_id,
        "fallback_model_id": fallback_model_id,
        "fallback_available": allow_failover,
        "source": selection.source,
        "warning": selection.warning,
    }


def _record_model_fallback_event(
    *,
    requested_model_id: str,
    primary_model_id: str,
    fallback_model_id: str,
    used_model_id: str,
    stage: str,
    reason: str,
    role_key: Optional[str] = None,
    role_label: Optional[str] = None,
) -> Dict[str, Any]:
    event = {
        "source": "unified_model_server",
        "requested_model_id": requested_model_id,
        "primary_model_id": primary_model_id,
        "fallback_model_id": fallback_model_id,
        "used_model_id": used_model_id,
        "stage": stage,
        "reason": reason,
        "role_key": role_key,
        "role_label": role_label,
    }
    state["last_fallback_event"] = event
    inc_metric_counter(
        "agent_nav_fallback_events_total",
        labels={
            "component": "ums",
            "fallback": "model_failover",
            "source": "unified_model_server",
            "stage": stage,
        },
    )
    return event


def _should_attempt_model_failover(exc: Exception) -> bool:
    if isinstance(exc, HTTPException):
        if exc.status_code == 429:
            return False
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        runtime_state = str(detail.get("runtime_state") or "").strip().lower()
        if runtime_state in {_RUNTIME_STATE_ERROR_GPU, _RUNTIME_STATE_DEGRADED_CPU, _RUNTIME_STATE_UNAVAILABLE}:
            return False
    return True


def _start_server_with_failover(model_id: str, device_mode: DeviceMode, *, stage: str = "startup") -> str:
    plan = _resolve_server_model_failover_plan(model_id)
    try:
        return _start_server_once(model_id, device_mode)
    except Exception as primary_exc:
        if not plan.get("fallback_available") or not _should_attempt_model_failover(primary_exc):
            raise

        fallback_model_id = str(plan.get("fallback_model_id") or model_id)
        if fallback_model_id == model_id:
            raise

        logger.warning(
            "Model failover for %s -> %s at stage=%s due to: %s",
            model_id,
            fallback_model_id,
            stage,
            primary_exc,
        )
        _cleanup_failed_start_state(model_id)
        _record_model_fallback_event(
            requested_model_id=model_id,
            primary_model_id=model_id,
            fallback_model_id=fallback_model_id,
            used_model_id=fallback_model_id,
            stage=stage,
            reason=str(primary_exc),
            role_key=plan.get("role_key"),
            role_label=plan.get("role_label"),
        )
        try:
            return _start_server_once(fallback_model_id, device_mode)
        except Exception as fallback_exc:
            logger.error(
                "Fallback model start failed for %s -> %s at stage=%s: %s",
                model_id,
                fallback_model_id,
                stage,
                fallback_exc,
            )
            raise fallback_exc

# === Dynamic Model Discovery ===

def get_model_config(model_id: str) -> Optional[Dict[str, Any]]:
    """Возвращает конфиг модели, либо из статики, либо из файловой системы."""
    if model_id in STATIC_MODELS_CONFIG:
        refreshed = _build_static_model_entry(model_id)
        if refreshed is not None:
            STATIC_MODELS_CONFIG[model_id].update(refreshed)
        return STATIC_MODELS_CONFIG[model_id]
    dynamic_models = state.get("dynamic_models") or {}
    if model_id in dynamic_models:
        return dict(dynamic_models[model_id])
    
    # Ищем файл в папке gguf
    for root, dirs, files in os.walk(MODELS_DIR):
        for file in files:
            if file.endswith(".gguf") and os.path.splitext(file)[0] == model_id:
                full_path = os.path.join(root, file)
                discovered_ports = state.setdefault("discovered_model_ports", {})
                assigned_port = discovered_ports.get(model_id)
                if assigned_port is None:
                    assigned_port = _reserve_port(model_id)
                    discovered_ports[model_id] = assigned_port
                return {
                    "type": "gguf",
                    "path": full_path,
                    "ctx_size": 8192,  # Дефолт для новых моделей
                    "gpu_layers": -1,  # Пытаемся все на GPU
                    "port": assigned_port,
                }
    return None

# === Model Management ===

def _stop_model(model_id: str):
    if model_id in state["processes"]:
        with _heavy_model_lifecycle_lock:
            proc = state["processes"].pop(model_id)
            _drop_process_log_buffer(getattr(proc, "pid", None))
            state["placements"].pop(model_id, None)
            state.setdefault("admission", {}).pop(model_id, None)
            logger.info(f"Stopping server for {model_id}...")
            if isinstance(proc, _RemoteProcess):
                logger.info(f"Remote runtime detached for {model_id}")
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=5)
                except:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except:
                        pass
            if state["active_model"] == model_id:
                state["active_model"] = None
    _set_runtime_state(model_id, _RUNTIME_STATE_UNAVAILABLE, reason="stopped")
    if model_id in (state.get("discovered_model_ports") or {}):
        state.get("discovered_model_ports", {}).pop(model_id, None)
        _release_port(model_id, reusable=True)

def _stop_all_servers():
    for model_id in list(state["processes"].keys()):
        _stop_model(model_id)

def _terminate_process(proc: subprocess.Popen) -> None:
    """Останавливает дочерний процесс и его process group."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def _register_process_log_reader(process: subprocess.Popen) -> None:
    stream = getattr(process, "stdout", None)
    if stream is None:
        return
    buffer = deque(maxlen=400)
    _process_log_buffers[int(process.pid)] = buffer

    def _reader() -> None:
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                buffer.append(str(line).rstrip("\n"))
                try:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                except Exception:
                    pass
        except Exception:
            return

    threading.Thread(target=_reader, name=f"ums-log-reader:{process.pid}", daemon=True).start()


def _drop_process_log_buffer(pid: Optional[int]) -> None:
    if pid is None:
        return
    with suppress(Exception):
        _process_log_buffers.pop(int(pid), None)


def _get_recent_process_log_lines(proc: Any, *, limit: int = 120) -> List[str]:
    pid = int(getattr(proc, "pid", 0) or 0)
    if pid <= 0:
        return []
    lines = list(_process_log_buffers.get(pid) or [])
    if limit <= 0:
        return lines
    return lines[-limit:]


def _detect_heavy_runtime_gpu_failure_reason(
    *,
    process: Any,
    placement: Dict[str, Any],
) -> Optional[str]:
    if str((placement or {}).get("placement_mode") or "") == "cpu":
        return None
    log_blob = "\n".join(_get_recent_process_log_lines(process)).lower()
    if not log_blob:
        return None
    for marker in _HEAVY_GPU_RUNTIME_FAILURE_MARKERS:
        if marker in log_blob:
            return marker
    return None


def _build_cpu_isolated_env() -> Dict[str, str]:
    env = os.environ.copy()
    # Ensure CPU-only child processes do not initialize CUDA contexts.
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["NVIDIA_VISIBLE_DEVICES"] = ""
    return env


def _launch_server_process(
    cmd: List[str],
    port: int,
    health_timeout_s: float = 120.0,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.Popen:
    """Запускает сервер и ждет его readiness по /health."""
    process = subprocess.Popen(
        cmd,
        preexec_fn=os.setsid,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _register_process_log_reader(process)
    try:
        start_time = time.time()
        while time.time() - start_time < health_timeout_s:
            if process.poll() is not None:
                raise RuntimeError(f"Server exited with code {process.returncode}")
            try:
                with httpx.Client(timeout=1.0) as client:
                    if client.get(f"http://localhost:{port}/health").status_code == 200:
                        return process
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError("Server start timeout")
    except Exception:
        _terminate_process(process)
        _drop_process_log_buffer(getattr(process, "pid", None))
        raise


def _build_heavy_llama_command(
    *,
    model_path: str,
    config: Dict[str, Any],
    placement: Dict[str, Any],
    device_mode: DeviceMode,
) -> List[str]:
    cmd = [
        "llama-server",
        "-m",
        model_path,
        "--port",
        str(config["port"]),
        "--host",
        "0.0.0.0",
        "-c",
        str(config["ctx_size"]),
        "-ngl",
        str(config["gpu_layers"] if device_mode != DeviceMode.CPU else 0),
    ]
    if placement.get("placement_mode") == "multi-gpu":
        tensor_split = placement.get("tensor_split") or []
        cmd.extend(["--tensor-split", ",".join(str(weight) for weight in tensor_split)])
    if config["type"] == "gguf-vl":
        mmproj_path = str(config.get("mmproj") or "").strip()
        if not mmproj_path:
            raise HTTPException(
                status_code=422,
                detail="Model path is not configured for qwen-vl-8b. Set MODEL_MMPROJ_PATH_VLM or MMPROJ_PATH.",
            )
        cmd.extend(["--mmproj", resolve_model_path(mmproj_path)])
    return cmd


def _launch_heavy_process_with_port_retry(
    *,
    model_id: str,
    config: Dict[str, Any],
    placement: Dict[str, Any],
    model_path: str,
    device_mode: DeviceMode,
) -> tuple[subprocess.Popen, Dict[str, Any]]:
    child_env = _build_cpu_isolated_env() if device_mode == DeviceMode.CPU else None
    cmd = _build_heavy_llama_command(
        model_path=model_path,
        config=config,
        placement=placement,
        device_mode=device_mode,
    )
    logger.info(f"Executing: {' '.join(cmd)}")
    try:
        process = _launch_server_process(cmd, config["port"], env=child_env)
        return process, dict(placement)
    except Exception as exc:
        detail = str(exc)
        fallback_port = _reassign_model_port(model_id, int(config["port"]))
        config["port"] = fallback_port
        retry_placement = dict(placement)
        retry_placement["port"] = fallback_port
        retry_cmd = list(cmd)
        retry_cmd[retry_cmd.index("--port") + 1] = str(fallback_port)
        logger.warning(
            "Retrying %s on fallback port %s after startup failure on %s",
            model_id,
            fallback_port,
            detail,
        )
        process = _launch_server_process(retry_cmd, fallback_port, env=child_env)
        return process, retry_placement


def _build_cpu_degraded_heavy_placement(
    placement: Dict[str, Any],
    *,
    requested_device: str,
) -> Dict[str, Any]:
    degraded = dict(placement)
    degraded["placement_mode"] = "cpu"
    degraded["gpu_indices"] = []
    degraded.pop("tensor_split", None)
    degraded["requested_device"] = requested_device
    degraded["resolved_device"] = DeviceMode.CPU.value
    degraded["admission"] = "degraded_candidate"
    return degraded


def _start_heavy_local_model(
    *,
    model_id: str,
    config: Dict[str, Any],
    placement: Dict[str, Any],
    model_path: str,
    device_mode: DeviceMode,
) -> str:
    requested_device = str(placement.get("requested_device") or device_mode.value)
    runtime_issue: Optional[str] = None
    attempt_count = 2 if device_mode != DeviceMode.CPU else 1

    for attempt_index in range(attempt_count):
        process, effective_placement = _launch_heavy_process_with_port_retry(
            model_id=model_id,
            config=config,
            placement=placement,
            model_path=model_path,
            device_mode=device_mode,
        )
        runtime_issue = _detect_heavy_runtime_gpu_failure_reason(
            process=process,
            placement=effective_placement,
        )
        if runtime_issue is None:
            state["processes"][model_id] = process
            state["placements"][model_id] = effective_placement
            state["active_model"] = model_id
            _set_runtime_state(
                model_id,
                _RUNTIME_STATE_AVAILABLE,
                reason="ok",
                placement=effective_placement,
            )
            return model_id

        logger.warning(
            "Heavy model %s reported GPU runtime failure during startup: %s",
            model_id,
            runtime_issue,
        )
        _terminate_process(process)
        _drop_process_log_buffer(getattr(process, "pid", None))
        _cleanup_failed_start_state(model_id)
        inc_metric_counter(
            "agent_nav_fallback_events_total",
            labels={
                "component": "ums",
                "fallback": "heavy_gpu_retry",
                "source": "unified_model_server",
            },
        )
        if attempt_index + 1 < attempt_count:
            continue

    if device_mode != DeviceMode.CPU and _allow_heavy_cpu_degrade_after_gpu_failure():
        degraded_placement = _build_cpu_degraded_heavy_placement(
            placement,
            requested_device=requested_device,
        )
        degraded_admission = dict((state.get("admission") or {}).get(model_id) or {})
        degraded_admission["requested_device"] = requested_device
        degraded_admission["resolved_device"] = DeviceMode.CPU.value
        degraded_admission["admission"] = "degraded_candidate"
        degraded_admission["effective_gpu_layers"] = 0
        degraded_warnings = list(degraded_admission.get("warnings") or [])
        degraded_warnings.append("heavy llm degraded to cpu after gpu startup failure")
        degraded_admission["warnings"] = degraded_warnings
        state.setdefault("admission", {})[model_id] = degraded_admission
        degraded_config = dict(config)
        degraded_config["gpu_layers"] = 0
        process, effective_placement = _launch_heavy_process_with_port_retry(
            model_id=model_id,
            config=degraded_config,
            placement=degraded_placement,
            model_path=model_path,
            device_mode=DeviceMode.CPU,
        )
        state["processes"][model_id] = process
        state["placements"][model_id] = effective_placement
        state["active_model"] = model_id
        _set_runtime_state(
            model_id,
            _RUNTIME_STATE_DEGRADED_CPU,
            reason=runtime_issue or "gpu_start_failed",
            placement=effective_placement,
        )
        return model_id

    _set_runtime_state(
        model_id,
        _RUNTIME_STATE_ERROR_GPU,
        reason=runtime_issue or "gpu_start_failed",
        placement=placement,
    )
    _cleanup_failed_start_state(model_id)
    raise HTTPException(
        status_code=503,
        detail=_build_runtime_unavailable_payload(
            requested_model_id=model_id,
            runtime_state=_RUNTIME_STATE_ERROR_GPU,
            reason=runtime_issue or "gpu_start_failed",
        ),
    )


def _start_server_once(model_id: str, device_mode: DeviceMode):
    with _get_model_start_lock(model_id):
        device_mode = _resolve_component_device_mode(model_id, device_mode)
        config = get_model_config(model_id)
        if not config:
            raise HTTPException(status_code=404, detail=f"Model {model_id} not found in filesystem.")
        config = dict(config)
        use_vllm_backend = _should_use_vllm_backend(config)
        model_path = ""
        if not use_vllm_backend:
            model_path = resolve_model_path(_require_configured_model_path(model_id, config))
        is_heavy = config["type"] in ["gguf", "gguf-vl"]

        existing_proc = state["processes"].get(model_id)
        if existing_proc is not None:
            if existing_proc.poll() is None:
                if (state.get("runtime_states") or {}).get(model_id) is None:
                    _set_runtime_state(
                        model_id,
                        _RUNTIME_STATE_AVAILABLE,
                        reason="already_running",
                        placement=(state.get("placements") or {}).get(model_id),
                    )
                return model_id  # Уже работает
            state["processes"].pop(model_id, None)
            state["placements"].pop(model_id, None)

        available_gpus = _get_gpu_info()
        n_gpu = len(available_gpus)
        placement = _build_model_placement_plan(
            model_id=model_id,
            config=config,
            device_mode=device_mode,
            available_gpus=available_gpus,
        )
        assigned_port = _reserve_port(model_id, config.get("port"))
        config["port"] = assigned_port
        placement = dict(placement)
        placement["port"] = assigned_port
        state.setdefault("admission", {})

        if use_vllm_backend:
            state["admission"][model_id] = {
                "requested_device": str(device_mode),
                "resolved_device": "remote",
                "admission": "ready",
                "warnings": [],
            }
            with _heavy_model_lifecycle_lock:
                active_heavy = None
                for pid in state["processes"]:
                    p_config = get_model_config(pid)
                    if p_config and p_config["type"] in ["gguf", "gguf-vl"]:
                        active_heavy = pid
                        break
                if active_heavy and active_heavy != model_id:
                    logger.info(f"Detaching {active_heavy} before activating remote vLLM model {model_id}")
                    _stop_model(active_heavy)
                _ensure_vllm_backend(model_id)
                state["processes"][model_id] = _RemoteProcess()
                vllm_placement = _build_vllm_placement(model_id, config)
                vllm_placement["port"] = assigned_port
                state["placements"][model_id] = vllm_placement
                state["active_model"] = model_id
                _set_runtime_state(model_id, _RUNTIME_STATE_AVAILABLE, reason="ok", placement=vllm_placement)
                return model_id

        if is_heavy:
            token_budget = int((state.get("runtime_budget") or {}).get("effective_context_tokens") or config.get("ctx_size") or 4096)
            llm_admission = _resolve_llm_admission(
                model_id=model_id,
                config=config,
                requested_device=device_mode,
                available_gpus=available_gpus,
                token_budget=token_budget,
            )
            state["admission"][model_id] = dict(llm_admission)
            placement.update(
                {
                    "requested_device": llm_admission["requested_device"],
                    "resolved_device": llm_admission["resolved_device"],
                    "admission": llm_admission["admission"],
                    "estimated_vram_gb": llm_admission["estimated_vram_gb"],
                    "available_vram_gb": llm_admission["available_vram_gb"],
                    "effective_token_budget": llm_admission["effective_token_budget"],
                    "warnings": list(llm_admission.get("warnings") or []),
                }
            )
            if llm_admission["admission"] == "requires_degraded":
                _set_runtime_state(
                    model_id,
                    _RUNTIME_STATE_UNAVAILABLE,
                    reason=f"llm_admission_requires_degraded:{llm_admission['requested_device']}",
                    placement=placement,
                )
                raise HTTPException(
                    status_code=503,
                    detail=_build_runtime_unavailable_payload(
                        requested_model_id=model_id,
                        runtime_state=_RUNTIME_STATE_UNAVAILABLE,
                        reason=f"llm_admission_requires_degraded:{llm_admission['requested_device']}",
                    ),
                )
            config["gpu_layers"] = int(llm_admission.get("effective_gpu_layers", config.get("gpu_layers", -1)))
            device_mode = DeviceMode(llm_admission["resolved_device"])
        else:
            embed_admission = _resolve_embedding_admission(
                model_id=model_id,
                requested_device=device_mode,
                resolved_device=str(placement.get("resolved_device") or "cpu"),
                available_gpus=available_gpus,
                fallback_applied=False,
            )
            state["admission"][model_id] = dict(embed_admission)
            placement.update(
                {
                    "requested_device": embed_admission["requested_device"],
                    "resolved_device": embed_admission["resolved_device"],
                    "admission": embed_admission["admission"],
                    "warnings": list(embed_admission.get("warnings") or []),
                }
            )

        if is_heavy:
            with _heavy_model_lifecycle_lock:
                active_heavy = None
                for pid in state["processes"]:
                    p_config = get_model_config(pid)
                    if p_config and p_config["type"] in ["gguf", "gguf-vl"]:
                        active_heavy = pid
                        break
                if active_heavy and active_heavy != model_id:
                    logger.info(f"Stopping {active_heavy} to free memory for {model_id}")
                    _stop_model(active_heavy)
                _reap_stale_listener_on_port(config["port"], tracked_proc=existing_proc)
                _set_runtime_state(model_id, _RUNTIME_STATE_LOADING, reason="startup", placement=placement)
                return _start_heavy_local_model(
                    model_id=model_id,
                    config=config,
                    placement=placement,
                    model_path=model_path,
                    device_mode=device_mode,
                )

        _reap_stale_listener_on_port(config["port"], tracked_proc=existing_proc)
        preferred_device = str(placement.get("device_arg") or "cpu")
        device_candidates = [preferred_device]
        if preferred_device.startswith("cuda"):
            device_candidates.append("cpu")

        last_error = None
        for idx, device_arg in enumerate(device_candidates):
            child_env = _build_cpu_isolated_env() if str(device_arg) == "cpu" else None
            cmd = [
                sys.executable,
                str(Path(__file__).parent / "st_server.py"),
                "--model", model_path,
                "--port", str(config["port"]),
                "--device", device_arg,
            ]
            logger.info(f"Executing: {' '.join(cmd)}")
            try:
                process = _launch_server_process(cmd, config["port"], env=child_env)
                state["processes"][model_id] = process
                state["placements"][model_id] = _placement_with_device_arg(placement, device_arg)
                state["admission"][model_id] = _resolve_embedding_admission(
                    model_id=model_id,
                    requested_device=device_mode,
                    resolved_device="gpu" if str(device_arg).startswith("cuda") else "cpu",
                    available_gpus=available_gpus,
                    fallback_applied=(idx > 0 and str(device_arg) == "cpu"),
                )
                _set_runtime_state(
                    model_id,
                    _RUNTIME_STATE_AVAILABLE,
                    reason="ok",
                    placement=state["placements"][model_id],
                )
                return model_id
            except Exception as e:
                detail = str(e)
                bind_like_error = (
                    "couldn't bind HTTP server socket" in detail
                    or "HTTP server error" in detail
                    or _port_has_foreign_listener(int(config["port"]))
                )
                if bind_like_error:
                    fallback_port = _reassign_model_port(model_id, int(config["port"]))
                    config["port"] = fallback_port
                    placement["port"] = fallback_port
                    retry_cmd = list(cmd)
                    retry_cmd[retry_cmd.index("--port") + 1] = str(fallback_port)
                    try:
                        process = _launch_server_process(retry_cmd, fallback_port, env=child_env)
                        state["processes"][model_id] = process
                        state["placements"][model_id] = _placement_with_device_arg(placement, device_arg)
                        state["admission"][model_id] = _resolve_embedding_admission(
                            model_id=model_id,
                            requested_device=device_mode,
                            resolved_device="gpu" if str(device_arg).startswith("cuda") else "cpu",
                            available_gpus=available_gpus,
                            fallback_applied=(idx > 0 and str(device_arg) == "cpu"),
                        )
                        _set_runtime_state(
                            model_id,
                            _RUNTIME_STATE_AVAILABLE,
                            reason="ok",
                            placement=state["placements"][model_id],
                        )
                        return model_id
                    except Exception:
                        pass
                last_error = e
                if idx < len(device_candidates) - 1:
                    logger.warning(
                        f"ST server startup failed on {device_arg}, retrying on {device_candidates[idx + 1]}: {e}"
                    )
                    inc_metric_counter(
                        "agent_nav_fallback_events_total",
                        labels={
                            "component": "ums",
                            "fallback": "st_start_cpu_retry",
                            "source": "unified_model_server",
                        },
                    )
                    continue
                logger.error(f"Start failed: {e}")
                raise
        raise RuntimeError(f"Failed to start {model_id}: {last_error}")


def _start_server(model_id: str, device_mode: DeviceMode, *, stage: str = "startup") -> str:
    return _start_server_with_failover(model_id, device_mode, stage=stage)


def _build_infer_readiness_payload(
    *,
    requested_model_id: str,
    infer_ready: bool,
    ready_model_id: Optional[str] = None,
    reason: str = "ok",
    runtime_state: Optional[str] = None,
) -> Dict[str, Any]:
    effective_runtime_state = runtime_state or (
        _RUNTIME_STATE_AVAILABLE
        if infer_ready
        else (_RUNTIME_STATE_UNAVAILABLE if reason == "model_not_found" else _RUNTIME_STATE_LOADING)
    )
    return {
        "status": "ready" if infer_ready and effective_runtime_state == _RUNTIME_STATE_AVAILABLE else effective_runtime_state,
        "infer_ready": infer_ready,
        "requested_model_id": requested_model_id,
        "ready_model_id": ready_model_id,
        "backend_mode": _resolve_backend_mode(),
        "fallback_used": bool(ready_model_id and ready_model_id != requested_model_id),
        "reason": reason,
        "runtime_state": effective_runtime_state,
    }

# === API ===

class InferRequest(BaseModel):
    model_id: str
    payload: Dict[str, Any]
    device_mode: Optional[DeviceMode] = DeviceMode.HYBRID
    stream: bool = False

class EmbeddingRequest(BaseModel):
    """OpenAI-compatible /v1/embeddings request."""
    input: Any  # str | List[str]
    model: str = "labse-embedding"
    encoding_format: Optional[str] = "float"


class ModelControlRequest(BaseModel):
    device_mode: Optional[DeviceMode] = None


class ModelRegistrationRequest(BaseModel):
    model_id: str
    type: str
    path: str
    port: Optional[int] = None
    ctx_size: Optional[int] = None
    gpu_layers: Optional[int] = None
    mmproj: Optional[str] = None
    replace: bool = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    # T3.7: Hardware profiling при старте
    state["dynamic_models"] = _load_dynamic_models_registry()
    _align_dynamic_port_counter()
    state["device_mode"] = _resolve_runtime_device_mode()
    try:
        sys.path.insert(0, str(BACKEND_ROOT))
        from services.hardware import HardwareProfiler, TierSelector
        profiler = HardwareProfiler()
        profile = profiler.detect()
        selector = TierSelector()
        tier_config = selector.select(profile)
        state["system_profile"] = profile
        state["tier_config"] = tier_config
        state["runtime_budget"] = resolve_runtime_budget(ctx_size=tier_config.llm_ctx_size)
        logger.info(f"Hardware detected:\n{profile}")
        logger.info(f"Selected: {tier_config}")
        logger.info(f"Runtime budget: {state['runtime_budget']}")

        # Обновляем конфиг модели из tier_config
        configured_tier_model_id = str(getattr(tier_config, "llm_model_id", "") or "").strip()
        target_llm_model_id = (
            configured_tier_model_id if configured_tier_model_id in STATIC_MODELS_CONFIG else _default_heavy_model_id()
        )
        if target_llm_model_id in STATIC_MODELS_CONFIG:
            tier_ctx_size = int(getattr(tier_config, "llm_ctx_size", STATIC_MODELS_CONFIG[target_llm_model_id]["ctx_size"]))
            tier_gpu_layers = int(getattr(tier_config, "llm_gpu_layers", STATIC_MODELS_CONFIG[target_llm_model_id]["gpu_layers"]))
            STATIC_MODELS_CONFIG[target_llm_model_id]["ctx_size"] = tier_ctx_size
            if tier_gpu_layers != -1:
                STATIC_MODELS_CONFIG[target_llm_model_id]["gpu_layers"] = tier_gpu_layers
            if state["device_mode"] != DeviceMode.CPU and STATIC_MODELS_CONFIG[target_llm_model_id]["gpu_layers"] == 0:
                logger.warning(
                    "DEVICE_MODE=%s overrides tier-selected cpu-only gpu_layers=0; forcing %s gpu_layers=-1",
                    state["device_mode"].value,
                    target_llm_model_id,
                )
                STATIC_MODELS_CONFIG[target_llm_model_id]["gpu_layers"] = -1
            logger.info(
                "Updated %s: ctx=%s, gpu_layers=%s",
                target_llm_model_id,
                tier_ctx_size,
                tier_gpu_layers,
            )
    except Exception as e:
        logger.warning(f"Hardware profiling failed, using defaults: {e}")
        state["system_profile"] = None
        state["tier_config"] = None
        state["runtime_budget"] = resolve_runtime_budget()

    # Предзагрузка Qwen LLM — убирает задержку перед первым запросом
    for model_id in _preload_sequence():
        try:
            logger.info("Preloading %s...", model_id)
            started_model_id = _start_server(model_id, state["device_mode"], stage="startup")
            logger.info("%s preloaded successfully", started_model_id)
        except Exception as e:
            logger.warning(f"Failed to preload {model_id}: {e}")

    yield
    _stop_all_servers()

app = FastAPI(title="Unified Model Server", version="3.0.0", lifespan=lifespan)

# Concurrency control: llama-server — ограниченный параллелизм, embeddings допускают более широкий.
_llm_semaphore = asyncio.Semaphore(1)
_embed_semaphore = asyncio.Semaphore(4)
_concurrency_controls = {"llm_limit": 1, "embed_limit": 4}


def _update_runtime_observability_metrics() -> None:
    set_ums_runtime_metrics(
        running_models=len(state.get("processes") or {}),
        active_heavy_model_present=bool(state.get("active_model")),
    )


app.add_middleware(
    ObservabilityMiddleware,
    service_name="ums",
    logger=logger,
    post_response_hook=_update_runtime_observability_metrics,
)


def _read_concurrency_limit(name: str, default: int, aliases: Optional[List[str]] = None) -> int:
    candidate_names = [name, *(aliases or [])]
    for candidate in candidate_names:
        raw = os.getenv(candidate)
        if raw is None:
            continue
        try:
            return max(1, int(raw))
        except ValueError:
            continue
    return default


def _get_concurrency_inflight_snapshot() -> Dict[str, int]:
    llm_limit = int(_concurrency_controls["llm_limit"])
    embed_limit = int(_concurrency_controls["embed_limit"])
    llm_available = max(0, int(getattr(_llm_semaphore, "_value", llm_limit)))
    embed_available = max(0, int(getattr(_embed_semaphore, "_value", embed_limit)))
    return {
        "llm_inflight": max(0, llm_limit - llm_available),
        "llm_available": min(llm_limit, llm_available),
        "llm_saturated": llm_available <= 0,
        "embedding_inflight": max(0, embed_limit - embed_available),
        "embedding_available": min(embed_limit, embed_available),
        "embedding_saturated": embed_available <= 0,
    }


def _get_concurrency_policy_snapshot(
    policy: Optional[Dict[str, Any]] = None,
    *,
    use_live_limits: bool = True,
) -> Dict[str, Any]:
    base_policy = dict(policy or _resolve_concurrency_policy())
    if use_live_limits:
        base_policy["llm_max_concurrency"] = int(_concurrency_controls["llm_limit"])
        base_policy["embed_max_concurrency"] = int(_concurrency_controls["embed_limit"])
    return {
        **base_policy,
        **_get_concurrency_inflight_snapshot(),
    }


def _resolve_concurrency_policy() -> Dict[str, Any]:
    backend_mode = _resolve_backend_mode()
    llm_admission_mode = "upstream_admission" if backend_mode == "vllm" else "bounded_wait"
    return {
        "llm_max_concurrency": _read_concurrency_limit(
            "UMS_LLM_MAX_CONCURRENCY",
            1,
            aliases=["UMS_LLM_CONCURRENCY"],
        ),
        "embed_max_concurrency": _read_concurrency_limit(
            "UMS_EMBED_MAX_CONCURRENCY",
            4,
            aliases=["UMS_EMBED_CONCURRENCY"],
        ),
        "acquire_timeout_s": max(0.1, _read_runtime_float("UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S", 5.0)),
        "fail_fast_on_saturation": _read_runtime_bool("UMS_FAIL_FAST_ON_SATURATION", False),
        "backend_mode": backend_mode,
        "llm_admission_mode": llm_admission_mode,
    }


def _refresh_concurrency_controls() -> Dict[str, Any]:
    global _llm_semaphore, _embed_semaphore
    policy = _resolve_concurrency_policy()
    llm_limit = int(policy["llm_max_concurrency"])
    embed_limit = int(policy["embed_max_concurrency"])
    if _concurrency_controls["llm_limit"] != llm_limit:
        _llm_semaphore = asyncio.Semaphore(llm_limit)
        _concurrency_controls["llm_limit"] = llm_limit
    if _concurrency_controls["embed_limit"] != embed_limit:
        _embed_semaphore = asyncio.Semaphore(embed_limit)
        _concurrency_controls["embed_limit"] = embed_limit
    state["concurrency_policy"] = _get_concurrency_policy_snapshot(policy, use_live_limits=False)
    return dict(state["concurrency_policy"])


@async_cm
async def _acquire_runtime_slot(sem: asyncio.Semaphore, kind: str):
    policy = await _reserve_runtime_slot(sem, kind)
    try:
        yield policy
    finally:
        _release_runtime_slot(sem)


async def _reserve_runtime_slot(sem: asyncio.Semaphore, kind: str) -> Dict[str, Any]:
    policy = _refresh_concurrency_controls()
    timeout_s = float(policy["acquire_timeout_s"])
    queue_wait_started_at = time.perf_counter()
    fail_fast = bool(policy.get("fail_fast_on_saturation"))
    if kind == "llm" and str(policy.get("llm_admission_mode") or "") == "upstream_admission":
        fail_fast = True
    if fail_fast and getattr(sem, "_value", 0) <= 0:
        inc_metric_counter(
            "agent_nav_ums_concurrency_saturation_total",
            labels={"kind": kind, "mode": "fail_fast"},
        )
        raise HTTPException(
            status_code=429,
            detail=_build_concurrency_saturation_detail(
                kind=kind,
                reason="fail_fast_saturated",
                queue_wait_ms=0,
                policy=policy,
            ),
        )
    try:
        await asyncio.wait_for(sem.acquire(), timeout=timeout_s)
    except TimeoutError as exc:
        inc_metric_counter(
            "agent_nav_ums_concurrency_saturation_total",
            labels={"kind": kind, "mode": "timeout"},
        )
        raise HTTPException(
            status_code=429,
            detail=_build_concurrency_saturation_detail(
                kind=kind,
                reason="queue_timeout",
                queue_wait_ms=int((time.perf_counter() - queue_wait_started_at) * 1000),
                policy=policy,
            ),
        ) from exc
    state["concurrency_policy"] = _get_concurrency_policy_snapshot(policy, use_live_limits=False)
    acquired_policy = dict(state["concurrency_policy"])
    acquired_policy["queue_wait_ms"] = int((time.perf_counter() - queue_wait_started_at) * 1000)
    return acquired_policy


def _build_concurrency_saturation_detail(
    *,
    kind: str,
    reason: str,
    queue_wait_ms: int,
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "status": "busy",
        "kind": kind,
        "reason": reason,
        "message": "Модель занята предыдущим тяжёлым запросом. Дождитесь освобождения слота или остановите активный запуск.",
        "retryable": True,
        "queue_wait_ms": max(0, int(queue_wait_ms)),
        "backend_mode": str(policy.get("backend_mode") or _resolve_backend_mode()),
        "llm_admission_mode": str(policy.get("llm_admission_mode") or ""),
        "llm_max_concurrency": int(policy.get("llm_max_concurrency") or _concurrency_controls["llm_limit"]),
        "embed_max_concurrency": int(policy.get("embed_max_concurrency") or _concurrency_controls["embed_limit"]),
    }


def _release_runtime_slot(sem: asyncio.Semaphore) -> None:
    sem.release()
    state["concurrency_policy"] = _get_concurrency_policy_snapshot()


async def _proxy_sse_stream(
    url: str,
    payload: Dict[str, Any],
    sem: asyncio.Semaphore,
    *,
    slot_pre_acquired: bool = False,
    headers: Optional[Dict[str, str]] = None,
) -> AsyncGenerator[bytes, None]:
    """
    Проксирует upstream SSE через очередь и отдельную producer-task.

    Это разрывает хрупкую связку:
    StreamingResponse consumer task -> httpx stream context
    и гарантирует, что upstream stream закрывается в том же task, где был открыт.
    """
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    done_chunk_forwarded = False

    async def producer() -> None:
        owns_slot = False
        released_slot = False

        def release_slot() -> None:
            nonlocal released_slot
            if released_slot:
                return
            if slot_pre_acquired or owns_slot:
                _release_runtime_slot(sem)
                released_slot = True

        try:
            if not slot_pre_acquired:
                await _reserve_runtime_slot(sem, "stream")
                owns_slot = True
            async with httpx.AsyncClient(timeout=_resolve_infer_timeout_s(), headers=headers) as stream_client:
                async with stream_client.stream("POST", url, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            stripped = line.strip()
                            if stripped == "data: [DONE]":
                                release_slot()
                                await queue.put(("chunk", b"data: [DONE]\n\n"))
                                return
                            await queue.put(("chunk", f"{line}\n\n".encode()))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Upstream SSE proxy error for {url}: {exc}")
            await queue.put(("error", exc))
        finally:
            release_slot()
            await queue.put(("done", None))

    producer_task = asyncio.create_task(producer(), name=f"ums-proxy:{os.path.basename(url)}")
    try:
        while True:
            kind, value = await queue.get()
            if kind == "chunk":
                if value == b"data: [DONE]\n\n":
                    done_chunk_forwarded = True
                yield value
                continue
            if kind == "error":
                return
            return
    finally:
        if not producer_task.done() and not done_chunk_forwarded:
            producer_task.cancel()
        with suppress(asyncio.CancelledError):
            await producer_task


def _discover_available_model_ids() -> List[str]:
    model_ids = list(STATIC_MODELS_CONFIG.keys())
    model_ids.extend((state.get("dynamic_models") or {}).keys())
    if MODELS_DIR.exists():
        for root, _, files in os.walk(MODELS_DIR):
            for file in files:
                if file.endswith(".gguf"):
                    model_id = os.path.splitext(file)[0]
                    if model_id not in model_ids:
                        model_ids.append(model_id)
    return sorted(model_ids)


def _build_model_view(model_id: str) -> Dict[str, Any]:
    _prune_dead_processes()
    config = get_model_config(model_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    resolved_path = resolve_model_path(config["path"]) if str(config.get("path") or "").strip() else ""
    backend = "vllm" if _should_use_vllm_backend(config) else "local"
    return {
        "model_id": model_id,
        "type": config["type"],
        "path": resolved_path,
        "resolved_path": resolved_path,
        "backend": backend,
        "port": config["port"],
        "running": model_id in state["processes"],
        "active": state.get("active_model") == model_id,
        "placement": copy.deepcopy((state.get("placements") or {}).get(model_id)),
        "admission": copy.deepcopy((state.get("admission") or {}).get(model_id)),
        "runtime_state": _get_runtime_state(model_id),
    }


def _ensure_dynamic_registry_parent() -> None:
    _dynamic_models_registry_path().parent.mkdir(parents=True, exist_ok=True)


def _load_dynamic_models_registry() -> Dict[str, Dict[str, Any]]:
    registry_path = _dynamic_models_registry_path()
    if not registry_path.exists():
        return {}
    try:
        with registry_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as e:
        logger.warning(f"Failed to load dynamic models registry: {e}")
        return {}
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, dict):
        return {}
    next_dynamic_port = payload.get("next_dynamic_port") if isinstance(payload, dict) else None
    if isinstance(next_dynamic_port, int):
        state["dynamic_ports"] = max(int(state.get("dynamic_ports") or 8100), next_dynamic_port)
    loaded: Dict[str, Dict[str, Any]] = {}
    for model_id, config in models.items():
        if not isinstance(model_id, str) or not isinstance(config, dict):
            continue
        loaded[model_id] = dict(config)
    state["dynamic_models"] = loaded
    _sync_port_registry()
    return loaded


def _save_dynamic_models_registry() -> None:
    _ensure_dynamic_registry_parent()
    payload = {
        "models": copy.deepcopy(state.get("dynamic_models") or {}),
        "next_dynamic_port": int(state.get("dynamic_ports") or 8100),
    }
    with _dynamic_models_registry_path().open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)


def _align_dynamic_port_counter() -> None:
    _sync_port_registry()
    reserved = set(state.get("reserved_ports") or set())
    state["dynamic_ports"] = max(8100, max(reserved, default=8099) + 1)


def _normalize_registered_model_type(raw_type: str) -> str:
    value = str(raw_type or "").strip().lower()
    if value not in {"gguf", "gguf-vl", "st"}:
        raise HTTPException(status_code=422, detail="Unsupported model type")
    return value


def _resolve_registration_path(path_str: str, *, expected_type: Optional[str] = None) -> str:
    resolved = resolve_model_path(path_str)
    resolved_path = Path(resolved)
    if not resolved_path.exists():
        raise HTTPException(status_code=422, detail=f"Model path not found: {resolved}")
    if expected_type in {"gguf", "gguf-vl"} and (not resolved_path.is_file() or resolved_path.suffix != ".gguf"):
        raise HTTPException(status_code=422, detail="GGUF models require an existing .gguf file")
    if expected_type == "st" and not resolved_path.is_dir():
        raise HTTPException(status_code=422, detail="ST models require an existing directory")
    return resolved


def _allocate_dynamic_port() -> int:
    _sync_port_registry()
    reserved = set(state.get("reserved_ports") or set())
    released_dynamic_ports = state.setdefault("released_dynamic_ports", [])
    if released_dynamic_ports:
        while released_dynamic_ports:
            port = int(released_dynamic_ports.pop(0))
            if port in reserved or _port_has_foreign_listener(port):
                continue
            state["dynamic_ports"] = max(int(state.get("dynamic_ports") or 8100), port + 1)
            return port
    port = int(state.get("dynamic_ports") or 8100)
    while port in reserved or _port_has_foreign_listener(port):
        port += 1
    state["dynamic_ports"] = port + 1
    return port


def _register_dynamic_model(request: ModelRegistrationRequest) -> Dict[str, Any]:
    model_id = request.model_id.strip()
    if not model_id:
        raise HTTPException(status_code=422, detail="model_id is required")
    if model_id in STATIC_MODELS_CONFIG:
        raise HTTPException(status_code=409, detail="Static models cannot be re-registered")

    dynamic_models = state.setdefault("dynamic_models", {})
    if model_id in dynamic_models and not request.replace:
        raise HTTPException(status_code=409, detail=f"Model {model_id} is already registered")

    model_type = _normalize_registered_model_type(request.type)
    resolved_path = _resolve_registration_path(request.path, expected_type=model_type)
    if request.port is not None:
        _sync_port_registry()
        requested_port = int(request.port)
        owner = (state.get("port_owners") or {}).get(requested_port)
        if owner is not None and owner != model_id:
            raise HTTPException(status_code=409, detail=f"Port {request.port} is already reserved")
    config: Dict[str, Any] = {
        "type": model_type,
        "path": resolved_path,
        "port": int(request.port) if request.port is not None else _allocate_dynamic_port(),
    }
    if request.port is not None:
        state["dynamic_ports"] = max(int(state.get("dynamic_ports") or 8100), int(request.port) + 1)
    if request.ctx_size is not None:
        config["ctx_size"] = int(request.ctx_size)
    if request.gpu_layers is not None:
        config["gpu_layers"] = int(request.gpu_layers)
    if model_type == "gguf-vl":
        if request.mmproj is None:
            raise HTTPException(status_code=422, detail="gguf-vl models require mmproj")
        config["mmproj"] = _resolve_registration_path(request.mmproj, expected_type="gguf")
    elif request.mmproj is not None:
        config["mmproj"] = _resolve_registration_path(request.mmproj)

    dynamic_models[model_id] = config
    _release_port(model_id, reusable=False)
    _register_port_owner(model_id, int(config["port"]))
    _save_dynamic_models_registry()
    return _build_model_view(model_id)


def _unregister_dynamic_model(model_id: str) -> None:
    if model_id in STATIC_MODELS_CONFIG:
        raise HTTPException(status_code=400, detail="Static models cannot be unregistered")
    dynamic_models = state.get("dynamic_models") or {}
    if model_id not in dynamic_models:
        raise HTTPException(status_code=404, detail=f"Dynamic model {model_id} not found")
    _sync_port_registry()
    if model_id in state.get("processes", {}):
        _stop_model(model_id)
    dynamic_models.pop(model_id, None)
    _release_port(model_id, reusable=True)
    _save_dynamic_models_registry()

@app.post("/infer")
async def infer(request: InferRequest):
    try:
        policy = _refresh_concurrency_controls()
        config = get_model_config(request.model_id)
        if not config:
            raise HTTPException(status_code=404, detail=f"Model {request.model_id} not found")
        sem = _embed_semaphore if config["type"] == "st" else _llm_semaphore
        slot_kind = "embedding" if config["type"] == "st" else "llm"

        is_chat = "messages" in request.payload
        url = _build_infer_url(config, is_chat=is_chat)
        headers = _build_upstream_headers(config)

        payload = _build_infer_payload(request.model_id, config, request.payload)
        if request.stream: payload["stream"] = True

        if request.stream:
            policy = await _reserve_runtime_slot(sem, "stream")
            try:
                started_model_id = await asyncio.to_thread(
                    _start_server,
                    request.model_id,
                    request.device_mode or state["device_mode"],
                    stage="infer",
                )
            except Exception:
                _release_runtime_slot(sem)
                raise
            started_config = get_model_config(started_model_id)
            if not started_config:
                raise HTTPException(status_code=404, detail=f"Model {started_model_id} not found")
            url = _build_infer_url(started_config, is_chat=is_chat)
            headers = _build_upstream_headers(started_config)
            payload = _build_infer_payload(started_model_id, started_config, request.payload)
            payload["stream"] = True
            return StreamingResponse(
                _proxy_sse_stream(url, payload, sem, slot_pre_acquired=True, headers=headers),
                media_type="text/event-stream",
                headers={"X-UMS-Concurrency-Policy": json.dumps(policy, sort_keys=True)},
            )
        else:
            async with _acquire_runtime_slot(sem, slot_kind) as policy:
                started_model_id = await asyncio.to_thread(
                    _start_server,
                    request.model_id,
                    request.device_mode or state["device_mode"],
                    stage="infer",
                )
                started_config = get_model_config(started_model_id)
                if not started_config:
                    raise HTTPException(status_code=404, detail=f"Model {started_model_id} not found")
                url = _build_infer_url(started_config, is_chat=is_chat)
                headers = _build_upstream_headers(started_config)
                payload = _build_infer_payload(started_model_id, started_config, request.payload)
                async with httpx.AsyncClient(timeout=_resolve_infer_timeout_s(), headers=headers) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    return {
                        "status": "success",
                        "model": started_model_id,
                        "result": resp.json(),
                        "concurrency_policy": policy,
                    }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.error(f"Inference Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/models")
async def list_available_models():
    _prune_dead_processes()
    return {
        "models": [_build_model_view(model_id) for model_id in _discover_available_model_ids()],
        "active_heavy_model": state.get("active_model"),
    }


@app.get("/models/running")
async def list_running_models():
    _prune_dead_processes()
    running_ids = list(state.get("processes") or {})
    return {
        "active_heavy_model": state.get("active_model"),
        "running_model_ids": running_ids,
        "running_models": [_build_model_view(model_id) for model_id in running_ids],
        "placements": copy.deepcopy(state.get("placements") or {}),
        "runtime_states": copy.deepcopy(state.get("runtime_states") or {}),
    }


@app.post("/models/register")
async def register_model(request: ModelRegistrationRequest):
    model = _register_dynamic_model(request)
    return {
        "status": "success",
        "action": "register",
        "model": model,
    }


@app.delete("/models/{model_id}/registration")
async def unregister_model(model_id: str):
    _unregister_dynamic_model(model_id)
    return {
        "status": "success",
        "action": "unregister",
        "model_id": model_id,
    }


@app.post("/models/{model_id}/preload")
async def preload_model(model_id: str, request: ModelControlRequest):
    config = get_model_config(model_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    started_model_id = await asyncio.to_thread(
        _start_server,
        model_id,
        request.device_mode or state["device_mode"],
        stage="preload",
    )
    return {
        "status": "success",
        "action": "preload",
        "model": _build_model_view(started_model_id),
    }


@app.post("/models/{model_id}/activate")
async def activate_model(model_id: str, request: ModelControlRequest):
    config = get_model_config(model_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    started_model_id = await asyncio.to_thread(
        _start_server,
        model_id,
        request.device_mode or state["device_mode"],
        stage="activate",
    )
    return {
        "status": "success",
        "action": "activate",
        "model": _build_model_view(started_model_id),
    }


@app.post("/models/{model_id}/stop")
async def stop_model(model_id: str):
    config = get_model_config(model_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    await asyncio.to_thread(_stop_model, model_id)
    return {
        "status": "success",
        "action": "stop",
        "model": _build_model_view(model_id),
    }


@app.get("/metrics")
async def metrics():
    _update_runtime_observability_metrics()
    return PlainTextResponse(render_metrics_text(), media_type="text/plain; version=0.0.4; charset=utf-8")

@app.get("/status")
async def get_status():
    _prune_dead_processes()
    tier_info = None
    if state.get("tier_config"):
        from services.hardware.tier_selector import describe_rag_mode

        tc = state["tier_config"]
        tier_info = {
            "tier": tc.tier,
            "rag_mode": tc.rag_mode,
            "rag_mode_label": describe_rag_mode(tc.rag_mode),
            "embedding_backend": tc.embedding_backend,
        }
    runtime_budget = resolve_runtime_budget()
    _update_runtime_observability_metrics()
    concurrency_policy = _get_concurrency_policy_snapshot()
    return {
        "active_heavy_model": state["active_model"],
        "running": list(state["processes"].keys()),
        "placements": dict(state.get("placements") or {}),
        "admission": copy.deepcopy(state.get("admission") or {}),
        "runtime_states": copy.deepcopy(state.get("runtime_states") or {}),
        "last_fallback_event": copy.deepcopy(state.get("last_fallback_event")),
        "backend_mode": _resolve_backend_mode(),
        "prompt_cache_policy": _resolve_prompt_cache_policy(
            get_model_config(state.get("active_model") or _default_heavy_model_id())
            or STATIC_MODELS_CONFIG.get(_default_heavy_model_id(), {})
        ),
        "vram_free_gb": _get_available_vram(),
        "tier": tier_info,
        "runtime_profile": runtime_budget["runtime_profile"],
        "effective_context_tokens": runtime_budget["effective_context_tokens"],
        "retrieved_context_tokens_budget": runtime_budget["retrieved_context_tokens_budget"],
        "generation_tokens_reserve": runtime_budget["generation_tokens_reserve"],
        "context_budget_ratio": runtime_budget["context_budget_ratio"],
        "concurrency_policy": concurrency_policy,
    }


@app.get("/ready/infer")
async def ready_infer(model_id: Optional[str] = None):
    requested_model_id = str(model_id or _default_heavy_model_id())
    config = get_model_config(requested_model_id)
    if not config:
        raise HTTPException(
            status_code=404,
            detail=_build_infer_readiness_payload(
                requested_model_id=requested_model_id,
                infer_ready=False,
                reason="model_not_found",
            ),
        )

    try:
        ready_model_id = await asyncio.to_thread(
            _start_server,
            requested_model_id,
            state["device_mode"],
            stage="readiness",
        )
    except HTTPException:
        raise
    except Exception as exc:
        return PlainTextResponse(
            content=json.dumps(
                _build_infer_readiness_payload(
                    requested_model_id=requested_model_id,
                    infer_ready=False,
                    reason=str(exc),
                )
            ),
            media_type="application/json",
            status_code=503,
        )

    return _build_infer_readiness_payload(
        requested_model_id=requested_model_id,
        ready_model_id=ready_model_id,
        infer_ready=True,
    )

@app.post("/v1/embeddings")
async def openai_embeddings(request: EmbeddingRequest):
    """OpenAI-compatible embeddings endpoint. Proxies to LaBSE st_server."""
    model_id = request.model
    if model_id not in STATIC_MODELS_CONFIG:
        model_id = resolve_model_selection("embedder.retrieval.legal_default").resolved_model_id

    try:
        # Нормализуем input в список
        texts = request.input if isinstance(request.input, list) else [request.input]

        payload = {"input": texts, "model": model_id}
        async with _acquire_runtime_slot(_embed_semaphore, "embedding"):
            started_model_id = await asyncio.to_thread(
                _start_server,
                model_id,
                state["device_mode"],
                stage="embeddings",
            )
            config = get_model_config(started_model_id)
            if not config:
                raise HTTPException(status_code=404, detail=f"Model {started_model_id} not found")
            url = f"http://localhost:{config['port']}/v1/embeddings"
            payload["model"] = started_model_id
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                result = resp.json()

        # Обеспечиваем OpenAI-совместимый формат
        if "data" not in result:
            # st_server может вернуть другой формат — адаптируем
            embeddings = result.get("embeddings", [])
            result = {
                "object": "list",
                "data": [
                    {"object": "embedding", "index": i, "embedding": emb}
                    for i, emb in enumerate(embeddings)
                ],
                "model": payload["model"],
                "usage": {"prompt_tokens": sum(len(t.split()) for t in texts), "total_tokens": sum(len(t.split()) for t in texts)},
            }

        return result
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.error(f"Embeddings Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health(): return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)
