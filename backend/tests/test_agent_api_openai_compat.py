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
    def __init__(self, payload: Dict[str, Any], headers: Dict[str, str] | None = None):
        self._payload = payload
        self.headers = headers or {}

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


def _raw_model_item(model_id: str = "qwen-14b-llm") -> Dict[str, Any]:
    return {"id": model_id, "object": "model", "created": 1, "owned_by": "llm-tools-platform-raw-provider"}


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
                "model": "llm-tools-platform",
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
async def test_openai_chat_completions_strips_timing_footer_from_compat_non_streaming(monkeypatch):
    async def fake_execute(request: Dict[str, Any], *, deps=None):
        return {
            "assistant_message": "compat answer\n\n---\nTiming / Quality\n- Полный ответ: 10 мс",
            "trace_id": "trace-footer-1",
            "sources": [],
            "telemetry": {"elapsed_ms": 10, "quality_summary": "LLM: да"},
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_discover_openai_attachments", lambda user_query: [])
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "llm-tools-platform",
                "messages": [{"role": "user", "content": "Привет"}],
                "stream": False,
            }
        )
    )

    content = response["choices"][0]["message"]["content"]
    assert content == "compat answer"
    assert "Timing / Quality" not in content
    assert "Полный ответ" not in content


@pytest.mark.asyncio
async def test_openai_chat_completions_strips_timing_footer_from_compat_streaming(monkeypatch):
    async def fake_execute(request: Dict[str, Any], *, deps=None):
        return {
            "assistant_message": "compat stream answer\n\n---\nTiming / Quality\n- Полный ответ: 15 мс",
            "trace_id": "trace-footer-2",
            "sources": [],
            "telemetry": {"elapsed_ms": 15, "quality_summary": "LLM: да"},
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_discover_openai_attachments", lambda user_query: [])
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "llm-tools-platform",
                "messages": [{"role": "user", "content": "Привет"}],
            }
        )
    )
    body = await _read_streaming_body(response)

    assert "compat stream answer" in body
    assert "Timing / Quality" not in body
    assert "Полный ответ" not in body
    assert "data: [DONE]" in body


@pytest.mark.asyncio
async def test_openai_chat_completions_without_tools_proxies_direct_model_to_raw_ums(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        raise AssertionError("execute_orchestration не должен вызываться для обычной raw-модели")

    async def fake_request_raw_openai_infer(*, target_model: str, payload: Dict[str, Any]):
        captured["model_id"] = target_model
        captured["payload"] = payload
        return {
            "id": "chatcmpl-direct",
            "object": "chat.completion",
            "created": 123,
            "model": "backend-model-label",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "raw direct answer"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(
        agent_api,
        "_discover_openai_attachments",
        lambda user_query: (_ for _ in ()).throw(AssertionError("raw path should not discover attachments")),
    )
    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-vl-8b")])
    monkeypatch.setattr(agent_api, "_request_raw_openai_infer", fake_request_raw_openai_infer)
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "qwen-vl-8b",
                "messages": [{"role": "user", "content": "Напиши функцию"}],
                "stream": False,
            }
        )
    )

    assert response["model"] == "qwen-vl-8b"
    assert response["choices"][0]["message"]["content"] == "raw direct answer"
    assert "Timing / Quality" not in response["choices"][0]["message"]["content"]
    assert captured["model_id"] == "qwen-vl-8b"
    assert captured["payload"]["model"] == "qwen-vl-8b"


