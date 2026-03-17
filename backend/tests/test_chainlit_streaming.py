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

    async def stream_token(self, token: str):
        self.tokens.append(token)
        self.content += token

    async def send(self):
        self.sent = True


@pytest.fixture
def chainlit_stream_module():
    mock_cl = MagicMock()
    mock_session = MagicMock()
    mock_session.get.return_value = None
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
