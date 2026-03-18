import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.model_manager.model_selection import resolve_model_selection


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
