#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

RUNTIME_ENV_PATH = BACKEND_ROOT / ".env.runtime"
SUPPORTED_PROFILES = {"default", "adaptive", "manual"}


def _safe_float(value: Optional[float], default: float) -> float:
    if value is None:
        return default
    return float(value)


def resolve_backend_mode() -> str:
    raw = str(os.getenv("BACKEND_MODE", "llama-cpp-python")).strip().lower()
    if raw in {"llama-cpp-python", "llama-server", "vllm"}:
        return raw
    return "llama-cpp-python"


def detect_hardware_snapshot() -> Dict[str, Any]:
    try:
        from services.hardware import HardwareProfiler

        profile = HardwareProfiler().detect()
        has_gpu = bool(getattr(profile, "has_gpu", False) or getattr(profile, "gpu_count", 0))
        return {
            "ram_gb": getattr(profile, "ram_gb", None),
            "cpu_cores": getattr(profile, "cpu_cores", None),
            "has_cuda": has_gpu,
            "gpus": getattr(profile, "gpus", None),
            "repr": str(profile),
        }
    except Exception as exc:
        return {
            "ram_gb": None,
            "cpu_cores": None,
            "has_cuda": None,
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
) -> Dict[str, Any]:
    from services.model_manager.unified_model_server import DeviceMode, resolve_runtime_budget
    from services.hardware.tier_selector import describe_rag_mode

    runtime_profile = str(runtime_profile or "adaptive").strip().lower()
    if runtime_profile not in SUPPORTED_PROFILES:
        runtime_profile = "adaptive"

    tier_info: Dict[str, Any] = {}
    llm_ctx_size: Optional[int] = None
    selected_device_mode = (device_mode or "").strip().lower() or None
    try:
        from services.hardware import HardwareProfiler, TierSelector

        profile = HardwareProfiler().detect()
        tier_config = TierSelector().select(profile)
        llm_ctx_size = int(getattr(tier_config, "llm_ctx_size", 0) or 0) or None
        tier_info = {
            "tier": getattr(tier_config, "tier", None),
            "rag_mode": getattr(tier_config, "rag_mode", "simple"),
            "embedding_backend": getattr(tier_config, "embedding_backend", "labse"),
        }
        if selected_device_mode is None:
            has_gpu = bool(getattr(profile, "has_gpu", False) or getattr(profile, "gpu_count", 0))
            selected_device_mode = DeviceMode.HYBRID.value if has_gpu else DeviceMode.CPU.value
    except Exception:
        tier_info = {"tier": None, "rag_mode": "simple", "embedding_backend": "labse"}
        if selected_device_mode is None:
            selected_device_mode = DeviceMode.HYBRID.value

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
    budget["tier"] = tier_info["tier"]
    budget["source"] = runtime_profile
    budget["backend_mode"] = resolve_backend_mode()
    return budget


def render_env_runtime(plan: Dict[str, Any]) -> str:
    lines = [
        f"UMS_RUNTIME_PROFILE={plan['runtime_profile']}",
        f"UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS={plan['effective_context_tokens']}",
        f"UMS_RETRIEVED_CONTEXT_RATIO={plan['context_budget_ratio']}",
        f"UMS_GENERATION_TOKENS_RESERVE={plan['generation_tokens_reserve']}",
        f"DEVICE_MODE={plan['device_mode']}",
    ]
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
    )

    if args.command == "apply" and not args.report_only:
        output = write_env_runtime(plan, Path(args.output))
        plan["env_runtime_path"] = str(output)
    _json_print(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
