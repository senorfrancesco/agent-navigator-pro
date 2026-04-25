import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.model_manager.model_selection import (
    resolve_execution_plan,
    resolve_model_selection,
    resolve_user_model_selection,
)


def test_model_selection_prefers_primary_env(monkeypatch):
    monkeypatch.setenv("LEGAL_EMBEDDER_MODEL", "primary-embedder")
    monkeypatch.setenv("CHAINLIT_FALLBACK_RETRIEVAL_EMBEDDER_MODEL", "fallback-embedder")

    selection = resolve_model_selection("legal.embedder")

    assert selection.resolved_model_id == "primary-embedder"
    assert selection.source == "primary_env_override"
    assert "deprecated env" in str(selection.warning)


def test_model_selection_uses_fallback_env_with_explicit_warning(monkeypatch):
    monkeypatch.delenv("LEGAL_EMBEDDER_MODEL", raising=False)
    monkeypatch.setenv("CHAINLIT_FALLBACK_RETRIEVAL_EMBEDDER_MODEL", "fallback-embedder")

    selection = resolve_model_selection("legal.embedder")

    assert selection.resolved_model_id == "qwen3-embedding-0.6b"
    assert selection.fallback_model_id == "fallback-embedder"
    assert selection.source == "registry_primary_with_fallback_override"
    assert "deprecated env" in str(selection.warning)
    assert "fallback-embedder" in str(selection.warning)


def test_model_selection_uses_registry_primary_when_envs_missing(monkeypatch):
    monkeypatch.delenv("LEGAL_EMBEDDER_MODEL", raising=False)
    monkeypatch.delenv("CHAINLIT_FALLBACK_RETRIEVAL_EMBEDDER_MODEL", raising=False)

    selection = resolve_model_selection("legal.embedder")

    assert selection.resolved_model_id == "qwen3-embedding-0.6b"
    assert selection.fallback_model_id == "qwen3-embedding-0.6b"
    assert selection.source == "registry_primary"
    assert selection.warning is None


def test_execution_plan_resolves_requested_model_id_to_registry_role(monkeypatch):
    monkeypatch.delenv("LEGAL_EMBEDDER_MODEL", raising=False)
    monkeypatch.delenv("CHAINLIT_FALLBACK_RETRIEVAL_EMBEDDER_MODEL", raising=False)

    selection = resolve_execution_plan(requested_model_id="qwen-14b-llm")

    assert selection.requested_model_id == "qwen-14b-llm"
    assert selection.primary_model_id == "qwen-14b-llm"
    assert selection.fallback_model_id == "qwen-14b-llm"
    assert selection.resolved_model_id == "qwen-14b-llm"
    assert selection.role_key == "llm.default_chat"
    assert selection.fallback_available is False


def test_execution_plan_handles_unknown_requested_model_id(monkeypatch):
    monkeypatch.delenv("LEGAL_EMBEDDER_MODEL", raising=False)
    monkeypatch.delenv("CHAINLIT_FALLBACK_RETRIEVAL_EMBEDDER_MODEL", raising=False)

    selection = resolve_execution_plan(requested_model_id="unknown-model-id")

    assert selection.requested_model_id == "unknown-model-id"
    assert selection.requested_model_key is None
    assert selection.role_key == ""
    assert selection.resolved_model_id == "unknown-model-id"
    assert selection.fallback_available is False
    assert "no fallback role is available" in str(selection.warning)


def test_execution_plan_treats_registered_role_key_as_role_not_model_id(monkeypatch):
    monkeypatch.delenv("CHAINLIT_MODEL_PROFILE_LEGAL_COMPARE_MODEL", raising=False)
    monkeypatch.delenv("CHAINLIT_FALLBACK_LLM_MODEL", raising=False)

    selection = resolve_execution_plan(requested_model_id="llm.legal_compare")

    assert selection.requested_model_id is None
    assert selection.role_key == "llm.legal_compare"
    assert selection.primary_model_id == "qwen-14b-llm"
    assert selection.resolved_model_id == "qwen-14b-llm"
    assert selection.source == "registry_primary"


def test_user_model_selection_accepts_registered_selectable_model():
    selection = resolve_user_model_selection("qwen-14b-llm", required_capabilities=["supports_tools"])

    assert selection.exists_in_registry is True
    assert selection.user_selectable is True
    assert selection.compatible is True
    assert selection.missing_capabilities == []
    assert selection.capabilities["supports_tools"] is True


def test_user_model_selection_rejects_service_only_model():
    selection = resolve_user_model_selection("labse-embedding")

    assert selection.exists_in_registry is True
    assert selection.user_selectable is False
    assert selection.compatible is False
    assert selection.missing_capabilities == ["user_selectable"]


def test_user_model_selection_reports_unknown_model_id():
    selection = resolve_user_model_selection("unknown-model-id", required_capabilities=["supports_tools"])

    assert selection.exists_in_registry is False
    assert selection.compatible is False
    assert selection.missing_capabilities == ["user_selectable", "supports_tools"]
    assert "canonical registry" in str(selection.warning)
