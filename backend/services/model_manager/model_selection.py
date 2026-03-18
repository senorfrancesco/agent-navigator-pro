from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional

from services.model_manager.model_registry import get_role_spec, resolve_registry_model_id


@dataclass(frozen=True)
class ModelSelection:
    role_key: str
    role_label: str
    primary_env_var: Optional[str]
    fallback_env_var: Optional[str]
    primary_model_id: str
    fallback_model_id: str
    resolved_model_id: str
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


def resolve_model_selection(role_key: str, *, env: Optional[Mapping[str, str]] = None) -> ModelSelection:
    source = env if env is not None else os.environ
    role_spec = get_role_spec(role_key, env=source)

    primary_env_var = str(role_spec.get("primary_override_env") or "").strip() or None
    fallback_env_var = str(role_spec.get("fallback_override_env") or "").strip() or None
    primary_model_id = resolve_registry_model_id(str(role_spec["primary_model"]), env=source)
    fallback_model_id = resolve_registry_model_id(str(role_spec["fallback_model"]), env=source)
    role_label = str(role_spec.get("role_label") or role_key)

    primary_override = _normalize_env_value(source, primary_env_var)
    if primary_override:
        return ModelSelection(
            role_key=role_key,
            role_label=role_label,
            primary_env_var=primary_env_var,
            fallback_env_var=fallback_env_var,
            primary_model_id=primary_model_id,
            fallback_model_id=fallback_model_id,
            resolved_model_id=primary_override,
            source="primary_env_override",
            warning=(
                f"Primary override for role '{role_label}' was taken from deprecated env {primary_env_var}. "
                f"Canonical source of truth is the model registry."
            ),
        )

    fallback_override = _normalize_env_value(source, fallback_env_var)
    if fallback_override:
        return ModelSelection(
            role_key=role_key,
            role_label=role_label,
            primary_env_var=primary_env_var,
            fallback_env_var=fallback_env_var,
            primary_model_id=primary_model_id,
            fallback_model_id=fallback_override,
            resolved_model_id=primary_model_id,
            source="registry_primary_with_fallback_override",
            warning=(
                f"Fallback override for role '{role_label}' was taken from deprecated env {fallback_env_var}. "
                f"Override value: '{fallback_override}'. Canonical source of truth is the model registry."
            ),
        )

    return ModelSelection(
        role_key=role_key,
        role_label=role_label,
        primary_env_var=primary_env_var,
        fallback_env_var=fallback_env_var,
        primary_model_id=primary_model_id,
        fallback_model_id=fallback_model_id,
        resolved_model_id=primary_model_id,
        source="registry_primary",
        warning=None,
    )
