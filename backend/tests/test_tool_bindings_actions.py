import asyncio
import json
import io
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


def _install_fake_openwebui_modules(monkeypatch, *, chat_store: dict[str, dict] | None = None):
    store = chat_store or {}

    class _FakeChats:
        @staticmethod
        def get_chat_by_id(chat_id):
            chat_payload = store.get(chat_id)
            if chat_payload is None:
                return None
            return SimpleNamespace(chat=chat_payload)

        @staticmethod
        def get_message_by_id_and_message_id(chat_id, message_id):
            chat_payload = store.get(chat_id) or {}
            return (
                chat_payload.get("history", {})
                .get("messages", {})
                .get(message_id, {})
            )

        @staticmethod
        def upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, message):
            chat_payload = store.setdefault(chat_id, {"history": {"messages": {}, "currentId": None}})
            history = chat_payload.setdefault("history", {})
            messages = history.setdefault("messages", {})
            existing = messages.get(message_id, {})
            messages[message_id] = {**existing, **message}
            history["currentId"] = message_id
            return SimpleNamespace(chat=chat_payload)

        @staticmethod
        def add_message_status_to_chat_by_id_and_message_id(chat_id, message_id, status):
            chat_payload = store.setdefault(chat_id, {"history": {"messages": {}, "currentId": None}})
            history = chat_payload.setdefault("history", {})
            messages = history.setdefault("messages", {})
            message = messages.setdefault(message_id, {"id": message_id})
            status_history = list(message.get("statusHistory", []))
            status_history.append(status)
            message["statusHistory"] = status_history
            return SimpleNamespace(chat=chat_payload)

    open_webui_module = ModuleType("open_webui")
    open_webui_module.__path__ = []
    open_webui_models_module = ModuleType("open_webui.models")
    open_webui_models_module.__path__ = []
    open_webui_chats_module = ModuleType("open_webui.models.chats")
    open_webui_chats_module.Chats = _FakeChats
    open_webui_module.models = open_webui_models_module
    open_webui_models_module.chats = open_webui_chats_module

    monkeypatch.setitem(sys.modules, "open_webui", open_webui_module)
    monkeypatch.setitem(sys.modules, "open_webui.models", open_webui_models_module)
    monkeypatch.setitem(sys.modules, "open_webui.models.chats", open_webui_chats_module)
    return store


@pytest.mark.asyncio
async def test_equipment_deep_workspace_tool_forwards_exact_prompt_as_equipment_query(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    tool_payload = next(item for item in export["workspaceTools"] if item["tool_id"] == "equipment_deep_tool")
    namespace = _load_action_namespace(tool_payload["pythonCode"])
    tools = namespace["Tools"]()

    captured: dict[str, dict] = {}

    def fake_urlopen(request, timeout=45):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "status": "accepted",
                "job_id": "job-last-user-1",
                "status_url": "/tool-server/tool-jobs/job-last-user-1",
            }
        )

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await action.action(
        {
            "messages": [
                {"role": "user", "content": "Первый общий запрос"},
                {"role": "assistant", "content": "Промежуточный ответ"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Сфокусируйся на процессорах и памяти."},
                        {"type": "text", "text": "Отдельно выдели обязательные формулировки."},
                    ],
                },
            ]
        }
    )

    assert captured["payload"]["equipment_query"] == (
        "Сфокусируйся на процессорах и памяти.\n"
        "Отдельно выдели обязательные формулировки."
    )
    assert response["job_id"] == "job-last-user-1"
    assert "status_url" not in response


@pytest.mark.asyncio
async def test_equipment_deep_workspace_tool_forwards_exact_prompt_as_equipment_query(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    tool_payload = next(item for item in export["workspaceTools"] if item["tool_id"] == "equipment_deep_tool")
    namespace = _load_action_namespace(tool_payload["pythonCode"])
    tools = namespace["Tools"]()

    captured: dict[str, dict] = {}

    def fake_urlopen(request, timeout=45):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "status": "accepted",
                "job_id": "job-tool-query-1",
                "status_url": "/tool-server/tool-jobs/job-tool-query-1",
            }
        )

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await tools.analyze_equipment_deep(
        "Сравни только диски и объём памяти для этой конфигурации."
    )

    assert captured["payload"] == {
        "equipment_query": "Сравни только диски и объём памяти для этой конфигурации.",
        "job_mode": "force_async",
    }
    assert response == (
        "Глубокий анализ оборудования принят как deep-job.\n"
        "Прогресс отображается в блоке `Deep job`."
    )


@pytest.mark.asyncio
async def test_equipment_deep_workspace_tool_routes_single_chat_file_to_document_deep(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    tool_payload = next(item for item in export["workspaceTools"] if item["tool_id"] == "equipment_deep_tool")
    namespace = _load_action_namespace(tool_payload["pythonCode"])
    tools = namespace["Tools"]()
    chat_store = {
        "chat-doc-deep-1": {
            "history": {
                "messages": {
                    "user-doc-1": {
                        "id": "user-doc-1",
                        "role": "user",
                        "timestamp": 100,
                        "content": "Сфокусируйся на процессорах и памяти.",
                        "files": [
                            {
                                "id": "file-req-1",
                                "name": "Requirements.pdf",
                                "file": {
                                    "id": "file-req-1",
                                    "filename": "Requirements.pdf",
                                    "path": "/app/backend/data/uploads/req.pdf",
                                    "meta": {"name": "Requirements.pdf"},
                                },
                            }
                        ],
                    }
                }
            }
        }
    }
    _install_fake_openwebui_modules(monkeypatch, chat_store=chat_store)

    captured: dict[str, dict] = {}

    def fake_urlopen(request, timeout=45):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "status": "accepted",
                "job_id": "job-doc-from-workspace-1",
                "status_url": "/tool-server/tool-jobs/job-doc-from-workspace-1",
            }
        )

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await tools.analyze_equipment_deep(
        "",
        __chat_id__="chat-doc-deep-1",
    )

    assert captured["url"].endswith("/tools/analyze_document_deep")
    assert captured["payload"]["analysis_goal"] == "Сфокусируйся на процессорах и памяти."
    assert captured["payload"]["document_refs"] == [
        {"label": "Requirements.pdf", "file_id": "file-req-1", "file_path": "/app/backend/data/uploads/req.pdf"}
    ]
    assert captured["payload"]["user_inputs"]["session_docs"]["Requirements.pdf"]["path"] == "/app/backend/data/uploads/req.pdf"
    assert "Глубокий анализ документа принят как deep-job." in response


