import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from orchestrator.tool_bindings import build_openwebui_binding_export


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _load_action_namespace(python_code: str) -> dict:
    namespace: dict = {}
    exec(python_code, namespace)
    return namespace


def _build_body_with_last_user_message(text: str) -> dict:
    return {
        "messages": [
            {
                "role": "user",
                "content": text,
            }
        ]
    }


@pytest.mark.asyncio
async def test_equipment_deep_action_returns_structured_job_context(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    action_payload = next(item for item in export["actionFunctions"] if item["action_id"] == "equipment_deep_action")
    namespace = _load_action_namespace(action_payload["pythonCode"])
    action = namespace["Action"]()

    def fake_urlopen(request, timeout=45):
        assert request.full_url.endswith("/tools/analyze_equipment_deep")
        return _FakeHTTPResponse(
            {
                "status": "accepted",
                "job_id": "job-42",
                "status_url": "/tool-server/tool-jobs/job-42",
            }
        )

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await action.action(_build_body_with_last_user_message("Проверь насос НП-100"))

    assert response["job_id"] == "job-42"
    assert response["status_url"] == "/tool-server/tool-jobs/job-42"
    assert response["tool_job"]["job_id"] == "job-42"
    assert response["tool_job"]["status_url"] == "/tool-server/tool-jobs/job-42"
    assert "job_id: job-42" in response["content"]


@pytest.mark.asyncio
async def test_refresh_and_cancel_actions_restore_context_from_previous_deep_action(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}

    deep_namespace = _load_action_namespace(actions["equipment_deep_action"]["pythonCode"])
    refresh_namespace = _load_action_namespace(actions["tool_job_refresh_action"]["pythonCode"])
    cancel_namespace = _load_action_namespace(actions["tool_job_cancel_action"]["pythonCode"])

    deep_action = deep_namespace["Action"]()
    refresh_action = refresh_namespace["Action"]()
    cancel_action = cancel_namespace["Action"]()

    def fake_deep_urlopen(request, timeout=45):
        return _FakeHTTPResponse(
            {
                "status": "accepted",
                "job_id": "job-77",
                "status_url": "/tool-server/tool-jobs/job-77",
            }
        )

    refresh_calls = []

    def fake_refresh_urlopen(request, timeout=45):
        refresh_calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/tool-server/tool-jobs/job-77"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-77",
                    "status": "completed",
                }
            )
        return _FakeHTTPResponse(
            {
                "assistant_message": "Deep job completed successfully.",
            }
        )

    cancel_calls = []

    def fake_cancel_urlopen(request, timeout=45):
        cancel_calls.append((request.get_method(), request.full_url))
        return _FakeHTTPResponse(
            {
                "job_id": "job-77",
                "status": "cancelling",
            }
        )

    monkeypatch.setattr(deep_namespace["urllib"].request, "urlopen", fake_deep_urlopen)
    deep_response = await deep_action.action(_build_body_with_last_user_message("Проверь компрессор КМ-42"))

    nested_body = {
        "previous_action_result": deep_response,
        "messages": [
            {
                "role": "assistant",
                "content": deep_response["content"],
            }
        ],
    }

    monkeypatch.setattr(refresh_namespace["urllib"].request, "urlopen", fake_refresh_urlopen)
    refresh_response = await refresh_action.action(nested_body)

    assert refresh_response["content"] == "Deep job completed successfully."
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-77") in refresh_calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-77/result") in refresh_calls

    async def fake_event_call(payload):
        assert payload["type"] == "confirm"
        return True

    monkeypatch.setattr(cancel_namespace["urllib"].request, "urlopen", fake_cancel_urlopen)
    cancel_response = await cancel_action.action(nested_body, __event_call__=fake_event_call)

    assert "job_id: job-77" in cancel_response["content"]
    assert ("POST", "http://host.docker.internal:18000/tool-server/tool-jobs/job-77/cancel") in cancel_calls


@pytest.mark.asyncio
async def test_refresh_and_cancel_actions_restore_context_from_persisted_status_history(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}

    deep_namespace = _load_action_namespace(actions["equipment_deep_action"]["pythonCode"])
    refresh_namespace = _load_action_namespace(actions["tool_job_refresh_action"]["pythonCode"])
    cancel_namespace = _load_action_namespace(actions["tool_job_cancel_action"]["pythonCode"])

    deep_action = deep_namespace["Action"]()
    refresh_action = refresh_namespace["Action"]()
    cancel_action = cancel_namespace["Action"]()

    emitted_events = []

    async def fake_event_emitter(payload):
        emitted_events.append(payload)

    def fake_deep_urlopen(request, timeout=45):
        return _FakeHTTPResponse(
            {
                "status": "accepted",
                "job_id": "job-88",
                "status_url": "/tool-server/tool-jobs/job-88",
            }
        )

    refresh_calls = []

    def fake_refresh_urlopen(request, timeout=45):
        refresh_calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/tool-server/tool-jobs/job-88"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-88",
                    "status": "completed",
                }
            )
        return _FakeHTTPResponse(
            {
                "assistant_message": "Deep job completed from persisted status history.",
            }
        )

    cancel_calls = []

    def fake_cancel_urlopen(request, timeout=45):
        cancel_calls.append((request.get_method(), request.full_url))
        return _FakeHTTPResponse(
            {
                "job_id": "job-88",
                "status": "cancelling",
            }
        )

    monkeypatch.setattr(deep_namespace["urllib"].request, "urlopen", fake_deep_urlopen)
    await deep_action.action(
        _build_body_with_last_user_message("Проверь компрессор КМ-88"),
        __event_emitter__=fake_event_emitter,
    )

    status_events = [
        payload["data"]
        for payload in emitted_events
        if payload.get("type") == "status" and isinstance(payload.get("data"), dict)
    ]

    assert status_events == [
        {
            "description": (
                "`Глубокий анализ оборудования` принят как deep-job.\n"
                "job_id: job-88\n"
                "status_url: /tool-server/tool-jobs/job-88"
            ),
            "status": "accepted",
            "job_id": "job-88",
            "status_url": "/tool-server/tool-jobs/job-88",
            "tool_name": "analyze_equipment_deep",
        }
    ]

    persisted_body = {
        "messages": [
            {
                "role": "assistant",
                "content": "deep action placeholder",
                "statusHistory": status_events,
            }
        ]
    }

    monkeypatch.setattr(refresh_namespace["urllib"].request, "urlopen", fake_refresh_urlopen)
    refresh_response = await refresh_action.action(persisted_body)

    assert refresh_response["content"] == "Deep job completed from persisted status history."
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-88") in refresh_calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-88/result") in refresh_calls

    async def fake_event_call(payload):
        assert payload["type"] == "confirm"
        return True

    monkeypatch.setattr(cancel_namespace["urllib"].request, "urlopen", fake_cancel_urlopen)
    cancel_response = await cancel_action.action(persisted_body, __event_call__=fake_event_call)

    assert "job_id: job-88" in cancel_response["content"]
    assert ("POST", "http://host.docker.internal:18000/tool-server/tool-jobs/job-88/cancel") in cancel_calls


