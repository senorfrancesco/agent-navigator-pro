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


DEFAULT_GENERATION_PARAMS = {
    "temperature": float(os.getenv("TEMPERATURE", "0.5")),
    "top_p": float(os.getenv("TOP_P", "0.9")),
    "repetition_penalty": float(os.getenv("REPETITION_PENALTY", "1.2")),
    "max_tokens": int(os.getenv("MAX_TOKENS", "2048")),
}

DEFAULT_MODEL_CAPABILITIES = {
    "supports_tools": False,
    "supports_vision": False,
    "supports_structured_output": False,
}


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

    resolved = _pick_env_value(source, canonical_env, *legacy_envs)
    if resolved:
        return resolved
    return ""


def get_model_path_env_contract(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    source = env if env is not None else os.environ
    spec = get_model_spec_by_id(model_id, env=source)
    path_spec = dict(spec.get("path") or {})
    canonical_env = str(path_spec.get("canonical_env") or "").strip()
    legacy_envs = [str(name).strip() for name in (path_spec.get("legacy_envs") or []) if str(name).strip()]
    return {
        "canonical_env": canonical_env,
        "legacy_envs": legacy_envs,
    }


def _resolve_generation_defaults(spec: Mapping[str, Any]) -> Dict[str, Any]:
    payload = get_default_params()
    payload.update(dict(spec.get("generation_defaults") or {}))
    return payload


def _resolve_capabilities(spec: Mapping[str, Any]) -> Dict[str, bool]:
    payload = dict(DEFAULT_MODEL_CAPABILITIES)
    payload.update(
        {
            key: bool(value)
            for key, value in dict(spec.get("capabilities") or {}).items()
            if key in DEFAULT_MODEL_CAPABILITIES
        }
    )
    return payload


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
            "display_name": str(spec.get("display_name") or model_id),
            "kind": model_kind,
            "runtime_type": runtime_type,
            "user_selectable": bool(spec.get("user_selectable")),
            "capabilities": _resolve_capabilities(spec),
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
            "generation_defaults": _resolve_generation_defaults(spec),
            "load_defaults": dict(spec.get("load_defaults") or {}),
        }

        if config["ctx_size"]:
            config["load_defaults"]["ctx_size"] = config["ctx_size"]
        if runtime.get("gpu_layers_env") or runtime.get("gpu_layers_default") is not None:
            config["load_defaults"]["gpu_layers"] = config["gpu_layers"]
        if config["quant"]:
            config["load_defaults"]["quant"] = config["quant"]

        if model_kind == "llm":
            config["type"] = "text"
            config["context_size"] = config["ctx_size"]
            config["n_gpu_layers"] = config["gpu_layers"]
        elif model_kind == "vision":
            config["type"] = "vision"
            config["context_size"] = config["ctx_size"]
            config["n_gpu_layers"] = config["gpu_layers"]
            mmproj_envs = [str(item) for item in (runtime.get("mmproj_envs") or [])]
            config["mmproj_path"] = _pick_env_value(source, *mmproj_envs) or ""
            if config["mmproj_path"]:
                config["load_defaults"]["mmproj_path"] = config["mmproj_path"]
        elif "embedder" in model_kind or model_kind == "reranker":
            config["type"] = "embedding" if model_kind != "reranker" else "reranker"
            config["context_size"] = config["ctx_size"]
            config["n_gpu_layers"] = config["gpu_layers"]
        else:
            config["type"] = model_kind or runtime_type or "component"

        payload[model_id] = config

    return payload


def get_model_config(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    model_config = _build_model_config(env=env)
    if model_id not in model_config:
        raise ValueError(f"Unknown model: {model_id}")
    return model_config[model_id]


def get_all_models(*, env: Optional[Mapping[str, str]] = None) -> Dict[str, Dict[str, Any]]:
    return _build_model_config(env=env)


def get_default_params() -> Dict[str, Any]:
    return DEFAULT_GENERATION_PARAMS.copy()