@pytest.mark.asyncio
async def test_equipment_fast_workspace_tool_routes_two_chat_files_to_equipment_fast(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    tool_payload = next(item for item in export["workspaceTools"] if item["tool_id"] == "equipment_fast_tool")
    namespace = _load_action_namespace(tool_payload["pythonCode"])
    tools = namespace["Tools"]()
    chat_store = {
        "chat-pair-fast-1": {
            "history": {
                "messages": {
                    "user-pair-1": {
                        "id": "user-pair-1",
                        "role": "user",
                        "timestamp": 200,
                        "content": "Сравни требования и предложение по процессорам и памяти.",
                        "files": [
                            {
                                "id": "file-req-1",
                                "name": "Requirements.pdf",
                                "file": {
                                    "id": "file-req-1",
                                    "filename": "Requirements.pdf",
                                    "path": "/app/backend/data/uploads/req.pdf",
                                    "meta": {"name": "Requirements.pdf"},
                                },
                            },
                            {
                                "id": "file-quote-1",
                                "name": "Quotation_12.pdf",
                                "file": {
                                    "id": "file-quote-1",
                                    "filename": "Quotation_12.pdf",
                                    "path": "/app/backend/data/uploads/quote.pdf",
                                    "meta": {"name": "Quotation_12.pdf"},
                                },
                            },
                        ],
                    }
                }
            }
        }
    }
    _install_fake_openwebui_modules(monkeypatch, chat_store=chat_store)

    captured: dict[str, dict] = {}

    def fake_urlopen(request, timeout=45):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "status": "completed",
                "assistant_message": "Сравнение выполнено.",
            }
        )

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await tools.analyze_equipment_fast(
        "",
        __chat_id__="chat-pair-fast-1",
    )

    assert captured["url"].endswith("/tools/analyze_equipment_fast")
    assert captured["payload"]["equipment_query"] == "Сравни требования и предложение по процессорам и памяти."
    assert captured["payload"]["document_refs"] == [
        {"label": "Requirements.pdf", "file_id": "file-req-1", "file_path": "/app/backend/data/uploads/req.pdf"},
        {"label": "Quotation_12.pdf", "file_id": "file-quote-1", "file_path": "/app/backend/data/uploads/quote.pdf"},
    ]
    assert response == "Сравнение выполнено."


@pytest.mark.asyncio
async def test_equipment_deep_action_autopolls_completed_job_and_persists_result_message(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    action_payload = next(item for item in export["actionFunctions"] if item["action_id"] == "equipment_deep_action")
    namespace = _load_action_namespace(action_payload["pythonCode"])
    action = namespace["Action"]()
    namespace["TERMINAL_REAPPLY_DELAY_SECONDS"] = 0
    namespace["TERMINAL_REAPPLY_ATTEMPTS"] = 1

    accepted_message_id = "assistant-deep-accepted-1"
    chat_store = _install_fake_openwebui_modules(
        monkeypatch,
        chat_store={
            "chat-deep-1": {
                "history": {
                    "currentId": accepted_message_id,
                    "messages": {
                        accepted_message_id: {
                            "id": accepted_message_id,
                            "role": "assistant",
                            "content": "Исходный статус",
                            "done": True,
                            "output": [],
                            "childrenIds": [],
                            "model": "raw.qwen-14b-llm",
                        }
                    },
                }
            }
        },
    )

    calls = []

    def fake_urlopen(request, timeout=45):
        calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/tools/analyze_equipment_deep"):
            return _FakeHTTPResponse(
                {
                    "status": "accepted",
                    "job_id": "job-auto-1",
                    "status_url": "/tool-server/tool-jobs/job-auto-1",
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-auto-1"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-auto-1",
                    "status": "completed",
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-auto-1/result"):
            return _FakeHTTPResponse(
                {
                    "assistant_message": "Готовый итог deep-job.",
                    "sources": [{"name": "Requirements.pdf"}],
                }
            )
        raise AssertionError(request.full_url)

    emitted_events = []

    async def fake_event_emitter(payload):
        emitted_events.append(payload)

    fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_tools_platform_deep_job_pollers={})))
    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await action.action(
        {
            "chat_id": "chat-deep-1",
            "id": accepted_message_id,
            "model": "raw.qwen-14b-llm",
            "messages": [
                {
                    "role": "user",
                    "content": "Проверь сервер с двумя процессорами Xeon Silver.",
                }
            ],
        },
        __event_emitter__=fake_event_emitter,
        __request__=fake_request,
    )

    pollers = getattr(fake_request.app.state, "llm_tools_platform_deep_job_pollers", {})
    assert pollers
    poller_entry = next(iter(pollers.values()))
    poller_task = poller_entry["task"] if isinstance(poller_entry, dict) else poller_entry
    await asyncio.wait_for(poller_task, timeout=1)

    accepted_message = chat_store["chat-deep-1"]["history"]["messages"][accepted_message_id]
    result_message_id = accepted_message.get("result_message_id")
    assert response["job_id"] == "job-auto-1"
    assert accepted_message["job_status"] == "completed"
    assert accepted_message["actions_disabled"] is True
    assert accepted_message["tool_job"]["status"] == "completed"
    assert accepted_message["content"] == (
        "`Глубокий анализ оборудования` принят как deep-job.\n"
        "Прогресс отображается в блоке `Deep job`."
    )
    assert result_message_id

    result_message = chat_store["chat-deep-1"]["history"]["messages"][result_message_id]
    assert result_message["parentId"] == accepted_message_id
    assert result_message["tool_job_result_for"] == accepted_message_id
    assert result_message["job_id"] == "job-auto-1"
    assert result_message["content"] == "Готовый итог deep-job."
    assert result_message["sources"] == [{"name": "Requirements.pdf"}]
    assert accepted_message["childrenIds"] == [result_message_id]
    assert chat_store["chat-deep-1"]["history"]["currentId"] == result_message_id

    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-auto-1") in calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-auto-1/result") in calls
    assert ("POST", "http://host.docker.internal:18000/tool-server/tool-jobs/job-auto-1/delivery") in calls
    assert any(
        event["type"] == "replace"
        and event["data"]["content"]
        == (
            "`Глубокий анализ оборудования` принят как deep-job.\n"
            "Прогресс отображается в блоке `Deep job`."
        )
        for event in emitted_events
    )
    assert any(event["type"] == "chat:message:new" for event in emitted_events)
    assert any(
        event["type"] == "chat:message:meta"
        and event["data"]["job_status"] == "completed"
        and event["data"]["reload"] is True
        for event in emitted_events
    )


