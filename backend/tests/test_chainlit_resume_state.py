"""Regression tests for Chainlit session resume contract and state serialization."""

import asyncio
import importlib
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class SessionStore:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeMessage:
    sent_messages = []

    def __init__(self, content=""):
        self.content = content
        self.metadata = None

    async def send(self):
        FakeMessage.sent_messages.append(self.content)
        return self


class FakeStep:
    created = []

    def __init__(self, name=None, type=None):
        self.name = name
        self.type = type
        self.output = ""
        self.metadata = None

    async def __aenter__(self):
        FakeStep.created.append(self)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def chainlit_module(monkeypatch):
    mock_cl = MagicMock()
    session = SessionStore()
    mock_cl.user_session = session
    mock_cl.Message = FakeMessage
    mock_cl.Step = FakeStep
    mock_cl.User = MagicMock()
    mock_cl.Action = MagicMock()
    mock_cl.AskActionMessage = MagicMock()
    mock_cl.on_chat_start = lambda f: f
    mock_cl.on_chat_resume = lambda f: f
    mock_cl.on_message = lambda f: f
    mock_cl.password_auth_callback = lambda f: f
    mock_cl.data_layer = lambda f: f

    monkeypatch.setitem(sys.modules, "chainlit", mock_cl)
    monkeypatch.setitem(sys.modules, "chainlit.data", MagicMock())
    monkeypatch.setitem(sys.modules, "chainlit.data.sql_alchemy", MagicMock())

    if "orchestrator.chainlit_app" in sys.modules:
        mod = importlib.reload(sys.modules["orchestrator.chainlit_app"])
    else:
        mod = importlib.import_module("orchestrator.chainlit_app")

    FakeMessage.sent_messages = []
    FakeStep.created = []
    return mod


def test_resume_chat_without_files(chainlit_module):
    thread = {
        "metadata": {
            "session_state": {
                "version": "1",
                "history": [{"role": "user", "content": "Привет"}],
                "session_docs": [],
                "rag_index_state": "empty",
                "selected_route_mode": "general_chat",
            }
        },
        "steps": [],
    }

    asyncio.run(chainlit_module.on_chat_resume(thread))

    assert chainlit_module.cl.user_session.get("history") == [{"role": "user", "content": "Привет"}]
    assert chainlit_module.cl.user_session.get("documents") == {}
    assert "Чат восстановлен" in FakeMessage.sent_messages[-1]
    assert "0" in FakeMessage.sent_messages[-1]


def test_resume_chat_with_one_file(tmp_path, chainlit_module, monkeypatch):
    f1 = tmp_path / "doc1.txt"
    f1.write_text("text1", encoding="utf-8")

    thread = {
        "metadata": {
            "session_state": {
                "version": "1",
                "history": [{"role": "user", "content": "Вопрос"}],
                "session_docs": [{"name": "doc1.txt", "path": str(f1)}],
                "rag_index_state": "ready",
                "selected_route_mode": "document_question",
            }
        },
        "steps": [],
    }

    monkeypatch.setattr(chainlit_module, "_load_files", AsyncMock(return_value=[{"name": "doc1.txt", "path": str(f1), "text": "text1"}]))

    class DummyRAG:
        def __init__(self, embed_fn=None, rag_mode="simple"):
            self.embed_fn = embed_fn
            self.rag_mode = rag_mode

        def index_documents(self, texts, doc_names=None):
            return None

    fake_pipeline_module = SimpleNamespace(AdaptiveRAGPipeline=DummyRAG)
    fake_ums_module = SimpleNamespace(create_ums_embed_fn=lambda: None)
    monkeypatch.setitem(sys.modules, "orchestrator.rag.pipeline", fake_pipeline_module)
    monkeypatch.setitem(sys.modules, "services.model_manager.ums_client", fake_ums_module)
    monkeypatch.setattr(chainlit_module, "_get_rag_mode", AsyncMock(return_value="simple"))

    asyncio.run(chainlit_module.on_chat_resume(thread))

    docs = chainlit_module.cl.user_session.get("documents")
    assert list(docs.keys()) == ["doc1.txt"]
    assert chainlit_module.cl.user_session.get("selected_route_mode") == "document_question"
    assert FakeStep.created[-1].metadata["session_state"]["rag_index_state"] == "ready"


