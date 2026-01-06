"""
Конфигурация моделей с поддержкой переменных окружения.
Загружает пути и параметры из .env файла.
"""

import os
from typing import Dict, Any
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# Конфигурация моделей с загрузкой путей из окружения
MODEL_CONFIG: Dict[str, Dict[str, Any]] = {
    "qwen-14b-llm": {
        "type": "text",
        "path": os.getenv("MODEL_PATH_QWEN14B", "./models/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf"),
        "n_gpu_layers": int(os.getenv("N_GPU_LAYERS_QWEN14B", "-1")),
        "context_size": int(os.getenv("CONTEXT_SIZE_QWEN14B", "16384")),
        "api_endpoint": "/v1/completions"
    },
    "qwen-vl-8b": {
        "type": "vision",
        "path": os.getenv("MODEL_PATH_QWENVL", "./models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"),
        "mmproj_path": os.getenv("MMPROJ_PATH", "./models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"),
        "n_gpu_layers": int(os.getenv("N_GPU_LAYERS_QWENVL", "-1")),
        "context_size": int(os.getenv("CONTEXT_SIZE_QWENVL", "16384")),
        "api_endpoint": "/v1/chat/completions"
    },
    "labse-embedding": {
        "type": "embedding",
        "path": os.getenv("MODEL_PATH_LABSE", "./models/st/LaBSE"),
        "n_gpu_layers": int(os.getenv("N_GPU_LAYERS_LABSE", "10")),
        "context_size": int(os.getenv("CONTEXT_SIZE_LABSE", "512")),
        "api_endpoint": "/v1/embeddings"
    }
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
    if model_id not in MODEL_CONFIG:
        raise ValueError(f"Unknown model: {model_id}")
    return MODEL_CONFIG[model_id]


def get_all_models() -> Dict[str, Dict[str, Any]]:
    """Получение конфигурации всех моделей."""
    return MODEL_CONFIG


def get_default_params() -> Dict[str, Any]:
    """Получение параметров генерации по умолчанию."""
    return DEFAULT_GENERATION_PARAMS.copy()
