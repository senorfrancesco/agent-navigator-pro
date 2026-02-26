"""
Тесты для _build_multiturn_prompt — ChatML из messages[].
"""

import sys
import os
import pytest

# Добавляем путь к бэкенду
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from orchestrator.agent_api import _build_multiturn_prompt, MAX_HISTORY_MESSAGES


class TestBuildMultiturnPrompt:
    """Тесты multi-turn промпта."""

    def test_single_user_message(self):
        """Одно user-сообщение → корректный ChatML."""
        messages = [{"role": "user", "content": "Привет"}]
        prompt = _build_multiturn_prompt(messages)
        assert "<|im_start|>system" in prompt
        assert "<|im_start|>user\nПривет<|im_end|>" in prompt
        assert prompt.endswith("<|im_start|>assistant\n")

    def test_multi_turn_conversation(self):
        """Multi-turn: user → assistant → user."""
        messages = [
            {"role": "user", "content": "Меня зовут Иван"},
            {"role": "assistant", "content": "Приятно познакомиться, Иван!"},
            {"role": "user", "content": "Как меня зовут?"},
        ]
        prompt = _build_multiturn_prompt(messages)
        assert "Меня зовут Иван" in prompt
        assert "Приятно познакомиться, Иван!" in prompt
        assert "Как меня зовут?" in prompt
        assert prompt.endswith("<|im_start|>assistant\n")

    def test_system_message_override(self):
        """System message из messages[] переопределяет дефолтный."""
        messages = [
            {"role": "system", "content": "Ты юрист."},
            {"role": "user", "content": "Помоги"},
        ]
        prompt = _build_multiturn_prompt(messages)
        assert "Ты юрист." in prompt
        assert "Agent Navigator" not in prompt.split("Ты юрист.")[0]

    def test_system_suffix(self):
        """system_suffix добавляется к system prompt."""
        messages = [{"role": "user", "content": "тест"}]
        prompt = _build_multiturn_prompt(messages, system_suffix="Контекст: документ X")
        assert "Контекст: документ X" in prompt

    def test_max_history_truncation(self):
        """Обрезка до MAX_HISTORY_MESSAGES."""
        messages = [
            {"role": "user", "content": f"Сообщение {i}"}
            for i in range(20)
        ]
        prompt = _build_multiturn_prompt(messages)
        # Только последние MAX_HISTORY_MESSAGES сообщений
        assert f"Сообщение {20 - MAX_HISTORY_MESSAGES}" in prompt
        assert "Сообщение 0" not in prompt

    def test_multimodal_content(self):
        """Multimodal content (list) → извлекается текст."""
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Описание"},
                {"type": "image_url", "image_url": {"url": "test.jpg"}},
            ]
        }]
        prompt = _build_multiturn_prompt(messages)
        assert "Описание" in prompt

    def test_empty_messages(self):
        """Пустой список messages → только system + assistant start."""
        prompt = _build_multiturn_prompt([])
        assert "<|im_start|>system" in prompt
        assert "<|im_start|>assistant\n" in prompt

    def test_chatml_format(self):
        """Формат соответствует ChatML."""
        messages = [{"role": "user", "content": "тест"}]
        prompt = _build_multiturn_prompt(messages)
        # Проверяем структуру
        assert prompt.count("<|im_start|>") >= 2  # system + user + assistant
        assert prompt.count("<|im_end|>") >= 2    # system + user

    def test_assistant_ending(self):
        """Промпт заканчивается на assistant start."""
        messages = [
            {"role": "user", "content": "вопрос"},
        ]
        prompt = _build_multiturn_prompt(messages)
        assert prompt.rstrip().endswith("<|im_start|>assistant")

    def test_no_double_assistant(self):
        """Если последнее сообщение от assistant, не добавляем дублирующий start."""
        messages = [
            {"role": "user", "content": "вопрос"},
            {"role": "assistant", "content": "ответ"},
        ]
        prompt = _build_multiturn_prompt(messages)
        # Не должно быть assistant\n в конце (т.к. последнее — assistant)
        assert not prompt.endswith("<|im_start|>assistant\n")
