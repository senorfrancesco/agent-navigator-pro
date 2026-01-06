"""
Unified Model Server (UMS) - Управляет жизненным циклом LLM и Embedding моделей.
Поддерживает динамическое переключение моделей и мониторинг ресурсов.
Использует бинарный llama-server для инференса GGUF моделей.
"""

import os
import sys
import json
import time
import signal
import subprocess
import asyncio
import logging
import psutil
import traceback
from enum import Enum
from typing import Dict, List, Optional, Any
from pathlib import Path
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='[UMS] %(levelname)s: %(message)s'
)
logger = logging.getLogger("UMS")

# Определяем корень бэкенда (на две папки выше текущего файла)
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = BACKEND_ROOT / ".env"

# Загрузка переменных окружения из .env файла в корне бэкенда
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
    logger.info(f"Loaded .env from {ENV_PATH}")
else:
    load_dotenv() # Fallback на стандартный поиск
    logger.warning(f".env not found at {ENV_PATH}, using default environment")

# Безопасный импорт pynvml
try:
    import pynvml
    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    logger.warning("nvidia-ml-py (pynvml) не установлен, мониторинг GPU ограничен")

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import httpx
import uvicorn

app = FastAPI(title="Unified Model Server", version="1.2.0")

# === Configuration ===

class DeviceMode(str, Enum):
    CPU = "cpu"
    GPU = "gpu"
    HYBRID = "hybrid"

class LoadStrategy(str, Enum):
    LAZY = "lazy"      # Загрузка при первом запросе
    EAGER = "eager"    # Загрузка при старте сервера

# Конфигурация моделей из .env
def resolve_model_path(path_str: str) -> str:
    """Разрешает путь к модели (поддержка ~ и относительных путей от корня backend)."""
    if not path_str:
        return ""
    path = Path(os.path.expanduser(path_str))
    if not path.is_absolute():
        path = BACKEND_ROOT / path_str
    return str(path.resolve())

MODELS_CONFIG = {
    "qwen-14b-llm": {
        "type": "gguf",
        "path": os.getenv("MODEL_PATH_QWEN14B", "./models/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf"),
        "ctx_size": 16384,
        "gpu_layers": 15,
        "port": 8091
    },
    "qwen-vl-8b": {
        "type": "gguf-vl",
        "path": os.getenv("MODEL_PATH_QWENVL", "./models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"),
        "mmproj": os.getenv("MMPROJ_PATH", "./models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"),
        "ctx_size": 8192,
        "gpu_layers": 20,
        "port": 8092
    },
    "labse-embedding": {
        "type": "st",
        "path": os.getenv("MODEL_PATH_LABSE", "./models/st/LaBSE"),
        "port": 8093
    }
}

# Состояние сервера
state = {
    "active_model": None,
    "processes": {},  # model_id -> subprocess.Popen
    "device_mode": DeviceMode.HYBRID,
    "load_strategy": LoadStrategy.LAZY
}

# === Resource Helpers ===

def _get_available_vram() -> float:
    if not PYNVML_AVAILABLE: return 0.0
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return info.free / (1024 ** 3)
    except: return 0.0

def _get_available_ram() -> float:
    try: return psutil.virtual_memory().available / (1024 ** 3)
    except: return 0.0

# === Model Management ===

def _stop_all_servers():
    """Остановка всех запущенных процессов llama-server."""
    for model_id, proc in list(state["processes"].items()):
        logger.info(f"Stopping server for {model_id}...")
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
        except:
            try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except: pass
    state["processes"] = {}
    state["active_model"] = None

def _start_server(model_id: str, device_mode: DeviceMode):
    """Запуск бинарного llama-server для GGUF модели."""
    if model_id not in MODELS_CONFIG:
        raise ValueError(f"Unknown model: {model_id}")
    
    config = MODELS_CONFIG[model_id]
    model_path = resolve_model_path(config["path"])
    
    if not os.path.exists(model_path):
        error_msg = f"Model file not found at: {model_path}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    if state["active_model"] and state["active_model"] != model_id:
        _stop_all_servers()

    # Формируем команду для бинарного llama-server (используем флаги из help)
    cmd = [
        "llama-server",
        "-m", model_path,
        "--port", str(config["port"]),
        "--host", "0.0.0.0",
        "-c", str(config["ctx_size"]),
    ]

    # Настройка GPU слоев (-ngl)
    ngl = config["gpu_layers"] if device_mode != DeviceMode.CPU else 0
    cmd.extend(["-ngl", str(ngl)])

    if config["type"] == "gguf-vl" and "mmproj" in config:
        mmproj_path = resolve_model_path(config["mmproj"])
        if os.path.exists(mmproj_path):
            cmd.extend(["--mmproj", mmproj_path])

    logger.info(f"Starting llama-server for model: {model_id}")
    logger.info(f"Command: {' '.join(cmd)}")

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid
        )
        
        # Ожидание готовности
        start_time = time.time()
        timeout = 60
        while time.time() - start_time < timeout:
            if process.poll() is not None:
                _, stderr = process.communicate()
                raise RuntimeError(f"llama-server exited immediately. Stderr: {stderr}")
            
            try:
                with httpx.Client(timeout=1.0) as client:
                    resp = client.get(f"http://localhost:{config['port']}/health")
                    if resp.status_code == 200:
                        state["processes"][model_id] = process
                        state["active_model"] = model_id
                        logger.info(f"Server for {model_id} started on port {config['port']}")
                        return
            except: pass
            time.sleep(1)
            
        raise TimeoutError(f"llama-server failed to start within {timeout}s")
    except Exception as e:
        logger.error(f"Failed to start llama-server: {str(e)}")
        raise

def switch_model(model_id: str, device_mode: DeviceMode = DeviceMode.HYBRID):
    if state["active_model"] == model_id: return
    _start_server(model_id, device_mode)

# === API Endpoints ===

class InferRequest(BaseModel):
    model_id: str
    payload: Dict[str, Any]
    device_mode: Optional[DeviceMode] = DeviceMode.HYBRID

@app.post("/infer")
async def infer(request: InferRequest):
    model_id = request.model_id
    device_mode = request.device_mode or state["device_mode"]
    try:
        switch_model(model_id, device_mode)
        config = MODELS_CONFIG[model_id]
        url = f"http://localhost:{config['port']}/v1/chat/completions" if "messages" in request.payload else f"http://localhost:{config['port']}/v1/completions"

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=request.payload)
            return {"status": "success", "model": model_id, "result": response.json()}
    except Exception as e:
        logger.error(f"API Error in /infer: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
async def get_status():
    models_status = {}
    for mid in MODELS_CONFIG:
        path = resolve_model_path(MODELS_CONFIG[mid]["path"])
        models_status[mid] = {
            "available": os.path.exists(path),
            "active": state["active_model"] == mid,
            "path": path
        }
    return {
        "active_model": state["active_model"],
        "vram_free_gb": _get_available_vram(),
        "ram_free_gb": _get_available_ram(),
        "models": models_status
    }

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}

@app.on_event("startup")
async def startup_event():
    logger.info(f"UMS Starting. Backend Root: {BACKEND_ROOT}")
    for mid, cfg in MODELS_CONFIG.items():
        path = resolve_model_path(cfg["path"])
        logger.info(f"Model {mid}: {'EXISTS' if os.path.exists(path) else 'NOT FOUND'} at {path}")

@app.on_event("shutdown")
def shutdown_event():
    _stop_all_servers()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)
