import asyncio
import os
import sys
from contextlib import suppress
from typing import Any, Dict

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator import agent_api
from services.model_manager import unified_model_server as ums_server


class _FakeRequest:
    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload

    async def json(self) -> Dict[str, Any]:
        return self._payload


class _FakeJSONResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")

    def json(self) -> Dict[str, Any]:
        return self._payload


class _BlockingStreamResponse:
    def __init__(self):
        self.status_code = 200

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"Привет"},"finish_reason":null}]}'
        await asyncio.Event().wait()


class _BlockingStreamContext:
    def __init__(self, response: _BlockingStreamResponse):
        self._response = response
        self.exited = False

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


class _FakeUpstreamAsyncClient:
    def __init__(self):
        self.stream_calls = 0
        self.post_calls = 0
        self.last_stream_context: _BlockingStreamContext | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str, json: Dict[str, Any]):
        self.stream_calls += 1
        context = _BlockingStreamContext(_BlockingStreamResponse())
        self.last_stream_context = context
        return context

    async def post(self, url: str, json: Dict[str, Any]):
        self.post_calls += 1
        return _FakeJSONResponse(
            {
                "id": "chatcmpl-success",
                "object": "chat.completion",
                "created": 123,
                "model": "qwen-14b-llm",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "raw answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )


class _DirectUMSRequest:
    def __init__(self, method: str, url: str, json_body: Dict[str, Any]):
        self.method = method
        self.url = url
        self.json_body = json_body


class _DirectUMSResponse:
    def __init__(
        self,
        *,
        request: httpx.Request,
        payload: Dict[str, Any] | None = None,
        status_code: int = 200,
        stream_response: StreamingResponse | None = None,
    ):
        self.request = request
        self.status_code = status_code
        self._payload = payload or {}
        self._stream_response = stream_response

    def raise_for_status(self):
        if self.status_code < 400:
            return None
        response = httpx.Response(status_code=self.status_code, request=self.request, json=self._payload)
        raise httpx.HTTPStatusError(f"{self.status_code} error", request=self.request, response=response)

    def json(self) -> Dict[str, Any]:
        return self._payload

    async def aiter_raw(self):
        if self._stream_response is None:
            return
        async for chunk in self._stream_response.body_iterator:
            yield chunk if isinstance(chunk, bytes) else str(chunk).encode()

    async def aclose(self):
        if self._stream_response is None:
            return None
        with suppress(Exception):
            await self._stream_response.body_iterator.aclose()
        return None


class _DirectUMSClient:
    def build_request(self, method: str, url: str, json: Dict[str, Any]):
        return _DirectUMSRequest(method, url, json)

    async def send(self, request: _DirectUMSRequest, stream: bool = False):
        return await self._dispatch(request.url, request.json_body)

    async def post(self, url: str, json: Dict[str, Any]):
        return await self._dispatch(url, json)

    async def _dispatch(self, url: str, request_body: Dict[str, Any]):
        request = httpx.Request("POST", url)
        try:
            response = await ums_server.infer(ums_server.InferRequest(**request_body))
        except HTTPException as exc:
            return _DirectUMSResponse(
                request=request,
                payload={"detail": exc.detail},
                status_code=int(exc.status_code),
            )
        if isinstance(response, StreamingResponse):
            return _DirectUMSResponse(request=request, stream_response=response)
        return _DirectUMSResponse(request=request, payload=response)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    original_base_url = agent_api.ums_client.base_url
    for env_name in (
        "UMS_LLM_MAX_CONCURRENCY",
        "UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S",
        "UMS_FAIL_FAST_ON_SATURATION",
        "BACKEND_MODE",
        "UMS_EMBED_MAX_CONCURRENCY",
        "UMS_LLM_CONCURRENCY",
        "UMS_EMBED_CONCURRENCY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    ums_server.state["processes"].clear()
    ums_server.state["placements"].clear()
    ums_server.state["admission"].clear()
    ums_server.state["active_model"] = None
    ums_server.state["dynamic_models"] = {}
    ums_server.state["reserved_ports"] = set()
    ums_server.state["port_owners"] = {}
    ums_server.state["released_dynamic_ports"] = []
    ums_server.state["concurrency_policy"] = {}
    ums_server._llm_semaphore = asyncio.Semaphore(1)
    ums_server._embed_semaphore = asyncio.Semaphore(4)
    ums_server._concurrency_controls["llm_limit"] = 1
    ums_server._concurrency_controls["embed_limit"] = 4

    yield

    agent_api.ums_client.base_url = original_base_url
    ums_server.state["processes"].clear()
    ums_server.state["placements"].clear()
    ums_server.state["admission"].clear()
    ums_server.state["active_model"] = None
    ums_server.state["concurrency_policy"] = {}


@pytest.mark.asyncio
async def test_raw_busy_cancel_retry_releases_slot(monkeypatch):
    monkeypatch.setenv("UMS_FAIL_FAST_ON_SATURATION", "true")
    monkeypatch.setenv("UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S", "0.01")

    fake_upstream = _FakeUpstreamAsyncClient()
    monkeypatch.setattr(ums_server.httpx, "AsyncClient", lambda *args, **kwargs: fake_upstream)
    monkeypatch.setattr(
        ums_server,
        "_start_server",
        lambda model_id, device_mode, stage="infer": model_id,
    )
    monkeypatch.setattr(
        ums_server,
        "get_model_config",
        lambda model_id: {"type": "gguf", "path": "./models/qwen.gguf", "port": 8091},
    )
    monkeypatch.setattr(agent_api, "get_all_models", lambda: {"qwen-14b-llm": {"kind": "llm"}})
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )

    agent_api.ums_client.base_url = "http://direct-ums"
    direct_client = _DirectUMSClient()

    async def fake_get_shared_client():
        return direct_client

    monkeypatch.setattr(agent_api, "get_shared_client", fake_get_shared_client)

    first_response = await agent_api.raw_openai_completions(
        _FakeRequest(
            {
                "messages": [{"role": "user", "content": "Первый длинный запрос"}],
                "stream": True,
            }
        )
    )

    assert isinstance(first_response, StreamingResponse)
    first_chunk = await anext(first_response.body_iterator)
    first_chunk_text = first_chunk.decode() if isinstance(first_chunk, bytes) else str(first_chunk)
    assert "Привет" in first_chunk_text
    assert getattr(ums_server._llm_semaphore, "_value", None) == 0

    second_response = await agent_api.raw_openai_completions(
        _FakeRequest(
            {
                "messages": [{"role": "user", "content": "Второй запрос во время занятости"}],
                "stream": False,
            }
        )
    )

    assert second_response["choices"][0]["message"]["content"].startswith("Модель занята")
    assert fake_upstream.post_calls == 0

    await first_response.body_iterator.aclose()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    retry_response = await agent_api.raw_openai_completions(
        _FakeRequest(
            {
                "messages": [{"role": "user", "content": "Повторный запрос после cancel"}],
                "stream": False,
            }
        )
    )

    assert retry_response["choices"][0]["message"]["content"] == "raw answer"
    assert fake_upstream.stream_calls == 1
    assert fake_upstream.post_calls == 1
    assert fake_upstream.last_stream_context is not None
    assert fake_upstream.last_stream_context.exited is True
    assert getattr(ums_server._llm_semaphore, "_value", None) == 1
