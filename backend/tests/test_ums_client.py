import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import httpx
import requests
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.model_manager.ums_client import (
    UMSBusyError,
    UMSClient,
    _resolve_execution_plan_for_model,
    create_ums_embed_fn,
)
from services.model_manager.model_selection import ModelSelection
from services.observability import render_metrics_text, reset_observability_metrics


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


class _FakeRequestsResponse:
    def __init__(self, status_code=200, payload=None, request=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.request = request or requests.Request("POST", "http://localhost:8090/infer").prepare()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"status={self.status_code}",
                response=self,
                request=self.request,
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


class _FakeStreamingResponse:
    def __init__(self, lines=None, error=None):
        self._lines = list(lines or [])
        self._error = error

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        if self._error is not None:
            raise self._error


class _FakeStreamContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeStreamingClient:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def stream(self, *args, **kwargs):
        self.calls += 1
        return _FakeStreamContext(self._response)


def _fallback_selection():
    return ModelSelection(
        requested_model_id="primary-model",
        requested_model_key="primary-key",
        role_key="test.role",
        role_label="test role",
        primary_model_key="primary-key",
        fallback_model_key="fallback-key",
        primary_env_var=None,
        fallback_env_var=None,
        primary_model_id="primary-model",
        fallback_model_id="fallback-model",
        resolved_model_id="primary-model",
        fallback_available=True,
        source="registry_primary",
        warning=None,
    )


@pytest.fixture(autouse=True)
def _reset_ums_client_metrics():
    reset_observability_metrics()
    yield
    reset_observability_metrics()


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
    metrics = render_metrics_text()
    assert "llm_tools_platform_fallback_events_total" in metrics
    assert 'component="ums_client"' in metrics
    assert 'fallback="async_infer_retry"' in metrics


def test_resolve_execution_plan_for_model_supports_role_key_compatibility():
    selection = _resolve_execution_plan_for_model("llm.legal_compare")

    assert selection.role_key == "llm.legal_compare"
    assert selection.resolved_model_id == "qwen-14b-llm"


@pytest.mark.asyncio
async def test_async_infer_accepts_role_key_and_uses_resolved_model_id(monkeypatch):
    client = UMSClient(base_url="http://localhost:8090")
    fake_client = _FakeClient([
        _FakeResponse(status_code=200, payload={"status": "success", "result": {"content": "ok"}}),
    ])

    with patch("services.model_manager.ums_client._get_async_client", new=AsyncMock(return_value=fake_client)):
        result = await client.async_infer("llm.legal_compare", {"prompt": "x"})

    assert result["content"] == "ok"
    assert client.last_model_execution["role_key"] == "llm.legal_compare"
    assert client.last_model_execution["used_model_id"] == "qwen-14b-llm"


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


def test_infer_falls_back_to_fallback_model(monkeypatch):
    client = UMSClient(base_url="http://localhost:8090")
    monkeypatch.setattr("services.model_manager.ums_client.UMS_SYNC_INFER_RETRIES", 1)
    monkeypatch.setattr(
        "services.model_manager.ums_client._resolve_execution_plan_for_model",
        lambda model_id, role_key=None: _fallback_selection(),
    )

    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json["model_id"])
        if json["model_id"] == "primary-model":
            return _FakeRequestsResponse(status_code=503)
        if json["model_id"] == "fallback-model":
            return _FakeRequestsResponse(
                status_code=200,
                payload={"status": "success", "result": {"content": "ok"}},
            )
        raise AssertionError(f"Unexpected model id: {json['model_id']}")

    with patch("services.model_manager.ums_client.requests.post", side_effect=fake_post):
        result = client.infer("primary-model", {"prompt": "x"})

    assert result["content"] == "ok"
    assert result["model_execution"]["fallback_used"] is True
    assert result["model_execution"]["used_model_id"] == "fallback-model"
    assert calls == ["primary-model", "fallback-model"]
    assert client.last_model_execution["used_model_id"] == "fallback-model"


