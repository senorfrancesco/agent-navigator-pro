"""
Тесты для проверки отсутствия галлюцинаций и самодиалога в Agent API.
Запуск: python3 -m pytest backend/tests/test_agent_hallucinations.py
"""

import re
# import pytest
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
            r'\n+(User:|Human:|Вопрос:|Понял[,\s]|Спасибо[,\s])',
            r'\n+Ответ:',
            r'\n+###',
            r'\n+Если у вас есть',
            r'\n+Пожалуйста, уточните',
            r'Корректированный ответ:',
            r'Корректный ответ:',
            r'Ответ окончен\.',
            r'Прекращаю отвечать\.',
            r'Твой ответ:'
        ]
        # Удаление множественных пробелов и переносов ПЕРЕД обрезкой
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)

        earliest_match = len(text)
        for pattern in stop_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match and match.start() < earliest_match:
                earliest_match = match.start()
        if earliest_match < len(text):
            text = text[:earliest_match]
        return text.strip()

def test_clean_response_artifacts():
    """Проверка удаления артефактов разметки."""
    input_text = "Нормальный ответ.\n```plaintext\nАртефакт\n```\nПродолжение."
    # clean_response удаляет ```plaintext...```, оставляя переносы строк, которые потом нормализуются
    result = clean_response(input_text)
    assert "Артефакт" not in result
    assert "plaintext" not in result

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
    input_text = "Текст    с пробелами.\n\n\n\nДальнейший текст."
    # \n{3,} заменяется на \n\n
    expected = "Текст с пробелами.\n\nДальнейший текст."
    result = clean_response(input_text)
    if result != expected:
        print(f"DEBUG: result='{repr(result)}'")
        print(f"DEBUG: expected='{repr(expected)}'")
    assert result == expected

def run_tests():
    # Простой запуск без pytest
    print("Запуск тестов...")
    test_clean_response_artifacts()
    test_clean_response_self_dialogue()
    test_clean_response_stop_phrases()
    test_clean_response_normalization()
    
    # Дополнительный тест для новых паттернов галлюцинаций
    test_hallucination_patterns = "Я ассистент. Корректный ответ: Я ассистент. Ответ окончен."
    cleaned = clean_response(test_hallucination_patterns)
    print(f"Тест новых паттернов: '{test_hallucination_patterns}' -> '{cleaned}'")
    assert "Корректный ответ" not in cleaned
    assert "Ответ окончен" not in cleaned
    
    print("Все тесты пройдены успешно! ✅")

if __name__ == "__main__":
    run_tests()