@pytest.mark.asyncio
async def test_openai_chat_completions_without_tools_streams_raw_chunks(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        raise AssertionError("execute_orchestration не должен вызываться для обычной raw-модели")

    async def fake_compat_stream(response: Dict[str, Any]):
        raise AssertionError("обычная raw-модель не должна склеиваться через compat stream")

    async def fake_open_stream(*, target_model: str, payload: Dict[str, Any]):
        captured["model_id"] = target_model
        captured["payload"] = payload
        return object()

    async def fake_stream(_response):
        yield "data: {\"choices\":[{\"delta\":{\"content\":\"Пр\"},\"finish_reason\":null}]}\n\n".encode("utf-8")
        yield "data: {\"choices\":[{\"delta\":{\"content\":\"ивет\"},\"finish_reason\":null}]}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_stream_openai_compat_response", fake_compat_stream)
    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
    monkeypatch.setattr(agent_api, "_open_raw_openai_stream", fake_open_stream)
    monkeypatch.setattr(agent_api, "_proxy_raw_openai_stream", fake_stream)
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "qwen-14b-llm",
                "messages": [{"role": "user", "content": "Привет"}],
            }
        )
    )
    body = await _read_streaming_body(response)

    assert isinstance(response, StreamingResponse)
    assert captured["model_id"] == "qwen-14b-llm"
    assert captured["payload"]["model"] == "qwen-14b-llm"
    assert "\"content\":\"Пр\"" in body
    assert "\"content\":\"ивет\"" in body
    assert "Timing / Quality" not in body
    assert "data: [DONE]" in body


def test_list_models_uses_registry_catalog_only(monkeypatch):
    monkeypatch.setattr(
        agent_api,
        "_list_raw_chat_capable_models",
        lambda: [_raw_model_item()],
    )

    response = agent_api.list_models()

    assert response["object"] == "list"
    assert [item["id"] for item in response["data"]] == ["qwen-14b-llm"]


def test_raw_models_filters_ums_catalog_to_ready_chat_models(monkeypatch):
    monkeypatch.setattr(
        agent_api,
        "_load_ums_model_catalog",
        lambda: {
            "qwen-14b-llm": {"model_id": "qwen-14b-llm", "kind": "llm", "user_selectable": True, "status": "ready"},
            "qwen-vl-8b": {"model_id": "qwen-vl-8b", "kind": "vision", "user_selectable": True, "status": "ready"},
            "llama-3.1-8b": {"model_id": "llama-3.1-8b", "kind": "llm", "user_selectable": True, "status": "incomplete"},
            "qwen-72b-llm": {"model_id": "qwen-72b-llm", "kind": "llm", "user_selectable": True, "status": "unsupported"},
            "ambiguous-qwen": {"model_id": "ambiguous-qwen", "kind": "llm", "user_selectable": True, "status": "ambiguous"},
            "labse-embedding": {"model_id": "labse-embedding", "kind": "retrieval_embedder", "status": "ready"},
            "llm-tools-platform": {"model_id": "llm-tools-platform", "kind": "llm", "user_selectable": True, "status": "ready"},
        },
    )

    response = agent_api.list_raw_models()

    assert [item["id"] for item in response["data"]] == ["qwen-14b-llm", "qwen-vl-8b"]


def test_raw_models_local_fallback_is_conservative(tmp_path, monkeypatch):
    model_path = tmp_path / "qwen.gguf"
    model_path.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(agent_api, "_load_ums_model_catalog", lambda: None)
    monkeypatch.setattr(
        agent_api,
        "get_all_models",
        lambda: {
            "qwen-14b-llm": {
                "kind": "llm",
                "runtime_type": "gguf",
                "path": str(model_path),
                "port": 8091,
                "user_selectable": True,
            },
            "llama-3.1-8b": {
                "kind": "llm",
                "runtime_type": "gguf",
                "path": "",
                "port": 8096,
                "user_selectable": True,
            },
            "qwen-72b-llm": {
                "kind": "llm",
                "runtime_type": "dynamic",
                "path": str(model_path),
                "port": 8097,
                "user_selectable": True,
            },
        },
    )

    response = agent_api.list_raw_models()

    assert [item["id"] for item in response["data"]] == ["qwen-14b-llm"]