@pytest.mark.asyncio
async def test_equipment_deep_workspace_tool_autopolls_completed_job_and_persists_result_message(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    tool_payload = next(item for item in export["workspaceTools"] if item["tool_id"] == "equipment_deep_tool")
    namespace = _load_action_namespace(tool_payload["pythonCode"])
    tools = namespace["Tools"]()
    namespace["AUTO_POLL_INTERVAL_SECONDS"] = 0
    namespace["TERMINAL_REAPPLY_DELAY_SECONDS"] = 0
    namespace["TERMINAL_REAPPLY_ATTEMPTS"] = 1

    accepted_message_id = "assistant-deep-tool-accepted-1"
    chat_store = _install_fake_openwebui_modules(
        monkeypatch,
        chat_store={
            "chat-deep-tool-1": {
                "history": {
                    "currentId": accepted_message_id,
                    "messages": {
                        accepted_message_id: {
                            "id": accepted_message_id,
                            "role": "assistant",
                            "content": "Исходный accepted bubble",
                            "done": True,
                            "output": [],
                            "childrenIds": [],
                            "model": "raw.qwen-14b-llm",
                        }
                    },
                }
            }
        },
    )

    calls = []

    def fake_urlopen(request, timeout=45):
        calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/tools/analyze_equipment_deep"):
            return _FakeHTTPResponse(
                {
                    "status": "accepted",
                    "job_id": "job-tool-auto-1",
                    "status_url": "/tool-server/tool-jobs/job-tool-auto-1",
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-tool-auto-1"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-tool-auto-1",
                    "status": "completed",
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-tool-auto-1/result"):
            return _FakeHTTPResponse(
                {
                    "assistant_message": "Итог deep-job из Workspace Tool.",
                    "sources": [{"name": "Requirements.pdf"}],
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-tool-auto-1/delivery"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-tool-auto-1",
                    "status": "completed",
                    "result_message_id": "ignored-by-client",
                }
            )
        raise AssertionError(request.full_url)

    emitted_events = []

    async def fake_event_emitter(payload):
        emitted_events.append(payload)

    fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_tools_platform_deep_job_pollers={})))
    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await tools.analyze_equipment_deep(
        "Проверь сервер с двумя процессорами Xeon Silver.",
        __request__=fake_request,
        __event_emitter__=fake_event_emitter,
        __chat_id__="chat-deep-tool-1",
        __message_id__=accepted_message_id,
        __model__=SimpleNamespace(id="raw.qwen-14b-llm"),
    )

    pollers = getattr(fake_request.app.state, "llm_tools_platform_deep_job_pollers", {})
    assert pollers
    poller_entry = next(iter(pollers.values()))
    poller_task = poller_entry["task"] if isinstance(poller_entry, dict) else poller_entry
    await asyncio.wait_for(poller_task, timeout=1)

    accepted_message = chat_store["chat-deep-tool-1"]["history"]["messages"][accepted_message_id]
    result_message_id = accepted_message.get("result_message_id")
    assert response == (
        "Глубокий анализ оборудования принят как deep-job.\n"
        "Прогресс отображается в блоке `Deep job`."
    )
    assert accepted_message["job_status"] == "completed"
    assert accepted_message["actions_disabled"] is True
    assert accepted_message["tool_job"]["status"] == "completed"
    assert accepted_message["content"] == "Исходный accepted bubble"
    assert result_message_id

    result_message = chat_store["chat-deep-tool-1"]["history"]["messages"][result_message_id]
    assert result_message["parentId"] == accepted_message_id
    assert result_message["tool_job_result_for"] == accepted_message_id
    assert result_message["job_id"] == "job-tool-auto-1"
    assert result_message["content"] == "Итог deep-job из Workspace Tool."
    assert result_message["sources"] == [{"name": "Requirements.pdf"}]
    assert accepted_message["childrenIds"] == [result_message_id]
    assert chat_store["chat-deep-tool-1"]["history"]["currentId"] == result_message_id

    assert ("POST", "http://host.docker.internal:18000/tool-server/tools/analyze_equipment_deep") in calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-tool-auto-1") in calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-tool-auto-1/result") in calls
    assert ("POST", "http://host.docker.internal:18000/tool-server/tool-jobs/job-tool-auto-1/delivery") in calls
    assert any(
        event["type"] == "replace" and event["data"]["content"] == "Исходный accepted bubble"
        for event in emitted_events
    )
    assert any(event["type"] == "chat:message:new" for event in emitted_events)
    assert any(
        event["type"] == "chat:message:meta"
        and event["data"]["job_status"] == "completed"
        and event["data"]["reload"] is True
        for event in emitted_events
    )


@pytest.mark.asyncio
async def test_equipment_deep_workspace_tool_rejects_accepted_without_job_context(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    tool_payload = next(item for item in export["workspaceTools"] if item["tool_id"] == "equipment_deep_tool")
    namespace = _load_action_namespace(tool_payload["pythonCode"])
    tools = namespace["Tools"]()

    def fake_urlopen(request, timeout=45):
        assert request.full_url.endswith("/tools/analyze_equipment_deep")
        return _FakeHTTPResponse({"status": "accepted"})

    fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_tools_platform_deep_job_pollers={})))
    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await tools.analyze_equipment_deep(
        "Проверь сервер с двумя процессорами Xeon Gold.",
        __request__=fake_request,
        __chat_id__="chat-deep-tool-invalid-accepted",
        __message_id__="assistant-deep-tool-invalid-accepted",
        __model__=SimpleNamespace(id="raw.qwen-14b-llm"),
    )

    assert "Не удалось запустить deep-job" in response
    assert "`job_id`" in response
    assert "`status_url`" in response
    assert not getattr(fake_request.app.state, "llm_tools_platform_deep_job_pollers", {})


