"""
Unified Model Server (UMS) - Управляет жизненным циклом LLM и Embedding моделей.
Поддерживает динамическое переключение моделей на основе файловой системы.
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
import warnings
from enum import Enum
from typing import Dict, List, Optional, Any, AsyncGenerator
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Подавление предупреждений pynvml
warnings.filterwarnings("ignore", category=FutureWarning, module="pynvml")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='[UMS] %(levelname)s: %(message)s'
)
logger = logging.getLogger("UMS")

# Определяем корень бэкенда
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = BACKEND_ROOT / ".env"

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
    logger.info(f"Loaded .env from {ENV_PATH}")
else:
    load_dotenv()

# Безопасный импорт pynvml
try:
    import pynvml
    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
import uvicorn

# === Configuration ===

class DeviceMode(str, Enum):
    CPU = "cpu"
    GPU = "gpu"
    HYBRID = "hybrid"

MODELS_DIR = BACKEND_ROOT / "models" / "gguf"

# Хардкодные конфиги для системных моделей
STATIC_MODELS_CONFIG = {
    "qwen-14b-llm": {
        "type": "gguf",
        "path": os.getenv("MODEL_PATH_QWEN14B", "./models/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf"),
        "ctx_size": 16384,
        "gpu_layers": -1,
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

state = {
    "active_model": None, # Последняя запрошенная "тяжелая" модель
    "processes": {},      # model_id -> process
    "device_mode": DeviceMode.HYBRID,
    "dynamic_ports": 8100 # Начальный порт для динамических моделей
}

# === Resource Helpers ===

def _get_gpu_info() -> List[Dict[str, Any]]:
    if not PYNVML_AVAILABLE: return []
    try:
        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()
        gpus = []
        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            gpus.append({
                "index": i, "name": name,
                "free_gb": info.free / (1024 ** 3),
                "total_gb": info.total / (1024 ** 3)
            })
        return gpus
    except: return []

def _get_available_vram() -> float:
    return sum(gpu["free_gb"] for gpu in _get_gpu_info())

def resolve_model_path(path_str: str) -> str:
    path = Path(os.path.expanduser(path_str))
    if not path.is_absolute():
        path = BACKEND_ROOT / path_str
    return str(path.resolve())

# === Dynamic Model Discovery ===

def get_model_config(model_id: str) -> Optional[Dict[str, Any]]:
    """Возвращает конфиг модели, либо из статики, либо из файловой системы."""
    if model_id in STATIC_MODELS_CONFIG:
        return STATIC_MODELS_CONFIG[model_id]
    
    # Ищем файл в папке gguf
    for root, dirs, files in os.walk(MODELS_DIR):
        for file in files:
            if file.endswith(".gguf") and os.path.splitext(file)[0] == model_id:
                full_path = os.path.join(root, file)
                return {
                    "type": "gguf",
                    "path": full_path,
                    "ctx_size": 8192,  # Дефолт для новых моделей
                    "gpu_layers": -1,  # Пытаемся все на GPU
                    "port": state["dynamic_ports"] # TODO: сделать пул портов
                }
    return None

# === Model Management ===

def _stop_model(model_id: str):
    if model_id in state["processes"]:
        proc = state["processes"].pop(model_id)
        logger.info(f"Stopping server for {model_id}...")
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
        except:
            try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except: pass
        if state["active_model"] == model_id:
            state["active_model"] = None

def _stop_all_servers():
    for model_id in list(state["processes"].keys()):
        _stop_model(model_id)

def _start_server(model_id: str, device_mode: DeviceMode):
    config = get_model_config(model_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found in filesystem.")

    model_path = resolve_model_path(config["path"])
    is_heavy = config["type"] in ["gguf", "gguf-vl"]
    
    if is_heavy:
        active_heavy = None
        for pid in state["processes"]:
            p_config = get_model_config(pid)
            if p_config and p_config["type"] in ["gguf", "gguf-vl"]:
                active_heavy = pid
                break
        if active_heavy and active_heavy != model_id:
            logger.info(f"Stopping {active_heavy} to free memory for {model_id}")
            _stop_model(active_heavy)

    if model_id in state["processes"]:
        return # Уже работает

    n_gpu = len(_get_gpu_info())
    
    if config["type"] == "st":
        device_arg = "cuda" if (device_mode != DeviceMode.CPU and n_gpu > 0) else "cpu"
        cmd = [sys.executable, str(Path(__file__).parent / "st_server.py"),
               "--model", model_path, "--port", str(config["port"]), "--device", device_arg]
    else:
        cmd = ["llama-server", "-m", model_path, "--port", str(config["port"]),
               "--host", "0.0.0.0", "-c", str(config["ctx_size"]),
               "-ngl", str(config["gpu_layers"] if device_mode != DeviceMode.CPU else 0)]
        if n_gpu > 1 and device_mode != DeviceMode.CPU:
            cmd.extend(["--tensor-split", ",".join(["1"] * n_gpu)])
        if config["type"] == "gguf-vl" and "mmproj" in config:
            cmd.extend(["--mmproj", resolve_model_path(config["mmproj"])])

    logger.info(f"Executing: {' '.join(cmd)}")
    try:
        process = subprocess.Popen(cmd, preexec_fn=os.setsid)
        start_time = time.time()
        while time.time() - start_time < 120:
            try:
                with httpx.Client(timeout=1.0) as client:
                    if client.get(f"http://localhost:{config['port']}/health").status_code == 200:
                        state["processes"][model_id] = process
                        if is_heavy: state["active_model"] = model_id
                        return
            except: pass
            time.sleep(1)
        raise TimeoutError("Server start timeout")
    except Exception as e:
        logger.error(f"Start failed: {e}")
        raise

# === API ===

class InferRequest(BaseModel):
    model_id: str
    payload: Dict[str, Any]
    device_mode: Optional[DeviceMode] = DeviceMode.HYBRID
    stream: bool = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    _stop_all_servers()

app = FastAPI(title="Unified Model Server", lifespan=lifespan)

@app.post("/infer")
async def infer(request: InferRequest):
    try:
        _start_server(request.model_id, request.device_mode or state["device_mode"])
        config = get_model_config(request.model_id)
        
        is_chat = "messages" in request.payload
        url_suffix = "v1/embeddings" if config["type"] == "st" else f"v1/{'chat/' if is_chat else ''}completions"
        url = f"http://localhost:{config['port']}/{url_suffix}"
        
        payload = request.payload.copy()
        if request.stream: payload["stream"] = True

        if request.stream:
            async def gen():
                async with httpx.AsyncClient(timeout=300.0) as stream_client:
                    async with stream_client.stream("POST", url, json=payload) as resp:
                        async for line in resp.aiter_lines():
                            if line: yield f"{line}\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        else:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(url, json=payload)
                return {"status": "success", "model": request.model_id, "result": resp.json()}
    except Exception as e:
        logger.error(f"Inference Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
async def get_status():
    return {
        "active_heavy_model": state["active_model"],
        "running": list(state["processes"].keys()),
        "vram_free_gb": _get_available_vram()
    }

@app.get("/health")
async def health(): return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)