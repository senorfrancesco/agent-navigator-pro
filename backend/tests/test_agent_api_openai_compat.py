import os
import sys
from typing import Any, Dict

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator import agent_api


class _FakeRequest:
    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload

    async def json(self) -> Dict[str, Any]:
        return self._payload


async def _read_streaming_body(response: StreamingResponse) -> str:
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode())
        else:
            chunks.append(str(chunk))
    return "".join(chunks)


@pytest.mark.asyncio
async def test_openai_chat_completions_routes_through_execute_orchestration(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        captured["request"] = request
        captured["deps"] = deps
        return {
            "assistant_message": "compat answer",
            "trace_id": "trace-1",
            "state_ref": "trace:trace-1",
            "pending_action_id": None,
            "ui_effects": {"clear_pending_action": True},
            "sources": [],
            "effective_settings": {"runtime_mode": "auto"},
            "rag_scope": "off",
            "knowledge_collection_id": None,
            "source_scope_summary": "off",
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_discover_openai_attachments", lambda user_query: [])
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "agent-navigator",
                "messages": [{"role": "user", "content": "Привет"}],
            }
        )
    )
    body = await _read_streaming_body(response)

    assert captured["request"]["message"] == "Привет"
    assert captured["request"]["history"] == []
    assert "compat answer" in body
    assert "data: [DONE]" in body


@pytest.mark.asyncio
async def test_openai_chat_completions_preserves_direct_model_override(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        captured["request"] = request
        return {
            "assistant_message": "coder answer",
            "trace_id": "trace-2",
            "state_ref": "trace:trace-2",
            "pending_action_id": None,
            "ui_effects": {"clear_pending_action": True},
            "sources": [],
            "effective_settings": request["effective_settings"],
            "rag_scope": "off",
            "knowledge_collection_id": None,
            "source_scope_summary": "off",
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_discover_openai_attachments", lambda user_query: [])
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "qwen-coder-32b",
                "messages": [{"role": "user", "content": "Напиши функцию"}],
            }
        )
    )
    body = await _read_streaming_body(response)

    assert captured["request"]["runtime_mode"] == "chat_only"
    assert captured["request"]["effective_settings"]["resolved_model_id"] == "qwen-coder-32b"
    assert "coder answer" in body


@pytest.mark.asyncio
async def test_openai_chat_completions_formats_multimodal_text_content(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        captured["request"] = request
        return {
            "assistant_message": "ok",
            "trace_id": "trace-3",
            "state_ref": "trace:trace-3",
            "pending_action_id": None,
            "ui_effects": {"clear_pending_action": True},
            "sources": [],
            "effective_settings": request["effective_settings"],
            "rag_scope": "off",
            "knowledge_collection_id": None,
            "source_scope_summary": "off",
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_discover_openai_attachments", lambda user_query: [])
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "agent-navigator",
                "messages": [
                    {"role": "system", "content": "Будь строгим."},
                    {"role": "assistant", "content": "Готов."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Проанализируй документ"},
                            {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
                        ],
                    },
                ],
            }
        )
    )
    await _read_streaming_body(response)

    assert captured["request"]["message"] == "Проанализируй документ"
    assert captured["request"]["history"] == [{"role": "assistant", "content": "Готов."}]
    assert captured["request"]["custom_system_prompt"] == "Будь строгим."


def test_discover_openai_attachments_does_not_pull_recent_uploads_by_default(monkeypatch):
    recent_calls = {"count": 0}

    def fake_recent_uploads():
        recent_calls["count"] += 1
        return []

    monkeypatch.delenv("OPENAI_COMPAT_ENABLE_UPLOAD_DISCOVERY", raising=False)
    monkeypatch.setattr(agent_api, "_discover_recent_uploaded_files", fake_recent_uploads)

    found = agent_api._discover_openai_attachments("Привет")

    assert found == []
    assert recent_calls["count"] == 0


@pytest.mark.asyncio
async def test_openai_chat_completions_non_streaming_returns_json(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        captured["request"] = request
        return {
            "assistant_message": "non-stream answer",
            "trace_id": "trace-json",
            "state_ref": "trace:trace-json",
            "pending_action_id": None,
            "ui_effects": {"clear_pending_action": True},
            "sources": [],
            "effective_settings": request["effective_settings"],
            "rag_scope": "off",
            "knowledge_collection_id": None,
            "source_scope_summary": "off",
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_discover_openai_attachments", lambda user_query: [])
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "agent-navigator",
                "messages": [{"role": "user", "content": "Привет"}],
                "stream": False,
            }
        )
    )

    assert not isinstance(response, StreamingResponse)
    assert response["choices"][0]["message"]["content"] == "non-stream answer"
    assert captured["request"]["message"] == "Привет"