@pytest.mark.asyncio
async def test_equipment_deep_workspace_tool_failed_job_emits_reload_meta_and_persists_terminal_state(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    tool_payload = next(item for item in export["workspaceTools"] if item["tool_id"] == "equipment_deep_tool")
    namespace = _load_action_namespace(tool_payload["pythonCode"])
    tools = namespace["Tools"]()
    namespace["AUTO_POLL_INTERVAL_SECONDS"] = 0
    namespace["TERMINAL_REAPPLY_DELAY_SECONDS"] = 0
    namespace["TERMINAL_REAPPLY_ATTEMPTS"] = 1

    accepted_message_id = "assistant-deep-tool-failed-1"
    chat_store = _install_fake_openwebui_modules(
        monkeypatch,
        chat_store={
            "chat-deep-tool-failed-1": {
                "history": {
                    "currentId": accepted_message_id,
                    "messages": {
                        accepted_message_id: {
                            "id": accepted_message_id,
                            "role": "assistant",
                            "content": "Исходный accepted bubble",
                            "done": True,
                            "output": [],
                            "childrenIds": [],
                            "model": "raw.qwen-14b-llm",
                        }
                    },
                }
            }
        },
    )

    calls = []

    def fake_urlopen(request, timeout=45):
        calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/tools/analyze_equipment_deep"):
            return _FakeHTTPResponse(
                {
                    "status": "accepted",
                    "job_id": "job-tool-failed-1",
                    "status_url": "/tool-server/tool-jobs/job-tool-failed-1",
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-tool-failed-1"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-tool-failed-1",
                    "status": "failed",
                    "error_summary": "Модель занята предыдущим тяжёлым запросом.",
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-tool-failed-1/result"):
            return _FakeHTTPResponse(
                {
                    "assistant_message": "Backend terminal failed result.\nerror: Модель занята предыдущим тяжёлым запросом.",
                    "execution_metadata": {"status": "failed", "reason": "Модель занята предыдущим тяжёлым запросом."},
                }
            )
        raise AssertionError(request.full_url)

    emitted_events = []

    async def fake_event_emitter(payload):
        emitted_events.append(payload)

    fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_tools_platform_deep_job_pollers={})))
    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await tools.analyze_equipment_deep(
        "Проверь сервер с двумя процессорами EPYC.",
        __request__=fake_request,
        __event_emitter__=fake_event_emitter,
        __chat_id__="chat-deep-tool-failed-1",
        __message_id__=accepted_message_id,
        __model__=SimpleNamespace(id="raw.qwen-14b-llm"),
    )

    pollers = getattr(fake_request.app.state, "llm_tools_platform_deep_job_pollers", {})
    assert pollers
    poller_entry = next(iter(pollers.values()))
    poller_task = poller_entry["task"] if isinstance(poller_entry, dict) else poller_entry
    await asyncio.wait_for(poller_task, timeout=1)

    accepted_message = chat_store["chat-deep-tool-failed-1"]["history"]["messages"][accepted_message_id]
    assert response == (
        "Глубокий анализ оборудования принят как deep-job.\n"
        "Прогресс отображается в блоке `Deep job`."
    )
    assert accepted_message["job_status"] == "failed"
    assert accepted_message["actions_disabled"] is True
    assert accepted_message["tool_job"]["status"] == "failed"
    result_message_id = accepted_message.get("result_message_id")
    assert result_message_id
    assert accepted_message["content"] == "Исходный accepted bubble"
    assert accepted_message["childrenIds"] == [result_message_id]

    result_message = chat_store["chat-deep-tool-failed-1"]["history"]["messages"][result_message_id]
    assert result_message["parentId"] == accepted_message_id
    assert result_message["tool_job_result_for"] == accepted_message_id
    assert result_message["job_id"] == "job-tool-failed-1"
    assert "Backend terminal failed result." in result_message["content"]
    assert "Модель занята предыдущим тяжёлым запросом." in result_message["content"]

    assert ("POST", "http://host.docker.internal:18000/tool-server/tools/analyze_equipment_deep") in calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-tool-failed-1") in calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-tool-failed-1/result") in calls
    assert any(event["type"] == "chat:message:new" for event in emitted_events)
    assert any(
        event["type"] == "chat:message:meta"
        and event["data"]["job_status"] == "failed"
        and event["data"]["result_message_id"] == result_message_id
        and event["data"]["reload"] is True
        for event in emitted_events
    )


