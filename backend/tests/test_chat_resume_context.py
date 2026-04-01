"""Тесты восстановления chat resume context на текущем backend snapshot / legacy-history contract."""

import importlib
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _SessionStore:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class _FakeStep:
    def __init__(self, name=None, type=None):
        self.name = name
        self.type = type
        self.output = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeMessage:
    sent_messages = []

    def __init__(self, content=""):
        self.content = content
        self.elements = []

    async def send(self):
        _FakeMessage.sent_messages.append(self.content)


class _FakeStore:
    def __init__(self):
        self.load_run = AsyncMock(return_value=None)
        self.get_or_create_run = AsyncMock()
        self.save_run = AsyncMock()


@pytest.fixture
def chainlit_module():
    mock_cl = MagicMock()
    session = _SessionStore()
    mock_cl.user_session = session
    mock_cl.Message = _FakeMessage
    mock_cl.Step = _FakeStep
    mock_cl.User = MagicMock()
    mock_cl.Action = MagicMock()
    mock_cl.AskActionMessage = MagicMock()
    mock_cl.on_chat_start = lambda f: f
    mock_cl.on_chat_resume = lambda f: f
    mock_cl.on_message = lambda f: f
    mock_cl.on_stop = lambda f: f
    mock_cl.password_auth_callback = lambda f: f
    mock_cl.data_layer = lambda f: f

    sys.modules["chainlit"] = mock_cl
    sys.modules["chainlit.data"] = MagicMock()
    sys.modules["chainlit.data.sql_alchemy"] = MagicMock()

    if "orchestrator.chainlit_app" in sys.modules:
        importlib.reload(sys.modules["orchestrator.chainlit_app"])
    else:
        import orchestrator.chainlit_app

    module = sys.modules["orchestrator.chainlit_app"]
    _FakeMessage.sent_messages = []

    yield module

    for mod_name in ["chainlit", "chainlit.data", "chainlit.data.sql_alchemy"]:
        sys.modules.pop(mod_name, None)
    sys.modules.pop("orchestrator.chainlit_app", None)


@pytest.mark.asyncio
async def test_resume_restores_only_history(chainlit_module):
    store = _FakeStore()
    thread = {
        "steps": [
            {"type": "user_message", "output": "Привет"},
            {"type": "assistant_message", "output": "Здравствуйте"},
        ]
    }

    with patch.object(chainlit_module, "get_orchestration_state_store", return_value=store), patch.object(
        chainlit_module, "_send_control_plane_settings", new=AsyncMock()
    ), patch.object(chainlit_module, "_sync_thread_presentation", new=AsyncMock()), patch.object(
        chainlit_module, "_persist_current_backend_state", new=AsyncMock()
    ):
        await chainlit_module.on_chat_resume(thread)

    history = chainlit_module.cl.user_session.get("history")
    documents = chainlit_module.cl.user_session.get("documents")

    assert history == [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
    ]
    assert documents == {}
    assert chainlit_module.cl.user_session.get("active_doc_ids") == []
    assert chainlit_module.cl.user_session.get("rag_pipeline_cache") == {}


