import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.observability import ObservabilityMiddleware, render_metrics_text, reset_observability_metrics


class _DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


async def _dummy_app(scope, receive, send):
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"ok",
            "more_body": False,
        }
    )


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_observability_metrics()
    yield
    reset_observability_metrics()


def test_observability_middleware_propagates_trace_id_and_records_http_metrics():
    middleware = ObservabilityMiddleware(_dummy_app, service_name="dummy", logger=_DummyLogger())
    sent_messages = []

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(message):
        sent_messages.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/health",
        "headers": [(b"x-trace-id", b"trace-123")],
        "state": {},
    }

    asyncio.run(middleware(scope, _receive, _send))

    response_start = next(msg for msg in sent_messages if msg["type"] == "http.response.start")
    assert (b"x-trace-id", b"trace-123") in response_start["headers"]
    payload = render_metrics_text()
    assert "llm_tools_platform_http_requests_total" in payload
    assert 'service="dummy"' in payload
    assert 'path="/health"' in payload


def test_observability_middleware_normalizes_dynamic_model_paths():
    middleware = ObservabilityMiddleware(_dummy_app, service_name="dummy", logger=_DummyLogger())
    sent_messages = []

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(message):
        sent_messages.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": "/models/qwen-14b-llm/activate",
        "headers": [],
        "state": {},
    }

    asyncio.run(middleware(scope, _receive, _send))

    payload = render_metrics_text()
    assert 'path="/models/{model_id}/activate"' in payload
    assert 'path="/models/qwen-14b-llm/activate"' not in payload


def test_observability_middleware_excludes_metrics_endpoint_from_http_metrics():
    middleware = ObservabilityMiddleware(_dummy_app, service_name="dummy", logger=_DummyLogger())
    sent_messages = []

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(message):
        sent_messages.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/metrics",
        "headers": [],
        "state": {},
    }

    asyncio.run(middleware(scope, _receive, _send))

    response_start = next(msg for msg in sent_messages if msg["type"] == "http.response.start")
    assert any(header[0] == b"x-trace-id" for header in response_start["headers"])
    payload = render_metrics_text()
    assert "llm_tools_platform_http_requests_total" not in payload
    assert 'path="/metrics"' not in payload