@pytest.mark.asyncio
async def test_equipment_deep_workspace_tool_failed_job_waits_for_openwebui_message_settle(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    tool_payload = next(item for item in export["workspaceTools"] if item["tool_id"] == "equipment_deep_tool")
    namespace = _load_action_namespace(tool_payload["pythonCode"])
    tools = namespace["Tools"]()
    namespace["AUTO_POLL_INTERVAL_SECONDS"] = 0
    namespace["MESSAGE_SETTLE_POLL_SECONDS"] = 0.01
    namespace["MESSAGE_SETTLE_TIMEOUT_SECONDS"] = 0.3
    namespace["TERMINAL_REAPPLY_DELAY_SECONDS"] = 0.05
    namespace["TERMINAL_REAPPLY_ATTEMPTS"] = 2

    accepted_message_id = "assistant-deep-tool-settle-1"
    chat_store = _install_fake_openwebui_modules(
        monkeypatch,
        chat_store={
            "chat-deep-tool-settle-1": {
                "history": {
                    "currentId": accepted_message_id,
                    "messages": {
                        accepted_message_id: {
                            "id": accepted_message_id,
                            "role": "assistant",
                            "content": "Черновик accepted bubble",
                            "done": True,
                            "childrenIds": [],
                            "model": "raw.qwen-14b-llm",
                        }
                    },
                }
            }
        },
    )

    def fake_urlopen(request, timeout=45):
        if request.full_url.endswith("/tools/analyze_equipment_deep"):
            return _FakeHTTPResponse(
                {
                    "status": "accepted",
                    "job_id": "job-tool-settle-1",
                    "status_url": "/tool-server/tool-jobs/job-tool-settle-1",
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-tool-settle-1"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-tool-settle-1",
                    "status": "failed",
                    "error_summary": "Модель занята предыдущим тяжёлым запросом.",
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-tool-settle-1/result"):
            return _FakeHTTPResponse(
                {
                    "assistant_message": "Backend terminal failed result.\nerror: Модель занята предыдущим тяжёлым запросом.",
                    "execution_metadata": {"status": "failed", "reason": "Модель занята предыдущим тяжёлым запросом."},
                }
            )
        raise AssertionError(request.full_url)

    emitted_events = []

    async def fake_event_emitter(payload):
        emitted_events.append(payload)

    fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_tools_platform_deep_job_pollers={})))
    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await tools.analyze_equipment_deep(
        "Проверь сервер с быстрыми NVMe и высокой плотностью памяти.",
        __request__=fake_request,
        __event_emitter__=fake_event_emitter,
        __chat_id__="chat-deep-tool-settle-1",
        __message_id__=accepted_message_id,
        __model__=SimpleNamespace(id="raw.qwen-14b-llm"),
    )

    async def late_openwebui_persist():
        await asyncio.sleep(0.03)
        accepted_message = chat_store["chat-deep-tool-settle-1"]["history"]["messages"][accepted_message_id]
        accepted_message.update(
            {
                "content": "Старый accepted из Open WebUI",
                "output": [{"type": "message", "status": "completed"}],
                "childrenIds": [],
                "result_message_id": None,
            }
        )

    overwrite_task = asyncio.create_task(late_openwebui_persist())
    pollers = getattr(fake_request.app.state, "llm_tools_platform_deep_job_pollers", {})
    assert pollers
    poller_entry = next(iter(pollers.values()))
    poller_task = poller_entry["task"] if isinstance(poller_entry, dict) else poller_entry
    await asyncio.wait_for(poller_task, timeout=1)
    await asyncio.wait_for(overwrite_task, timeout=1)

    accepted_message = chat_store["chat-deep-tool-settle-1"]["history"]["messages"][accepted_message_id]
    result_message_id = accepted_message.get("result_message_id")

    assert response == (
        "Глубокий анализ оборудования принят как deep-job.\n"
        "Прогресс отображается в блоке `Deep job`."
    )
    assert accepted_message["job_status"] == "failed"
    assert accepted_message["actions_disabled"] is True
    assert accepted_message["tool_job"]["status"] == "failed"
    assert result_message_id
    assert accepted_message["content"] == "Старый accepted из Open WebUI"
    assert accepted_message["childrenIds"] == [result_message_id]
    assert chat_store["chat-deep-tool-settle-1"]["history"]["currentId"] == result_message_id

    result_message = chat_store["chat-deep-tool-settle-1"]["history"]["messages"][result_message_id]
    assert result_message["parentId"] == accepted_message_id
    assert result_message["tool_job_result_for"] == accepted_message_id
    assert "Backend terminal failed result." in result_message["content"]
    assert any(event["type"] == "chat:message:new" for event in emitted_events)


