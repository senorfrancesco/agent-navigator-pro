import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeMessage:
    sent_payloads = []

    def __init__(self, content="", actions=None, **kwargs):
        self.content = content
        self.actions = actions or []
        self.kwargs = kwargs

    async def send(self):
        _FakeMessage.sent_payloads.append(
            {
                "content": self.content,
                "actions": self.actions,
                "kwargs": self.kwargs,
            }
        )
        return {"content": self.content, "actions": self.actions}


class TestChainlitResumeRestoreSmoke:
    @pytest.fixture(autouse=True)
    def _setup_chainlit_mock(self):
        _FakeMessage.sent_payloads = []

        mock_cl = MagicMock()
        mock_session = MagicMock()
        _store = {}

        def _get(key, default=None):
            return _store.get(key, default)

        def _set(key, value):
            _store[key] = value

        mock_session.get.side_effect = _get
        mock_session.set.side_effect = _set

        mock_cl.user_session = mock_session
        mock_cl.Message = _FakeMessage
        mock_cl.Action = lambda **kwargs: kwargs
        mock_cl.Step = MagicMock()
        mock_cl.User = MagicMock()
        mock_cl.on_chat_start = lambda f: f
        mock_cl.on_chat_resume = lambda f: f
        mock_cl.on_message = lambda f: f
        mock_cl.action_callback = lambda _: (lambda f: f)
        mock_cl.password_auth_callback = lambda f: f
        mock_cl.data_layer = lambda f: f

        sys.modules["chainlit"] = mock_cl
        sys.modules["chainlit.data"] = MagicMock()
        sys.modules["chainlit.data.sql_alchemy"] = MagicMock()

        if "orchestrator.chainlit_app" in sys.modules:
            importlib.reload(sys.modules["orchestrator.chainlit_app"])
        else:
            import orchestrator.chainlit_app

        self.module = sys.modules["orchestrator.chainlit_app"]
        self.store = _store
        yield

        for mod_name in ["chainlit", "chainlit.data", "chainlit.data.sql_alchemy"]:
            sys.modules.pop(mod_name, None)
        sys.modules.pop("orchestrator.chainlit_app", None)

    def test_resume_shows_manual_restore_action_when_index_missing(self):
        self.store["rag_pipeline"] = None
        thread = {
            "steps": [
                {"name": "Загрузка документов", "output": "Загружено: contract.pdf"},
                {"type": "assistant_message", "output": "ok"},
            ]
        }

        fake_step = AsyncMock()
        fake_step.__aenter__.return_value = fake_step
        fake_step.__aexit__.return_value = False

        with patch.object(self.module.os.path, "exists", return_value=True), patch.object(
            self.module, "_load_files", new=AsyncMock(return_value=[{"name": "contract.pdf", "path": "/app/uploads/contract.pdf", "text": "doc text"}])
        ), patch.object(
            self.module, "_reindex_session_documents", new=AsyncMock(return_value={"indexed": False})
        ), patch.object(self.module.cl, "Step", return_value=fake_step):
            asyncio.run(self.module.on_chat_resume(thread))

        assert any("состояние индекса — отсутствует" in p["content"] for p in _FakeMessage.sent_payloads)
        assert any(p["actions"] for p in _FakeMessage.sent_payloads)

    def test_manual_restore_action_reports_ready(self):
        self.store["documents"] = {"contract.pdf": {"text": "doc text", "path": "/app/uploads/contract.pdf"}}

        with patch.object(self.module.asyncio, "create_task") as create_task:
            asyncio.run(self.module.on_restore_doc_context({"name": "restore_doc_context"}))

        create_task.assert_called_once()
        scheduled_coro = create_task.call_args[0][0]
        scheduled_coro.close()
        assert any("в фоне" in p["content"] for p in _FakeMessage.sent_payloads)
