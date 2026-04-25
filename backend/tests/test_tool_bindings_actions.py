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


def _build_body_with_last_user_message(text: str, *, ui_locale: str | None = None) -> dict:
    body = {
        "messages": [
            {
                "role": "user",
                "content": text,
            }
        ]
    }
    if ui_locale is not None:
        body["ui_locale"] = ui_locale
    return body


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
                "job_id": "job-tool-query-1",
                "status_url": "/tool-server/tool-jobs/job-tool-query-1",
            }
        )

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await tools.analyze_equipment_deep(
        "Сравни только диски и объём памяти для этой конфигурации.",
        __metadata__={"ui_locale": "ru-RU"},
    )

    assert captured["payload"] == {
        "equipment_query": "Сравни только диски и объём памяти для этой конфигурации.",
        "job_mode": "force_async",
        "ui_locale": "ru",
    }
    assert response == (
        "Принят в работу: Глубокий анализ оборудования.\n"
        "Прогресс отображается в блоке `Инструмент долгого выполнения`."
    )


@pytest.mark.asyncio
async def test_equipment_deep_workspace_tool_localizes_acceptance_for_english(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    tool_payload = next(item for item in export["workspaceTools"] if item["tool_id"] == "equipment_deep_tool")
    namespace = _load_action_namespace(tool_payload["pythonCode"])
    tools = namespace["Tools"]()

    def fake_urlopen(request, timeout=45):
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["ui_locale"] == "en"
        return _FakeHTTPResponse(
            {
                "status": "accepted",
                "job_id": "job-tool-query-en-1",
                "status_url": "/tool-server/tool-jobs/job-tool-query-en-1",
            }
        )

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await tools.analyze_equipment_deep(
        "Compare only disks and memory volume for this configuration.",
        __metadata__={"ui_locale": "en-US"},
    )

    assert response == (
        "Accepted: Deep equipment analysis.\n"
        "Progress is shown in the `Long-running tool` panel."
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
        __metadata__={"ui_locale": "ru-RU"},
    )

    assert captured["url"].endswith("/tools/analyze_document_deep")
    assert captured["payload"]["analysis_goal"] == "Сфокусируйся на процессорах и памяти."
    assert captured["payload"]["document_refs"] == [
        {"label": "Requirements.pdf", "file_id": "file-req-1", "file_path": "/app/backend/data/uploads/req.pdf"}
    ]
    assert captured["payload"]["user_inputs"]["session_docs"]["Requirements.pdf"]["path"] == "/app/backend/data/uploads/req.pdf"
    assert "Принят в работу: Глубокий анализ документа." in response


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
        __metadata__={"ui_locale": "ru-RU"},
    )

    pollers = getattr(fake_request.app.state, "llm_tools_platform_deep_job_pollers", {})
    assert pollers
    poller_entry = next(iter(pollers.values()))
    poller_task = poller_entry["task"] if isinstance(poller_entry, dict) else poller_entry
    await asyncio.wait_for(poller_task, timeout=1)

    accepted_message = chat_store["chat-deep-tool-1"]["history"]["messages"][accepted_message_id]
    result_message_id = accepted_message.get("result_message_id")
    assert response == (
        "Принят в работу: Глубокий анализ оборудования.\n"
        "Прогресс отображается в блоке `Инструмент долгого выполнения`."
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
        __metadata__={"ui_locale": "ru-RU"},
    )

    assert "Не удалось запустить: Глубокий анализ оборудования." in response
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
        __metadata__={"ui_locale": "ru-RU"},
    )

    pollers = getattr(fake_request.app.state, "llm_tools_platform_deep_job_pollers", {})
    assert pollers
    poller_entry = next(iter(pollers.values()))
    poller_task = poller_entry["task"] if isinstance(poller_entry, dict) else poller_entry
    await asyncio.wait_for(poller_task, timeout=1)

    accepted_message = chat_store["chat-deep-tool-failed-1"]["history"]["messages"][accepted_message_id]
    assert response == (
        "Принят в работу: Глубокий анализ оборудования.\n"
        "Прогресс отображается в блоке `Инструмент долгого выполнения`."
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
        __metadata__={"ui_locale": "ru-RU"},
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
        "Принят в работу: Глубокий анализ оборудования.\n"
        "Прогресс отображается в блоке `Инструмент долгого выполнения`."
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
                                "Принят в работу: Глубокий анализ оборудования.\n"
                                "Прогресс отображается в блоке `Инструмент долгого выполнения`."
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
        __metadata__={"ui_locale": "ru-RU"},
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
        "Принят в работу: Глубокий анализ оборудования.\n"
        "Прогресс отображается в блоке `Инструмент долгого выполнения`."
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