@pytest.mark.asyncio
async def test_equipment_deep_workspace_tool_resolves_visible_accepted_bubble_from_job_context(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    tool_payload = next(item for item in export["workspaceTools"] if item["tool_id"] == "equipment_deep_tool")
    namespace = _load_action_namespace(tool_payload["pythonCode"])
    tools = namespace["Tools"]()
    namespace["AUTO_POLL_INTERVAL_SECONDS"] = 0
    namespace["TERMINAL_REAPPLY_DELAY_SECONDS"] = 0
    namespace["TERMINAL_REAPPLY_ATTEMPTS"] = 1

    stale_message_id = "assistant-deep-tool-stale-1"
    visible_message_id = "assistant-deep-tool-visible-1"
    chat_store = _install_fake_openwebui_modules(
        monkeypatch,
        chat_store={
            "chat-deep-tool-resolve-1": {
                "history": {
                    "currentId": visible_message_id,
                    "messages": {
                        stale_message_id: {
                            "id": stale_message_id,
                            "role": "assistant",
                            "content": "Внутренний bubble Open WebUI.",
                            "done": True,
                            "output": [],
                            "childrenIds": [],
                            "model": "raw.qwen-14b-llm",
                        },
                        visible_message_id: {
                            "id": visible_message_id,
                            "role": "assistant",
                            "content": (
                                "Глубокий анализ принят как deep-job.\n"
                                "Прогресс отображается в блоке `Deep job`."
                            ),
                            "job_id": "job-tool-resolve-1",
                            "tool_job": {
                                "job_id": "job-tool-resolve-1",
                                "status_url": "/tool-server/tool-jobs/job-tool-resolve-1",
                                "tool_name": "analyze_equipment_deep",
                                "status": "accepted",
                            },
                            "done": True,
                            "output": [],
                            "childrenIds": [],
                            "model": "raw.qwen-14b-llm",
                        },
                    },
                }
            }
        },
    )

    def fake_urlopen(request, timeout=45):
        if request.full_url.endswith("/tools/analyze_equipment_deep"):
            return _FakeHTTPResponse(
                {
                    "status": "accepted",
                    "job_id": "job-tool-resolve-1",
                    "status_url": "/tool-server/tool-jobs/job-tool-resolve-1",
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-tool-resolve-1"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-tool-resolve-1",
                    "status": "failed",
                    "error_summary": "Модель занята предыдущим тяжёлым запросом.",
                }
            )
        if request.full_url.endswith("/tool-server/tool-jobs/job-tool-resolve-1/result"):
            return _FakeHTTPResponse(
                {
                    "assistant_message": "Backend terminal failed result.\nerror: Модель занята предыдущим тяжёлым запросом.",
                    "execution_metadata": {"status": "failed", "reason": "Модель занята предыдущим тяжёлым запросом."},
                }
            )
        raise AssertionError(request.full_url)

    emitted_events = []

    async def fake_event_emitter(payload):
        emitted_events.append(payload)

    fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_tools_platform_deep_job_pollers={})))
    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await tools.analyze_equipment_deep(
        "Проверь сервер с большим объёмом памяти.",
        __request__=fake_request,
        __event_emitter__=fake_event_emitter,
        __chat_id__="chat-deep-tool-resolve-1",
        __message_id__=stale_message_id,
        __model__=SimpleNamespace(id="raw.qwen-14b-llm"),
    )

    pollers = getattr(fake_request.app.state, "llm_tools_platform_deep_job_pollers", {})
    assert pollers
    poller_entry = next(iter(pollers.values()))
    poller_task = poller_entry["task"] if isinstance(poller_entry, dict) else poller_entry
    await asyncio.wait_for(poller_task, timeout=1)

    stale_message = chat_store["chat-deep-tool-resolve-1"]["history"]["messages"][stale_message_id]
    visible_message = chat_store["chat-deep-tool-resolve-1"]["history"]["messages"][visible_message_id]
    result_message_id = visible_message.get("result_message_id")

    assert response == (
        "Глубокий анализ оборудования принят как deep-job.\n"
        "Прогресс отображается в блоке `Deep job`."
    )
    assert stale_message.get("job_status") is None
    assert stale_message.get("result_message_id") is None
    assert stale_message["childrenIds"] == []

    assert visible_message["job_status"] == "failed"
    assert visible_message["actions_disabled"] is True
    assert visible_message["tool_job"]["status"] == "failed"
    assert result_message_id
    assert visible_message["childrenIds"] == [result_message_id]

    result_message = chat_store["chat-deep-tool-resolve-1"]["history"]["messages"][result_message_id]
    assert result_message["parentId"] == visible_message_id
    assert result_message["tool_job_result_for"] == visible_message_id
    assert "Backend terminal failed result." in result_message["content"]
    assert any(
        event["type"] == "chat:message:new"
        and event["data"]["parent_message_id"] == visible_message_id
        for event in emitted_events
    )


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
    assert refresh_response["job_status"] == "completed"
    assert refresh_response["tool_job"]["status"] == "completed"
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-77") in refresh_calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-77/result") in refresh_calls

    async def fake_event_call(payload):
        assert payload["type"] == "confirm"
        return True

    monkeypatch.setattr(cancel_namespace["urllib"].request, "urlopen", fake_cancel_urlopen)
    cancel_response = await cancel_action.action(nested_body, __event_call__=fake_event_call)

    assert "job_id: job-77" in cancel_response["content"]
    assert cancel_response["job_status"] == "cancelling"
    assert cancel_response["tool_job"]["status"] == "cancelling"
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
                "Прогресс отображается в блоке `Deep job`."
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
    assert refresh_response["job_status"] == "completed"
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-88") in refresh_calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-88/result") in refresh_calls

    async def fake_event_call(payload):
        assert payload["type"] == "confirm"
        return True

    monkeypatch.setattr(cancel_namespace["urllib"].request, "urlopen", fake_cancel_urlopen)
    cancel_response = await cancel_action.action(persisted_body, __event_call__=fake_event_call)

    assert "job_id: job-88" in cancel_response["content"]
    assert cancel_response["job_status"] == "cancelling"
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
                "Прогресс отображается в блоке `Deep job`."
            ),
            "status": "accepted",
            "job_id": "job-98",
            "status_url": "/tool-server/tool-jobs/job-98",
            "tool_name": "analyze_equipment_deep",
        },
        {
            "description": (
                "`Глубокий анализ оборудования` принят как deep-job.\n"
                "Прогресс отображается в блоке `Deep job`."
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
    assert refresh_response["job_status"] == "completed"
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-99") in refresh_calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-99/result") in refresh_calls

    async def fake_event_call(payload):
        assert payload["type"] == "confirm"
        return True

    monkeypatch.setattr(cancel_namespace["urllib"].request, "urlopen", fake_cancel_urlopen)
    cancel_response = await cancel_action.action(live_body, __event_call__=fake_event_call)

    assert "job_id: job-99" in cancel_response["content"]
    assert cancel_response["job_status"] == "cancelling"
    assert ("POST", "http://host.docker.internal:18000/tool-server/tool-jobs/job-99/cancel") in cancel_calls


@pytest.mark.asyncio
async def test_refresh_action_fetches_terminal_failed_result_from_backend(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}
    refresh_namespace = _load_action_namespace(actions["tool_job_refresh_action"]["pythonCode"])
    refresh_action = refresh_namespace["Action"]()

    refresh_calls = []

    def fake_refresh_urlopen(request, timeout=45):
        refresh_calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/tool-server/tool-jobs/job-busy-1/result"):
            return _FakeHTTPResponse(
                {
                    "assistant_message": "Backend terminal failed result.\nerror: Модель занята предыдущим тяжёлым запросом.",
                    "execution_metadata": {"status": "failed", "reason": "Модель занята предыдущим тяжёлым запросом."},
                }
            )
        return _FakeHTTPResponse(
            {
                "job_id": "job-busy-1",
                "status": "failed",
                "current_stage": "busy",
                "error_summary": "Модель занята предыдущим тяжёлым запросом.",
            }
        )

    monkeypatch.setattr(refresh_namespace["urllib"].request, "urlopen", fake_refresh_urlopen)
    response = await refresh_action.action(
        {
            "tool_job": {
                "job_id": "job-busy-1",
                "status_url": "/tool-server/tool-jobs/job-busy-1",
                "tool_name": "analyze_equipment_deep",
                "status": "accepted",
            }
        }
    )

    assert response["job_status"] == "failed"
    assert "Backend terminal failed result." in response["content"]
    assert "error: Модель занята предыдущим тяжёлым запросом." in response["content"]
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-busy-1") in refresh_calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-busy-1/result") in refresh_calls


