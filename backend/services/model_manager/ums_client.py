"""
UMS Client - HTTP-клиент для взаимодействия с Unified Model Server.

Все MCP-серверы используют этот клиент для выполнения инференса.
"""

import asyncio
import os
import time
import requests
import httpx
import numpy as np
from typing import Dict, Any, Optional, List, AsyncGenerator, Callable
import json
import random

UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")

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
        Асинхронный инференс через UMS (использует httpx.AsyncClient).
        Используется в async LangGraph нодах для избежания блокировки event loop.
        """
        url = f"{self.base_url}/infer"
        request_body = {
            "model_id": model_id,
            "payload": payload,
            "device_mode": device_mode,
            "priority": "normal"
        }
        retries = int(os.getenv("UMS_INFER_RETRIES", "5"))
        base_delay_s = float(os.getenv("UMS_RETRY_BASE_DELAY_S", "1.5"))
        max_delay_s = float(os.getenv("UMS_RETRY_MAX_DELAY_S", "12"))
        timeout_s = float(os.getenv("UMS_INFER_TIMEOUT_S", "300"))
        last_error = None

        def _should_retry(exc: Exception) -> bool:
            # Повторяем при временных ошибках доступности/перегрузки модели.
            if isinstance(exc, httpx.TimeoutException):
                return True
            if isinstance(exc, httpx.HTTPStatusError):
                code = exc.response.status_code
                return code in {429, 500, 502, 503, 504}
            if isinstance(exc, httpx.RequestError):
                return True
            return False

        for attempt in range(retries):
            try:
                print(f"[UMS_CLIENT] Async inference for model: {model_id} (attempt {attempt+1})")
                async with httpx.AsyncClient(timeout=timeout_s) as client:
                    response = await client.post(url, json=request_body)
                    response.raise_for_status()
                    data = response.json()
                    if data.get("status") == "success":
                        return data.get("result", {})
                    else:
                        raise RuntimeError(f"UMS returned error: {data}")
            except Exception as e:
                last_error = e
                print(f"[UMS_CLIENT] Async error (attempt {attempt+1}/{retries}): {e}")
                if attempt < retries - 1 and _should_retry(e):
                    # Exponential backoff + небольшой jitter, чтобы не создавать burst.
                    backoff = min(max_delay_s, base_delay_s * (2 ** attempt))
                    jitter = random.uniform(0.0, min(0.5, backoff * 0.2))
                    await asyncio.sleep(backoff + jitter)
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
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", url, json=request_body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    # UMS проксирует SSE от llama-server: "data: {...}"
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data_str)
                            # llama-server completions format
                            choices = chunk.get("choices", [])
                            if choices:
                                text = choices[0].get("text", "") or choices[0].get("delta", {}).get("content", "")
                                if text:
                                    yield text
                        except json.JSONDecodeError:
                            continue

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
    """
    url = (base_url or UMS_URL).rstrip("/") + "/v1/embeddings"

    # Probe: проверяем доступность UMS и LaBSE
    try:
        resp = requests.post(url, json={"input": ["test"], "model": "labse-embedding"}, timeout=10)
        resp.raise_for_status()
    except Exception:
        return None

    def embed_fn(texts: List[str]) -> np.ndarray:
        resp = requests.post(url, json={"input": texts, "model": "labse-embedding"}, timeout=60)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        data.sort(key=lambda x: x.get("index", 0))
        return np.array([item["embedding"] for item in data], dtype=np.float32)

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
