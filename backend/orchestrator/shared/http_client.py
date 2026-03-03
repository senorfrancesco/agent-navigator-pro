import httpx
import logging
import asyncio
from typing import Optional

logger = logging.getLogger("http_client")

class AsyncHttpClient:
    """
    Shared HTTP client singleton with connection pooling.
    TD-11 Fix: Reuses connections to microservices.
    """
    _instance: Optional[httpx.AsyncClient] = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    # Настраиваем пул соединений
                    limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
                    cls._instance = httpx.AsyncClient(
                        timeout=httpx.Timeout(300.0, connect=10.0),
                        limits=limits,
                        follow_redirects=True
                    )
                    logger.info("Initialized shared AsyncHttpClient with connection pooling.")
        return cls._instance

    @classmethod
    async def close_client(cls):
        async with cls._lock:
            if cls._instance:
                await cls._instance.aclose()
                cls._instance = None
                logger.info("Shared AsyncHttpClient closed.")

# Глобальный хелпер для удобного доступа
async def get_shared_client() -> httpx.AsyncClient:
    return await AsyncHttpClient.get_client()
