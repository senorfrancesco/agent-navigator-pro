import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.model_manager import unified_model_server as ums_server


class _FakeResponse:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")

    async def aiter_lines(self):
        for line in self._lines:
            if isinstance(line, tuple) and line[0] == "sleep":
                await asyncio.sleep(line[1])
                continue
            yield line


class _FakeStreamContext:
    def __init__(self, response):
        self._response = response
        self.exited = False

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


class _FakeAsyncClient:
    def __init__(self, stream_context):
        self._stream_context = stream_context

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, json):
        return self._stream_context


@pytest.mark.asyncio
async def test_proxy_sse_stream_yields_upstream_chunks():
    response = _FakeResponse([
        'data: {"choices":[{"text":"Hello"}]}',
        "data: [DONE]",
    ])
    stream_context = _FakeStreamContext(response)

    with patch(
        "services.model_manager.unified_model_server.httpx.AsyncClient",
        return_value=_FakeAsyncClient(stream_context),
    ):
        chunks = [
            chunk async for chunk in ums_server._proxy_sse_stream(
                "http://localhost:8091/v1/completions",
                {"prompt": "x", "stream": True},
                asyncio.Semaphore(1),
            )
        ]

    assert chunks == [
        b'data: {"choices":[{"text":"Hello"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    assert stream_context.exited is True


@pytest.mark.asyncio
async def test_proxy_sse_stream_closes_upstream_when_consumer_stops_early():
    response = _FakeResponse([
        'data: {"choices":[{"text":"Hello"}]}',
        ("sleep", 10),
        'data: {"choices":[{"text":" world"}]}',
    ])
    stream_context = _FakeStreamContext(response)

    with patch(
        "services.model_manager.unified_model_server.httpx.AsyncClient",
        return_value=_FakeAsyncClient(stream_context),
    ):
        stream = ums_server._proxy_sse_stream(
            "http://localhost:8091/v1/completions",
            {"prompt": "x", "stream": True},
            asyncio.Semaphore(1),
        )

        first = await anext(stream)
        assert first == b'data: {"choices":[{"text":"Hello"}]}\n\n'

        await stream.aclose()

    assert stream_context.exited is True