@pytest.mark.asyncio
async def test_openai_chat_completions_loads_attachment_text_into_session_docs(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        captured["request"] = request
        return {
            "assistant_message": "summary",
            "trace_id": "trace-summary",
            "state_ref": "trace:trace-summary",
            "pending_action_id": None,
            "ui_effects": {"clear_pending_action": True},
            "sources": [],
            "effective_settings": request["effective_settings"],
            "rag_scope": "session_rag",
            "knowledge_collection_id": None,
            "source_scope_summary": "session",
        }

    async def fake_load_session_docs(attachments):
        return {
            "contract.pdf": {
                "document_id": "contract.pdf",
                "path": "/tmp/contract.pdf",
                "text": "Штраф составляет 10 процентов.",
            }
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(
        agent_api,
        "_discover_openai_attachments",
        lambda user_query: [
            agent_api.FileAttachment(
                name="contract.pdf",
                path="/tmp/contract.pdf",
                size=10,
                type="application/pdf",
            )
        ],
    )
    monkeypatch.setattr(agent_api, "_load_openai_session_docs", fake_load_session_docs, raising=False)
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "agent-navigator",
                "messages": [{"role": "user", "content": "Сделай сводку по документам"}],
                "stream": False,
            }
        )
    )

    assert response["choices"][0]["message"]["content"] == "summary"
    assert captured["request"]["has_session_docs"] is True
    assert captured["request"]["session_docs"]["contract.pdf"]["text"] == "Штраф составляет 10 процентов."


def test_raw_models_lists_chat_capable_models_without_agent_wrapper(monkeypatch):
    monkeypatch.setattr(
        agent_api,
        "get_all_models",
        lambda: {
            "qwen-14b-llm": {"kind": "llm"},
            "qwen-vl-8b": {"kind": "vision"},
            "labse-embedding": {"kind": "retrieval_embedder"},
        },
    )

    response = agent_api.list_raw_models()

    assert response["object"] == "list"
    assert [item["id"] for item in response["data"]] == ["qwen-14b-llm", "qwen-vl-8b"]
    assert all(item["id"] != "agent-navigator" for item in response["data"])


@pytest.mark.asyncio
async def test_raw_chat_completions_non_streaming_proxies_to_ums(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_request_raw_openai_infer(*, target_model: str, payload: Dict[str, Any]):
        captured["model_id"] = target_model
        captured["payload"] = payload
        return {
            "id": "chatcmpl-raw",
            "object": "chat.completion",
            "created": 123,
            "model": target_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "raw answer"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(agent_api, "get_all_models", lambda: {"qwen-14b-llm": {"kind": "llm"}})
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )
    monkeypatch.setattr(agent_api, "_request_raw_openai_infer", fake_request_raw_openai_infer)

    response = await agent_api.raw_openai_completions(
        _FakeRequest(
            {
                "messages": [{"role": "user", "content": "Привет"}],
                "stream": False,
            }
        )
    )

    assert response["choices"][0]["message"]["content"] == "raw answer"
    assert captured["model_id"] == "qwen-14b-llm"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "Привет"}]