def test_resolve_raw_model_id_prefers_active_ums_model(monkeypatch):
    monkeypatch.setattr(
        agent_api,
        "_list_raw_chat_capable_models",
        lambda: [
            _raw_model_item("qwen-14b-llm"),
            _raw_model_item("qwen-vl-8b"),
        ],
    )
    monkeypatch.setattr(agent_api.ums_client, "get_status", lambda: {"active_model_id": "qwen-vl-8b"})

    assert agent_api._resolve_raw_model_id({"messages": [{"role": "user", "content": "Привет"}]}) == "qwen-vl-8b"


def test_resolve_native_tool_passthrough_model_rejects_incompatible_model(monkeypatch):
    monkeypatch.setattr(
        agent_api,
        "_list_raw_chat_capable_models",
        lambda: [_raw_model_item("qwen-14b-llm")],
    )
    monkeypatch.setattr(
        agent_api,
        "resolve_user_model_selection",
        lambda requested_model_id, required_capabilities=None: type(
            "Resolution",
            (),
            {
                "exists_in_registry": True,
                "resolved_model_id": requested_model_id,
                "required_capabilities": list(required_capabilities or []),
                "missing_capabilities": ["supports_tools"],
                "capabilities": {
                    "supports_tools": False,
                    "supports_vision": False,
                    "supports_structured_output": True,
                },
                "user_selectable": True,
                "compatible": False,
            },
        )(),
    )

    with pytest.raises(HTTPException) as exc_info:
        agent_api._resolve_native_tool_passthrough_model_id(
            {
                "model": "qwen-14b-llm",
                "messages": [{"role": "user", "content": "Привет"}],
                "tools": [{"type": "function", "function": {"name": "equipment_deep_tool"}}],
            }
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["status"] == "incompatible_model"
    assert exc_info.value.detail["missing_capabilities"] == ["supports_tools"]


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
                "model": "llm-tools-platform",
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
                "model": "llm-tools-platform",
                "messages": [{"role": "user", "content": "Привет"}],
                "stream": False,
            }
        )
    )

    assert not isinstance(response, StreamingResponse)
    assert response["choices"][0]["message"]["content"] == "non-stream answer"
    assert captured["request"]["message"] == "Привет"


@pytest.mark.asyncio
async def test_openai_chat_completions_native_tools_passthrough_non_streaming(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        raise AssertionError("execute_orchestration не должен вызываться для native tool passthrough")

    async def fake_request_raw_openai_infer(*, target_model: str, payload: Dict[str, Any]):
        captured["model_id"] = target_model
        captured["payload"] = payload
        return {
            "id": "chatcmpl-native-tools",
            "object": "chat.completion",
            "created": 123,
            "model": target_model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "type": "function",
                                "function": {
                                    "name": "equipment_deep_tool",
                                    "arguments": "{\"query\":\"test\"}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )
    monkeypatch.setattr(agent_api, "_request_raw_openai_infer", fake_request_raw_openai_infer)
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "qwen-14b-llm",
                "messages": [{"role": "user", "content": "Сделай глубокий анализ"}],
                "stream": False,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "equipment_deep_tool",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": "equipment_deep_tool"}},
            }
        )
    )

    assert captured["model_id"] == "qwen-14b-llm"
    assert captured["payload"]["model"] == "qwen-14b-llm"
    assert captured["payload"]["tools"][0]["function"]["name"] == "equipment_deep_tool"
    assert response["model"] == "qwen-14b-llm"
    assert response["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_openai_chat_completions_native_tools_legacy_model_label_resolves_to_raw_model(monkeypatch):
    async def fake_execute(request: Dict[str, Any], *, deps=None):
        raise AssertionError("execute_orchestration не должен вызываться для native tool passthrough")

    async def fake_request_raw_openai_infer(*, target_model: str, payload: Dict[str, Any]):
        return {
            "id": "chatcmpl-native-tools",
            "object": "chat.completion",
            "created": 123,
            "model": "llm-tools-platform",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "type": "function",
                                "function": {"name": "equipment_deep_tool", "arguments": "{}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )
    monkeypatch.setattr(agent_api, "_request_raw_openai_infer", fake_request_raw_openai_infer)

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "llm-tools-platform",
                "messages": [{"role": "user", "content": "Сделай глубокий анализ"}],
                "stream": False,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "equipment_deep_tool",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            }
        )
    )

    assert response["model"] == "qwen-14b-llm"
    assert response["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_openai_chat_completions_native_tools_passthrough_streaming(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        raise AssertionError("execute_orchestration не должен вызываться для native tool passthrough")

    async def fake_open_stream(*, target_model: str, payload: Dict[str, Any]):
        captured["model_id"] = target_model
        captured["payload"] = payload
        return object()

    async def fake_stream(_response):
        yield (
            "data: "
            + "{\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_123\",\"type\":\"function\",\"function\":{\"name\":\"equipment_deep_tool\",\"arguments\":\"{}\"}}]},\"finish_reason\":null}]}\n\n"
        ).encode("utf-8")
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )
    monkeypatch.setattr(agent_api, "_open_raw_openai_stream", fake_open_stream)
    monkeypatch.setattr(agent_api, "_proxy_raw_openai_stream", fake_stream)
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "qwen-14b-llm",
                "messages": [{"role": "user", "content": "Сделай глубокий анализ"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "equipment_deep_tool",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": "equipment_deep_tool"}},
            }
        )
    )
    body = await _read_streaming_body(response)

    assert isinstance(response, StreamingResponse)
    assert captured["model_id"] == "qwen-14b-llm"
    assert captured["payload"]["model"] == "qwen-14b-llm"
    assert "\"tool_calls\"" in body
    assert "equipment_deep_tool" in body
    assert "data: [DONE]" in body


