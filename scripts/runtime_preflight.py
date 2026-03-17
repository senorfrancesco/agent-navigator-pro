#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

RUNTIME_ENV_PATH = BACKEND_ROOT / ".env.runtime"
HARDWARE_OVERRIDE_ENV_PATH = BACKEND_ROOT / ".env.hardware.override"
SUPPORTED_PROFILES = {"default", "adaptive", "manual"}
SUPPORTED_GPU_LAYERS_MODES = {"auto", "max", "manual"}
SUPPORTED_COMPONENT_DEVICE_MODES = {"cpu", "gpu", "hybrid"}


def _safe_float(value: Optional[float], default: float) -> float:
    if value is None:
        return default
    return float(value)


def resolve_backend_mode() -> str:
    raw = str(os.getenv("BACKEND_MODE", "llama-cpp-python")).strip().lower()
    if raw in {"llama-cpp-python", "llama-server", "vllm"}:
        return raw
    return "llama-cpp-python"


def _safe_int(value: Optional[Any], default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _serialize_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {
            str(key): _serialize_value(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _serialize_gpu(gpu: Any) -> Dict[str, Any]:
    serialized = _serialize_value(gpu)
    if isinstance(serialized, dict):
        return serialized
    return {"name": str(serialized)}


def _normalize_runtime_gpu_snapshot(gpu: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(gpu)
    if "free_gb" not in normalized:
        normalized["free_gb"] = _safe_float(normalized.get("free_vram_gb"), 0.0)
    if "total_gb" not in normalized:
        normalized["total_gb"] = _safe_float(normalized.get("total_vram_gb"), 0.0)
    if "index" not in normalized:
        normalized["index"] = 0
    return normalized


def _hardware_profile_to_snapshot(profile: Any) -> Dict[str, Any]:
    gpus = [_serialize_gpu(gpu) for gpu in list(getattr(profile, "gpus", []) or [])]
    cpu = _serialize_value(getattr(profile, "cpu", None))
    memory = _serialize_value(getattr(profile, "memory", None))
    best_gpu = _serialize_gpu(getattr(profile, "best_gpu", None)) if getattr(profile, "best_gpu", None) else None
    ram_gb = getattr(profile, "ram_gb", None)
    if ram_gb is None and isinstance(memory, dict):
        ram_gb = memory.get("total_ram_gb")
    cpu_cores = getattr(profile, "cpu_cores", None)
    if cpu_cores is None and isinstance(cpu, dict):
        cpu_cores = cpu.get("logical_cores") or cpu.get("physical_cores")
    has_gpu = bool(getattr(profile, "has_gpu", False) or getattr(profile, "gpu_count", 0) or gpus)
    return {
        "platform": getattr(profile, "platform_name", None),
        "ram_gb": ram_gb,
        "cpu_cores": cpu_cores,
        "has_gpu": has_gpu,
        "has_cuda": has_gpu,
        "gpu_count": int(getattr(profile, "gpu_count", len(gpus)) or len(gpus)),
        "total_vram_gb": getattr(profile, "total_vram_gb", None),
        "free_vram_gb": getattr(profile, "free_vram_gb", None),
        "best_gpu": best_gpu,
        "cpu": cpu,
        "memory": memory,
        "gpus": gpus,
        "repr": str(profile),
    }


def _resolve_gpu_layers_mode(raw_mode: Optional[str], raw_layers: Optional[int]) -> tuple[str, Optional[int], str]:
    env_mode = str(os.getenv("GPU_LAYERS_MODE", "auto")).strip().lower()
    mode = str(raw_mode or env_mode or "auto").strip().lower()
    if mode not in SUPPORTED_GPU_LAYERS_MODES:
        mode = "auto"
    requested_layers: Optional[int] = raw_layers
    if requested_layers is None and mode == "manual":
        env_override = os.getenv("N_GPU_LAYERS_OVERRIDE")
        if env_override not in {None, ""}:
            try:
                requested_layers = int(env_override)
            except ValueError:
                requested_layers = None
    source = "cli_override" if raw_mode or raw_layers is not None else "env_override" if env_mode != "auto" or os.getenv("N_GPU_LAYERS_OVERRIDE") else "auto"
    return mode, requested_layers, source


def _normalize_component_device_mode(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if normalized in SUPPORTED_COMPONENT_DEVICE_MODES:
        return normalized
    return None


def _resolve_component_device_modes(
    *,
    llm_device_mode: Optional[str],
    vlm_device_mode: Optional[str],
    intent_embedder_device_mode: Optional[str],
    retrieval_embedder_device_mode: Optional[str],
    fallback_llm_device_mode: str,
    fallback_embedding_device_mode: str,
) -> Dict[str, str]:
    return {
        "llm": _normalize_component_device_mode(llm_device_mode) or _normalize_component_device_mode(os.getenv("LLM_DEVICE_MODE")) or fallback_llm_device_mode,
        "vlm": _normalize_component_device_mode(vlm_device_mode) or _normalize_component_device_mode(os.getenv("VLM_DEVICE_MODE")) or fallback_llm_device_mode,
        "intent_embedder": _normalize_component_device_mode(intent_embedder_device_mode) or _normalize_component_device_mode(os.getenv("INTENT_EMBEDDER_DEVICE_MODE")) or fallback_embedding_device_mode,
        "retrieval_embedder": _normalize_component_device_mode(retrieval_embedder_device_mode) or _normalize_component_device_mode(os.getenv("RETRIEVAL_EMBEDDER_DEVICE_MODE")) or fallback_embedding_device_mode,
    }


def detect_hardware_snapshot() -> Dict[str, Any]:
    try:
        from services.hardware import HardwareProfiler

        profile = HardwareProfiler().detect()
        return _hardware_profile_to_snapshot(profile)
    except Exception as exc:
        return {
            "platform": None,
            "ram_gb": None,
            "cpu_cores": None,
            "has_gpu": None,
            "has_cuda": None,
            "gpu_count": 0,
            "total_vram_gb": None,
            "free_vram_gb": None,
            "best_gpu": None,
            "cpu": None,
            "memory": None,
            "gpus": [],
            "repr": f"hardware-detect-unavailable: {exc}",
        }


def build_runtime_plan(
    *,
    runtime_profile: str,
    manual_effective_context_tokens: Optional[int] = None,
    retrieved_context_ratio: Optional[float] = None,
    generation_tokens_reserve: Optional[int] = None,
    device_mode: Optional[str] = None,
    gpu_layers_mode: Optional[str] = None,
    gpu_layers: Optional[int] = None,
    llm_device_mode: Optional[str] = None,
    vlm_device_mode: Optional[str] = None,
    intent_embedder_device_mode: Optional[str] = None,
    retrieval_embedder_device_mode: Optional[str] = None,
) -> Dict[str, Any]:
    from services.model_manager.unified_model_server import (
        DeviceMode,
        _build_model_placement_plan,
        _resolve_embedding_admission,
        _resolve_llm_admission,
        resolve_runtime_budget,
    )
    from services.hardware.tier_selector import describe_rag_mode

    runtime_profile = str(runtime_profile or "adaptive").strip().lower()
    if runtime_profile not in SUPPORTED_PROFILES:
        runtime_profile = "adaptive"

    tier_info: Dict[str, Any] = {}
    llm_ctx_size: Optional[int] = None
    selected_device_mode = (device_mode or "").strip().lower() or None
    hardware_snapshot = detect_hardware_snapshot()
    resolved_gpu_layers_mode, requested_gpu_layers, gpu_layers_mode_source = _resolve_gpu_layers_mode(
        gpu_layers_mode, gpu_layers
    )
    try:
        from services.hardware import HardwareProfiler, TierSelector

        profile = HardwareProfiler().detect()
        tier_config = TierSelector().select(profile)
        llm_ctx_size = int(getattr(tier_config, "llm_ctx_size", 0) or 0) or None
        tier_info = {
            "tier": getattr(tier_config, "tier", None),
            "rag_mode": getattr(tier_config, "rag_mode", "simple"),
            "embedding_backend": getattr(tier_config, "embedding_backend", "labse"),
            "embedding_device": getattr(tier_config, "embedding_device", "cpu"),
            "llm_model_id": getattr(tier_config, "llm_model_id", "qwen-14b-llm"),
            "llm_quant": getattr(tier_config, "llm_quant", "Q4_K_M"),
            "llm_gpu_layers": getattr(tier_config, "llm_gpu_layers", 0),
        }
        if selected_device_mode is None:
            has_gpu = bool(getattr(profile, "has_gpu", False) or getattr(profile, "gpu_count", 0))
            selected_device_mode = DeviceMode.HYBRID.value if has_gpu else DeviceMode.CPU.value
    except Exception:
        tier_info = {
            "tier": None,
            "rag_mode": "simple",
            "embedding_backend": "labse",
            "embedding_device": "cpu",
            "llm_model_id": "qwen-14b-llm",
            "llm_quant": "Q4_K_M",
            "llm_gpu_layers": 0,
        }
        if selected_device_mode is None:
            selected_device_mode = DeviceMode.HYBRID.value

    selected_gpu_layers = _safe_int(tier_info.get("llm_gpu_layers"), 0)
    gpu_layers_source = "tier_auto"
    if resolved_gpu_layers_mode == "max":
        selected_gpu_layers = -1
        gpu_layers_source = "max_override"
    elif resolved_gpu_layers_mode == "manual" and requested_gpu_layers is not None:
        selected_gpu_layers = int(requested_gpu_layers)
        gpu_layers_source = "manual_override"
    elif resolved_gpu_layers_mode == "manual":
        gpu_layers_source = "manual_missing_value"

    budget = resolve_runtime_budget(
        ctx_size=llm_ctx_size,
        runtime_profile=runtime_profile,
        manual_effective_context_tokens=manual_effective_context_tokens,
        context_budget_ratio=retrieved_context_ratio,
        generation_tokens_reserve=generation_tokens_reserve,
    )
    budget["device_mode"] = selected_device_mode
    budget["rag_mode"] = tier_info["rag_mode"]
    budget["rag_mode_label"] = describe_rag_mode(tier_info["rag_mode"])
    budget["embedding_backend"] = tier_info["embedding_backend"]
    budget["embedding_device"] = tier_info["embedding_device"]
    budget["tier"] = tier_info["tier"]
    budget["source"] = runtime_profile
    budget["backend_mode"] = resolve_backend_mode()
    budget["llm_model_id"] = tier_info["llm_model_id"]
    budget["llm_quant"] = tier_info["llm_quant"]
    budget["llm_ctx_size"] = llm_ctx_size
    budget["llm_gpu_layers"] = selected_gpu_layers
    budget["gpu_layers_mode"] = resolved_gpu_layers_mode
    budget["requested_gpu_layers"] = requested_gpu_layers
    budget["gpu_layers_source"] = gpu_layers_source
    budget["gpu_layers_mode_source"] = gpu_layers_mode_source
    budget["hardware"] = hardware_snapshot
    budget["decision_source"] = "env_override" if gpu_layers_mode_source == "env_override" or device_mode else "auto"
    warnings: List[str] = []
    if hardware_snapshot.get("has_gpu") and selected_gpu_layers == 0 and resolved_gpu_layers_mode == "auto":
        warnings.append("auto selected cpu-only placement despite detected GPU")
        warnings.append("hint: try --gpu-layers-mode max or set N_GPU_LAYERS_OVERRIDE")
    budget["warnings"] = warnings
    component_modes = _resolve_component_device_modes(
        llm_device_mode=llm_device_mode,
        vlm_device_mode=vlm_device_mode,
        intent_embedder_device_mode=intent_embedder_device_mode,
        retrieval_embedder_device_mode=retrieval_embedder_device_mode,
        fallback_llm_device_mode=selected_device_mode,
        fallback_embedding_device_mode="gpu" if tier_info["embedding_device"] == "cuda" else "cpu",
    )
    available_gpus = [_normalize_runtime_gpu_snapshot(gpu) for gpu in list(hardware_snapshot.get("gpus") or [])]
    if not available_gpus and hardware_snapshot.get("gpu_count"):
        available_gpus = [
            {
                "index": 0,
                "free_gb": _safe_float(hardware_snapshot.get("free_vram_gb"), 0.0),
                "total_gb": _safe_float(hardware_snapshot.get("total_vram_gb"), 0.0),
            }
        ]
    llm_config = {
        "type": "gguf",
        "ctx_size": llm_ctx_size or 4096,
        "gpu_layers": selected_gpu_layers,
        "quant": tier_info["llm_quant"],
    }
    llm_admission = _resolve_llm_admission(
        model_id=tier_info["llm_model_id"],
        config=llm_config,
        requested_device=DeviceMode(component_modes["llm"]),
        available_gpus=available_gpus,
        token_budget=budget["effective_context_tokens"],
    )
    llm_placement = _build_model_placement_plan(
        model_id=tier_info["llm_model_id"],
        config=llm_config,
        device_mode=DeviceMode(component_modes["llm"]),
        available_gpus=available_gpus,
    )
    intent_admission = _resolve_embedding_admission(
        model_id=os.getenv("INTENT_CLASSIFIER_EMBEDDER_MODEL", "qwen3-embedding-0.6b"),
        requested_device=DeviceMode(component_modes["intent_embedder"]),
        resolved_device=component_modes["intent_embedder"] if available_gpus or component_modes["intent_embedder"] == "cpu" else "cpu",
        available_gpus=available_gpus,
        fallback_applied=bool(component_modes["intent_embedder"] == "gpu" and not available_gpus),
    )
    intent_placement = _build_model_placement_plan(
        model_id=os.getenv("INTENT_CLASSIFIER_EMBEDDER_MODEL", "qwen3-embedding-0.6b"),
        config={"type": "st"},
        device_mode=DeviceMode(component_modes["intent_embedder"]),
        available_gpus=available_gpus,
    )
    retrieval_admission = _resolve_embedding_admission(
        model_id=os.getenv("LEGAL_EMBEDDER_MODEL", "labse-embedding"),
        requested_device=DeviceMode(component_modes["retrieval_embedder"]),
        resolved_device=component_modes["retrieval_embedder"] if available_gpus or component_modes["retrieval_embedder"] == "cpu" else "cpu",
        available_gpus=available_gpus,
        fallback_applied=bool(component_modes["retrieval_embedder"] == "gpu" and not available_gpus),
    )
    retrieval_placement = _build_model_placement_plan(
        model_id=os.getenv("LEGAL_EMBEDDER_MODEL", "labse-embedding"),
        config={"type": "st"},
        device_mode=DeviceMode(component_modes["retrieval_embedder"]),
        available_gpus=available_gpus,
    )
    if llm_admission["admission"] == "requires_degraded":
        warnings.append(
            f"llm requested_device={llm_admission['requested_device']} admission=requires_degraded"
        )
    elif llm_admission["resolved_device"] != llm_admission["requested_device"]:
        warnings.append(
            f"llm requested_device={llm_admission['requested_device']} resolved_device={llm_admission['resolved_device']}"
        )
    budget["placements"] = {
        "llm": {
            "model_id": tier_info["llm_model_id"],
            "device_mode": component_modes["llm"],
            "gpu_layers": selected_gpu_layers,
            "gpu_layers_mode": resolved_gpu_layers_mode,
            "gpu_layers_source": gpu_layers_source,
            "quant": tier_info["llm_quant"],
            "ctx_size": llm_ctx_size,
            "placement_mode": llm_placement.get("placement_mode"),
            "gpu_indices": llm_placement.get("gpu_indices"),
            "tensor_split": llm_placement.get("tensor_split"),
            "requested_device": llm_admission["requested_device"],
            "resolved_device": llm_admission["resolved_device"],
            "admission": llm_admission["admission"],
        },
        "vlm": {
            "model_id": "qwen-vl-8b",
            "device_mode": component_modes["vlm"],
            "gpu_layers": -1 if component_modes["vlm"] != DeviceMode.CPU.value else 0,
            "gpu_layers_mode": "auto",
            "gpu_layers_source": "device_mode_follow_llm",
        },
        "intent_embedder": {
            "model_id": os.getenv("INTENT_CLASSIFIER_EMBEDDER_MODEL", "qwen3-embedding-0.6b"),
            "device_mode": component_modes["intent_embedder"],
            "backend": tier_info["embedding_backend"],
            "source": "component_override_or_tier_embedding_policy",
            "placement_mode": intent_placement.get("placement_mode"),
            "gpu_indices": intent_placement.get("gpu_indices"),
            "requested_device": intent_admission["requested_device"],
            "resolved_device": intent_admission["resolved_device"],
            "admission": intent_admission["admission"],
        },
        "retrieval_embedder": {
            "model_id": os.getenv("LEGAL_EMBEDDER_MODEL", "labse-embedding"),
            "device_mode": component_modes["retrieval_embedder"],
            "backend": tier_info["embedding_backend"],
            "source": "component_override_or_tier_embedding_policy",
            "placement_mode": retrieval_placement.get("placement_mode"),
            "gpu_indices": retrieval_placement.get("gpu_indices"),
            "requested_device": retrieval_admission["requested_device"],
            "resolved_device": retrieval_admission["resolved_device"],
            "admission": retrieval_admission["admission"],
        },
    }
    budget["component_device_modes"] = component_modes
    budget["admission"] = {
        tier_info["llm_model_id"]: llm_admission,
        os.getenv("INTENT_CLASSIFIER_EMBEDDER_MODEL", "qwen3-embedding-0.6b"): intent_admission,
        os.getenv("LEGAL_EMBEDDER_MODEL", "labse-embedding"): retrieval_admission,
    }
    return budget


def render_env_runtime(plan: Dict[str, Any]) -> str:
    component_modes = plan.get("component_device_modes") or {}
    lines = [
        f"UMS_RUNTIME_PROFILE={plan['runtime_profile']}",
        f"UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS={plan['effective_context_tokens']}",
        f"UMS_RETRIEVED_CONTEXT_RATIO={plan['context_budget_ratio']}",
        f"UMS_GENERATION_TOKENS_RESERVE={plan['generation_tokens_reserve']}",
        f"DEVICE_MODE={plan['device_mode']}",
        f"GPU_LAYERS_MODE={plan.get('gpu_layers_mode', 'auto')}",
        f"UMS_SELECTED_GPU_LAYERS={plan.get('llm_gpu_layers', 0)}",
        f"LLM_DEVICE_MODE={component_modes.get('llm', plan['device_mode'])}",
        f"VLM_DEVICE_MODE={component_modes.get('vlm', plan['device_mode'])}",
        f"INTENT_EMBEDDER_DEVICE_MODE={component_modes.get('intent_embedder', 'cpu')}",
        f"RETRIEVAL_EMBEDDER_DEVICE_MODE={component_modes.get('retrieval_embedder', 'cpu')}",
    ]
    gpu_layers_mode = str(plan.get("gpu_layers_mode") or "auto").strip().lower()
    if gpu_layers_mode == "max":
        lines.append("N_GPU_LAYERS_OVERRIDE=-1")
    elif gpu_layers_mode == "manual" and plan.get("requested_gpu_layers") is not None:
        lines.append(f"N_GPU_LAYERS_OVERRIDE={int(plan['requested_gpu_layers'])}")
    if plan.get("rag_mode"):
        lines.append(f"RAG_MODE_OVERRIDE={plan['rag_mode']}")
    return "\n".join(lines) + "\n"


def write_env_runtime(plan: Dict[str, Any], output_path: Path = RUNTIME_ENV_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_env_runtime(plan), encoding="utf-8")
    return output_path


def _json_print(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Runtime preflight and applied env planner.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_common_args(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--profile", choices=sorted(SUPPORTED_PROFILES), default=os.getenv("UMS_RUNTIME_PROFILE", "adaptive"))
        subparser.add_argument("--manual-effective-context-tokens", type=int, default=None)
        subparser.add_argument("--retrieved-context-ratio", type=float, default=None)
        subparser.add_argument("--generation-tokens-reserve", type=int, default=None)
        subparser.add_argument(
            "--device-mode",
            choices=["cpu", "gpu", "hybrid"],
            default=(os.getenv("DEVICE_MODE") or None),
        )
        subparser.add_argument("--llm-device-mode", choices=sorted(SUPPORTED_COMPONENT_DEVICE_MODES), default=(os.getenv("LLM_DEVICE_MODE") or None))
        subparser.add_argument("--vlm-device-mode", choices=sorted(SUPPORTED_COMPONENT_DEVICE_MODES), default=(os.getenv("VLM_DEVICE_MODE") or None))
        subparser.add_argument("--intent-embedder-device-mode", choices=sorted(SUPPORTED_COMPONENT_DEVICE_MODES), default=(os.getenv("INTENT_EMBEDDER_DEVICE_MODE") or None))
        subparser.add_argument("--retrieval-embedder-device-mode", choices=sorted(SUPPORTED_COMPONENT_DEVICE_MODES), default=(os.getenv("RETRIEVAL_EMBEDDER_DEVICE_MODE") or None))
        subparser.add_argument(
            "--gpu-layers-mode",
            choices=sorted(SUPPORTED_GPU_LAYERS_MODES),
            default=(os.getenv("GPU_LAYERS_MODE") or None),
        )
        subparser.add_argument("--gpu-layers", type=int, default=None)

    detect_parser = subparsers.add_parser("detect", help="Report detected hardware snapshot.")
    detect_parser.add_argument("--json", action="store_true", default=True)

    plan_parser = subparsers.add_parser("plan", help="Build runtime plan JSON.")
    _add_common_args(plan_parser)

    report_parser = subparsers.add_parser("report", help="Print runtime plan JSON.")
    _add_common_args(report_parser)

    apply_parser = subparsers.add_parser("apply", help="Write backend/.env.runtime and print applied plan.")
    _add_common_args(apply_parser)
    apply_parser.add_argument("--output", default=str(RUNTIME_ENV_PATH))
    apply_parser.add_argument("--report-only", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "detect":
        _json_print({"hardware": detect_hardware_snapshot()})
        return 0

    plan = build_runtime_plan(
        runtime_profile=args.profile,
        manual_effective_context_tokens=args.manual_effective_context_tokens,
        retrieved_context_ratio=args.retrieved_context_ratio,
        generation_tokens_reserve=args.generation_tokens_reserve,
        device_mode=args.device_mode,
        gpu_layers_mode=args.gpu_layers_mode,
        gpu_layers=args.gpu_layers,
        llm_device_mode=args.llm_device_mode,
        vlm_device_mode=args.vlm_device_mode,
        intent_embedder_device_mode=args.intent_embedder_device_mode,
        retrieval_embedder_device_mode=args.retrieval_embedder_device_mode,
    )

    if args.command == "apply" and not args.report_only:
        output = write_env_runtime(plan, Path(args.output))
        plan["env_runtime_path"] = str(output)
    _json_print(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
