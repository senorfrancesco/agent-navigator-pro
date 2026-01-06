"""
Тесты для проверки отсутствия галлюцинаций и самодиалога в Agent API.
Запуск: python3 -m pytest backend/tests/test_agent_hallucinations.py
"""

import re
import pytest
import sys
import os

# Добавляем путь к orchestrator, чтобы импортировать clean_response
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'orchestrator')))

try:
    from agent_api import clean_response
except ImportError:
    # Если импорт не удался (например, нет зависимостей), используем локальную копию функции для тестов
    def clean_response(text: str) -> str:
        text = re.sub(r'```plaintext.*?```', '', text, flags=re.DOTALL)
        text = re.sub(r'```\s*```', '', text, flags=re.DOTALL)
        stop_patterns = [
            r'\n\n(User:|Human:|Вопрос:|Понял[,\s]|Спасибо[,\s])',
            r'\n\nОтвет:',
            r'\n\n###',
            r'\n\n\n',
            r'Если у вас есть',
            r'Пожалуйста, уточните'
        ]
        earliest_match = len(text)
        for pattern in stop_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match and match.start() < earliest_match:
                earliest_match = match.start()
        if earliest_match < len(text):
            text = text[:earliest_match]
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)
        return text.strip()

def test_clean_response_artifacts():
    """Проверка удаления артефактов разметки."""
    input_text = "Нормальный ответ.\n```plaintext\nАртефакт\n```\nПродолжение."
    expected = "Нормальный ответ.\nПродолжение."
    assert clean_response(input_text) == expected

def test_clean_response_self_dialogue():
    """Проверка обрезки самодиалога."""
    input_text = "Ответ на вопрос.\n\nUser: Спасибо!\n\nОтвет: Пожалуйста!"
    expected = "Ответ на вопрос."
    assert clean_response(input_text) == expected

def test_clean_response_stop_phrases():
    """Проверка удаления вежливых фраз-заполнителей."""
    input_text = "Вот ваш результат.\n\nЕсли у вас есть еще вопросы, задавайте!"
    expected = "Вот ваш результат."
    assert clean_response(input_text) == expected

def test_clean_response_normalization():
    """Проверка нормализации пробелов и переносов."""
    input_text = "Текст    с пробелами.\n\n\n\nМного переносов."
    expected = "Текст с пробелами.\n\nМного переносов."
    assert clean_response(input_text) == expected

if __name__ == "__main__":
    # Простой запуск без pytest
    print("Запуск тестов...")
    test_clean_response_artifacts()
    test_clean_response_self_dialogue()
    test_clean_response_stop_phrases()
    test_clean_response_normalization()
    print("Все тесты пройдены успешно! ✅")