@pytest.mark.asyncio
async def test_refresh_action_returns_extended_running_status_payload(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}
    refresh_namespace = _load_action_namespace(actions["tool_job_refresh_action"]["pythonCode"])
    refresh_action = refresh_namespace["Action"]()

    def fake_refresh_urlopen(request, timeout=45):
        return _FakeHTTPResponse(
            {
                "job_id": "job-running-1",
                "status": "running",
                "current_stage": "stage:analysis",
                "status_text": "Собираем итоговый вывод.",
                "progress": {"phase": "stage:analysis", "fraction": 0.8},
                "status_history": [
                    {
                        "key": "stage:indexing",
                        "title": "Индексация",
                        "content": "Подготавливаем фрагменты.",
                    }
                ],
                "embeds": [{"kind": "status", "title": "Анализ"}],
                "sources": [{"source_id": "src-1", "title": "Requirements.pdf"}],
                "artifacts": [{"artifact_id": "artifact-1", "kind": "report"}],
            }
        )

    monkeypatch.setattr(refresh_namespace["urllib"].request, "urlopen", fake_refresh_urlopen)
    response = await refresh_action.action(
        {
            "tool_job": {
                "job_id": "job-running-1",
                "status_url": "/tool-server/tool-jobs/job-running-1",
                "tool_name": "analyze_equipment_deep",
                "status": "accepted",
            }
        }
    )

    assert response["job_status"] == "running"
    assert response["status_text"] == "Собираем итоговый вывод."
    assert response["progress"]["phase"] == "stage:analysis"
    assert response["status_history"][0]["key"] == "stage:indexing"
    assert response["embeds"][0]["kind"] == "status"
    assert response["sources"][0]["source_id"] == "src-1"
    assert response["artifacts"][0]["artifact_id"] == "artifact-1"


@pytest.mark.asyncio
async def test_refresh_action_persists_extended_running_status_payload(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}
    refresh_namespace = _load_action_namespace(actions["tool_job_refresh_action"]["pythonCode"])
    refresh_action = refresh_namespace["Action"]()
    assistant_message_id = "assistant-running-extended-1"
    chat_store = _install_fake_openwebui_modules(
        monkeypatch,
        chat_store={
            "chat-running-extended-1": {
                "history": {
                    "currentId": assistant_message_id,
                    "messages": {
                        assistant_message_id: {
                            "id": assistant_message_id,
                            "role": "assistant",
                            "content": "Глубокий анализ оборудования принят как deep-job.",
                            "done": True,
                            "childrenIds": [],
                        }
                    },
                }
            }
        },
    )

    def fake_refresh_urlopen(request, timeout=45):
        return _FakeHTTPResponse(
            {
                "job_id": "job-running-extended-1",
                "status": "running",
                "current_stage": "stage:analysis",
                "status_text": "Собираем итоговый вывод.",
                "progress": {"phase": "stage:analysis", "fraction": 0.8},
                "status_history": [
                    {
                        "key": "stage:indexing",
                        "title": "Индексация",
                        "content": "Подготавливаем фрагменты.",
                    }
                ],
                "embeds": [{"kind": "status", "title": "Анализ"}],
                "sources": [{"source_id": "src-1", "title": "Requirements.pdf"}],
                "artifacts": [{"artifact_id": "artifact-1", "kind": "report"}],
            }
        )

    monkeypatch.setattr(refresh_namespace["urllib"].request, "urlopen", fake_refresh_urlopen)
    response = await refresh_action.action(
        {
            "chat_id": "chat-running-extended-1",
            "id": assistant_message_id,
            "tool_job": {
                "job_id": "job-running-extended-1",
                "status_url": "/tool-server/tool-jobs/job-running-extended-1",
                "tool_name": "analyze_equipment_deep",
                "status": "accepted",
            },
        },
        __request__=SimpleNamespace(),
    )

    persisted_message = chat_store["chat-running-extended-1"]["history"]["messages"][assistant_message_id]
    assert response["job_status"] == "running"
    assert persisted_message["job_status"] == "running"
    assert persisted_message["status_text"] == "Собираем итоговый вывод."
    assert persisted_message["progress"]["phase"] == "stage:analysis"
    assert persisted_message["status_history"][0]["key"] == "stage:indexing"
    assert persisted_message["embeds"][0]["kind"] == "status"
    assert persisted_message["sources"][0]["source_id"] == "src-1"
    assert persisted_message["artifacts"][0]["artifact_id"] == "artifact-1"


@pytest.mark.asyncio
async def test_refresh_action_handles_completed_result_race_without_exception(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}
    refresh_namespace = _load_action_namespace(actions["tool_job_refresh_action"]["pythonCode"])
    refresh_action = refresh_namespace["Action"]()

    refresh_calls = []

    def fake_refresh_urlopen(request, timeout=45):
        refresh_calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/tool-server/tool-jobs/job-race-1"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-race-1",
                    "status": "completed",
                }
            )
        raise refresh_namespace["urllib"].error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            hdrs=None,
            fp=io.BytesIO(json.dumps({"detail": "job-not-ready:job-race-1"}).encode("utf-8")),
        )

    monkeypatch.setattr(refresh_namespace["urllib"].request, "urlopen", fake_refresh_urlopen)
    response = await refresh_action.action(
        {
            "tool_job": {
                "job_id": "job-race-1",
                "status_url": "/tool-server/tool-jobs/job-race-1",
                "tool_name": "analyze_equipment_deep",
                "status": "accepted",
            }
        }
    )

    assert response["job_status"] == "completed"
    assert "итог ещё не опубликован" in response["content"]
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-race-1") in refresh_calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-race-1/result") in refresh_calls


