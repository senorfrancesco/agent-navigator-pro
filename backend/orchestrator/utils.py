import json
import re
from typing import Union, Dict, List

def parse_json_garbage(text: str) -> Union[Dict, List, None]:
    """Агрессивный парсер JSON с множественными попытками (извлечено из claude_expertV3.py)"""
    if not text:
        return None
    
    # Попытка 1: Удаляем markdown и пробуем парсить
    try:
        clean = re.sub(r'```json\s*|\s*```', '', text).strip()
        result = json.loads(clean)
        if isinstance(result, dict) or isinstance(result, list):
            return result
    except:
        pass
    
    # Попытка 2: Ищем JSON блок в тексте
    try:
        match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        if match:
            result = json.loads(match.group())
            return result
    except:
        pass
    
    # Попытка 3: Ищем JSON после двоеточия
    try:
        if ':' in text:
            after_colon = text.split(':', 1)[1].strip()
            match = re.search(r'(\{.*\}|\[.*\])', after_colon, re.DOTALL)
            if match:
                result = json.loads(match.group())
                return result
    except:
        pass
    
    # Попытка 5: Убираем всё до первой [ или {
    try:
        for start_char in ['{', '[']:
            if start_char in text:
                idx = text.index(start_char)
                json_like = text[idx:]
                end_char = '}' if start_char == '{' else ']'
                if end_char in json_like:
                    last_idx = json_like.rfind(end_char)
                    candidate = json_like[:last_idx + 1]
                    result = json.loads(candidate)
                    return result
    except:
        pass
    
    return None