@pytest.mark.asyncio
async def test_refresh_and_cancel_actions_restore_context_from_live_chat_lookup(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}

    refresh_namespace = _load_action_namespace(actions["tool_job_refresh_action"]["pythonCode"])
    cancel_namespace = _load_action_namespace(actions["tool_job_cancel_action"]["pythonCode"])

    refresh_action = refresh_namespace["Action"]()
    cancel_action = cancel_namespace["Action"]()

    assistant_message_id = "assistant-msg-42"
    structured_status = [
        {
            "description": (
                "`Глубокий анализ оборудования` принят как deep-job.\n"
                "job_id: job-98\n"
                "status_url: /tool-server/tool-jobs/job-98"
            ),
            "status": "accepted",
            "job_id": "job-98",
            "status_url": "/tool-server/tool-jobs/job-98",
            "tool_name": "analyze_equipment_deep",
        },
        {
            "description": (
                "`Глубокий анализ оборудования` принят как deep-job.\n"
                "job_id: job-99\n"
                "status_url: /tool-server/tool-jobs/job-99"
            ),
            "status": "accepted",
            "job_id": "job-99",
            "status_url": "/tool-server/tool-jobs/job-99",
            "tool_name": "analyze_equipment_deep",
        }
    ]
    fake_chat = SimpleNamespace(
        chat={
            "history": {
                "messages": {
                    assistant_message_id: {
                        "id": assistant_message_id,
                        "role": "assistant",
                        "content": "plain assistant content without job metadata",
                        "statusHistory": structured_status,
                    }
                }
            }
        }
    )

    open_webui_module = ModuleType("open_webui")
    open_webui_module.__path__ = []
    open_webui_models_module = ModuleType("open_webui.models")
    open_webui_models_module.__path__ = []
    open_webui_chats_module = ModuleType("open_webui.models.chats")
    open_webui_chats_module.Chats = SimpleNamespace(
        get_chat_by_id=lambda chat_id: fake_chat if chat_id == "chat-live-1" else None
    )
    open_webui_module.models = open_webui_models_module
    open_webui_models_module.chats = open_webui_chats_module
    monkeypatch.setitem(sys.modules, "open_webui", open_webui_module)
    monkeypatch.setitem(sys.modules, "open_webui.models", open_webui_models_module)
    monkeypatch.setitem(sys.modules, "open_webui.models.chats", open_webui_chats_module)

    refresh_calls = []

    def fake_refresh_urlopen(request, timeout=45):
        refresh_calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/tool-server/tool-jobs/job-99"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-99",
                    "status": "completed",
                }
            )
        return _FakeHTTPResponse(
            {
                "assistant_message": "Deep job completed from live chat lookup.",
            }
        )

    cancel_calls = []

    def fake_cancel_urlopen(request, timeout=45):
        cancel_calls.append((request.get_method(), request.full_url))
        return _FakeHTTPResponse(
            {
                "job_id": "job-99",
                "status": "cancelling",
            }
        )

    live_body = {
        "model": "raw.qwen-14b-llm",
        "messages": [
            {
                "id": "user-msg-1",
                "role": "user",
                "content": "Какие инструменты доступны для анализа оборудования?",
            },
            {
                "id": assistant_message_id,
                "role": "assistant",
                "content": "plain assistant content without job metadata",
            },
        ],
        "chat_id": "chat-live-1",
        "id": assistant_message_id,
    }

    monkeypatch.setattr(refresh_namespace["urllib"].request, "urlopen", fake_refresh_urlopen)
    refresh_response = await refresh_action.action(live_body)

    assert refresh_response["content"] == "Deep job completed from live chat lookup."
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-99") in refresh_calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-99/result") in refresh_calls

    async def fake_event_call(payload):
        assert payload["type"] == "confirm"
        return True

    monkeypatch.setattr(cancel_namespace["urllib"].request, "urlopen", fake_cancel_urlopen)
    cancel_response = await cancel_action.action(live_body, __event_call__=fake_event_call)

    assert "job_id: job-99" in cancel_response["content"]
    assert ("POST", "http://host.docker.internal:18000/tool-server/tool-jobs/job-99/cancel") in cancel_calls
