import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import asyncio

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class SessionStore:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


@pytest.fixture
def chainlit_module():
    mock_cl = MagicMock()
    mock_cl.user_session = SessionStore()
    mock_cl.Message = MagicMock()
    mock_cl.Step = MagicMock()
    mock_cl.User = MagicMock()
    mock_cl.on_chat_start = lambda f: f
    mock_cl.on_chat_resume = lambda f: f
    mock_cl.on_message = lambda f: f
    mock_cl.password_auth_callback = lambda f: f
    mock_cl.data_layer = lambda f: f

    sys.modules["chainlit"] = mock_cl
    sys.modules["httpx"] = MagicMock()
    sys.modules["chainlit.data"] = MagicMock()
    sys.modules["chainlit.data.sql_alchemy"] = MagicMock()

    if "orchestrator.chainlit_app" in sys.modules:
        importlib.reload(sys.modules["orchestrator.chainlit_app"])
    else:
        import orchestrator.chainlit_app

    module = sys.modules["orchestrator.chainlit_app"]
    yield module

    for mod_name in ["chainlit", "chainlit.data", "chainlit.data.sql_alchemy", "httpx"]:
        sys.modules.pop(mod_name, None)
    sys.modules.pop("orchestrator.chainlit_app", None)


def _make_long_history(count: int = 36):
    history = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        history.append(
            {
                "role": role,
                "content": (
                    f"Сообщение {i}: обсуждаем тему {'А' if i < 20 else 'Б'}; "
                    f"ключевые детали поставки и условия договора. "
                    f"Дата 12.11.2025, сумма 1 250 000 руб, файл contract_v{i%3}.pdf. "
                    + "детали " * 35
                ),
            }
        )
    return history


def test_long_chat_prompt_gets_shorter_but_keeps_facts(chainlit_module):
    history = _make_long_history(36)

    prompt = chainlit_module._build_prompt("Проверь факты", history, "sys")

    no_compress_prompt = "<|im_start|>system\nsys<|im_end|>\n"
    for msg in history[-chainlit_module.MAX_HISTORY_MESSAGES:]:
        no_compress_prompt += f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>\n"
    no_compress_prompt += "<|im_start|>user\nПроверь факты<|im_end|>\n<|im_start|>assistant\n"

    assert len(prompt) < len(no_compress_prompt)
    assert "12.11.2025" in prompt
    assert "1 250 000 руб" in prompt
    assert "contract_v" in prompt


def test_topic_switch_and_return_preserves_old_topic_context(chainlit_module):
    history = []
    history.extend([{"role": "user", "content": "Тема A: гарантия 36 месяцев для tender_v1.pdf"}] * 14)
    history.extend([{"role": "assistant", "content": "Тема B: обсуждаем логистику и график"}] * 12)
    history.extend([{"role": "user", "content": "Вернёмся к теме A, напомни гарантию"}] * 6)

    prompt = chainlit_module._build_prompt("Что было по теме A?", history, "sys")

    assert "Критичные факты" in prompt
    assert "36 месяцев" in prompt
    assert "tender_v1.pdf" in prompt


def test_summarization_error_fallback_does_not_break_chat(chainlit_module):
    history = _make_long_history(32)
    chainlit_module.cl.user_session.set(chainlit_module.SUMMARY_SESSION_KEY, "")

    with patch.object(
        chainlit_module,
        "_generate_history_summary",
        side_effect=RuntimeError("summary failed"),
    ):
        prompt = chainlit_module._build_prompt("Последний вопрос", history, "sys")

    assert "<|im_start|>user\nПоследний вопрос" in prompt
    for msg in history[-chainlit_module.SUMMARY_KEEP_RECENT:]:
        assert msg["content"] in prompt


def test_on_chat_resume_restores_summary(chainlit_module):
    steps = []
    for i in range(34):
        steps.append({"type": "user_message", "output": f"msg {i} от user, дата 2025-10-0{i%9+1}"})
        steps.append({"type": "assistant_message", "output": f"resp {i}, файл archive_{i%2}.docx"})

    asyncio.run(chainlit_module.on_chat_resume({"steps": steps}))

    restored_summary = chainlit_module.cl.user_session.get(chainlit_module.SUMMARY_SESSION_KEY)
    assert restored_summary
    assert "2025-10" in restored_summary
    assert "archive_" in restored_summary


def test_invariants_critical_facts_not_lost_and_recent_messages_included(chainlit_module):
    history = _make_long_history(33)
    prompt = chainlit_module._build_prompt("Подведи итог", history, "sys")

    assert "12.11.2025" in prompt
    assert "1 250 000 руб" in prompt
    for msg in history[-chainlit_module.SUMMARY_KEEP_RECENT:]:
        assert msg["content"] in prompt