@pytest.mark.asyncio
async def test_openai_chat_completions_tool_result_followup_uses_raw_model(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        raise AssertionError("execute_orchestration не должен вызываться для tool result follow-up")

    async def fake_request_raw_openai_infer(*, target_model: str, payload: Dict[str, Any]):
        captured["model_id"] = target_model
        captured["payload"] = payload
        return {
            "id": "chatcmpl-tool-followup",
            "object": "chat.completion",
            "created": 123,
            "model": target_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Итог по инструменту"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
    monkeypatch.setattr(
        agent_api,
        "resolve_model_selection",
        lambda role_key: type("Selection", (), {"resolved_model_id": "qwen-14b-llm"})(),
    )
    monkeypatch.setattr(agent_api, "_request_raw_openai_infer", fake_request_raw_openai_infer)

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "llm-tools-platform",
                "stream": False,
                "messages": [
                    {"role": "user", "content": "Сделай анализ"},
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "type": "function",
                                "function": {"name": "equipment_deep_tool", "arguments": "{}"},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_123", "content": "tool result"},
                ],
            }
        )
    )

    assert captured["model_id"] == "qwen-14b-llm"
    assert captured["payload"]["model"] == "qwen-14b-llm"
    assert response["model"] == "qwen-14b-llm"
    assert response["choices"][0]["message"]["content"] == "Итог по инструменту"


@pytest.mark.asyncio
async def test_openai_chat_completions_plain_compat_does_not_discover_or_load_session_docs(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        captured["request"] = request
        return {
            "assistant_message": "plain answer",
            "trace_id": "trace-plain-no-rag",
            "sources": [],
            "effective_settings": request["effective_settings"],
            "rag_scope": "off",
            "knowledge_collection_id": None,
            "source_scope_summary": "off",
        }

    async def fake_load_session_docs(attachments):
        raise AssertionError("plain OpenAI-compatible chat не должен загружать session docs")

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(
        agent_api,
        "_discover_openai_attachments",
        lambda user_query: (_ for _ in ()).throw(
            AssertionError("plain OpenAI-compatible chat не должен искать вложения")
        ),
    )
    monkeypatch.setattr(agent_api, "_load_openai_session_docs", fake_load_session_docs, raising=False)
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "llm-tools-platform",
                "messages": [{"role": "user", "content": "Привет"}],
                "stream": False,
            }
        )
    )

    assert response["choices"][0]["message"]["content"] == "plain answer"
    assert captured["request"]["has_session_docs"] is False
    assert captured["request"]["session_docs"] == {}
    assert captured["request"]["attachments_meta"] == []
    assert captured["request"]["active_doc_ids"] == []
    assert captured["request"]["effective_settings"]["rag_scope"] == "off"
    assert "rag_scope" not in captured["request"]


