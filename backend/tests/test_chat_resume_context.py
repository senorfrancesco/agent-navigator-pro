"""Тесты восстановления контекста чата через on_chat_resume."""

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

    async def send(self):
        _FakeMessage.sent_messages.append(self.content)


class _FakeRagPipeline:
    def __init__(self, embed_fn=None, rag_mode=None):
        self.embed_fn = embed_fn
        self.rag_mode = rag_mode
        self._indexed = False
        self.index_calls = []

    def index_documents(self, texts, doc_names=None):
        self.index_calls.append((list(texts), list(doc_names or [])))
        self._indexed = True


@pytest.fixture
def chainlit_module():
    mock_cl = MagicMock()
    session = _SessionStore()
    mock_cl.user_session = session
    mock_cl.Message = _FakeMessage
    mock_cl.Step = _FakeStep
    mock_cl.User = MagicMock()
    mock_cl.on_chat_start = lambda f: f
    mock_cl.on_chat_resume = lambda f: f
    mock_cl.on_message = lambda f: f
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
    thread = {
        "steps": [
            {"type": "user_message", "output": "Привет"},
            {"type": "assistant_message", "output": "Здравствуйте"},
        ]
    }

    await chainlit_module.on_chat_resume(thread)

    history = chainlit_module.cl.user_session.get("history")
    documents = chainlit_module.cl.user_session.get("documents")

    assert history == [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
    ]
    assert documents == {}
    assert chainlit_module.cl.user_session.get("rag_pipeline") is None


@pytest.mark.asyncio
async def test_resume_restores_history_and_one_document(chainlit_module):
    thread = {
        "steps": [
            {"type": "user_message", "output": "Проверь документ"},
            {"name": "Загрузка документов", "output": "Загружено: doc1.pdf"},
            {"type": "assistant_message", "output": "Готово"},
        ]
    }

    with patch.object(chainlit_module.os.path, "exists", return_value=True), patch.object(
        chainlit_module,
        "_load_files",
        new=AsyncMock(return_value=[{"name": "doc1.pdf", "path": "/app/uploads/doc1.pdf", "text": "TEXT1"}]),
    ), patch.dict(
        sys.modules,
        {
            "orchestrator.rag.pipeline": SimpleNamespace(AdaptiveRAGPipeline=_FakeRagPipeline),
            "services.model_manager.ums_client": SimpleNamespace(create_ums_embed_fn=lambda: object()),
        },
    ), patch.object(chainlit_module, "_get_rag_mode", new=AsyncMock(return_value="simple")):
        await chainlit_module.on_chat_resume(thread)

    history = chainlit_module.cl.user_session.get("history")
    documents = chainlit_module.cl.user_session.get("documents")
    rag = chainlit_module.cl.user_session.get("rag_pipeline")

    assert history == [
        {"role": "user", "content": "Проверь документ"},
        {"role": "assistant", "content": "Готово"},
    ]
    assert list(documents.keys()) == ["doc1.pdf"]
    assert len(documents) == 1
    assert rag is not None
    assert rag._indexed is True
    assert rag.index_calls == [(["TEXT1"], ["doc1.pdf"])]


@pytest.mark.asyncio
async def test_resume_restores_two_documents_and_reindexes(chainlit_module):
    thread = {
        "steps": [
            {"name": "Загрузка документов", "output": "Загружено: a.pdf, b.pdf"},
        ]
    }

    loaded_docs = [
        {"name": "a.pdf", "path": "/app/uploads/a.pdf", "text": "A"},
        {"name": "b.pdf", "path": "/app/uploads/b.pdf", "text": "B"},
    ]

    with patch.object(chainlit_module.os.path, "exists", return_value=True), patch.object(
        chainlit_module,
        "_load_files",
        new=AsyncMock(return_value=loaded_docs),
    ), patch.dict(
        sys.modules,
        {
            "orchestrator.rag.pipeline": SimpleNamespace(AdaptiveRAGPipeline=_FakeRagPipeline),
            "services.model_manager.ums_client": SimpleNamespace(create_ums_embed_fn=lambda: object()),
        },
    ), patch.object(chainlit_module, "_get_rag_mode", new=AsyncMock(return_value="hybrid")):
        await chainlit_module.on_chat_resume(thread)

    documents = chainlit_module.cl.user_session.get("documents")
    rag = chainlit_module.cl.user_session.get("rag_pipeline")

    assert len(documents) == 2
    assert set(documents.keys()) == {"a.pdf", "b.pdf"}
    assert rag is not None
    assert rag._indexed is True
    assert rag.index_calls == [(["A", "B"], ["a.pdf", "b.pdf"])]


@pytest.mark.asyncio
async def test_resume_partial_restore_when_file_missing(chainlit_module):
    thread = {
        "steps": [
            {"name": "Загрузка документов", "output": "Загружено: exists.pdf, missing.pdf"},
        ]
    }

    def _exists(path):
        return path.endswith("exists.pdf")

    with patch.object(chainlit_module.os.path, "exists", side_effect=_exists), patch.object(
        chainlit_module,
        "_load_files",
        new=AsyncMock(return_value=[{"name": "exists.pdf", "path": "/app/uploads/exists.pdf", "text": "OK"}]),
    ), patch.dict(
        sys.modules,
        {
            "orchestrator.rag.pipeline": SimpleNamespace(AdaptiveRAGPipeline=_FakeRagPipeline),
            "services.model_manager.ums_client": SimpleNamespace(create_ums_embed_fn=lambda: object()),
        },
    ), patch.object(chainlit_module, "_get_rag_mode", new=AsyncMock(return_value="simple")):
        await chainlit_module.on_chat_resume(thread)

    documents = chainlit_module.cl.user_session.get("documents")
    rag = chainlit_module.cl.user_session.get("rag_pipeline")

    assert list(documents.keys()) == ["exists.pdf"]
    assert len(documents) == 1
    assert rag is not None
    assert rag._indexed is True


@pytest.mark.asyncio
async def test_routing_after_resume_document_question_only_for_valid_restore(chainlit_module):
    message = SimpleNamespace(content="Что в документе?", elements=[])

    with patch.object(chainlit_module, "_execute_intent", new=AsyncMock()) as mock_execute:
        with patch.object(chainlit_module, "_stream_response", new=AsyncMock()):
            await chainlit_module.on_chat_resume({"steps": [{"type": "user_message", "output": "hi"}]})
            await chainlit_module.on_message(message)

        assert mock_execute.await_args_list[-1].args[0] == "general_chat"

    valid_thread = {"steps": [{"name": "Загрузка документов", "output": "Загружено: doc.pdf"}]}
    with patch.object(chainlit_module.os.path, "exists", return_value=True), patch.object(
        chainlit_module,
        "_load_files",
        new=AsyncMock(return_value=[{"name": "doc.pdf", "path": "/app/uploads/doc.pdf", "text": "DOC"}]),
    ), patch.dict(
        sys.modules,
        {
            "orchestrator.rag.pipeline": SimpleNamespace(AdaptiveRAGPipeline=_FakeRagPipeline),
            "services.model_manager.ums_client": SimpleNamespace(create_ums_embed_fn=lambda: object()),
        },
    ), patch.object(chainlit_module, "_get_rag_mode", new=AsyncMock(return_value="simple")), patch.object(
        chainlit_module, "_execute_intent", new=AsyncMock()
    ) as mock_execute:
        await chainlit_module.on_chat_resume(valid_thread)
        await chainlit_module.on_message(message)

    assert mock_execute.await_args_list[-1].args[0] == "document_question"