@pytest.mark.parametrize("route_mode", ["compare_documents", "equipment_analysis"])
def test_resume_chat_with_multiple_files_and_route_mode(tmp_path, chainlit_module, monkeypatch, route_mode):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("a", encoding="utf-8")
    f2.write_text("b", encoding="utf-8")

    thread = {
        "metadata": {
            "session_state": {
                "version": "1",
                "history": [{"role": "user", "content": "Сценарий"}],
                "session_docs": [
                    {"name": "a.txt", "path": str(f1)},
                    {"name": "b.txt", "path": str(f2)},
                ],
                "rag_index_state": "ready",
                "selected_route_mode": route_mode,
            }
        },
        "steps": [],
    }

    monkeypatch.setattr(
        chainlit_module,
        "_load_files",
        AsyncMock(
            return_value=[
                {"name": "a.txt", "path": str(f1), "text": "a"},
                {"name": "b.txt", "path": str(f2), "text": "b"},
            ]
        ),
    )

    class DummyRAG:
        def __init__(self, embed_fn=None, rag_mode="simple"):
            pass

        def index_documents(self, texts, doc_names=None):
            return None

    monkeypatch.setitem(sys.modules, "orchestrator.rag.pipeline", SimpleNamespace(AdaptiveRAGPipeline=DummyRAG))
    monkeypatch.setitem(sys.modules, "services.model_manager.ums_client", SimpleNamespace(create_ums_embed_fn=lambda: None))
    monkeypatch.setattr(chainlit_module, "_get_rag_mode", AsyncMock(return_value="simple"))

    asyncio.run(chainlit_module.on_chat_resume(thread))

    assert len(chainlit_module.cl.user_session.get("documents")) == 2
    assert chainlit_module.cl.user_session.get("selected_route_mode") == route_mode


def test_switch_chat_a_b_a_without_context_loss(tmp_path, chainlit_module, monkeypatch):
    fa = tmp_path / "a.txt"
    fb = tmp_path / "b.txt"
    fa.write_text("A", encoding="utf-8")
    fb.write_text("B", encoding="utf-8")

    class DummyRAG:
        def __init__(self, embed_fn=None, rag_mode="simple"):
            pass

        def index_documents(self, texts, doc_names=None):
            return None

    monkeypatch.setitem(sys.modules, "orchestrator.rag.pipeline", SimpleNamespace(AdaptiveRAGPipeline=DummyRAG))
    monkeypatch.setitem(sys.modules, "services.model_manager.ums_client", SimpleNamespace(create_ums_embed_fn=lambda: None))
    monkeypatch.setattr(chainlit_module, "_get_rag_mode", AsyncMock(return_value="simple"))

    async def fake_load(files):
        return [{"name": f["name"], "path": f["path"], "text": f["name"]} for f in files]

    monkeypatch.setattr(chainlit_module, "_load_files", fake_load)

    thread_a = {
        "metadata": {"session_state": {"version": "1", "history": [{"role": "user", "content": "A"}], "session_docs": [{"name": "a.txt", "path": str(fa)}], "rag_index_state": "ready", "selected_route_mode": "document_question"}},
        "steps": [],
    }
    thread_b = {
        "metadata": {"session_state": {"version": "1", "history": [{"role": "user", "content": "B"}], "session_docs": [{"name": "b.txt", "path": str(fb)}], "rag_index_state": "ready", "selected_route_mode": "equipment_analysis"}},
        "steps": [],
    }

    asyncio.run(chainlit_module.on_chat_resume(thread_a))
    assert list(chainlit_module.cl.user_session.get("documents").keys()) == ["a.txt"]

    asyncio.run(chainlit_module.on_chat_resume(thread_b))
    assert list(chainlit_module.cl.user_session.get("documents").keys()) == ["b.txt"]

    asyncio.run(chainlit_module.on_chat_resume(thread_a))
    assert list(chainlit_module.cl.user_session.get("documents").keys()) == ["a.txt"]
