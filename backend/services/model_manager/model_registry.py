from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_REGISTRY_PATH = BACKEND_ROOT / "config" / "models.yaml"
DEFAULT_MODEL_CAPABILITIES = {
    "supports_tools": False,
    "supports_vision": False,
    "supports_structured_output": False,
}


@dataclass(frozen=True)
class ModelRegistry:
    version: int
    models: Dict[str, Dict[str, Any]]
    roles: Dict[str, Dict[str, Any]]
    tiers: Dict[str, Dict[str, Any]]
    runtime: Dict[str, Any]


def _registry_path_from_env(env: Optional[Mapping[str, str]] = None) -> str:
    source = env if env is not None else os.environ
    return str(source.get("MODEL_REGISTRY_CONFIG_PATH") or DEFAULT_MODEL_REGISTRY_PATH)


@lru_cache(maxsize=8)
def _load_registry_from_path(registry_path: str) -> ModelRegistry:
    path = Path(registry_path)
    if not path.exists():
        raise FileNotFoundError(f"Model registry file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}

    version = int(payload.get("version") or 1)
    models = dict(payload.get("models") or {})
    roles = dict(payload.get("roles") or {})
    tiers = dict(payload.get("tiers") or {})
    runtime = dict(payload.get("runtime") or {})

    _validate_registry(path, models=models, roles=roles)
    return ModelRegistry(version=version, models=models, roles=roles, tiers=tiers, runtime=runtime)


def clear_model_registry_cache() -> None:
    _load_registry_from_path.cache_clear()


def get_model_registry(*, env: Optional[Mapping[str, str]] = None) -> ModelRegistry:
    return _load_registry_from_path(_registry_path_from_env(env))


def get_model_spec_by_key(model_key: str, *, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    registry = get_model_registry(env=env)
    spec = registry.models.get(model_key)
    if spec is None:
        raise KeyError(f"Unknown model key: {model_key}")
    return dict(spec)


def get_model_spec_by_id(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    registry = get_model_registry(env=env)
    for model_key, spec in registry.models.items():
        if str(spec.get("model_id") or "") == model_id:
            return {"model_key": model_key, **dict(spec)}
    raise KeyError(f"Unknown model id: {model_id}")


def get_model_key_by_id(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> str:
    return str(get_model_spec_by_id(model_id, env=env).get("model_key") or "")


def get_model_display_name(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> str:
    spec = get_model_spec_by_id(model_id, env=env)
    return str(spec.get("display_name") or spec.get("model_id") or model_id)


def get_model_capabilities(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> Dict[str, bool]:
    spec = get_model_spec_by_id(model_id, env=env)
    payload = dict(DEFAULT_MODEL_CAPABILITIES)
    raw_capabilities = spec.get("capabilities") or {}
    if isinstance(raw_capabilities, Mapping):
        for key in DEFAULT_MODEL_CAPABILITIES:
            if key in raw_capabilities:
                payload[key] = bool(raw_capabilities.get(key))
    return payload


def is_user_selectable_model(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> bool:
    spec = get_model_spec_by_id(model_id, env=env)
    return bool(spec.get("user_selectable"))


def get_role_spec(role_key: str, *, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    registry = get_model_registry(env=env)
    spec = registry.roles.get(role_key)
    if spec is None:
        raise KeyError(f"Unknown model role: {role_key}")
    return dict(spec)


def get_roles_for_model_key(model_key: str, *, env: Optional[Mapping[str, str]] = None) -> Dict[str, Dict[str, Any]]:
    registry = get_model_registry(env=env)
    matches: Dict[str, Dict[str, Any]] = {}
    for role_key, spec in registry.roles.items():
        if str(spec.get("primary_model") or "") == model_key or str(spec.get("fallback_model") or "") == model_key:
            matches[role_key] = dict(spec)
    return matches


def get_roles_for_model_id(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> Dict[str, Dict[str, Any]]:
    try:
        model_key = get_model_key_by_id(model_id, env=env)
    except KeyError:
        return {}
    return get_roles_for_model_key(model_key, env=env)


def get_preferred_role_for_model_id(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    registry = get_model_registry(env=env)
    try:
        model_key = get_model_key_by_id(model_id, env=env)
    except KeyError:
        return None
    preferred_role = str(registry.runtime.get("default_active_heavy_role") or "").strip() or None
    if preferred_role:
        preferred_spec = registry.roles.get(preferred_role)
        if preferred_spec and (
            str(preferred_spec.get("primary_model") or "") == model_key
            or str(preferred_spec.get("fallback_model") or "") == model_key
        ):
            return preferred_role

    for role_key, spec in registry.roles.items():
        if str(spec.get("primary_model") or "") == model_key:
            return role_key

    for role_key, spec in registry.roles.items():
        if str(spec.get("fallback_model") or "") == model_key:
            return role_key

    return None


def get_tier_spec(tier_name: str, *, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    registry = get_model_registry(env=env)
    spec = registry.tiers.get(tier_name)
    if spec is None:
        raise KeyError(f"Unknown tier spec: {tier_name}")
    return dict(spec)


def resolve_registry_model_id(model_key: str, *, env: Optional[Mapping[str, str]] = None) -> str:
    return str(get_model_spec_by_key(model_key, env=env).get("model_id") or "")


def list_registered_model_ids(*, env: Optional[Mapping[str, str]] = None) -> List[str]:
    registry = get_model_registry(env=env)
    return [str(spec.get("model_id") or "") for spec in registry.models.values() if spec.get("model_id")]


def get_runtime_config(*, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    return dict(get_model_registry(env=env).runtime)


def _validate_registry(path: Path, *, models: Dict[str, Dict[str, Any]], roles: Dict[str, Dict[str, Any]]) -> None:
    if not models:
        raise ValueError(f"Model registry {path} does not define any models")
    if not roles:
        raise ValueError(f"Model registry {path} does not define any roles")

    seen_model_ids: Dict[str, str] = {}
    for model_key, spec in models.items():
        model_id = str(spec.get("model_id") or "").strip()
        if not model_id:
            raise ValueError(f"Model registry {path} has empty model_id for key {model_key}")
        previous_key = seen_model_ids.get(model_id)
        if previous_key is not None:
            raise ValueError(
                f"Model registry {path} duplicates model_id {model_id!r} for keys {previous_key!r} and {model_key!r}"
            )
        seen_model_ids[model_id] = model_key

        raw_capabilities = spec.get("capabilities")
        if raw_capabilities is not None and not isinstance(raw_capabilities, Mapping):
            raise ValueError(f"Model {model_key} in {path} defines non-mapping capabilities")
        if raw_capabilities is not None:
            for key in DEFAULT_MODEL_CAPABILITIES:
                value = raw_capabilities.get(key)
                if value is not None and not isinstance(value, bool):
                    raise ValueError(f"Model {model_key} in {path} defines non-boolean capability {key!r}")

        raw_user_selectable = spec.get("user_selectable")
        if raw_user_selectable is not None and not isinstance(raw_user_selectable, bool):
            raise ValueError(f"Model {model_key} in {path} defines non-boolean user_selectable")

        raw_generation_defaults = spec.get("generation_defaults")
        if raw_generation_defaults is not None and not isinstance(raw_generation_defaults, Mapping):
            raise ValueError(f"Model {model_key} in {path} defines non-mapping generation_defaults")

        raw_load_defaults = spec.get("load_defaults")
        if raw_load_defaults is not None and not isinstance(raw_load_defaults, Mapping):
            raise ValueError(f"Model {model_key} in {path} defines non-mapping load_defaults")

    for role_key, spec in roles.items():
        primary_model = str(spec.get("primary_model") or "").strip()
        fallback_model = str(spec.get("fallback_model") or "").strip()
        if not primary_model:
            raise ValueError(f"Role {role_key} in {path} does not define primary_model")
        if not fallback_model:
            raise ValueError(f"Role {role_key} in {path} does not define fallback_model")
        if primary_model not in models:
            raise ValueError(f"Role {role_key} references unknown primary model key {primary_model!r}")
        if fallback_model not in models:
            raise ValueError(f"Role {role_key} references unknown fallback model key {fallback_model!r}")
