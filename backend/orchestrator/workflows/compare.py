"""
Workflow: Compare Documents
Логика сравнения двух юридических документов с использованием LangGraph и микросервисов.
"""

import json
import os
import re
import httpx
from typing import TypedDict, List, Dict, Any, Annotated, Optional
import operator
from langgraph.graph import StateGraph, END

# Настройки URL серверов (через переменные окружения)
MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
MCP_LEGAL_SERVER_URL = os.getenv("MCP_LEGAL_SERVER_URL", "http://localhost:8002")
UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")

# Импортируем клиент UMS (предполагая правильный путь в sys.path)
try:
    from services.model_manager.ums_client import ums_client
except ImportError:
    # Fallback если структура папок в runtime отличается
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
    from services.model_manager.ums_client import ums_client

# Импортируем утилиты
try:
    from orchestrator.utils import parse_json_garbage
except ImportError:
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
    from utils import parse_json_garbage

# === State Definition ===

class CompareState(TypedDict):
    input_1: str  # Путь к старому файлу
    input_2: str  # Путь к новому файлу
    chunks_old: List[str]
    chunks_new: List[str]
    matches: List[Dict[str, Any]]
    analysis_results: List[Any]
    final_report: str
    errors: List[str]

# === Nodes ===

def truncate_text(text: str, max_chars: int = 2000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(' ', 1)[0] + "..."

def dc_create_prompt(old, new):
    return f"""<|im_start|>system
Ты эксперт-юрист.<|im_end|>
<|im_start|>user
Сравни тексты.
СТАРЫЙ: {truncate_text(old, 2000)}
НОВЫЙ: {truncate_text(new, 2000)}
Найди юридические изменения (сроки, права, обязанности, штрафы). Игнорируй стиль.
Ответ JSON: {{"is_critical": true/false, "diff": "описание изменения", "impact": "последствия"}}<|im_end|>
<|im_start|>assistant
"""

async def load_documents_node(state: CompareState):
    """Загружает и разбивает документы на чанки через Document Server."""
    print(f"[Workflow] Loading documents: {state['input_1']} and {state['input_2']}")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # Загружаем первый документ
            resp1 = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": state['input_1']})
            resp1.raise_for_status()
            text1 = resp1.json().get("text", "")
            
            # Загружаем второй документ
            resp2 = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": state['input_2']})
            resp2.raise_for_status()
            text2 = resp2.json().get("text", "")
            
            # Разбиваем на чанки
            def dc_smart_chunk(text: str) -> List[str]:
                chunks = []
                section_pattern = r'\n(?=\d+\.(?:\d+\.)*\s+[А-ЯA])'
                sections = re.split(section_pattern, text)
                for section in sections:
                    if len(section) > 2000:
                        parts = re.split(r'\n\s*\n', section)
                        for p in parts:
                            clean = ' '.join(p.split())
                            if len(clean) > 40: chunks.append(clean)
                    else:
                        clean = ' '.join(section.split())
                        if len(clean) > 40: chunks.append(clean)
                return chunks

            return {
                "chunks_old": dc_smart_chunk(text1),
                "chunks_new": dc_smart_chunk(text2)
            }
        except Exception as e:
            return {"errors": [f"Error loading docs: {str(e)}"]}

async def match_chunks_node(state: CompareState):
    """Сопоставляет чанки через Legal Server (батчевое семантическое сходство)."""
    print(f"[Workflow] Matching batches: {len(state['chunks_old'])} vs {len(state['chunks_new'])}")
    
    if not state['chunks_old'] or not state['chunks_new']:
        return {"matches": []}

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # Вызываем новый батчевый эндпоинт
            resp = await client.post(f"{MCP_LEGAL_SERVER_URL}/match_batches", json={
                "list_old": state['chunks_old'],
                "list_new": state['chunks_new'],
                "threshold": 0.72
            })
            resp.raise_for_status()
            data = resp.json()
            
            # Фильтруем результаты (оставляем только измененные, удаленные или добавленные)
            all_matches = data.get("matches", [])
            diffs = [m for m in all_matches if m["type"] != "UNCHANGED"]
            
            print(f"   - Найдено различий: {len(diffs)}")
            return {"matches": diffs}
        except Exception as e:
            print(f"Error in match_batches workflow: {e}")
            return {"errors": [f"Matching failed: {str(e)}"]}

async def analyze_differences_node(state: CompareState):
    """Анализирует найденные различия через LLM (UMS)."""
    print(f"[Workflow] Analyzing {len(state['matches'])} differences")
    
    results = []
    critical_kw = ["обязан", "штраф", "срок", "рублей", "не вправе", "запрещено"]
    
    for i, m in enumerate(state['matches']):
        m_type = m.get('type', 'MODIFIED')
        old_txt = m.get('old_text', '')
        new_txt = m.get('new_text', '')
        
        # Если это добавление или удаление - помечаем как структурное изменение
        if m_type in ['ADDED', 'DELETED']:
            content = new_txt if m_type == 'ADDED' else old_txt
            results.append({
                "type": m_type,
                "diff": "Структурное изменение",
                "content": content
            })
            continue
            
        # Фильтрация незначительных изменений
        txt = (old_txt + new_txt).lower()
        score = m.get('score', 0)
        
        # Анализируем только если низкий скор или есть ключевые слова
        if score < 0.9 or any(k in txt for k in critical_kw):
            prompt = dc_create_prompt(old_txt, new_txt)
            
            try:
                # Используем параметры как в монолите
                payload = {"prompt": prompt, "max_tokens": 300, "temperature": 0.1, "echo": False}
                response = ums_client.infer("qwen-14b-llm", payload)
                
                # Извлекаем текст
                content = response.get("content", "")
                if not content and "choices" in response:
                    content = response["choices"][0].get("text", "")
                elif not content and "result" in response and "choices" in response["result"]:
                     content = response["result"]["choices"][0].get("text", "")

                # Используем мощный парсер
                data = parse_json_garbage(content)
                
                if data and (data.get("is_critical") or len(data.get("diff"," ")) > 5):
                    results.append({
                        "type": "MODIFIED",
                        "is_critical": data.get("is_critical"),
                        "diff": data.get("diff"),
                        "impact": data.get("impact"),
                        "old_text": old_txt, "new_text": new_txt
                    })
                else:
                    # Если LLM не вернула полезного, но изменение есть - сохраняем как есть
                    # Но только если это не пустой JSON
                    pass 

            except Exception as e:
                print(f"Error analyzing chunk {i}: {e}")
                results.append({
                    "type": "MODIFIED",
                    "is_critical": False, 
                    "diff": "Ошибка анализа", 
                    "impact": "Требуется ручной анализ",
                    "old_text": old_txt, "new_text": new_txt
                })
            
    return {"analysis_results": results}

async def generate_report_node(state: CompareState):
    """Формирует финальный Markdown отчет и сохраняет его."""
    import time
    
    report = "# Отчет о сравнении документов\n\n"
    report += f"**Дата:** {time.strftime('%Y-%m-%d %H:%M')}\n"
    report += f"**Файлы:**\n- {os.path.basename(state['input_1'])}\n- {os.path.basename(state['input_2'])}\n\n"
    report += f"**Найдено изменений:** {len(state['analysis_results'])}\n\n"
    
    for r in state['analysis_results']:
        diff_type = r.get('type', 'MODIFIED')
        
        if diff_type == 'MODIFIED':
            icon = "🔴 КРИТИЧНО" if r.get('is_critical') else "📝 ИЗМЕНЕНО"
            report += f"### {icon}\n"
            report += f"**Суть:** {r.get('diff', 'Изменение текста')}\n"
            if r.get('impact'):
                report += f"**Влияние:** {r.get('impact')}\n"
            report += f"> **Было:** {r.get('old_text', '')[:200]}...\n"
            report += f"> **Стало:** {r.get('new_text', '')[:200]}...\n\n"
            
        elif diff_type == 'ADDED':
            report += f"### ✅ ДОБАВЛЕНО\n"
            report += f"> {r.get('content', '')[:200]}...\n\n"
            
        elif diff_type == 'DELETED':
            report += f"### ❌ УДАЛЕНО\n"
            report += f"> {r.get('content', '')[:200]}...\n\n"
            
    # Сохранение в файл
    try:
        # Определяем путь к папке загрузок (общая с Open WebUI)
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        uploads_dir = os.path.join(base_dir, 'backend', 'open_webui_uploads')
        
        if not os.path.exists(uploads_dir):
            os.makedirs(uploads_dir, exist_ok=True)
            
        filename = f"Report_Compare_{{int(time.time())}}.md"
        filepath = os.path.join(uploads_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report)
            
        # Добавляем информацию о файле в конец отчета
        report += f"\n---\n**Отчет сохранен:** `{filename}`"
        
    except Exception as e:
        report += f"\n---\n**Ошибка сохранения отчета:** {e}"
        
    return {"final_report": report}

# === Build Graph ===

def create_compare_graph():
    workflow = StateGraph(CompareState)
    
    workflow.add_node("load", load_documents_node)
    workflow.add_node("match", match_chunks_node)
    workflow.add_node("analyze", analyze_differences_node)
    workflow.add_node("report", generate_report_node)
    
    workflow.set_entry_point("load")
    workflow.add_edge("load", "match")
    workflow.add_edge("match", "analyze")
    workflow.add_edge("analyze", "report")
    workflow.add_edge("report", END)
    
    return workflow.compile()