@pytest.mark.asyncio
async def test_async_infer_falls_back_to_fallback_model(monkeypatch):
    client = UMSClient(base_url="http://localhost:8090")
    monkeypatch.setenv("UMS_INFER_RETRIES", "1")
    monkeypatch.setattr(
        "services.model_manager.ums_client._resolve_execution_plan_for_model",
        lambda model_id, role_key=None: _fallback_selection(),
    )

    fake_client = _FakeClient([
        _FakeResponse(status_code=503),
        _FakeResponse(status_code=200, payload={"status": "success", "result": {"content": "ok"}}),
    ])

    with patch("services.model_manager.ums_client._get_async_client", new=AsyncMock(return_value=fake_client)):
        result = await client.async_infer("primary-model", {"prompt": "x"})

    assert result["content"] == "ok"
    assert result["model_execution"]["fallback_used"] is True
    assert result["model_execution"]["used_model_id"] == "fallback-model"
    assert fake_client.calls == 2
    assert client.last_model_execution["used_model_id"] == "fallback-model"


def test_infer_does_not_fallback_on_429(monkeypatch):
    client = UMSClient(base_url="http://localhost:8090")
    monkeypatch.setenv("UMS_SYNC_INFER_RETRIES", "1")
    monkeypatch.setattr(
        "services.model_manager.ums_client._resolve_execution_plan_for_model",
        lambda model_id, role_key=None: _fallback_selection(),
    )

    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json["model_id"])
        return _FakeRequestsResponse(status_code=429)

    with patch("services.model_manager.ums_client.requests.post", side_effect=fake_post):
        with pytest.raises(UMSBusyError):
            client.infer("primary-model", {"prompt": "x"})

    assert calls == ["primary-model"]


def test_create_ums_embed_fn_falls_back_on_probe_and_batch(monkeypatch):
    monkeypatch.setattr(
        "services.model_manager.ums_client._resolve_default_retrieval_embedder_selection",
        lambda: _fallback_selection(),
    )
    monkeypatch.setattr("services.model_manager.ums_client.UMS_EMBED_BATCH_RETRIES", 1)

    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((json["model"], tuple(json["input"])))
        if json["model"] == "primary-model":
            return _FakeRequestsResponse(status_code=503)
        payload = {
            "data": [
                {"index": idx, "embedding": [float(idx) + 0.5]}
                for idx, _ in enumerate(json["input"])
            ]
        }
        return _FakeRequestsResponse(status_code=200, payload=payload)

    with patch("services.model_manager.ums_client.requests.post", side_effect=fake_post):
        embed_fn = create_ums_embed_fn()
        assert embed_fn is not None
        embeddings = embed_fn(["alpha", "beta"])

    assert embeddings.shape == (2, 1)
    assert embed_fn.last_model_execution["used_model_id"] == "fallback-model"  # type: ignore[attr-defined]
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_async_infer_stream_does_not_fallback_after_first_token(monkeypatch):
    client = UMSClient(base_url="http://localhost:8090")
    monkeypatch.setattr(
        "services.model_manager.ums_client._resolve_execution_plan_for_model",
        lambda model_id, role_key=None: _fallback_selection(),
    )

    fake_client = _FakeStreamingClient(
        _FakeStreamingResponse(
            lines=[
                'data: {"choices":[{"text":"hello"}]}',
            ],
            error=httpx.ReadError("stream failed"),
        )
    )

    collected = []
    with patch("services.model_manager.ums_client._get_async_client", new=AsyncMock(return_value=fake_client)):
        with pytest.raises(httpx.ReadError):
            async for token in client.async_infer_stream("primary-model", {"prompt": "x"}):
                collected.append(token)

    assert collected == ["hello"]
    assert fake_client.calls == 1
    assert client.last_model_execution["used_model_id"] == "primary-model"


@pytest.mark.asyncio
async def test_async_infer_stream_records_stream_error_metric():
    client = UMSClient(base_url="http://localhost:8090")

    class _FailingStreamContext:
        async def __aenter__(self):
            raise httpx.ReadTimeout("stream timeout")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeStreamingClient:
        def stream(self, *args, **kwargs):
            return _FailingStreamContext()

    with patch("services.model_manager.ums_client._get_async_client", new=AsyncMock(return_value=_FakeStreamingClient())):
        with pytest.raises(httpx.ReadTimeout):
            async for _ in client.async_infer_stream("qwen-14b-llm", {"prompt": "x"}):
                pass

    metrics = render_metrics_text()
    assert "llm_tools_platform_fallback_events_total" in metrics
    assert 'component="ums_client"' in metrics
    assert 'fallback="stream_error"' in metrics
