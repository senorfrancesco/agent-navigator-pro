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
    def __init__(self, response, *, exit_delay_s: float = 0.0):
        self._response = response
        self.exited = False
        self.exit_delay_s = exit_delay_s

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        if self.exit_delay_s:
            await asyncio.sleep(self.exit_delay_s)
        self.exited = True
        return False


class _FakeAsyncClient:
    def __init__(self, stream_context, *, headers=None):
        self._stream_context = stream_context
        self.headers = headers or {}
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, json):
        self.calls.append({"method": method, "url": url, "json": json, "headers": dict(self.headers)})
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


@pytest.mark.asyncio
async def test_proxy_sse_stream_passes_upstream_headers():
    response = _FakeResponse(['data: {"choices":[{"text":"Hello"}]}', "data: [DONE]"])
    stream_context = _FakeStreamContext(response)
    fake_client = _FakeAsyncClient(stream_context, headers={"Authorization": "Bearer test-token"})

    with patch(
        "services.model_manager.unified_model_server.httpx.AsyncClient",
        return_value=fake_client,
    ):
        chunks = [
            chunk async for chunk in ums_server._proxy_sse_stream(
                "http://vllm.local/v1/completions",
                {"prompt": "x", "stream": True},
                asyncio.Semaphore(1),
                headers={"Authorization": "Bearer test-token"},
            )
        ]

    assert chunks[-1] == b"data: [DONE]\n\n"
    assert fake_client.calls == [
        {
            "method": "POST",
            "url": "http://vllm.local/v1/completions",
            "json": {"prompt": "x", "stream": True},
            "headers": {"Authorization": "Bearer test-token"},
        }
    ]


@pytest.mark.asyncio
async def test_proxy_sse_stream_releases_slot_on_done_before_upstream_exit():
    response = _FakeResponse([
        'data: {"choices":[{"text":"Hello"}]}',
        "data: [DONE]",
    ])
    stream_context = _FakeStreamContext(response, exit_delay_s=0.2)
    sem = asyncio.Semaphore(1)

    with patch(
        "services.model_manager.unified_model_server.httpx.AsyncClient",
        return_value=_FakeAsyncClient(stream_context),
    ):
        stream = ums_server._proxy_sse_stream(
            "http://localhost:8091/v1/completions",
            {"prompt": "x", "stream": True},
            sem,
        )

        first = await anext(stream)
        assert first == b'data: {"choices":[{"text":"Hello"}]}\n\n'

        done_chunk = await anext(stream)
        assert done_chunk == b"data: [DONE]\n\n"
        assert getattr(sem, "_value", None) == 1
        assert stream_context.exited is False

        await stream.aclose()

    assert stream_context.exited is True
