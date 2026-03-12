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
import threading
from contextlib import suppress
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
    },
    "qwen3-embedding-0.6b": {
        "type": "st",
        "path": os.getenv("MODEL_PATH_QWEN3_EMBEDDING_06B", "./models/st/Qwen3-Embedding-0.6B"),
        "port": 8094
    }
}

state = {
    "active_model": None, # Последняя запрошенная "тяжелая" модель
    "processes": {},      # model_id -> process
    "placements": {},     # model_id -> placement metadata
    "device_mode": DeviceMode.HYBRID,
    "runtime_budget": {},
    "dynamic_ports": 8100 # Начальный порт для динамических моделей
}
_model_start_locks: Dict[str, threading.Lock] = {}
_model_start_locks_guard = threading.Lock()

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


def _parse_gpu_indices_env(name: str, available_gpus: List[Dict[str, Any]]) -> Optional[List[int]]:
    raw = os.getenv(name)
    if not raw:
        return None
    available = {int(gpu["index"]) for gpu in available_gpus}
    selected: List[int] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            idx = int(token)
        except ValueError:
            continue
        if idx in available and idx not in selected:
            selected.append(idx)
    return selected or None


def _normalize_tensor_split(weights: List[float]) -> List[float]:
    total = sum(max(weight, 0.0) for weight in weights)
    if total <= 0:
        if not weights:
            return []
        return [round(1.0 / len(weights), 4) for _ in weights]
    return [round(max(weight, 0.0) / total, 4) for weight in weights]