@pytest.mark.asyncio
async def test_openai_chat_completions_ignores_forwarded_openwebui_files_in_plain_compat_chat(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(request: Dict[str, Any], *, deps=None):
        captured["request"] = request
        return {
            "assistant_message": "plain forwarded answer",
            "trace_id": "trace-forwarded-no-rag",
            "sources": [],
            "effective_settings": request["effective_settings"],
            "rag_scope": "off",
            "knowledge_collection_id": None,
            "source_scope_summary": "off",
        }

    def fake_discover_openai_attachments(user_query: str):
        raise AssertionError("plain OpenAI-compatible chat не должен искать вложения")

    async def fake_load_session_docs(_attachments):
        raise AssertionError("plain OpenAI-compatible chat не должен загружать session docs")

    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_discover_openai_attachments", fake_discover_openai_attachments)
    monkeypatch.setattr(agent_api, "_load_openai_session_docs", fake_load_session_docs, raising=False)
    agent_api._active_workflows.clear()

    response = await agent_api.openai_completions(
        _FakeRequest(
            {
                "model": "llm-tools-platform",
                "thread_id": "chat-123",
                "openwebui_session_rag_handoff": {"mode": "preferred", "enabled": True},
                "files": [
                    {
                        "id": "file-contract-1",
                        "name": "contract.pdf",
                        "type": "text",
                        "content": "Штраф составляет 10 процентов.",
                        "file": {
                            "meta": {"content_type": "application/pdf"},
                            "data": {
                                "content": "Штраф составляет 10 процентов.",
                                "metadata": {"source": "openwebui-upload"},
                            },
                        },
                    }
                ],
                "messages": [{"role": "user", "content": "Сделай сводку по прикреплённому документу"}],
                "stream": False,
            }
        )
    )

    assert response["choices"][0]["message"]["content"] == "plain forwarded answer"
    assert "thread_id" not in captured["request"]
    assert "document_bindings" not in captured["request"]
    assert "rag_scope" not in captured["request"]
    assert captured["request"]["has_session_docs"] is False
    assert captured["request"]["active_doc_ids"] == []
    assert captured["request"]["attachments_meta"] == []
    assert captured["request"]["session_docs"] == {}
    assert captured["request"]["effective_settings"]["rag_scope"] == "off"


def test_raw_models_lists_chat_capable_models_without_agent_wrapper(monkeypatch):
    monkeypatch.setattr(
        agent_api,
        "_load_ums_model_catalog",
        lambda: {
            "qwen-14b-llm": {"model_id": "qwen-14b-llm", "kind": "llm", "status": "ready"},
            "qwen-vl-8b": {"model_id": "qwen-vl-8b", "kind": "vision", "status": "ready"},
            "labse-embedding": {"model_id": "labse-embedding", "kind": "retrieval_embedder", "status": "ready"},
        },
    )

    response = agent_api.list_raw_models()

    assert response["object"] == "list"
    assert [item["id"] for item in response["data"]] == ["qwen-14b-llm", "qwen-vl-8b"]
    assert all(item["id"] != "llm-tools-platform" for item in response["data"])


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

    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
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

    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
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

    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
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

    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
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

    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
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

    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
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
    monkeypatch.setattr(agent_api, "_list_raw_chat_capable_models", lambda: [_raw_model_item("qwen-14b-llm")])
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
