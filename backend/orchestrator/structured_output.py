from __future__ import annotations

import json
from typing import Any, Dict, Optional, Type


def extract_model_text(response: Dict[str, Any]) -> str:
    """Нормализует текстовый ответ модели без salvage/repair."""
    if not isinstance(response, dict):
        return str(response or "")
    content = response.get("content", "")
    if content:
        return str(content)
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] or {}
        if isinstance(choice, dict):
            return str(choice.get("text", "") or choice.get("message", {}).get("content", ""))
    result = response.get("result")
    if isinstance(result, dict):
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0] or {}
            if isinstance(choice, dict):
                return str(choice.get("text", "") or choice.get("message", {}).get("content", ""))
    return ""


def parse_strict_json(text: str, *, expected_type: Optional[Type[Any]] = None) -> Optional[Any]:
    """Строгий JSON parser без salvage, fence stripping и garbage recovery."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if expected_type is not None and not isinstance(parsed, expected_type):
        return None
    return parsed