@pytest.mark.asyncio
async def test_resume_restores_history_and_one_document(chainlit_module):
    store = _FakeStore()
    thread = {
        "steps": [
            {"type": "user_message", "output": "Проверь документ"},
            {"name": "Загрузка документов", "output": "Загружено: doc1.pdf"},
            {"type": "assistant_message", "output": "Готово"},
        ]
    }

    with patch.object(chainlit_module, "get_orchestration_state_store", return_value=store), patch.object(
        chainlit_module.os.path, "exists", return_value=True
    ), patch.object(
        chainlit_module,
        "_load_files",
        new=AsyncMock(return_value=[{"name": "doc1.pdf", "path": "/app/uploads/doc1.pdf", "text": "TEXT1"}]),
    ), patch.object(
        chainlit_module, "_ensure_rag_index_for_active_docs", new=AsyncMock()
    ) as mock_reindex, patch.object(
        chainlit_module, "_send_control_plane_settings", new=AsyncMock()
    ), patch.object(chainlit_module, "_sync_thread_presentation", new=AsyncMock()), patch.object(
        chainlit_module, "_persist_current_backend_state", new=AsyncMock()
    ):
        await chainlit_module.on_chat_resume(thread)

    history = chainlit_module.cl.user_session.get("history")
    documents = chainlit_module.cl.user_session.get("documents")
    active_doc_ids = chainlit_module.cl.user_session.get("active_doc_ids")

    assert history == [
        {"role": "user", "content": "Проверь документ"},
        {"role": "assistant", "content": "Готово"},
    ]
    assert list(documents.keys()) == ["doc1.pdf"]
    assert len(active_doc_ids) == 1
    mock_reindex.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_partial_restore_when_file_missing(chainlit_module):
    store = _FakeStore()
    thread = {
        "steps": [
            {"name": "Загрузка документов", "output": "Загружено: exists.pdf, missing.pdf"},
        ]
    }

    def _exists(path):
        return path.endswith("exists.pdf")

    with patch.object(chainlit_module, "get_orchestration_state_store", return_value=store), patch.object(
        chainlit_module.os.path, "exists", side_effect=_exists
    ), patch.object(
        chainlit_module,
        "_load_files",
        new=AsyncMock(return_value=[{"name": "exists.pdf", "path": "/app/uploads/exists.pdf", "text": "OK"}]),
    ), patch.object(
        chainlit_module, "_ensure_rag_index_for_active_docs", new=AsyncMock()
    ) as mock_reindex, patch.object(
        chainlit_module, "_send_control_plane_settings", new=AsyncMock()
    ), patch.object(chainlit_module, "_sync_thread_presentation", new=AsyncMock()), patch.object(
        chainlit_module, "_persist_current_backend_state", new=AsyncMock()
    ):
        await chainlit_module.on_chat_resume(thread)

    documents = chainlit_module.cl.user_session.get("documents")
    active_doc_ids = chainlit_module.cl.user_session.get("active_doc_ids")

    assert list(documents.keys()) == ["exists.pdf"]
    assert len(active_doc_ids) == 1
    mock_reindex.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_message_uses_restored_session_docs_for_backend_request(chainlit_module):
    store = _FakeStore()
    message = SimpleNamespace(content="Что в документе?", elements=[], command=None)

    base_response = {
        "assistant_message": "ok",
        "route": "general_chat",
        "executor": "general_chat",
        "confidence": 0.0,
        "margin": 0.0,
        "reason": "test",
        "action_required": None,
    }

    with patch.object(chainlit_module, "get_orchestration_state_store", return_value=store), patch.object(
        chainlit_module, "_send_control_plane_settings", new=AsyncMock()
    ), patch.object(chainlit_module, "_sync_thread_presentation", new=AsyncMock()), patch.object(
        chainlit_module, "_persist_current_backend_state", new=AsyncMock()
    ), patch.object(
        chainlit_module, "_render_execution_response", new=AsyncMock()
    ), patch.object(
        chainlit_module, "_backend_execute_orchestration", new=AsyncMock(return_value=base_response)
    ) as mock_exec:
        await chainlit_module.on_chat_resume({"steps": [{"type": "user_message", "output": "hi"}]})
        await chainlit_module.on_message(message)

    request_without_docs = mock_exec.await_args_list[-1].args[0]
    assert request_without_docs["session_docs"] == {}

    valid_thread = {"steps": [{"name": "Загрузка документов", "output": "Загружено: doc.pdf"}]}
    with patch.object(chainlit_module, "get_orchestration_state_store", return_value=store), patch.object(
        chainlit_module.os.path, "exists", return_value=True
    ), patch.object(
        chainlit_module,
        "_load_files",
        new=AsyncMock(return_value=[{"name": "doc.pdf", "path": "/app/uploads/doc.pdf", "text": "DOC"}]),
    ), patch.object(
        chainlit_module, "_ensure_rag_index_for_active_docs", new=AsyncMock()
    ), patch.object(
        chainlit_module, "_send_control_plane_settings", new=AsyncMock()
    ), patch.object(chainlit_module, "_sync_thread_presentation", new=AsyncMock()), patch.object(
        chainlit_module, "_persist_current_backend_state", new=AsyncMock()
    ), patch.object(
        chainlit_module, "_render_execution_response", new=AsyncMock()
    ), patch.object(
        chainlit_module, "_backend_execute_orchestration", new=AsyncMock(return_value=base_response)
    ) as mock_exec:
        await chainlit_module.on_chat_resume(valid_thread)
        await chainlit_module.on_message(message)

    request_with_docs = mock_exec.await_args_list[-1].args[0]
    assert list(request_with_docs["session_docs"].keys()) == ["doc.pdf"]

