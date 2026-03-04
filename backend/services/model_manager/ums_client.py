"""
UMS Client - HTTP-клиент для взаимодействия с Unified Model Server.

Все MCP-серверы используют этот клиент для выполнения инференса.
"""

import asyncio
import inspect
import os
import time
import random
from contextlib import suppress
import requests
import httpx
import numpy as np
from typing import Dict, Any, Optional, List, AsyncGenerator, Callable, Awaitable
import json

UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")

# Shared async client для повторного использования соединений
_async_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def _get_async_client() -> httpx.AsyncClient:
    """Shared httpx.AsyncClient singleton для UMS."""
    global _async_client
    if _async_client is None:
        async with _client_lock:
            if _async_client is None:
                _async_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(300.0, connect=10.0),
                    limits=httpx.Limits(max_keepalive_connections=10, max_connections=50),
                )
    return _async_client


def _should_retry_async_infer(exc: Exception) -> bool:
    """Retry only on transient transport/backend failures."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return False


def _extract_sse_text(line: str) -> str:
    """Парсит одну SSE-строку от llama-server/UMS и достает текстовый токен."""
    line = line.strip()
    if not line or not line.startswith("data: "):
        return ""

    data_str = line[6:]
    if data_str == "[DONE]":
        return ""

    try:
        chunk = json.loads(data_str)
    except json.JSONDecodeError:
        return ""

    choices = chunk.get("choices", [])
    if not choices:
        return ""

    return choices[0].get("text", "") or choices[0].get("delta", {}).get("content", "")


class UMSClient:
    """HTTP-клиент для взаимодействия с UMS."""
    
    def __init__(self, base_url: str = UMS_URL):
        self.base_url = base_url
    
    def infer(self, model_id: str, payload: Dict[str, Any], device_mode: str = "hybrid") -> Dict[str, Any]:
        """
        Выполняет инференс через UMS.
        
        Args:
            model_id: ID модели (qwen-14b-llm, qwen-vl-8b, labse-embedding)
            payload: Тело запроса, специфичное для модели
            device_mode: Режим работы (gpu, cpu, hybrid)
        
        Returns:
            Результат инференса
        """
        url = f"{self.base_url}/infer"
        
        request_body = {
            "model_id": model_id,
            "payload": payload,
            "device_mode": device_mode,
            "priority": "normal"
        }
        
        retries = 3
        last_error = None
        for attempt in range(retries):
            try:
                print(f"[UMS_CLIENT] Sending inference request for model: {model_id} (attempt {attempt+1})")
                response = requests.post(url, json=request_body, timeout=300)
                response.raise_for_status()
                data = response.json()

                if data.get("status") == "success":
                    return data.get("result", {})
                else:
                    raise RuntimeError(f"UMS returned error: {data}")

            except requests.exceptions.RequestException as e:
                last_error = e
                print(f"[UMS_CLIENT] Error (attempt {attempt+1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(2)
        raise RuntimeError(f"Failed to connect to UMS after {retries} attempts: {last_error}")

    async def async_infer(self, model_id: str, payload: Dict[str, Any], device_mode: str = "hybrid") -> Dict[str, Any]:
        """
        Асинхронный инференс через UMS (использует shared client).
        Retry only on transient failures. Shared client is preserved for pooling.
        """
        url = f"{self.base_url}/infer"
        request_body = {
            "model_id": model_id,
            "payload": payload,
            "device_mode": device_mode,
            "priority": "normal"
        }
        retries = int(os.getenv("UMS_INFER_RETRIES", "4"))
        base_delay_s = float(os.getenv("UMS_RETRY_BASE_DELAY_S", "1.0"))
        max_delay_s = float(os.getenv("UMS_RETRY_MAX_DELAY_S", "10.0"))
        timeout_s = float(os.getenv("UMS_INFER_TIMEOUT_S", "300.0"))
        last_error = None
        for attempt in range(retries):
            try:
                print(f"[UMS_CLIENT] Async inference for model: {model_id} (attempt {attempt+1})")
                client = await _get_async_client()
                response = await client.post(url, json=request_body, timeout=timeout_s)
                response.raise_for_status()
                data = response.json()
                if data.get("status") == "success":
                    return data.get("result", {})
                else:
                    raise RuntimeError(f"UMS returned error: {data}")
            except Exception as e:
                last_error = e
                print(f"[UMS_CLIENT] Async error (attempt {attempt+1}/{retries}): {e}")
                if attempt < retries - 1 and _should_retry_async_infer(e):
                    delay = min(max_delay_s, base_delay_s * (2 ** attempt))
                    jitter = random.uniform(0.0, min(0.5, delay * 0.2))
                    await asyncio.sleep(delay + jitter)
                    continue
                break
        raise RuntimeError(f"Failed to connect to UMS after {retries} attempts: {last_error}")

    async def async_infer_stream(self, model_id: str, payload: Dict[str, Any], device_mode: str = "hybrid") -> AsyncGenerator[str, None]:
        """
        Асинхронный стриминг инференса через UMS.
        Возвращает токены по мере генерации (SSE от llama-server).
        Используется для token-by-token стриминга в прямом чате.
        """
        url = f"{self.base_url}/infer"
        request_body = {
            "model_id": model_id,
            "payload": payload,
            "device_mode": device_mode,
            "priority": "normal",
            "stream": True
        }
        print(f"[UMS_CLIENT] Stream inference for model: {model_id}")
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        async def producer() -> None:
            client = await _get_async_client()
            try:
                async with client.stream("POST", url, json=request_body) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        stripped = line.strip()
                        if stripped == "data: [DONE]":
                            return

                        text = _extract_sse_text(line)
                        if text:
                            await queue.put(("token", text))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await queue.put(("error", exc))
            finally:
                await queue.put(("done", None))

        producer_task = asyncio.create_task(producer(), name=f"ums-stream:{model_id}")
        try:
            while True:
                kind, value = await queue.get()
                if kind == "token":
                    yield value
                    continue
                if kind == "error":
                    raise value
                return
        finally:
            if not producer_task.done():
                producer_task.cancel()
            with suppress(asyncio.CancelledError):
                await producer_task

    async def async_infer_stream_to_callback(
        self,
        model_id: str,
        payload: Dict[str, Any],
        on_token: Callable[[str], Awaitable[None] | None],
        device_mode: str = "hybrid",
    ) -> None:
        """
        Асинхронный стриминг инференса через callback без промежуточного async-generator
        на стороне вызывающего кода. Это снижает риск GeneratorExit/cancel-scope конфликтов
        в UI-интеграциях, которые рано завершают consumer task.
        """
        async for text in self.async_infer_stream(model_id, payload, device_mode=device_mode):
            result = on_token(text)
            if inspect.isawaitable(result):
                await result

    def switch_model(self, model_id: str, device_mode: str = "hybrid") -> Dict[str, Any]:
        """Переключает активную модель на UMS."""
        url = f"{self.base_url}/switch_model"
        
        request_body = {
            "model_id": model_id,
            "device_mode": device_mode
        }
        
        try:
            print(f"[UMS_CLIENT] Switching to model: {model_id}")
            response = requests.post(url, json=request_body, timeout=120)
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.RequestException as e:
            print(f"[UMS_CLIENT] Error switching model: {e}")
            raise RuntimeError(f"Failed to switch model: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Получает статус UMS."""
        url = f"{self.base_url}/status"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"[UMS_CLIENT] Error getting status: {e}")
            return {"error": str(e)}

