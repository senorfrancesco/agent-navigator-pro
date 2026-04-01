#!/usr/bin/env python3
"""Model downloader/validator for canonical MODEL_PATH_* contract."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_LLM_PATH = "./models/gguf/qwen-14b/Qwen3-14B.Q4_K_M.gguf"
DEFAULT_VLM_PATH = "./models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
DEFAULT_INTENT_EMBEDDING_PATH = "./models/st/Qwen3-Embedding-0.6B"
DEFAULT_RETRIEVAL_EMBEDDING_PATH = "./models/st/LaBSE"
DEFAULT_MMPROJ_PATH = "./models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"

ASSETS: Dict[str, Dict[str, str]] = {
    "llm": {
        "asset_set": "core",
        "kind": "file",
        "repo_id": "MaziyarPanahi/Qwen3-14B-GGUF",
        "filename": "Qwen3-14B.Q4_K_M.gguf",
        "target_env": "MODEL_PATH_LLM",
        "default_path": DEFAULT_LLM_PATH,
    },
    "intent_embedding": {
        "asset_set": "core",
        "kind": "snapshot",
        "repo_id": "Qwen/Qwen3-Embedding-0.6B",
        "target_env": "MODEL_PATH_EMBEDDING_INTENT",
        "default_path": DEFAULT_INTENT_EMBEDDING_PATH,
    },
    "retrieval_embedding": {
        "asset_set": "core",
        "kind": "snapshot",
        "repo_id": "cointegrated/LaBSE-en-ru",
        "target_env": "MODEL_PATH_EMBEDDING_RETRIEVAL",
        "default_path": DEFAULT_RETRIEVAL_EMBEDDING_PATH,
    },
    "vlm": {
        "asset_set": "all",
        "kind": "file",
        "repo_id": "Qwen/Qwen3-VL-8B-Instruct-GGUF",
        "filename": "Qwen3-VL-8B-Instruct-Q4_K_M.gguf",
        "target_env": "MODEL_PATH_VLM",
        "default_path": DEFAULT_VLM_PATH,
    },
    "vlm_mmproj": {
        "asset_set": "all",
        "kind": "file",
        "repo_id": "Qwen/Qwen3-VL-8B-Instruct-GGUF",
        "filename": "mmproj-Qwen3-VL-8B-Instruct-F16.gguf",
        "target_env": "MMPROJ_PATH",
        "default_path": DEFAULT_MMPROJ_PATH,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Проверить наличие модельных артефактов и при необходимости скачать отсутствующие.")
    parser.add_argument(
        "--mode",
        default="ensure-present",
        choices=["ensure-present"],
        help="Режим работы downloader; сейчас поддерживается только ensure-present.",
    )
    parser.add_argument("--asset-set", default="core", choices=["core", "all"], help="Набор артефактов: core или all.")
    parser.add_argument("--models-root", help="Переопределить корневой каталог моделей для канонического layout.")
    parser.add_argument("--huggingface-cache", help="Переопределить корневой каталог кэша Hugging Face.")
    parser.add_argument("--dry-run", action="store_true", help="Показать план скачивания и целевые пути без фактических загрузок.")
    return parser.parse_args()


def resolve_relative_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    cleaned = path_str[2:] if path_str.startswith("./") else path_str
    return BACKEND_DIR / cleaned


def path_from_models_root(default_path: str, models_root: Path) -> Path:
    cleaned = default_path[2:] if default_path.startswith("./") else default_path
    if cleaned.startswith("models/"):
        cleaned = cleaned[len("models/") :]
    return models_root / cleaned


def resolve_target(asset: Dict[str, str], env: Dict[str, str], models_root: Path | None) -> Path:
    if models_root is not None:
        return path_from_models_root(asset["default_path"], models_root)
    env_name = asset["target_env"]
    explicit = env.get(env_name)
    if explicit:
        return resolve_relative_path(explicit)
    if asset["target_env"] == "MMPROJ_PATH":
        explicit_mmproj = env.get("MODEL_MMPROJ_PATH_VLM")
        if explicit_mmproj:
            return resolve_relative_path(explicit_mmproj)
    if models_root is not None:
        return path_from_models_root(asset["default_path"], models_root)
    return resolve_relative_path(asset["default_path"])


def is_present(asset: Dict[str, str], target: Path) -> bool:
    if asset["kind"] == "file":
        return target.is_file()
    return target.is_dir() and (target / "config.json").exists()


def iter_assets(asset_set: str) -> Iterable[tuple[str, Dict[str, str]]]:
    for asset_name, asset in ASSETS.items():
        if asset["asset_set"] == "core" or asset_set == "all":
            yield asset_name, asset


def ensure_hf_dependency() -> tuple[object, object]:
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "huggingface_hub не установлен. Установите зависимости: "
            "cd backend && pip install -r requirements.txt"
        ) from exc
    return hf_hub_download, snapshot_download


def print_plan(lines: List[str], env_block: List[str]) -> None:
    print("models:plan")
    for line in lines:
        print(line)
    print("models:env")
    for line in env_block:
        print(line)


def build_env_block(resolved_targets: Dict[str, Path]) -> List[str]:
    return [
        f'MODEL_PATH_LLM="{resolved_targets["llm"]}"',
        f'MODEL_PATH_VLM="{resolved_targets["vlm"]}"',
        f'MMPROJ_PATH="{resolved_targets["vlm_mmproj"]}"',
        f'MODEL_PATH_EMBEDDING_INTENT="{resolved_targets["intent_embedding"]}"',
        f'MODEL_PATH_EMBEDDING_RETRIEVAL="{resolved_targets["retrieval_embedding"]}"',
    ]


def download_assets(
    args: argparse.Namespace, resolved_targets: Dict[str, Path], env: Dict[str, str]
) -> None:
    hf_hub_download, snapshot_download = ensure_hf_dependency()
    cache_dir = args.huggingface_cache or env.get("HF_HOME") or env.get("HUGGINGFACE_HUB_CACHE")
    for asset_name, asset in iter_assets(args.asset_set):
        target = resolved_targets[asset_name]
        if is_present(asset, target):
            print(f"models:skip asset={asset_name} target={target}")
            continue

        if asset["kind"] == "file":
            target.parent.mkdir(parents=True, exist_ok=True)
            hf_hub_download(
                repo_id=asset["repo_id"],
                filename=asset["filename"],
                local_dir=str(target.parent),
                cache_dir=cache_dir,
            )
            print(f"models:downloaded asset={asset_name} target={target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            snapshot_download(
                repo_id=asset["repo_id"],
                local_dir=str(target),
                cache_dir=cache_dir,
            )
            print(f"models:downloaded asset={asset_name} target={target}")


def main() -> int:
    args = parse_args()
    env = dict(os.environ)
    models_root = Path(args.models_root).expanduser() if args.models_root else None

    resolved_targets = {
        asset_name: resolve_target(asset, env, models_root)
        for asset_name, asset in ASSETS.items()
    }

    plan_lines = []
    for asset_name, asset in iter_assets(args.asset_set):
        target = resolved_targets[asset_name]
        status = "present" if is_present(asset, target) else "missing"
        line = (
            f"models:asset name={asset_name} kind={asset['kind']} status={status} "
            f"repo={asset['repo_id']} target={target}"
        )
        if asset["kind"] == "file":
            line += f" file={asset['filename']}"
        plan_lines.append(line)

    env_block = build_env_block(resolved_targets)
    if args.dry_run:
        print_plan(plan_lines, env_block)
        return 0

    download_assets(args, resolved_targets, env)
    print("models:ok")
    for line in env_block:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
