import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.model_manager.model_registry import (
    clear_model_registry_cache,
    get_model_capabilities,
    get_model_display_name,
    get_model_registry,
    get_role_spec,
    is_user_selectable_model,
)


@pytest.fixture(autouse=True)
def _clear_registry_cache():
    clear_model_registry_cache()
    yield
    clear_model_registry_cache()


def test_model_registry_loads_default_yaml():
    registry = get_model_registry()

    assert registry.version == 1
    assert "qwen14b_llm" in registry.models
    assert "qwen32b_llm" in registry.models
    assert "bge_m3_embedder" in registry.models
    assert "llm.default_chat" in registry.roles
    assert registry.runtime["default_active_heavy_role"] == "llm.default_chat"
    assert get_model_display_name("qwen-14b-llm") == "Qwen2.5 14B Instruct"
    assert get_model_display_name("qwen-32b-llm") == "Qwen2.5 32B Instruct"
    assert get_model_capabilities("qwen-vl-8b") == {
        "supports_tools": True,
        "supports_vision": True,
        "supports_structured_output": True,
    }
    assert is_user_selectable_model("qwen-14b-llm") is True
    assert is_user_selectable_model("qwen-32b-llm") is True
    assert is_user_selectable_model("labse-embedding") is False
    assert registry.models["qwen32b_llm"]["runtime_type"] == "gguf"
    assert registry.models["qwen32b_llm"]["path"]["canonical_env"] == "MODEL_PATH_QWEN32B"
    assert registry.roles["llm.low_vram"]["primary_model"] == "phi3_mini_q4_llm"
    assert registry.tiers["tier_3"]["use_reranker"] is False
    assert registry.tiers["tier_4"]["use_reranker"] is False


def test_model_registry_can_be_overridden_via_env(monkeypatch, tmp_path: Path):
    registry_path = tmp_path / "models.yaml"
    registry_path.write_text(
        """
version: 1
models:
  tiny:
    model_id: tiny-llm
    kind: llm
    runtime_type: gguf
    path:
      canonical_env: MODEL_PATH_LLM
      legacy_envs: []
      default: /tmp/tiny.gguf
    runtime:
      api_endpoint: /v1/completions
      port: 9001
      ctx_size_default: 2048
      gpu_layers_default: 0
roles:
  llm.default_chat:
    role_label: tiny llm
    primary_model: tiny
    fallback_model: tiny
tiers: {}
runtime:
  default_active_heavy_role: llm.default_chat
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_REGISTRY_CONFIG_PATH", str(registry_path))

    role = get_role_spec("llm.default_chat")
    registry = get_model_registry()

    assert role["primary_model"] == "tiny"
    assert registry.models["tiny"]["model_id"] == "tiny-llm"


def test_model_registry_rejects_invalid_role_reference(monkeypatch, tmp_path: Path):
    registry_path = tmp_path / "broken.yaml"
    registry_path.write_text(
        """
version: 1
models:
  tiny:
    model_id: tiny-llm
    kind: llm
roles:
  llm.default_chat:
    role_label: broken
    primary_model: missing
    fallback_model: tiny
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_REGISTRY_CONFIG_PATH", str(registry_path))

    with pytest.raises(ValueError, match="unknown primary model key"):
        get_model_registry()


def test_model_registry_rejects_non_boolean_capability(monkeypatch, tmp_path: Path):
    registry_path = tmp_path / "broken-capabilities.yaml"
    registry_path.write_text(
        """
version: 1
models:
  tiny:
    model_id: tiny-llm
    kind: llm
    capabilities:
      supports_tools: "sometimes"
roles:
  llm.default_chat:
    role_label: tiny
    primary_model: tiny
    fallback_model: tiny
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_REGISTRY_CONFIG_PATH", str(registry_path))

    with pytest.raises(ValueError, match="non-boolean capability"):
        get_model_registry()