# Глобальный экземпляр клиента
ums_client = UMSClient()

# ============================================================================
# Вспомогательные функции для MCP-серверов
# ============================================================================

def generate_text_via_ums(prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
    """
    Генерирует текст через UMS, используя LLM-модель (Qwen-14B).
    
    Используется MCP Legal Server для анализа.
    """
    payload = {
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.9
    }
    
    try:
        response = ums_client.infer("qwen-14b-llm", payload, device_mode="hybrid")
        
        # Парсим ответ от llama-server
        if "choices" in response:
            return response["choices"][0].get("text", "")
        elif "content" in response:
            return response["content"]
        else:
            return str(response)
    
    except Exception as e:
        print(f"[UMS_CLIENT] Error generating text: {e}")
        raise

def get_embeddings_via_ums(text: str, normalize: bool = True) -> List[float]:
    """
    Получает эмбеддинги текста через UMS, используя Embedding-модель (LaBSE).
    
    Используется MCP Legal Server для сравнения текстов.
    """
    payload = {
        "input": text,
        "normalize": normalize
    }
    
    try:
        response = ums_client.infer("labse-embedding", payload, device_mode="cpu")
        
        # Парсим ответ от llama-server
        if "data" in response:
            return response["data"][0].get("embedding", [])
        elif "embedding" in response:
            return response["embedding"]
        else:
            return []
    
    except Exception as e:
        print(f"[UMS_CLIENT] Error getting embeddings: {e}")
        raise

def create_ums_embed_fn(base_url: str = None) -> Optional[Callable]:
    """
    Фабрика embed_fn для AdaptiveRAGPipeline.

    Возвращает функцию (List[str]) -> np.ndarray или None если UMS недоступен.
    Синхронный requests.post — вызывается изнутри sync кода HybridRetriever.
    Retry с exponential backoff при ошибках.
    """
    url = (base_url or UMS_URL).rstrip("/") + "/v1/embeddings"

    # Probe: проверяем доступность UMS и LaBSE
    try:
        resp = requests.post(url, json={"input": ["test"], "model": "labse-embedding"}, timeout=10)
        resp.raise_for_status()
    except Exception:
        return None

    def embed_fn(texts: List[str]) -> np.ndarray:
        """Синхронная обертка с батчингом (TD-Batching)."""
        if not texts: return np.array([], dtype=np.float32)
        
        all_embeddings = []
        BATCH_SIZE = 10
        last_error = None
        
        try:
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                for i in range(0, len(texts), BATCH_SIZE):
                    batch = texts[i : i + BATCH_SIZE]
                    
                    # Ретраи для каждого батча
                    for attempt in range(3):
                        try:
                            resp = client.post(url, json={"input": batch, "model": "labse-embedding"})
                            resp.raise_for_status()
                            data = resp.json().get("data", [])
                            data.sort(key=lambda x: x.get("index", i))
                            batch_embs = [item["embedding"] for item in data]
                            all_embeddings.extend(batch_embs)
                            break # Успех
                        except Exception as e:
                            last_error = e
                            if attempt < 2: time.sleep(1)
                            else: raise
                            
            return np.array(all_embeddings, dtype=np.float32)
        except Exception as e:
            print(f"[UMS_CLIENT] Critical embedding error: {e}")
            raise RuntimeError(f"embed_fn failed: {e}")

    return embed_fn


def process_vision_via_ums(image_path: str, prompt: str) -> str:
    """
    Обрабатывает изображение через UMS, используя Vision-модель (Qwen-VL).
    
    Используется MCP Document Server для OCR и анализа изображений.
    """
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_path}},
                    {"type": "text", "text": prompt}
                ]
            }
        ]
    }
    
    try:
        response = ums_client.infer("qwen-vl-8b", payload, device_mode="hybrid")
        
        # Парсим ответ от llama-server
        if "choices" in response:
            return response["choices"][0].get("message", {}).get("content", "")
        else:
            return str(response)
    
    except Exception as e:
        print(f"[UMS_CLIENT] Error processing vision: {e}")
        raise