@pytest.mark.asyncio
async def test_raw_chat_completions_non_streaming_surfaces_runtime_unavailable(monkeypatch):
    async def fake_request_raw_openai_infer(*, target_model: str, payload: Dict[str, Any]):
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error_gpu",
                "runtime_state": "error_gpu",
                "requested_model_id": target_model,
                "reason": "ggml_cuda_init: failed",
            },
        )

    monkeypatch.setattr(agent_api, "get_all_models", lambda: {"qwen-14b-llm": {"kind": "llm"}})
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )
    monkeypatch.setattr(agent_api, "_request_raw_openai_infer", fake_request_raw_openai_infer)

    with pytest.raises(HTTPException) as exc_info:
        await agent_api.raw_openai_completions(
            _FakeRequest(
                {
                    "messages": [{"role": "user", "content": "Привет"}],
                    "stream": False,
                }
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["runtime_state"] == "error_gpu"


@pytest.mark.asyncio
async def test_raw_chat_completions_streaming_proxies_to_ums(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_open_stream(*, target_model: str, payload: Dict[str, Any]):
        captured["model_id"] = target_model
        captured["payload"] = payload
        return object()

    async def fake_stream(_response):
        yield "data: {\"choices\":[{\"delta\":{\"content\":\"Пр\"},\"finish_reason\":null}]}\n\n".encode("utf-8")
        yield "data: {\"choices\":[{\"delta\":{\"content\":\"ивет\"},\"finish_reason\":null}]}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(agent_api, "get_all_models", lambda: {"qwen-14b-llm": {"kind": "llm"}})
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )
    monkeypatch.setattr(agent_api, "_open_raw_openai_stream", fake_open_stream)
    monkeypatch.setattr(agent_api, "_proxy_raw_openai_stream", fake_stream)

    response = await agent_api.raw_openai_completions(
        _FakeRequest(
            {
                "messages": [{"role": "user", "content": "Привет"}],
            }
        )
    )
    body = await _read_streaming_body(response)

    assert isinstance(response, StreamingResponse)
    assert captured["model_id"] == "qwen-14b-llm"
    assert "\"content\":\"Пр\"" in body
    assert "\"content\":\"ивет\"" in body
    assert "data: [DONE]" in body


@pytest.mark.asyncio
async def test_raw_chat_completions_returns_busy_stream_response_before_stream_starts(monkeypatch):
    class _FakeStreamResponse:
        def __init__(self):
            self.request = httpx.Request("POST", "http://localhost:8090/infer")
            self.status_code = 429

        def raise_for_status(self):
            response = httpx.Response(
                status_code=429,
                request=self.request,
                json={
                    "detail": {
                        "status": "busy",
                        "kind": "stream",
                        "reason": "fail_fast_saturated",
                        "message": "busy message",
                    }
                },
            )
            raise httpx.HTTPStatusError("429 Too Many Requests", request=self.request, response=response)

        async def aclose(self):
            return None

    class _FakeClient:
        def build_request(self, method: str, url: str, json: Dict[str, Any]):
            return httpx.Request(method, url)

        async def send(self, request: httpx.Request, stream: bool = False):
            return _FakeStreamResponse()

    async def fake_get_shared_client():
        return _FakeClient()

    monkeypatch.setattr(agent_api, "get_all_models", lambda: {"qwen-14b-llm": {"kind": "llm"}})
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )
    monkeypatch.setattr(agent_api, "get_shared_client", fake_get_shared_client)

    response = await agent_api.raw_openai_completions(
        _FakeRequest(
            {
                "messages": [{"role": "user", "content": "Привет"}],
                "stream": True,
            }
        )
    )
    body = await _read_streaming_body(response)

    assert isinstance(response, StreamingResponse)
    assert "busy message" in body
    assert "data: [DONE]" in body


@pytest.mark.asyncio
async def test_raw_chat_completions_returns_busy_non_stream_response(monkeypatch):
    class _FakeBusyResponse:
        def __init__(self):
            self.request = httpx.Request("POST", "http://localhost:8090/infer")
            self.status_code = 429

        def raise_for_status(self):
            response = httpx.Response(
                status_code=429,
                request=self.request,
                json={
                    "detail": {
                        "status": "busy",
                        "kind": "llm",
                        "reason": "queue_timeout",
                        "message": "busy message",
                    }
                },
            )
            raise httpx.HTTPStatusError("429 Too Many Requests", request=self.request, response=response)

        def json(self):
            return {"detail": {"status": "busy"}}

    class _FakeClient:
        async def post(self, url: str, json: Dict[str, Any]):
            return _FakeBusyResponse()

    async def fake_get_shared_client():
        return _FakeClient()

    monkeypatch.setattr(agent_api, "get_all_models", lambda: {"qwen-14b-llm": {"kind": "llm"}})
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )
    monkeypatch.setattr(agent_api, "get_shared_client", fake_get_shared_client)

    response = await agent_api.raw_openai_completions(
        _FakeRequest(
            {
                "messages": [{"role": "user", "content": "Привет"}],
                "stream": False,
            }
        )
    )

    assert response["choices"][0]["message"]["content"] == "busy message"
    assert response["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_raw_chat_completions_returns_runtime_unavailable_before_stream_starts(monkeypatch):
    runtime_detail = {
        "status": "error_gpu",
        "runtime_state": "error_gpu",
        "requested_model_id": "qwen-14b-llm",
        "reason": "ggml_cuda_init: failed",
    }

    class _FakeStreamResponse:
        def __init__(self):
            self.request = httpx.Request("POST", "http://localhost:8090/infer")
            self.status_code = 503

        def raise_for_status(self):
            response = httpx.Response(
                status_code=503,
                request=self.request,
                json={"detail": runtime_detail},
            )
            raise httpx.HTTPStatusError("503 Service Unavailable", request=self.request, response=response)

        async def aclose(self):
            return None

    class _FakeClient:
        def build_request(self, method: str, url: str, json: Dict[str, Any]):
            return httpx.Request(method, url)

        async def send(self, request: httpx.Request, stream: bool = False):
            return _FakeStreamResponse()

    async def fake_get_shared_client():
        return _FakeClient()

    monkeypatch.setattr(agent_api, "get_all_models", lambda: {"qwen-14b-llm": {"kind": "llm"}})
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )
    monkeypatch.setattr(agent_api, "get_shared_client", fake_get_shared_client)

    with pytest.raises(HTTPException) as exc_info:
        await agent_api.raw_openai_completions(
            _FakeRequest(
                {
                    "messages": [{"role": "user", "content": "Привет"}],
                    "stream": True,
                }
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["runtime_state"] == "error_gpu"


@pytest.mark.asyncio
async def test_raw_chat_completions_rejects_unknown_model(monkeypatch):
    monkeypatch.setattr(agent_api, "get_all_models", lambda: {"qwen-14b-llm": {"kind": "llm"}})
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )

    with pytest.raises(HTTPException) as exc:
        await agent_api.raw_openai_completions(
            _FakeRequest(
                {
                    "model": "unknown-model",
                    "messages": [{"role": "user", "content": "Привет"}],
                    "stream": False,
                }
            )
        )

    assert exc.value.status_code == 404