def _select_llm_gpus(available_gpus: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected_gpu_indices = _parse_gpu_indices_env("UMS_LLM_GPU_INDICES", available_gpus)
    selected = [
        gpu for gpu in available_gpus
        if selected_gpu_indices is None or int(gpu["index"]) in set(selected_gpu_indices)
    ]
    min_free_gb = _read_runtime_float("UMS_LLM_MIN_FREE_VRAM_GB", 0.0)
    selected = [gpu for gpu in selected if float(gpu.get("free_gb", 0.0)) >= min_free_gb]
    if len(selected) <= 1:
        return selected
    max_free = max(float(gpu.get("free_gb", 0.0)) for gpu in selected)
    min_balance_ratio = _clamp_float(_read_runtime_float("UMS_LLM_MIN_BALANCE_RATIO", 0.5), min_value=0.1, max_value=1.0)
    balanced = [
        gpu for gpu in selected
        if max_free <= 0 or float(gpu.get("free_gb", 0.0)) / max_free >= min_balance_ratio
    ]
    if not balanced:
        return [max(selected, key=lambda gpu: float(gpu.get("free_gb", 0.0)))]
    if len(balanced) == 1:
        return balanced
    return balanced


def _resolve_embedding_tier_preference() -> str:
    tier_config = state.get("tier_config")
    preferred = str(getattr(tier_config, "embedding_device", "cuda") or "cuda").strip().lower()
    return preferred if preferred in {"cpu", "cuda"} else "cuda"


def _build_model_placement_plan(
    *,
    model_id: str,
    config: Dict[str, Any],
    device_mode: DeviceMode,
    available_gpus: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if config["type"] == "st":
        if device_mode == DeviceMode.CPU or not available_gpus or _resolve_embedding_tier_preference() == "cpu":
            return {"placement_mode": "cpu", "device_arg": "cpu", "gpu_indices": []}
        selected_gpu = _parse_gpu_indices_env("UMS_EMBEDDING_GPU_INDEX", available_gpus)
        if selected_gpu:
            gpu_index = selected_gpu[0]
        else:
            llm_gpu_indices = set()
            active_heavy_model = state.get("active_model")
            if active_heavy_model:
                llm_gpu_indices = set((state.get("placements", {}).get(active_heavy_model) or {}).get("gpu_indices") or [])
            candidate_gpus = [gpu for gpu in available_gpus if int(gpu["index"]) not in llm_gpu_indices]
            if not candidate_gpus and llm_gpu_indices:
                return {"placement_mode": "cpu", "device_arg": "cpu", "gpu_indices": []}
            if not candidate_gpus:
                candidate_gpus = available_gpus
            gpu_index = max(candidate_gpus, key=lambda gpu: float(gpu.get("free_gb", 0.0)))["index"]
        return {
            "placement_mode": "single-gpu",
            "device_arg": f"cuda:{gpu_index}",
            "gpu_indices": [int(gpu_index)],
        }

    if device_mode == DeviceMode.CPU or not available_gpus:
        return {"placement_mode": "cpu", "gpu_indices": [], "tensor_split": []}

    selected_gpus = _select_llm_gpus(available_gpus)
    if not selected_gpus:
        return {"placement_mode": "cpu", "gpu_indices": [], "tensor_split": []}
    if len(selected_gpus) == 1:
        return {
            "placement_mode": "single-gpu",
            "gpu_indices": [int(selected_gpus[0]["index"])],
            "tensor_split": [1.0],
        }
    tensor_split = _normalize_tensor_split([float(gpu.get("free_gb", 0.0)) for gpu in selected_gpus])
    return {
        "placement_mode": "multi-gpu",
        "gpu_indices": [int(gpu["index"]) for gpu in selected_gpus],
        "tensor_split": tensor_split,
    }


def _placement_with_device_arg(placement: Dict[str, Any], device_arg: str) -> Dict[str, Any]:
    resolved = dict(placement)
    resolved["device_arg"] = device_arg
    if device_arg == "cpu":
        resolved["placement_mode"] = "cpu"
        resolved["gpu_indices"] = []
    return resolved


def _clamp_float(value: float, *, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _resolve_runtime_profile() -> str:
    raw = str(os.getenv("UMS_RUNTIME_PROFILE", "adaptive")).strip().lower()
    if raw in {"default", "adaptive", "manual"}:
        return raw
    return "adaptive"


def _read_runtime_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _read_runtime_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_runtime_ctx_size() -> int:
    active_model = state.get("active_model") or "qwen-14b-llm"
    config = get_model_config(active_model) or STATIC_MODELS_CONFIG.get("qwen-14b-llm", {})
    ctx_size = int(config.get("ctx_size") or STATIC_MODELS_CONFIG["qwen-14b-llm"]["ctx_size"])
    return max(ctx_size, 1024)


def resolve_runtime_budget(
    *,
    ctx_size: Optional[int] = None,
    runtime_profile: Optional[str] = None,
    default_effective_context_tokens: Optional[int] = None,
    manual_effective_context_tokens: Optional[int] = None,
    context_budget_ratio: Optional[float] = None,
    generation_tokens_reserve: Optional[int] = None,
    adaptive_min_context_tokens: Optional[int] = None,
    adaptive_context_utilization: Optional[float] = None,
) -> Dict[str, Any]:
    profile = runtime_profile or _resolve_runtime_profile()
    if profile not in {"default", "adaptive", "manual"}:
        profile = "adaptive"
    llm_ctx_size = int(ctx_size or _get_runtime_ctx_size())
    default_ctx = (
        int(default_effective_context_tokens)
        if default_effective_context_tokens is not None
        else _read_runtime_int("UMS_DEFAULT_EFFECTIVE_CONTEXT_TOKENS", min(llm_ctx_size, 8192))
    )
    adaptive_floor = (
        int(adaptive_min_context_tokens)
        if adaptive_min_context_tokens is not None
        else _read_runtime_int("UMS_ADAPTIVE_MIN_CONTEXT_TOKENS", 4096)
    )
    adaptive_util = _clamp_float(
        float(adaptive_context_utilization)
        if adaptive_context_utilization is not None
        else _read_runtime_float("UMS_ADAPTIVE_CONTEXT_UTILIZATION", 0.85),
        min_value=0.50,
        max_value=1.0,
    )
    manual_ctx = (
        int(manual_effective_context_tokens)
        if manual_effective_context_tokens is not None
        else _read_runtime_int("UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS", default_ctx)
    )
    context_budget_ratio = _clamp_float(
        float(context_budget_ratio)
        if context_budget_ratio is not None
        else _read_runtime_float("UMS_RETRIEVED_CONTEXT_RATIO", 0.60),
        min_value=0.55,
        max_value=0.65,
    )
    generation_reserve = max(
        256,
        int(generation_tokens_reserve)
        if generation_tokens_reserve is not None
        else _read_runtime_int("UMS_GENERATION_TOKENS_RESERVE", 1024),
    )

    if profile == "manual":
        effective_context_tokens = manual_ctx
    elif profile == "default":
        effective_context_tokens = default_ctx
    else:
        effective_context_tokens = max(adaptive_floor, int(llm_ctx_size * adaptive_util))

    effective_context_tokens = max(1024, min(effective_context_tokens, llm_ctx_size))
    generation_tokens_reserve = min(generation_reserve, max(256, effective_context_tokens // 2))
    retrieved_context_tokens_budget = min(
        max(512, int(effective_context_tokens * context_budget_ratio)),
        max(512, effective_context_tokens - generation_tokens_reserve),
    )

    return {
        "runtime_profile": profile,
        "llm_ctx_size": llm_ctx_size,
        "effective_context_tokens": effective_context_tokens,
        "retrieved_context_tokens_budget": retrieved_context_tokens_budget,
        "generation_tokens_reserve": generation_tokens_reserve,
        "context_budget_ratio": context_budget_ratio,
    }

def resolve_model_path(path_str: str) -> str:
    path = Path(os.path.expanduser(path_str))
    if not path.is_absolute():
        path = BACKEND_ROOT / path_str
    return str(path.resolve())


def _get_model_start_lock(model_id: str) -> threading.Lock:
    with _model_start_locks_guard:
        lock = _model_start_locks.get(model_id)
        if lock is None:
            lock = threading.Lock()
            _model_start_locks[model_id] = lock
        return lock


def _find_listener_pids(port: int) -> List[int]:
    pids = set()
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN:
                continue
            if not conn.laddr:
                continue
            if conn.laddr.port != port:
                continue
            if conn.pid:
                pids.add(conn.pid)
    except Exception as e:
        logger.warning(f"Failed to inspect listeners on port {port}: {e}")
    return sorted(pids)


def _kill_process_tree(pid: int) -> None:
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return

    children = proc.children(recursive=True)
    for child in children:
        with suppress(psutil.Error):
            child.terminate()
    with suppress(psutil.Error):
        proc.terminate()

    gone, alive = psutil.wait_procs(children + [proc], timeout=3)
    for survivor in alive:
        with suppress(psutil.Error):
            survivor.kill()


def _reap_stale_listener_on_port(port: int, tracked_proc: Optional[subprocess.Popen] = None) -> None:
    tracked_pid = tracked_proc.pid if tracked_proc is not None else None
    for pid in _find_listener_pids(port):
        if tracked_pid and pid == tracked_pid:
            continue
        logger.warning(f"Reaping stale listener on port {port}: pid={pid}")
        _kill_process_tree(pid)

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
        state["placements"].pop(model_id, None)
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

def _terminate_process(proc: subprocess.Popen) -> None:
    """Останавливает дочерний процесс и его process group."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def _launch_server_process(cmd: List[str], port: int, health_timeout_s: float = 120.0) -> subprocess.Popen:
    """Запускает сервер и ждет его readiness по /health."""
    process = subprocess.Popen(cmd, preexec_fn=os.setsid)
    try:
        start_time = time.time()
        while time.time() - start_time < health_timeout_s:
            if process.poll() is not None:
                raise RuntimeError(f"Server exited with code {process.returncode}")
            try:
                with httpx.Client(timeout=1.0) as client:
                    if client.get(f"http://localhost:{port}/health").status_code == 200:
                        return process
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError("Server start timeout")
    except Exception:
        _terminate_process(process)
        raise


def _start_server(model_id: str, device_mode: DeviceMode):
    with _get_model_start_lock(model_id):
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

        existing_proc = state["processes"].get(model_id)
        if existing_proc is not None:
            if existing_proc.poll() is None:
                return  # Уже работает
            state["processes"].pop(model_id, None)
            state["placements"].pop(model_id, None)

        _reap_stale_listener_on_port(config["port"], tracked_proc=existing_proc)

        available_gpus = _get_gpu_info()
        n_gpu = len(available_gpus)
        placement = _build_model_placement_plan(
            model_id=model_id,
            config=config,
            device_mode=device_mode,
            available_gpus=available_gpus,
        )

        if config["type"] == "st":
            preferred_device = str(placement.get("device_arg") or "cpu")
            device_candidates = [preferred_device]
            if preferred_device.startswith("cuda"):
                device_candidates.append("cpu")

            last_error = None
            for idx, device_arg in enumerate(device_candidates):
                cmd = [
                    sys.executable,
                    str(Path(__file__).parent / "st_server.py"),
                    "--model", model_path,
                    "--port", str(config["port"]),
                    "--device", device_arg,
                ]
                logger.info(f"Executing: {' '.join(cmd)}")
                try:
                    process = _launch_server_process(cmd, config["port"])
                    state["processes"][model_id] = process
                    state["placements"][model_id] = _placement_with_device_arg(placement, device_arg)
                    return
                except Exception as e:
                    last_error = e
                    if idx < len(device_candidates) - 1:
                        logger.warning(
                            f"ST server startup failed on {device_arg}, retrying on {device_candidates[idx + 1]}: {e}"
                        )
                        continue
                    logger.error(f"Start failed: {e}")
                    raise
            raise RuntimeError(f"Failed to start {model_id}: {last_error}")
        else:
            cmd = ["llama-server", "-m", model_path, "--port", str(config["port"]),
                   "--host", "0.0.0.0", "-c", str(config["ctx_size"]),
                   "-ngl", str(config["gpu_layers"] if device_mode != DeviceMode.CPU else 0)]
            if placement.get("placement_mode") == "multi-gpu":
                tensor_split = placement.get("tensor_split") or []
                cmd.extend(["--tensor-split", ",".join(str(weight) for weight in tensor_split)])
            if config["type"] == "gguf-vl" and "mmproj" in config:
                cmd.extend(["--mmproj", resolve_model_path(config["mmproj"])])

        logger.info(f"Executing: {' '.join(cmd)}")
        try:
            process = _launch_server_process(cmd, config["port"])
            state["processes"][model_id] = process
            state["placements"][model_id] = placement
            if is_heavy:
                state["active_model"] = model_id
            return
        except Exception as e:
            logger.error(f"Start failed: {e}")
            raise

# === API ===

class InferRequest(BaseModel):
    model_id: str
    payload: Dict[str, Any]
    device_mode: Optional[DeviceMode] = DeviceMode.HYBRID
    stream: bool = False

class EmbeddingRequest(BaseModel):
    """OpenAI-compatible /v1/embeddings request."""
    input: Any  # str | List[str]
    model: str = "labse-embedding"
    encoding_format: Optional[str] = "float"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # T3.7: Hardware profiling при старте
    try:
        sys.path.insert(0, str(BACKEND_ROOT))
        from services.hardware import HardwareProfiler, TierSelector
        profiler = HardwareProfiler()
        profile = profiler.detect()
        selector = TierSelector()
        tier_config = selector.select(profile)
        state["system_profile"] = profile
        state["tier_config"] = tier_config
        state["runtime_budget"] = resolve_runtime_budget(ctx_size=tier_config.llm_ctx_size)
        logger.info(f"Hardware detected:\n{profile}")
        logger.info(f"Selected: {tier_config}")
        logger.info(f"Runtime budget: {state['runtime_budget']}")

        # Обновляем конфиг модели из tier_config
        if "qwen-14b-llm" in STATIC_MODELS_CONFIG:
            STATIC_MODELS_CONFIG["qwen-14b-llm"]["ctx_size"] = tier_config.llm_ctx_size
            if tier_config.llm_gpu_layers != -1:
                STATIC_MODELS_CONFIG["qwen-14b-llm"]["gpu_layers"] = tier_config.llm_gpu_layers
            logger.info(f"Updated qwen-14b-llm: ctx={tier_config.llm_ctx_size}, gpu_layers={tier_config.llm_gpu_layers}")
    except Exception as e:
        logger.warning(f"Hardware profiling failed, using defaults: {e}")
        state["system_profile"] = None
        state["tier_config"] = None
        state["runtime_budget"] = resolve_runtime_budget()

    # Предзагрузка Qwen LLM — убирает задержку перед первым запросом
    try:
        logger.info("Preloading qwen-14b-llm...")
        _start_server("qwen-14b-llm", state["device_mode"])
        logger.info("qwen-14b-llm preloaded successfully")
    except Exception as e:
        logger.warning(f"Failed to preload qwen-14b-llm: {e}")

    yield
    _stop_all_servers()

app = FastAPI(title="Unified Model Server", version="3.0.0", lifespan=lifespan)

# Concurrency control: llama-server — однопоточный inference, ONNX LaBSE — допускает параллелизм
_llm_semaphore = asyncio.Semaphore(1)
_embed_semaphore = asyncio.Semaphore(4)


async def _proxy_sse_stream(url: str, payload: Dict[str, Any], sem: asyncio.Semaphore) -> AsyncGenerator[bytes, None]:
    """
    Проксирует upstream SSE через очередь и отдельную producer-task.

    Это разрывает хрупкую связку:
    StreamingResponse consumer task -> httpx stream context
    и гарантирует, что upstream stream закрывается в том же task, где был открыт.
    """
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def producer() -> None:
        try:
            async with sem:
                async with httpx.AsyncClient(timeout=300.0) as stream_client:
                    async with stream_client.stream("POST", url, json=payload) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if line:
                                await queue.put(("chunk", f"{line}\n\n".encode()))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Upstream SSE proxy error for {url}: {exc}")
            await queue.put(("error", exc))
        finally:
            await queue.put(("done", None))

    producer_task = asyncio.create_task(producer(), name=f"ums-proxy:{os.path.basename(url)}")
    try:
        while True:
            kind, value = await queue.get()
            if kind == "chunk":
                yield value
                continue
            if kind == "error":
                return
            return
    finally:
        if not producer_task.done():
            producer_task.cancel()
        with suppress(asyncio.CancelledError):
            await producer_task

@app.post("/infer")
async def infer(request: InferRequest):
    try:
        await asyncio.to_thread(_start_server, request.model_id, request.device_mode or state["device_mode"])
        config = get_model_config(request.model_id)
        sem = _embed_semaphore if config["type"] == "st" else _llm_semaphore

        is_chat = "messages" in request.payload
        url_suffix = "v1/embeddings" if config["type"] == "st" else f"v1/{'chat/' if is_chat else ''}completions"
        url = f"http://localhost:{config['port']}/{url_suffix}"

        payload = request.payload.copy()
        if request.stream: payload["stream"] = True

        if request.stream:
            return StreamingResponse(
                _proxy_sse_stream(url, payload, sem),
                media_type="text/event-stream",
            )
        else:
            async with sem:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    resp = await client.post(url, json=payload)
                    return {"status": "success", "model": request.model_id, "result": resp.json()}
    except Exception as e:
        logger.error(f"Inference Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
async def get_status():
    tier_info = None
    if state.get("tier_config"):
        from services.hardware.tier_selector import describe_rag_mode

        tc = state["tier_config"]
        tier_info = {
            "tier": tc.tier,
            "rag_mode": tc.rag_mode,
            "rag_mode_label": describe_rag_mode(tc.rag_mode),
            "embedding_backend": tc.embedding_backend,
        }
    runtime_budget = resolve_runtime_budget()
    return {
        "active_heavy_model": state["active_model"],
        "running": list(state["processes"].keys()),
        "placements": dict(state.get("placements") or {}),
        "vram_free_gb": _get_available_vram(),
        "tier": tier_info,
        "runtime_profile": runtime_budget["runtime_profile"],
        "effective_context_tokens": runtime_budget["effective_context_tokens"],
        "retrieved_context_tokens_budget": runtime_budget["retrieved_context_tokens_budget"],
        "generation_tokens_reserve": runtime_budget["generation_tokens_reserve"],
        "context_budget_ratio": runtime_budget["context_budget_ratio"],
    }

@app.post("/v1/embeddings")
async def openai_embeddings(request: EmbeddingRequest):
    """OpenAI-compatible embeddings endpoint. Proxies to LaBSE st_server."""
    model_id = request.model
    if model_id not in STATIC_MODELS_CONFIG:
        model_id = "labse-embedding"

    try:
        await asyncio.to_thread(_start_server, model_id, state["device_mode"])
        config = get_model_config(model_id)
        url = f"http://localhost:{config['port']}/v1/embeddings"

        # Нормализуем input в список
        texts = request.input if isinstance(request.input, list) else [request.input]

        payload = {"input": texts, "model": model_id}
        async with _embed_semaphore:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                result = resp.json()

        # Обеспечиваем OpenAI-совместимый формат
        if "data" not in result:
            # st_server может вернуть другой формат — адаптируем
            embeddings = result.get("embeddings", [])
            result = {
                "object": "list",
                "data": [
                    {"object": "embedding", "index": i, "embedding": emb}
                    for i, emb in enumerate(embeddings)
                ],
                "model": model_id,
                "usage": {"prompt_tokens": sum(len(t.split()) for t in texts), "total_tokens": sum(len(t.split()) for t in texts)},
            }

        return result
    except Exception as e:
        logger.error(f"Embeddings Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health(): return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)
