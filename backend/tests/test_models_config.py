import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.model_manager import models_config


@pytest.fixture(autouse=True)
def _reset_model_path_env(monkeypatch):
    for env_name in (
        "MODEL_PATH_LLM",
        "MODEL_PATH_VLM",
        "MODEL_PATH_EMBEDDING_INTENT",
        "MODEL_PATH_EMBEDDING_RETRIEVAL",
        "MODEL_PATH_QWEN32B",
        "MODEL_PATH_LLAMA31_8B",
        "MODEL_PATH_MISTRAL_7B",
        "MODEL_PATH_PHI3_MINI_Q4",
        "MODEL_PATH_SAIGA_YANDEXGPT_8B",
        "MODEL_PATH_YANDEXGPT_LITE_8B",
        "MODEL_PATH_BGE_M3",
        "MODEL_PATH_MULTILINGUAL_E5",
        "MODEL_PATH_XLMR_XNLI",
        "MODEL_MMPROJ_PATH_VLM",
        "MODEL_PATH_QWEN14B",
        "MODEL_PATH_QWENVL",
        "MODEL_PATH_LABSE",
        "MODEL_PATH_QWEN3_EMBEDDING_06B",
    ):
        monkeypatch.delenv(env_name, raising=False)


def test_models_config_prefers_canonical_model_path_env(monkeypatch):
    monkeypatch.setenv("MODEL_PATH_LLM", "/models/runtime/llm.gguf")
    monkeypatch.setenv("MODEL_PATH_VLM", "/models/runtime/vlm.gguf")
    monkeypatch.setenv("MODEL_PATH_EMBEDDING_INTENT", "/models/runtime/intent")
    monkeypatch.setenv("MODEL_PATH_EMBEDDING_RETRIEVAL", "/models/runtime/retrieval")
    monkeypatch.setenv("MODEL_PATH_QWEN14B", "/legacy/llm.gguf")
    monkeypatch.setenv("MODEL_PATH_QWENVL", "/legacy/vlm.gguf")
    monkeypatch.setenv("MODEL_PATH_QWEN3_EMBEDDING_06B", "/legacy/intent")
    monkeypatch.setenv("MODEL_PATH_LABSE", "/legacy/retrieval")

    assert models_config.get_model_config("qwen-14b-llm")["path"] == "/models/runtime/llm.gguf"
    assert models_config.get_model_config("qwen-vl-8b")["path"] == "/models/runtime/vlm.gguf"
    assert models_config.get_model_config("qwen3-embedding-0.6b")["path"] == "/models/runtime/intent"
    assert models_config.get_model_config("labse-embedding")["path"] == "/models/runtime/retrieval"


def test_models_config_falls_back_to_legacy_model_path_aliases(monkeypatch):
    monkeypatch.setenv("MODEL_PATH_QWEN14B", "/legacy/llm.gguf")
    monkeypatch.setenv("MODEL_PATH_QWENVL", "/legacy/vlm.gguf")
    monkeypatch.setenv("MODEL_PATH_QWEN3_EMBEDDING_06B", "/legacy/intent")
    monkeypatch.setenv("MODEL_PATH_LABSE", "/legacy/retrieval")

    assert models_config.get_model_config("qwen-14b-llm")["path"] == "/legacy/llm.gguf"
    assert models_config.get_model_config("qwen-vl-8b")["path"] == "/legacy/vlm.gguf"
    assert models_config.get_model_config("qwen3-embedding-0.6b")["path"] == "/legacy/intent"
    assert models_config.get_model_config("labse-embedding")["path"] == "/legacy/retrieval"


def test_models_config_does_not_fall_back_to_registry_default_path_when_env_missing():
    assert models_config.get_model_config("qwen-14b-llm")["path"] == ""
    assert models_config.get_model_config("qwen-vl-8b")["path"] == ""
    assert models_config.get_model_config("qwen3-embedding-0.6b")["path"] == ""
    assert models_config.get_model_config("labse-embedding")["path"] == ""


def test_models_config_surfaces_registry_metadata(monkeypatch):
    monkeypatch.setenv("MODEL_PATH_VLM", "/models/runtime/vlm.gguf")
    monkeypatch.setenv("MODEL_MMPROJ_PATH_VLM", "/models/runtime/mmproj.gguf")

    llm_config = models_config.get_model_config("qwen-14b-llm")
    vlm_config = models_config.get_model_config("qwen-vl-8b")
    embedder_config = models_config.get_model_config("labse-embedding")

    assert llm_config["display_name"] == "Qwen2.5 14B Instruct"
    assert llm_config["user_selectable"] is True
    assert llm_config["capabilities"] == {
        "supports_tools": True,
        "supports_vision": False,
        "supports_structured_output": True,
    }
    assert llm_config["generation_defaults"]["temperature"] == pytest.approx(0.5)
    assert llm_config["load_defaults"]["ctx_size"] == 16384

    assert vlm_config["user_selectable"] is True
    assert vlm_config["capabilities"]["supports_vision"] is True
    assert vlm_config["load_defaults"]["mmproj_path"] == "/models/runtime/mmproj.gguf"

    assert embedder_config["user_selectable"] is False
    assert embedder_config["capabilities"]["supports_tools"] is False


def test_models_config_resolves_inventory_backed_paths(monkeypatch):
    monkeypatch.setenv("MODEL_PATH_QWEN32B", "/models/runtime/qwen32b.gguf")
    monkeypatch.setenv("MODEL_PATH_BGE_M3", "/models/runtime/bge-m3")

    qwen32_config = models_config.get_model_config("qwen-32b-llm")
    bge_config = models_config.get_model_config("bge-m3-embedding")

    assert qwen32_config["path"] == "/models/runtime/qwen32b.gguf"
    assert qwen32_config["display_name"] == "Qwen2.5 32B Instruct"
    assert qwen32_config["runtime_type"] == "gguf"
    assert qwen32_config["user_selectable"] is True
    assert qwen32_config["load_defaults"]["quant"] == "Q4_K_M"

    assert bge_config["path"] == "/models/runtime/bge-m3"
    assert bge_config["runtime_type"] == "st"
    assert bge_config["user_selectable"] is False
    assert bge_config["type"] == "embedding"
