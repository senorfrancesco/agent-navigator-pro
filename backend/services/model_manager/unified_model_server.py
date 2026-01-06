"""
Unified Model Server (UMS) - Управляет жизненным циклом LLM и Embedding моделей.
Поддерживает динамическое переключение моделей, мониторинг ресурсов и стриминг.
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
    logger.warning(f".env not found at {ENV_PATH}")

# Безопасный импорт pynvml
try:
    import pynvml
    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    logger.warning("nvidia-ml-py (pynvml) не установлен")

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

def resolve_model_path(path_str: str) -> str:
    if not path_str: return ""
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

state = {
    "active_model": None,
    "processes": {},
    "device_mode": DeviceMode.HYBRID
}

# === Resource Helpers ===

def _get_available_vram() -> float:
    if not PYNVML_AVAILABLE: return 0.0
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        return pynvml.nvmlDeviceGetMemoryInfo(handle).free / (1024 ** 3)
    except: return 0.0

def _get_available_ram() -> float:
    try: return psutil.virtual_memory().available / (1024 ** 3)
    except: return 0.0

# === Model Management ===

def _stop_all_servers():
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
    config = MODELS_CONFIG[model_id]
    model_path = resolve_model_path(config["path"])
    
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found at: {model_path}")

    if state["active_model"] and state["active_model"] != model_id:
        _stop_all_servers()

    cmd = [
        "llama-server",
        "-m", model_path,
        "--port", str(config["port"]),
        "--host", "0.0.0.0",
        "-c", str(config["ctx_size"]),
        "-ngl", str(config["gpu_layers"] if device_mode != DeviceMode.CPU else 0)
    ]

    if config["type"] == "gguf-vl" and "mmproj" in config:
        mmproj_path = resolve_model_path(config["mmproj"])
        if os.path.exists(mmproj_path):
            cmd.extend(["--mmproj", mmproj_path])

    logger.info(f"Starting llama-server: {' '.join(cmd)}")
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=os.setsid)
        
        start_time = time.time()
        while time.time() - start_time < 60:
            if process.poll() is not None:
                _, stderr = process.communicate()
                raise RuntimeError(f"llama-server exited: {stderr}")
            try:
                with httpx.Client(timeout=1.0) as client:
                    if client.get(f"http://localhost:{config['port']}/health").status_code == 200:
                        state["processes"][model_id] = process
                        state["active_model"] = model_id
                        return
            except: pass
            time.sleep(1)
        raise TimeoutError("llama-server timeout")
    except Exception as e:
        logger.error(f"Start failed: {e}")
        raise

def switch_model(model_id: str, device_mode: DeviceMode = DeviceMode.HYBRID):
    if state["active_model"] == model_id: return
    _start_server(model_id, device_mode)

# === Lifespan ===

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("UMS Starting...")
    yield
    # Shutdown
    logger.info("UMS Shutting down...")
    _stop_all_servers()

app = FastAPI(title="Unified Model Server", version="1.3.1", lifespan=lifespan)

# === API Endpoints ===

class InferRequest(BaseModel):
    model_id: str
    payload: Dict[str, Any]
    device_mode: Optional[DeviceMode] = DeviceMode.HYBRID
    stream: bool = False

@app.post("/infer")
async def infer(request: InferRequest):
    model_id = request.model_id
    device_mode = request.device_mode or state["device_mode"]
    
    try:
        switch_model(model_id, device_mode)
        config = MODELS_CONFIG[model_id]
        
        is_chat = "messages" in request.payload
        url = f"http://localhost:{config['port']}/v1/{'chat/' if is_chat else ''}completions"
        
        payload = request.payload.copy()
        if request.stream:
            payload["stream"] = True

        if request.stream:
            async def stream_generator():
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream("POST", url, json=payload) as response:
                        async for line in response.aiter_lines():
                            if line:
                                yield f"{line}\n\n"
            
            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, json=payload)
                return {"status": "success", "model": model_id, "result": response.json()}
                
    except Exception as e:
        logger.error(f"API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
async def get_status():
    models_status = {}
    for mid in MODELS_CONFIG:
        path = resolve_model_path(MODELS_CONFIG[mid]["path"])
        models_status[mid] = {
            "available": os.path.exists(path),
            "active": state["active_model"] == mid
        }
    return {
        "active_model": state["active_model"],
        "vram_free_gb": _get_available_vram(),
        "ram_free_gb": _get_available_ram(),
        "models": models_status
    }

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)
