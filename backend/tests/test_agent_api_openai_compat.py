import os
import sys
from typing import Any, Dict

import pytest
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
