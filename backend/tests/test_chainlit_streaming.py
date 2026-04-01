"""
Regression tests for Chainlit streaming path and UMS SSE client.
"""

import asyncio
import importlib
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.model_manager.ums_client import UMSClient


class FakeMessage:
    def __init__(self):
        self.content = ""
        self.tokens = []
        self.sent = False
        self.elements = []

    async def stream_token(self, token: str):
        self.tokens.append(token)
        self.content += token

    async def send(self):
        self.sent = True


class FakeStep:
    def __init__(self, **kwargs):
        self.name = kwargs.get("name")
        self.type = kwargs.get("type")
        self.show_input = kwargs.get("show_input")
        self.default_open = kwargs.get("default_open")
        self.autoCollapse = kwargs.get("autoCollapse")
        self.output = ""
        self.sent = False
        self.updated = False

    async def send(self):
        self.sent = True

    async def update(self):
        self.updated = True


class FakeUserSession:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


@pytest.fixture
def chainlit_stream_module():
    mock_cl = MagicMock()
    mock_session = FakeUserSession()
    mock_cl.user_session = mock_session
    mock_cl.Message = MagicMock()
    mock_cl.Step = MagicMock()
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

    from orchestrator.chainlit_app import _stream_response

    module = sys.modules["orchestrator.chainlit_app"]
    yield module, _stream_response

    for mod_name in ["chainlit", "chainlit.data", "chainlit.data.sql_alchemy"]:
        sys.modules.pop(mod_name, None)
    sys.modules.pop("orchestrator.chainlit_app", None)


@pytest.mark.asyncio
async def test_stream_response_uses_direct_infer(chainlit_stream_module):
    module, stream_response = chainlit_stream_module
    msg = FakeMessage()
    history = []

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch.object(
             module.asyncio,
             "to_thread",
             new=AsyncMock(side_effect=fake_to_thread),
         ), patch.object(
             module.ums_client,
             "infer",
             return_value={"choices": [{"text": "direct infer response"}]},
         ) as mock_infer:
        await stream_response("prompt", msg, history)

    mock_infer.assert_called_once()
    assert msg.sent is True
    assert msg.content == "direct infer response"
    assert history[-1]["content"] == "direct infer response"


@pytest.mark.asyncio
async def test_stream_response_retries_sync_on_threaded_infer_failure(chainlit_stream_module):
    module, stream_response = chainlit_stream_module
    msg = FakeMessage()
    history = []

    with patch.object(
        module.asyncio,
        "to_thread",
        new=AsyncMock(side_effect=RuntimeError("threaded infer failed")),
    ), patch.object(
        module.ums_client,
        "infer",
        return_value={"choices": [{"text": "sync retry response"}]},
    ) as mock_infer:
        await stream_response("prompt", msg, history)

    mock_infer.assert_called_once()
    assert msg.sent is True
    assert msg.content == "sync retry response"
    assert history[-1]["content"] == "sync retry response"


