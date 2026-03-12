import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.ui_control_plane import resolve_effective_settings


def test_resolve_effective_settings_defaults(monkeypatch):
    monkeypatch.setenv("CHAINLIT_DEFAULT_TEMPERATURE", "0.7")
    monkeypatch.setenv("CHAINLIT_DEFAULT_TOP_P", "0.9")
    monkeypatch.setenv("CHAINLIT_DEFAULT_MAX_TOKENS", "2048")

    effective = resolve_effective_settings({})

    assert effective["assistant_mode"] == "general_chat"
    assert effective["runtime_mode"] == "auto"
    assert effective["rag_scope"] == "off"
    assert effective["model_profile"] == "default-chat"
    assert effective["prompt_profile"] == "default-assistant"
    assert effective["tool_scope"] == "chat"
    assert effective["knowledge_collection_id"] is None
    assert effective["custom_system_prompt"] is None
    assert effective["device_mode"] == "auto"
    assert effective["context_budget_profile"] == "standard"
    assert effective["intent_embedder_profile"] == "intent-default"
    assert effective["retrieval_embedder_profile"] == "legal-default"
    assert effective["generation"] == {
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 2048,
    }
    assert effective["resolved_model_id"] == "qwen-14b-llm"
    assert effective["resolved_intent_embedder_model_id"] == "qwen3-embedding-0.6b"
    assert effective["resolved_retrieval_embedder_model_id"] == "labse-embedding"


def test_specific_tasks_preset_defaults_to_session_rag():
    effective = resolve_effective_settings({"assistant_mode": "specific_tasks"})

    assert effective["assistant_mode"] == "specific_tasks"
    assert effective["runtime_mode"] == "specialized_tasks"
    assert effective["rag_scope"] == "session_rag"
    assert effective["model_profile"] == "legal-compare"
    assert effective["prompt_profile"] == "task-router"
    assert effective["tool_scope"] == "domain_tasks"
    assert effective["device_mode"] == "prefer-gpu"
    assert effective["context_budget_profile"] == "legal-compare"
    assert effective["resolved_retrieval_embedder_model_id"] == "labse-embedding"


def test_rag_qa_preset_defaults_to_knowledge_base_rag():
    effective = resolve_effective_settings({"assistant_mode": "rag_qa"})

    assert effective["assistant_mode"] == "rag_qa"
    assert effective["runtime_mode"] == "specialized_tasks"
    assert effective["rag_scope"] == "knowledge_base_rag"
    assert effective["model_profile"] == "legal-compare"
    assert effective["prompt_profile"] == "strict-grounded-doc-qa"
    assert effective["tool_scope"] == "document_qa"
    assert effective["device_mode"] == "prefer-gpu"
    assert effective["resolved_retrieval_embedder_model_id"] == "labse-embedding"


def test_explicit_overrides_win_over_preset_defaults():
    effective = resolve_effective_settings(
        {
            "assistant_mode": "specific_tasks",
            "runtime_mode": "chat_only",
            "rag_scope": "knowledge_base_rag",
            "model_profile": "low-vram",
            "prompt_profile": "coding-assistant",
            "tool_scope": "coding",
            "knowledge_collection_id": "legal",
            "generation_overrides": {
                "temperature": 0.15,
                "top_p": 0.42,
                "max_tokens": 512,
            },
        }
    )

    assert effective["assistant_mode"] == "specific_tasks"
    assert effective["runtime_mode"] == "chat_only"
    assert effective["rag_scope"] == "knowledge_base_rag"
    assert effective["model_profile"] == "low-vram"
    assert effective["prompt_profile"] == "coding-assistant"
    assert effective["tool_scope"] == "coding"
    assert effective["knowledge_collection_id"] == "legal"
    assert effective["device_mode"] == "low-vram"
    assert effective["context_budget_profile"] == "compact"
    assert effective["retrieval_embedder_profile"] == "low-vram"
    assert effective["generation"] == {
        "temperature": 0.15,
        "top_p": 0.42,
        "max_tokens": 512,
    }


def test_generation_overrides_are_clamped_and_empty_prompt_is_normalized():
    effective = resolve_effective_settings(
        {
            "assistant_mode": "rag_qa",
            "custom_system_prompt": "   ",
            "generation_overrides": {
                "temperature": 3.7,
                "top_p": -2.0,
                "max_tokens": 999999,
            },
        }
    )

    assert effective["custom_system_prompt"] is None
    assert effective["generation"] == {
        "temperature": 2.0,
        "top_p": 0.0,
        "max_tokens": 4096,
    }


def test_rag_scope_is_independent_from_runtime_mode():
    effective = resolve_effective_settings(
        {
            "assistant_mode": "general_chat",
            "rag_scope": "knowledge_base_rag",
        }
    )

    assert effective["assistant_mode"] == "general_chat"
    assert effective["runtime_mode"] == "chat_only"
    assert effective["rag_scope"] == "knowledge_base_rag"


def test_effective_settings_include_env_resolved_model_and_generation_defaults(monkeypatch):
    monkeypatch.setenv("CHAINLIT_DEFAULT_TEMPERATURE", "0.61")
    monkeypatch.setenv("CHAINLIT_DEFAULT_TOP_P", "0.73")
    monkeypatch.setenv("CHAINLIT_DEFAULT_MAX_TOKENS", "3072")
    monkeypatch.setenv("CHAINLIT_MODEL_PROFILE_LONG_CONTEXT_MODEL", "qwen-coder-32b")
    monkeypatch.setenv("CHAINLIT_INTENT_EMBEDDER_PROFILE_DEFAULT_MODEL", "qwen3-embedding-0.6b-custom")
    monkeypatch.setenv("CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LEGAL_DEFAULT_MODEL", "labse-custom")
    monkeypatch.setenv("CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LOW_VRAM_MODEL", "qwen3-embedding-low-vram")

    default_effective = resolve_effective_settings({})
    long_context_effective = resolve_effective_settings({"model_profile": "long-context"})

    assert default_effective["generation"] == {
        "temperature": 0.61,
        "top_p": 0.73,
        "max_tokens": 3072,
    }
    assert default_effective["resolved_model_id"] == "qwen-14b-llm"
    assert default_effective["resolved_intent_embedder_model_id"] == "qwen3-embedding-0.6b-custom"
    assert default_effective["resolved_retrieval_embedder_model_id"] == "labse-custom"
    assert long_context_effective["resolved_model_id"] == "qwen-coder-32b"
    assert long_context_effective["device_mode"] == "prefer-gpu"
    assert long_context_effective["context_budget_profile"] == "long-context"

    low_vram_effective = resolve_effective_settings({"model_profile": "low-vram"})
    assert low_vram_effective["resolved_retrieval_embedder_model_id"] == "qwen3-embedding-low-vram"


def test_legacy_model_profile_aliases_resolve_to_canonical_profiles():
    analyst_effective = resolve_effective_settings({"model_profile": "analyst"})
    coder_effective = resolve_effective_settings({"model_profile": "coder"})

    assert analyst_effective["model_profile"] == "legal-compare"
    assert analyst_effective["context_budget_profile"] == "legal-compare"
    assert coder_effective["model_profile"] == "long-context"
    assert coder_effective["context_budget_profile"] == "long-context"