@pytest.mark.asyncio
async def test_refresh_action_records_terminal_delivery_when_result_message_is_created(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}
    refresh_namespace = _load_action_namespace(actions["tool_job_refresh_action"]["pythonCode"])
    refresh_action = refresh_namespace["Action"]()

    accepted_message_id = "assistant-refresh-create-1"
    chat_store = _install_fake_openwebui_modules(
        monkeypatch,
        chat_store={
            "chat-refresh-create-1": {
                "history": {
                    "currentId": accepted_message_id,
                    "messages": {
                        accepted_message_id: {
                            "id": accepted_message_id,
                            "role": "assistant",
                            "content": "Завершено — результат добавлен ниже.",
                            "done": True,
                            "childrenIds": [],
                            "tool_job": {
                                "job_id": "job-refresh-create-1",
                                "status_url": "/tool-server/tool-jobs/job-refresh-create-1",
                                "tool_name": "analyze_equipment_deep",
                                "status": "completed",
                            },
                            "job_status": "completed",
                            "actions_disabled": True,
                            "model": "raw.qwen-14b-llm",
                        },
                    },
                }
            }
        },
    )

    refresh_calls = []

    def fake_refresh_urlopen(request, timeout=45):
        refresh_calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/tool-server/tool-jobs/job-refresh-create-1/result"):
            return _FakeHTTPResponse({"assistant_message": "Новый terminal result."})
        if request.full_url.endswith("/tool-server/tool-jobs/job-refresh-create-1/delivery"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-refresh-create-1",
                    "status": "completed",
                    "result_message_id": "ignored-by-client",
                }
            )
        return _FakeHTTPResponse({"job_id": "job-refresh-create-1", "status": "completed"})

    monkeypatch.setattr(refresh_namespace["urllib"].request, "urlopen", fake_refresh_urlopen)
    response = await refresh_action.action(
        {
            "chat_id": "chat-refresh-create-1",
            "id": accepted_message_id,
            "model": "raw.qwen-14b-llm",
            "tool_job": {
                "job_id": "job-refresh-create-1",
                "status_url": "/tool-server/tool-jobs/job-refresh-create-1",
                "tool_name": "analyze_equipment_deep",
                "status": "completed",
            },
            "job_status": "completed",
        },
        __request__=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_tools_platform_deep_job_pollers={}))),
    )

    result_message_id = response["result_message_id"]
    assert response["job_status"] == "completed"
    assert result_message_id
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-refresh-create-1") in refresh_calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-refresh-create-1/result") in refresh_calls
    assert ("POST", "http://host.docker.internal:18000/tool-server/tool-jobs/job-refresh-create-1/delivery") in refresh_calls
    assert chat_store["chat-refresh-create-1"]["history"]["messages"][accepted_message_id]["result_message_id"] == result_message_id
    assert chat_store["chat-refresh-create-1"]["history"]["messages"][result_message_id]["content"] == "Новый terminal result."


@pytest.mark.asyncio
async def test_refresh_action_does_not_duplicate_result_message_when_it_is_already_persisted(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}
    refresh_namespace = _load_action_namespace(actions["tool_job_refresh_action"]["pythonCode"])
    refresh_action = refresh_namespace["Action"]()

    accepted_message_id = "assistant-refresh-accepted-1"
    result_message_id = "assistant-refresh-result-1"
    chat_store = _install_fake_openwebui_modules(
        monkeypatch,
        chat_store={
            "chat-refresh-1": {
                "history": {
                    "currentId": result_message_id,
                    "messages": {
                        accepted_message_id: {
                            "id": accepted_message_id,
                            "role": "assistant",
                            "content": "Завершено — результат добавлен ниже.",
                            "done": True,
                            "childrenIds": [result_message_id],
                            "tool_job": {
                                "job_id": "job-refresh-1",
                                "status_url": "/tool-server/tool-jobs/job-refresh-1",
                                "tool_name": "analyze_equipment_deep",
                                "status": "completed",
                            },
                            "job_status": "completed",
                            "result_message_id": result_message_id,
                            "actions_disabled": True,
                        },
                        result_message_id: {
                            "id": result_message_id,
                            "role": "assistant",
                            "content": "Уже сохранённый итог.",
                            "parentId": accepted_message_id,
                            "tool_job_result_for": accepted_message_id,
                        },
                    },
                }
            }
        },
    )

    refresh_calls = []

    def fake_refresh_urlopen(request, timeout=45):
        refresh_calls.append((request.get_method(), request.full_url))
        return _FakeHTTPResponse(
            {
                "job_id": "job-refresh-1",
                "status": "completed",
            }
        )

    monkeypatch.setattr(refresh_namespace["urllib"].request, "urlopen", fake_refresh_urlopen)
    response = await refresh_action.action(
        {
            "chat_id": "chat-refresh-1",
            "id": accepted_message_id,
            "tool_job": {
                "job_id": "job-refresh-1",
                "status_url": "/tool-server/tool-jobs/job-refresh-1",
                "tool_name": "analyze_equipment_deep",
                "status": "completed",
            },
            "job_status": "completed",
            "result_message_id": result_message_id,
        },
        __request__=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_tools_platform_deep_job_pollers={}))),
    )

    assert response["job_status"] == "completed"
    assert response["result_message_id"] == result_message_id
    assert "Результат уже добавлен ниже" in response["content"]
    assert refresh_calls == [("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-refresh-1")]
    assert set(chat_store["chat-refresh-1"]["history"]["messages"].keys()) == {
        accepted_message_id,
        result_message_id,
    }


@pytest.mark.asyncio
async def test_cancel_action_handles_terminal_job_without_exception(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}
    cancel_namespace = _load_action_namespace(actions["tool_job_cancel_action"]["pythonCode"])
    cancel_action = cancel_namespace["Action"]()

    cancel_calls = []

    def fake_cancel_urlopen(request, timeout=45):
        cancel_calls.append((request.get_method(), request.full_url))
        return _FakeHTTPResponse(
            {
                "job_id": "job-terminal-1",
                "status": "completed",
            }
        )

    monkeypatch.setattr(cancel_namespace["urllib"].request, "urlopen", fake_cancel_urlopen)
    response = await cancel_action.action(
        {
            "tool_job": {
                "job_id": "job-terminal-1",
                "status_url": "/tool-server/tool-jobs/job-terminal-1",
                "tool_name": "analyze_equipment_deep",
                "status": "completed",
            }
        }
    )

    assert response["job_status"] == "completed"
    assert "Задача уже находится в терминальном состоянии." in response["content"]
    assert cancel_calls == [("POST", "http://host.docker.internal:18000/tool-server/tool-jobs/job-terminal-1/cancel")]
