"""Конфигурация моделей с env-based resolution и backward compatibility."""

import os
from typing import Any, Dict, Mapping, Optional

from dotenv import load_dotenv

load_dotenv()

_MODEL_PATH_SPECS: Dict[str, Dict[str, Any]] = {
    "qwen-14b-llm": {
        "canonical_env": "MODEL_PATH_LLM",
        "legacy_envs": ("MODEL_PATH_QWEN14B",),
        "default_path": "./models/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf",
    },
    "qwen-vl-8b": {
        "canonical_env": "MODEL_PATH_VLM",
        "legacy_envs": ("MODEL_PATH_QWENVL",),
        "default_path": "./models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf",
    },
    "labse-embedding": {
        "canonical_env": "MODEL_PATH_EMBEDDING_RETRIEVAL",
        "legacy_envs": ("MODEL_PATH_LABSE",),
        "default_path": "./models/st/LaBSE",
    },
    "qwen3-embedding-0.6b": {
        "canonical_env": "MODEL_PATH_EMBEDDING_INTENT",
        "legacy_envs": ("MODEL_PATH_QWEN3_EMBEDDING_06B",),
        "default_path": "./models/st/Qwen3-Embedding-0.6B",
    },
}


def _pick_env_value(*names: Optional[str]) -> Optional[str]:
    for name in names:
        if not name:
            continue
        value = os.getenv(name)
        if value:
            return value
    return None


def resolve_model_path(model_id: str, *, env: Optional[Mapping[str, str]] = None) -> str:
    source = env if env is not None else os.environ
    spec = _MODEL_PATH_SPECS.get(model_id)
    if not spec:
        raise ValueError(f"Unknown model path config: {model_id}")

    for key in (spec["canonical_env"], *spec["legacy_envs"]):
        value = source.get(key)
        if value:
            return value
    return spec["default_path"]


def _build_model_config() -> Dict[str, Dict[str, Any]]:
    return {
        "qwen-14b-llm": {
            "type": "text",
            "path": resolve_model_path("qwen-14b-llm"),
            "n_gpu_layers": int(os.getenv("N_GPU_LAYERS_QWEN14B", "-1")),
            "context_size": int(os.getenv("CONTEXT_SIZE_QWEN14B", "16384")),
            "api_endpoint": "/v1/completions",
        },
        "qwen-vl-8b": {
            "type": "vision",
            "path": resolve_model_path("qwen-vl-8b"),
            "mmproj_path": _pick_env_value("MODEL_MMPROJ_PATH_VLM", "MMPROJ_PATH")
            or "./models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf",
            "n_gpu_layers": int(os.getenv("N_GPU_LAYERS_QWENVL", "-1")),
            "context_size": int(os.getenv("CONTEXT_SIZE_QWENVL", "16384")),
            "api_endpoint": "/v1/chat/completions",
        },
        "labse-embedding": {
            "type": "embedding",
            "path": resolve_model_path("labse-embedding"),
            "n_gpu_layers": int(os.getenv("N_GPU_LAYERS_LABSE", "10")),
            "context_size": int(os.getenv("CONTEXT_SIZE_LABSE", "512")),
            "api_endpoint": "/v1/embeddings",
        },
        "qwen3-embedding-0.6b": {
            "type": "embedding",
            "path": resolve_model_path("qwen3-embedding-0.6b"),
            "n_gpu_layers": int(os.getenv("N_GPU_LAYERS_QWEN3_EMBEDDING_06B", "0")),
            "context_size": int(os.getenv("CONTEXT_SIZE_QWEN3_EMBEDDING_06B", "512")),
            "api_endpoint": "/v1/embeddings",
        },
    }

# Параметры генерации по умолчанию (для борьбы с галлюцинациями)
DEFAULT_GENERATION_PARAMS = {
    "temperature": float(os.getenv("TEMPERATURE", "0.5")),  # Снижено для стабильности
    "top_p": float(os.getenv("TOP_P", "0.9")),
    "repetition_penalty": float(os.getenv("REPETITION_PENALTY", "1.2")),
    "max_tokens": int(os.getenv("MAX_TOKENS", "2048")),
}

# Текущая активная модель
ACTIVE_MODEL_ID = os.getenv("ACTIVE_MODEL_ID", "none")


def get_model_config(model_id: str) -> Dict[str, Any]:
    """Получение конфигурации модели по ID."""
    model_config = _build_model_config()
    if model_id not in model_config:
        raise ValueError(f"Unknown model: {model_id}")
    return model_config[model_id]


def get_all_models() -> Dict[str, Dict[str, Any]]:
    """Получение конфигурации всех моделей."""
    return _build_model_config()


def get_default_params() -> Dict[str, Any]:
    """Получение параметров генерации по умолчанию."""
    return DEFAULT_GENERATION_PARAMS.copy()
