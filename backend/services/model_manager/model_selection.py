from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional

from services.model_manager.model_registry import (
    get_model_key_by_id,
    get_preferred_role_for_model_id,
    get_role_spec,
    resolve_registry_model_id,
)


@dataclass(frozen=True)
class ModelSelection:
    requested_model_id: Optional[str]
    requested_model_key: Optional[str]
    role_key: str
    role_label: str
    primary_model_key: Optional[str]
    fallback_model_key: Optional[str]
    primary_env_var: Optional[str]
    fallback_env_var: Optional[str]
    primary_model_id: str
    fallback_model_id: str
    resolved_model_id: str
    fallback_available: bool
    source: str
    warning: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_env_value(source: Mapping[str, str], env_var: Optional[str]) -> Optional[str]:
    if not env_var:
        return None
    value = source.get(env_var)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _safe_get_model_key_by_id(model_id: Optional[str], *, env: Mapping[str, str]) -> Optional[str]:
    if not model_id:
        return None
    try:
        return get_model_key_by_id(model_id, env=env)
    except KeyError:
        return None


def resolve_model_selection(
    role_key: Optional[str] = None,
    *,
    requested_model_id: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> ModelSelection:
    return resolve_execution_plan(role_key=role_key, requested_model_id=requested_model_id, env=env)


def resolve_execution_plan(
    *,
    role_key: Optional[str] = None,
    requested_model_id: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> ModelSelection:
    source = env if env is not None else os.environ
    role_key = str(role_key or "").strip() or None
    requested_model_id = str(requested_model_id or "").strip() or None

    if role_key is None and requested_model_id is None:
        raise ValueError("resolve_execution_plan requires role_key or requested_model_id")

    if role_key is None and requested_model_id is not None:
        try:
            role_key = get_preferred_role_for_model_id(requested_model_id, env=source)
        except KeyError:
            role_key = None

    role_spec = get_role_spec(role_key, env=source) if role_key is not None else None
    if role_spec is None:
        if requested_model_id is None:
            raise ValueError("Unable to resolve model execution plan")
        model_key = _safe_get_model_key_by_id(requested_model_id, env=source)
        return ModelSelection(
            requested_model_id=requested_model_id,
            requested_model_key=model_key,
            role_key="",
            role_label="",
            primary_model_key=model_key,
            fallback_model_key=model_key,
            primary_env_var=None,
            fallback_env_var=None,
            primary_model_id=requested_model_id,
            fallback_model_id=requested_model_id,
            resolved_model_id=requested_model_id,
            fallback_available=False,
            source="model_id_only",
            warning="Model id is not registered in the canonical registry; no fallback role is available.",
        )

    primary_env_var = str(role_spec.get("primary_override_env") or "").strip() or None
    fallback_env_var = str(role_spec.get("fallback_override_env") or "").strip() or None
    primary_model_key = str(role_spec["primary_model"])
    fallback_model_key = str(role_spec["fallback_model"])
    primary_model_id = resolve_registry_model_id(primary_model_key, env=source)
    fallback_model_id = resolve_registry_model_id(fallback_model_key, env=source)
    role_label = str(role_spec.get("role_label") or role_key)

    primary_override = _normalize_env_value(source, primary_env_var)
    if primary_override:
        return ModelSelection(
            requested_model_id=requested_model_id,
            requested_model_key=_safe_get_model_key_by_id(requested_model_id, env=source) or primary_model_key,
            role_key=role_key,
            role_label=role_label,
            primary_model_key=primary_model_key,
            fallback_model_key=fallback_model_key,
            primary_env_var=primary_env_var,
            fallback_env_var=fallback_env_var,
            primary_model_id=primary_model_id,
            fallback_model_id=fallback_model_id,
            resolved_model_id=primary_override,
            fallback_available=bool(fallback_model_id and fallback_model_id != primary_override),
            source="primary_env_override",
            warning=(
                f"Primary override for role '{role_label}' was taken from deprecated env {primary_env_var}. "
                f"Canonical source of truth is the model registry."
            ),
        )

    fallback_override = _normalize_env_value(source, fallback_env_var)
    if fallback_override:
        return ModelSelection(
            requested_model_id=requested_model_id,
            requested_model_key=_safe_get_model_key_by_id(requested_model_id, env=source) or primary_model_key,
            role_key=role_key,
            role_label=role_label,
            primary_model_key=primary_model_key,
            fallback_model_key=fallback_model_key,
            primary_env_var=primary_env_var,
            fallback_env_var=fallback_env_var,
            primary_model_id=primary_model_id,
            fallback_model_id=fallback_override,
            resolved_model_id=primary_model_id,
            fallback_available=True,
            source="registry_primary_with_fallback_override",
            warning=(
                f"Fallback override for role '{role_label}' was taken from deprecated env {fallback_env_var}. "
                f"Override value: '{fallback_override}'. Canonical source of truth is the model registry."
            ),
        )

    return ModelSelection(
        requested_model_id=requested_model_id,
        requested_model_key=_safe_get_model_key_by_id(requested_model_id, env=source) or primary_model_key,
        role_key=role_key,
        role_label=role_label,
        primary_model_key=primary_model_key,
        fallback_model_key=fallback_model_key,
        primary_env_var=primary_env_var,
        fallback_env_var=fallback_env_var,
        primary_model_id=primary_model_id,
        fallback_model_id=fallback_model_id,
        resolved_model_id=primary_model_id,
        fallback_available=bool(fallback_model_id and fallback_model_id != primary_model_id),
        source="registry_primary",
        warning=None,
    )
