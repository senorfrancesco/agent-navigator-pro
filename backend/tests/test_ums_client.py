import httpx
import pytest
import asyncio

from services.model_manager.ums_client import UMSClient


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, request=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.request = request or httpx.Request("POST", "http://localhost:8090/infer")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status={self.status_code}", request=self.request, response=self
            )

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        raise NotImplementedError


def test_async_infer_retries_on_503(monkeypatch):
    client = UMSClient(base_url="http://localhost:8090")
    calls = {"n": 0}

    class FakeClient503ThenOk(_FakeAsyncClient):
        async def post(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                return _FakeResponse(status_code=503)
            return _FakeResponse(status_code=200, payload={"status": "success", "result": {"content": "ok"}})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient503ThenOk)
    monkeypatch.setenv("UMS_INFER_RETRIES", "4")
    monkeypatch.setenv("UMS_RETRY_BASE_DELAY_S", "0")
    monkeypatch.setenv("UMS_RETRY_MAX_DELAY_S", "0")

    result = asyncio.run(client.async_infer("qwen-14b-llm", {"prompt": "x"}))

    assert result["content"] == "ok"
    assert calls["n"] == 3


def test_async_infer_does_not_retry_on_400(monkeypatch):
    client = UMSClient(base_url="http://localhost:8090")
    calls = {"n": 0}

    class FakeClient400(_FakeAsyncClient):
        async def post(self, *args, **kwargs):
            calls["n"] += 1
            return _FakeResponse(status_code=400)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient400)
    monkeypatch.setenv("UMS_INFER_RETRIES", "5")

    with pytest.raises(RuntimeError):
        asyncio.run(client.async_infer("qwen-14b-llm", {"prompt": "x"}))

    assert calls["n"] == 1
