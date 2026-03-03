import json
import re
from typing import Union, Dict, List

def parse_json_garbage(text: str) -> Union[Dict, List, None]:
    """Агрессивный парсер JSON с множественными попытками и исправлением артефактов."""
    if not text:
        return None
    
    # Предварительная очистка "умных" кавычек и артефактов
    text = text.replace('“', '"').replace('”', '"').replace('„', '"').replace('«', '"').replace('»', '"')
    
    # Попытка 1: Прямой парсинг после удаления markdown-блоков
    try:
        clean = re.sub(r'```json\s*|\s*```', '', text).strip()
        return json.loads(clean)
    except:
        pass
    
    # Попытка 2: Поиск первого вхождения { или [ и последнего вхождения } или ]
    try:
        start_idx = -1
        for i, char in enumerate(text):
            if char in ('{', '['):
                start_idx = i
                break
        
        if start_idx != -1:
            end_char = '}' if text[start_idx] == '{' else ']'
            end_idx = text.rfind(end_char)
            
            if end_idx != -1:
                candidate = text[start_idx : end_idx + 1]
                # Исправляем возможные проблемы с экранированием внутри
                candidate = candidate.replace('\n', ' ').replace('\r', '')
                try:
                    return json.loads(candidate)
                except:
                    # Если всё ещё не парсится, пробуем починить незакрытые строки/скобки
                    # (Для упрощения просто возвращаем None если не сработало)
                    pass
    except:
        pass
    
    return None
