import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.model_manager.ums_client import UMSClient


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, request=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.request = request or httpx.Request("POST", "http://localhost:8090/infer")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status={self.status_code}",
                request=self.request,
                response=self,
            )

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def post(self, *args, **kwargs):
        self.calls += 1
        response = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_async_infer_retries_on_503(monkeypatch):
    client = UMSClient(base_url="http://localhost:8090")
    fake_client = _FakeClient([
        _FakeResponse(status_code=503),
        _FakeResponse(status_code=503),
        _FakeResponse(status_code=200, payload={"status": "success", "result": {"content": "ok"}}),
    ])

    monkeypatch.setenv("UMS_INFER_RETRIES", "4")
    monkeypatch.setenv("UMS_RETRY_BASE_DELAY_S", "0")
    monkeypatch.setenv("UMS_RETRY_MAX_DELAY_S", "0")

    with patch("services.model_manager.ums_client._get_async_client", new=AsyncMock(return_value=fake_client)):
        result = await client.async_infer("qwen-14b-llm", {"prompt": "x"})

    assert result["content"] == "ok"
    assert fake_client.calls == 3


@pytest.mark.asyncio
async def test_async_infer_does_not_retry_on_400(monkeypatch):
    client = UMSClient(base_url="http://localhost:8090")
    fake_client = _FakeClient([
        _FakeResponse(status_code=400),
    ])

    monkeypatch.setenv("UMS_INFER_RETRIES", "5")

    with patch("services.model_manager.ums_client._get_async_client", new=AsyncMock(return_value=fake_client)):
        with pytest.raises(RuntimeError):
            await client.async_infer("qwen-14b-llm", {"prompt": "x"})

    assert fake_client.calls == 1
