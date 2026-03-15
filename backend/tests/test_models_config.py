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