@pytest.mark.asyncio
async def test_infer_assistant_text_skips_sync_retry_when_disabled(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch.object(
        module.asyncio,
        "to_thread",
        new=AsyncMock(side_effect=fake_to_thread),
    ), patch.object(
        module.ums_client,
        "infer",
        side_effect=RuntimeError("ums failed"),
    ) as mock_infer:
        with pytest.raises(RuntimeError, match="ums failed"):
            await module._infer_assistant_text(
                "prompt",
                allow_sync_retry=False,
                raise_on_error=True,
                summary_stage="global",
            )

    mock_infer.assert_called_once()


@pytest.mark.asyncio
async def test_infer_assistant_text_strips_leaked_system_lines(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    leaked_text = (
        "Используй краткий ответ и переход к small talk, если это не приветствие.\n"
        "Не повторяйся.\n"
        "Привет! Как могу помочь тебе сегодня?"
    )

    with patch.object(
        module.asyncio,
        "to_thread",
        new=AsyncMock(side_effect=fake_to_thread),
    ), patch.object(
        module.ums_client,
        "infer",
        return_value={"choices": [{"text": leaked_text}]},
    ) as mock_infer:
        result = await module._infer_assistant_text("prompt")

    mock_infer.assert_called_once()
    assert "Используй краткий ответ" not in result
    assert "Не повторяйся" not in result
    assert result == "Привет! Как могу помочь тебе сегодня?"


@pytest.mark.asyncio
async def test_compare_appendix_is_not_kept_inline_in_main_message(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module
    history = []
    sent_messages = []
    sent_steps = []

    compare_report = """## Юридический вывод
Краткий юридический вывод.

## Ключевые смысловые различия
- Изменение 1

## Приложение: различия по пунктам
1. Строка различия 1
2. Строка различия 2
3. Строка различия 3
4. Строка различия 4
5. Строка различия 5
6. Строка различия 6
"""

    def message_factory(*args, **kwargs):
        msg = FakeMessage()
        msg.content = kwargs.get("content", "")
        msg.elements = kwargs.get("elements", [])
        sent_messages.append(msg)
        return msg

    def step_factory(*args, **kwargs):
        step = FakeStep(**kwargs)
        sent_steps.append(step)
        return step

    with patch.object(module.cl, "Message", side_effect=message_factory), patch.object(
        module.cl, "Step", side_effect=step_factory
    ), patch.object(
        module, "_sync_run_metadata_from_response"
    ), patch.object(module, "_apply_session_state_patch"), patch.object(
        module, "_finalize_progress_box", new=AsyncMock()
    ), patch.object(
        module, "_persist_current_backend_state", new=AsyncMock()
    ):
        await module._render_execution_response(
            {"assistant_message": compare_report},
            history,
        )

    assert len(sent_messages) == 1
    assert "## Юридический вывод" in sent_messages[0].content
    assert "## Приложение: различия по пунктам" not in sent_messages[0].content
    assert len(sent_steps) == 1


def test_compare_appendix_split_returns_original_when_section_missing(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module
    report = """## Юридический вывод
Краткий юридический вывод.
"""

    split_result = module._split_compare_report_appendix(report)

    assert split_result["main_body"] == report
    assert split_result["appendix_body"] is None
    assert split_result["appendix_lines"] == 0


def test_compare_appendix_split_extracts_canonical_appendix_section(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module
    report = """## Юридический вывод
Краткий юридический вывод.

## Ключевые смысловые различия
- Изменение 1

## Приложение: различия по пунктам
1. Строка различия 1
2. Строка различия 2
"""

    split_result = module._split_compare_report_appendix(report)

    assert "## Приложение: различия по пунктам" not in split_result["main_body"]
    assert split_result["appendix_body"].startswith("## Приложение: различия по пунктам")
    assert split_result["appendix_lines"] == 2


def test_compare_appendix_split_ignores_partial_noncanonical_header(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module
    report = """## Юридический вывод
Краткий юридический вывод.

## Приложение
1. Строка различия 1
"""

    split_result = module._split_compare_report_appendix(report)

    assert split_result["main_body"] == report
    assert split_result["appendix_body"] is None
    assert split_result["appendix_lines"] == 0


def test_compare_appendix_threshold_keeps_short_appendix_inline(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module
    split_result = {
        "main_body": "## Юридический вывод\nКратко.",
        "appendix_body": "## Приложение: различия по пунктам\n1. Короткая строка",
        "appendix_lines": 1,
    }

    assert module._should_render_compare_appendix_in_step(split_result) is False


def test_compare_appendix_threshold_moves_long_appendix_to_step(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module
    split_result = {
        "main_body": "## Юридический вывод\nКратко.",
        "appendix_body": (
            "## Приложение: различия по пунктам\n"
            "1. Строка различия 1\n"
            "2. Строка различия 2\n"
            "3. Строка различия 3\n"
            "4. Строка различия 4\n"
            "5. Строка различия 5\n"
            "6. Строка различия 6"
        ),
        "appendix_lines": 6,
    }

    assert module._should_render_compare_appendix_in_step(split_result) is True


def test_compare_progress_stages_cover_long_running_compare(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module

    stages = module._build_execution_progress_stages(
        {
            "message": "Сравни документы",
            "forced_route": "compare_documents",
            "session_docs": {"a": {}, "b": {}},
        }
    )

    assert stages is not None
    assert stages[0]["title"] == "Сравнение документов"
    assert "Загружаю документы" in stages[0]["content"]
    assert any("Сопоставляю смысловые фрагменты" in stage["content"] for stage in stages)
    assert any("Формирую юридический вывод" in stage["content"] for stage in stages)


@pytest.mark.asyncio
async def test_await_backend_execution_shows_compare_progress_states(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module
    progress_updates = []

    async def fake_execute(_request, deps=None):
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return {"assistant_message": "готово"}

    async def fake_update_progress_box(*, key: str, title: str, content: str):
        progress_updates.append({"key": key, "title": title, "content": content})

    with patch.object(module, "_backend_execute_orchestration", new=AsyncMock(side_effect=fake_execute)), patch.object(
        module, "_build_execution_dependencies", return_value={}
    ), patch.object(
        module, "_update_progress_box", new=AsyncMock(side_effect=fake_update_progress_box)
    ), patch.object(
        module, "_clear_progress_box", new=AsyncMock()
    ), patch.object(
        module, "_persist_current_backend_state", new=AsyncMock()
    ), patch.object(
        module, "EXECUTION_PROGRESS_POLL_S", 0
    ), patch.object(
        module.cl, "Message", side_effect=lambda *args, **kwargs: FakeMessage()
    ):
        response = await module._await_backend_execution(
            {
                "message": "Сравни два закона",
                "forced_route": "compare_documents",
                "session_docs": {"doc-1": {}, "doc-2": {}},
                "trace_id": "trace-1",
            }
        )

    assert response == {"assistant_message": "готово"}
    assert progress_updates
    assert progress_updates[0]["key"] == "execution_progress"
    assert progress_updates[0]["title"] == "Сравнение документов"
    assert any("Сопоставляю смысловые фрагменты" in item["content"] for item in progress_updates)


@pytest.mark.asyncio
async def test_compare_long_appendix_renders_in_collapsed_step(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module
    history = []
    sent_messages = []
    sent_steps = []

    compare_report = """## Юридический вывод
Краткий юридический вывод.

## Ключевые смысловые различия
- Изменение 1

## Приложение: различия по пунктам
1. Строка различия 1
2. Строка различия 2
3. Строка различия 3
4. Строка различия 4
5. Строка различия 5
6. Строка различия 6
"""

    def message_factory(*args, **kwargs):
        msg = FakeMessage()
        msg.content = kwargs.get("content", "")
        msg.elements = kwargs.get("elements", [])
        sent_messages.append(msg)
        return msg

    def step_factory(*args, **kwargs):
        step = FakeStep(**kwargs)
        sent_steps.append(step)
        return step

    with patch.object(module.cl, "Message", side_effect=message_factory), patch.object(
        module.cl, "Step", side_effect=step_factory
    ), patch.object(
        module, "_sync_run_metadata_from_response"
    ), patch.object(module, "_apply_session_state_patch"), patch.object(
        module, "_finalize_progress_box", new=AsyncMock()
    ), patch.object(
        module, "_persist_current_backend_state", new=AsyncMock()
    ):
        await module._render_execution_response(
            {"assistant_message": compare_report},
            history,
        )

    assert len(sent_messages) == 1
    assert "## Юридический вывод" in sent_messages[0].content
    assert "## Приложение: различия по пунктам" not in sent_messages[0].content
    assert len(sent_steps) == 1
    assert sent_steps[0].name == "Приложение: различия по пунктам (6)"
    assert sent_steps[0].autoCollapse is True
    assert sent_steps[0].sent is True
    assert "## Приложение: различия по пунктам" in sent_steps[0].output


@pytest.mark.asyncio
async def test_compare_short_appendix_stays_inline_without_step(chainlit_stream_module):
    module, _stream_response = chainlit_stream_module
    history = []
    sent_messages = []
    sent_steps = []

    compare_report = """## Юридический вывод
Краткий юридический вывод.

## Приложение: различия по пунктам
1. Строка различия 1
"""

    def message_factory(*args, **kwargs):
        msg = FakeMessage()
        msg.content = kwargs.get("content", "")
        msg.elements = kwargs.get("elements", [])
        sent_messages.append(msg)
        return msg

    def step_factory(*args, **kwargs):
        step = FakeStep(**kwargs)
        sent_steps.append(step)
        return step

    with patch.object(module.cl, "Message", side_effect=message_factory), patch.object(
        module.cl, "Step", side_effect=step_factory
    ), patch.object(
        module, "_sync_run_metadata_from_response"
    ), patch.object(module, "_apply_session_state_patch"), patch.object(
        module, "_finalize_progress_box", new=AsyncMock()
    ), patch.object(
        module, "_persist_current_backend_state", new=AsyncMock()
    ):
        await module._render_execution_response(
            {"assistant_message": compare_report},
            history,
        )

    assert len(sent_messages) == 1
    assert "## Приложение: различия по пунктам" in sent_messages[0].content
    assert sent_steps == []


class TestAsyncInferStream:
    class _FakeResponse:
        def __init__(self, lines):
            self._lines = lines
            self.exited = False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    class _FakeStreamContext:
        def __init__(self, response):
            self._response = response

        async def __aenter__(self):
            return self._response

        async def __aexit__(self, exc_type, exc, tb):
            self._response.exited = True
            return False

    class _FakeClient:
        def __init__(self, response):
            self._response = response

        def stream(self, method, url, json):
            return TestAsyncInferStream._FakeStreamContext(self._response)

    @pytest.mark.asyncio
    async def test_async_infer_stream_handles_done_marker_cleanly(self):
        response = self._FakeResponse([
            'data: {"choices":[{"text":"Hello"}]}',
            'data: {"choices":[{"text":" world"}]}',
            "data: [DONE]",
        ])
        client = self._FakeClient(response)
        ums = UMSClient(base_url="http://test")

        with patch("services.model_manager.ums_client._get_async_client", new=AsyncMock(return_value=client)):
            tokens = [token async for token in ums.async_infer_stream("qwen-14b-llm", {"prompt": "x"})]

        assert tokens == ["Hello", " world"]

    @pytest.mark.asyncio
    async def test_async_infer_stream_to_callback_invokes_callback(self):
        response = self._FakeResponse([
            'data: {"choices":[{"text":"Hello"}]}',
            'data: {"choices":[{"text":" world"}]}',
            "data: [DONE]",
        ])
        client = self._FakeClient(response)
        ums = UMSClient(base_url="http://test")
        tokens = []

        async def on_token(token: str):
            tokens.append(token)

        with patch("services.model_manager.ums_client._get_async_client", new=AsyncMock(return_value=client)):
            await ums.async_infer_stream_to_callback("qwen-14b-llm", {"prompt": "x"}, on_token)

        assert tokens == ["Hello", " world"]

    @pytest.mark.asyncio
    async def test_async_infer_stream_closes_upstream_when_consumer_stops_early(self):
        response = self._FakeResponse([
            'data: {"choices":[{"text":"Hello"}]}',
            "data: [DONE]",
        ])
        client = self._FakeClient(response)
        ums = UMSClient(base_url="http://test")

        with patch("services.model_manager.ums_client._get_async_client", new=AsyncMock(return_value=client)):
            stream = ums.async_infer_stream("qwen-14b-llm", {"prompt": "x"})
            first = await anext(stream)
            assert first == "Hello"
            await stream.aclose()

        assert response.exited is True
