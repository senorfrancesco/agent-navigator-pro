"""Конфигурация моделей на основе канонического YAML registry."""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

from dotenv import load_dotenv

from services.model_manager.model_registry import (
    get_model_registry,
    get_model_spec_by_id,
)

load_dotenv()


def _pick_env_value(source: Mapping[str, str], *names: Optional[str]) -> Optional[str]:
    for name in names:
        if not name:
            continue
        value = source.get(name)
        if value:
            return value
    return None


def resolve_model_path(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> str:
    source = env if env is not None else os.environ
    spec = get_model_spec_by_id(model_id, env=source)
    path_spec = dict(spec.get("path") or {})
    canonical_env = str(path_spec.get("canonical_env") or "").strip()
    legacy_envs = [str(name).strip() for name in (path_spec.get("legacy_envs") or []) if str(name).strip()]
    default_path = str(path_spec.get("default") or "").strip()

    resolved = _pick_env_value(source, canonical_env, *legacy_envs)
    if resolved:
        return resolved
    return default_path


def _build_model_config(*, env: Optional[Mapping[str, str]] = None) -> Dict[str, Dict[str, Any]]:
    source = env if env is not None else os.environ
    registry = get_model_registry(env=source)
    payload: Dict[str, Dict[str, Any]] = {}

    for spec in registry.models.values():
        model_id = str(spec.get("model_id") or "").strip()
        runtime = dict(spec.get("runtime") or {})
        model_kind = str(spec.get("kind") or "").strip()
        runtime_type = str(spec.get("runtime_type") or "").strip()
        path = resolve_model_path(model_id, env=source) if spec.get("path") else ""

        config: Dict[str, Any] = {
            "model_key": next((key for key, value in registry.models.items() if value is spec), None),
            "kind": model_kind,
            "runtime_type": runtime_type,
            "path": path,
            "api_endpoint": runtime.get("api_endpoint"),
            "port": runtime.get("port"),
            "ctx_size": int(
                _pick_env_value(source, runtime.get("ctx_size_env")) or runtime.get("ctx_size_default") or 0
            ),
            "gpu_layers": int(
                _pick_env_value(source, runtime.get("gpu_layers_env")) or runtime.get("gpu_layers_default") or 0
            ),
            "quant": runtime.get("quant_default"),
        }

        if model_kind == "llm":
            config["type"] = "text"
            config["context_size"] = config["ctx_size"]
            config["n_gpu_layers"] = config["gpu_layers"]
        elif model_kind == "vision":
            config["type"] = "vision"
            config["context_size"] = config["ctx_size"]
            config["n_gpu_layers"] = config["gpu_layers"]
            mmproj_envs = [str(item) for item in (runtime.get("mmproj_envs") or [])]
            config["mmproj_path"] = _pick_env_value(source, *mmproj_envs) or runtime.get("mmproj_default")
        elif "embedder" in model_kind or model_kind == "reranker":
            config["type"] = "embedding" if model_kind != "reranker" else "reranker"
            config["context_size"] = config["ctx_size"]
            config["n_gpu_layers"] = config["gpu_layers"]
        else:
            config["type"] = model_kind or runtime_type or "component"

        payload[model_id] = config

    return payload


DEFAULT_GENERATION_PARAMS = {
    "temperature": float(os.getenv("TEMPERATURE", "0.5")),
    "top_p": float(os.getenv("TOP_P", "0.9")),
    "repetition_penalty": float(os.getenv("REPETITION_PENALTY", "1.2")),
    "max_tokens": int(os.getenv("MAX_TOKENS", "2048")),
}

ACTIVE_MODEL_ID = os.getenv("ACTIVE_MODEL_ID", "none")


def get_model_config(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    model_config = _build_model_config(env=env)
    if model_id not in model_config:
        raise ValueError(f"Unknown model: {model_id}")
    return model_config[model_id]


def get_all_models(*, env: Optional[Mapping[str, str]] = None) -> Dict[str, Dict[str, Any]]:
    return _build_model_config(env=env)


def get_default_params() -> Dict[str, Any]:
    return DEFAULT_GENERATION_PARAMS.copy()
