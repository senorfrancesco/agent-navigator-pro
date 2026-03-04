#!/usr/bin/env python3
"""Preflight-проверка и расчет runtime-профиля для Agent Navigator Pro."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
RUNTIME_ENV_PATH = BACKEND_DIR / ".env.runtime"
DEFAULT_ENV_PATH = BACKEND_DIR / ".env"
ENV_EXAMPLE_PATH = BACKEND_DIR / ".env.example"

# Импорт backend модулей без установки пакета
sys.path.insert(0, str(BACKEND_DIR))

from services.hardware.profiler import HardwareProfiler  # noqa: E402
from services.hardware.tier_selector import Tier, TierConfig, TierSelector  # noqa: E402


def parse_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    data: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def detect_phase(env_data: Dict[str, str]) -> Tuple[dict, List[str]]:
    profiler = HardwareProfiler()
    profile = profiler.detect()

    binaries = {
        "llama-server": shutil.which("llama-server") is not None,
        "docker": shutil.which("docker") is not None,
        "conda": shutil.which("conda") is not None,
    }

    model_paths = {}
    for key in [
        "MODEL_PATH_QWEN14B",
        "MODEL_PATH_QWENVL",
        "MMPROJ_PATH",
        "MODEL_PATH_LABSE",
    ]:
        value = env_data.get(key)
        if not value:
            model_paths[key] = {"path": "<missing>", "exists": False}
            continue
        p = Path(value)
        if not p.is_absolute():
            p = BACKEND_DIR / p
        model_paths[key] = {"path": str(p), "exists": p.exists()}

    reasons = [
        f"CPU: {profile.cpu.physical_cores}C/{profile.cpu.logical_cores}T",
        f"RAM available: {profile.memory.available_ram_gb:.1f} GB",
        f"GPU count: {profile.gpu_count}, VRAM total: {profile.total_vram_gb:.1f} GB",
    ]

    return {
        "system_profile": profile,
        "binaries": binaries,
        "model_paths": model_paths,
    }, reasons


def choose_tier_interactive(default_tier: Tier) -> Tier:
    print("\nManual mode: выберите tier:")
    for t in Tier:
        suffix = " (рекомендуется)" if t == default_tier else ""
        print(f"  {int(t)} - {t.name}{suffix}")

    while True:
        raw = input(f"Tier [1-4, default={int(default_tier)}]: ").strip()
        if not raw:
            return default_tier
        if raw in {"1", "2", "3", "4"}:
            return Tier(int(raw))
        print("Некорректный выбор, попробуйте снова.")


def choose_int(prompt: str, default: int) -> int:
    raw = input(f"{prompt} [default={default}]: ").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print("Некорректное число, используется default.")
        return default


def plan_phase(detected: dict, profile_mode: str, non_interactive: bool, yes: bool) -> Tuple[TierConfig, List[str]]:
    selector = TierSelector()
    auto_config = selector.select(detected["system_profile"])
    reasons = [f"Auto-selector рекомендует {auto_config.tier.name}."]

    if profile_mode == "default":
        config = selector._build_config(Tier.TIER_2, detected["system_profile"])
        reasons.append("Профиль default фиксирован на TIER_2 (стабильный баланс качества/ресурсов).")
    elif profile_mode == "adaptive":
        config = auto_config
        reasons.append("Профиль adaptive использует автоматический tier selection.")
    else:
        tier = auto_config.tier
        if not non_interactive and not yes:
            tier = choose_tier_interactive(auto_config.tier)
        config = selector._build_config(tier, detected["system_profile"])
        reasons.append(f"Профиль manual: выбран {tier.name}.")

        if not non_interactive and not yes:
            config.llm_ctx_size = choose_int("LLM context size", config.llm_ctx_size)
            config.max_concurrent_llm = choose_int("Max concurrent LLM", config.max_concurrent_llm)
            reasons.append("В manual применены интерактивные лимиты ctx/concurrency.")
        else:
            reasons.append("Manual в non-interactive/--yes: использованы значения по умолчанию выбранного tier.")

    return config, reasons


def apply_phase(config: TierConfig, profile_mode: str, reasons: List[str], detected: dict) -> Path:
    payload = {
        "RUNTIME_PROFILE": profile_mode,
        "TIER_OVERRIDE": str(int(config.tier)),
        "N_GPU_LAYERS_OVERRIDE": str(config.llm_gpu_layers),
        "CONTEXT_SIZE_QWEN14B": str(config.llm_ctx_size),
        "RAG_MODE_OVERRIDE": config.rag_mode,
        "MAX_CONCURRENT_LLM": str(config.max_concurrent_llm),
        "LLM_BATCH_SIZE": str(config.llm_batch_size),
        "EMBEDDING_DEVICE": config.embedding_device,
        "EMBEDDING_BACKEND": config.embedding_backend,
        "PREFLIGHT_CPU_CORES": str(detected["system_profile"].cpu.logical_cores),
        "PREFLIGHT_RAM_GB": f"{detected['system_profile'].memory.total_ram_gb:.1f}",
        "PREFLIGHT_GPU_COUNT": str(detected["system_profile"].gpu_count),
        "PREFLIGHT_TOTAL_VRAM_GB": f"{detected['system_profile'].total_vram_gb:.1f}",
    }

    lines = [
        "# Auto-generated by scripts/preflight.py",
        "# Do not edit manually; regenerate via ./scripts/preflight.py",
    ]
    for key, value in payload.items():
        lines.append(f"{key}={value}")

    for idx, reason in enumerate(reasons, start=1):
        lines.append(f'RUNTIME_REASON_{idx}="{reason}"')

    RUNTIME_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return RUNTIME_ENV_PATH


def report_phase(detected: dict, config: TierConfig, reasons: List[str], runtime_path: Path) -> None:
    sp = detected["system_profile"]
    print("\n=== PRE-FLIGHT REPORT ===")
    print("Phase detect:")
    print(f"  CPU       : {sp.cpu.name or 'Unknown'} | {sp.cpu.physical_cores}C/{sp.cpu.logical_cores}T")
    print(f"  RAM       : total {sp.memory.total_ram_gb:.1f} GB | free {sp.memory.available_ram_gb:.1f} GB")
    print(f"  GPU/VRAM  : {sp.gpu_count} | total {sp.total_vram_gb:.1f} GB")
    print("  Binaries  :")
    for name, ok in detected["binaries"].items():
        mark = "OK" if ok else "MISS"
        print(f"    - {name:<12} {mark}")
    print("  Model paths:")
    for key, item in detected["model_paths"].items():
        mark = "OK" if item["exists"] else "MISS"
        print(f"    - {key:<18} {mark} {item['path']}")

    print("\nPhase plan/apply:")
    cfg = asdict(config)
    print(f"  tier={config.tier.name}, rag_mode={cfg['rag_mode']}, ctx={cfg['llm_ctx_size']}, gpu_layers={cfg['llm_gpu_layers']}")
    print(f"  runtime file: {runtime_path}")

    print("\nПричины выбора:")
    for i, reason in enumerate(reasons, start=1):
        print(f"  {i}. {reason}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight detect/plan/apply/report")
    parser.add_argument("--mode", choices=["all", "detect", "plan", "apply", "report"], default="all")
    parser.add_argument("--profile", choices=["default", "adaptive", "manual"], default="adaptive")
    parser.add_argument("--non-interactive", action="store_true", help="Запуск без интерактивных вопросов")
    parser.add_argument("--yes", action="store_true", help="Автоматически подтверждать значения по умолчанию")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_data = parse_env_file(DEFAULT_ENV_PATH)
    if not env_data:
        env_data = parse_env_file(ENV_EXAMPLE_PATH)

    detected, detect_reasons = detect_phase(env_data)
    config, plan_reasons = plan_phase(detected, args.profile, args.non_interactive, args.yes)
    reasons = [*detect_reasons, *plan_reasons]

    runtime_path = RUNTIME_ENV_PATH
    if args.mode in {"all", "apply"}:
        runtime_path = apply_phase(config, args.profile, reasons, detected)

    if args.mode in {"all", "detect", "plan", "report", "apply"}:
        report_phase(detected, config, reasons, runtime_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
