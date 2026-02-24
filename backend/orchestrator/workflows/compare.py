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

BATCH_SIZE = 5  # Кол-во различий в одном LLM-вызове (batch analysis)

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
    session_id: str  # Привязка workflow к сессии (для дедупликации)

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

            chunks_old = dc_smart_chunk(text1)
            chunks_new = dc_smart_chunk(text2)
            print(f"[Workflow] Chunks: old={len(chunks_old)}, new={len(chunks_new)}")
            return {
                "chunks_old": chunks_old,
                "chunks_new": chunks_new
            }
        except Exception as e:
            return {"errors": [f"Error loading docs: {str(e)}"]}

async def match_chunks_node(state: CompareState):
    """Сопоставляет чанки через Legal Server (батчевое семантическое сходство)."""
    print(f"[Workflow] Matching batches: {len(state['chunks_old'])} vs {len(state['chunks_new'])}")
    
    if not state['chunks_old'] or not state['chunks_new']:
        return {"matches": []}

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            # Вызываем новый батчевый эндпоинт
            resp = await client.post(f"{MCP_LEGAL_SERVER_URL}/match_batches", json={
                "list_old": state['chunks_old'],
                "list_new": state['chunks_new'],
                "threshold": 0.72
            })
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "error":
                raise RuntimeError(data.get("error", "Unknown error from legal server"))

            # Фильтруем результаты (оставляем только измененные, удаленные или добавленные)
            all_matches = data.get("matches", [])
            diffs = [m for m in all_matches if m["type"] != "UNCHANGED"]

            print(f"   - Найдено различий: {len(diffs)}")
            return {"matches": diffs}
        except Exception as e:
            print(f"Error in match_batches workflow: {e}")
            return {"matches": [], "errors": [f"Matching failed: {str(e)}"]}

async def analyze_differences_node(state: CompareState):
    """Анализирует найденные различия через LLM (UMS) — batch по BATCH_SIZE штук за вызов."""
    # Ранний выход если на предыдущем шаге была ошибка
    if state.get('errors'):
        print(f"[Workflow] Skipping analyze due to errors: {state['errors']}")
        return {"analysis_results": []}

    print(f"[Workflow] Analyzing {len(state['matches'])} differences (batch_size={BATCH_SIZE})")

    results = []
    critical_kw = ["обязан", "штраф", "срок", "рублей", "не вправе", "запрещено"]

    # Разделяем на структурные (ADDED/DELETED) и требующие LLM анализа (MODIFIED)
    structural = []
    to_analyze = []
    for m in state['matches']:
        if m.get('type') in ['ADDED', 'DELETED']:
            structural.append(m)
        else:
            txt = (m.get('old_text', '') + m.get('new_text', '')).lower()
            score = m.get('similarity_score', m.get('score', 0))
            if score < 0.9 or any(k in txt for k in critical_kw):
                to_analyze.append(m)

    # Структурные изменения без LLM
    for m in structural:
        content = m.get('new_text', '') if m.get('type') == 'ADDED' else m.get('old_text', '')
        results.append({"type": m['type'], "diff": "Структурное изменение", "content": content})

    print(f"[Workflow] Structural: {len(structural)}, needs LLM: {len(to_analyze)}")

    # Batch LLM анализ: по BATCH_SIZE различий в одном промпте
    total_batches = (len(to_analyze) + BATCH_SIZE - 1) // BATCH_SIZE if to_analyze else 0
    for batch_idx, batch_start in enumerate(range(0, len(to_analyze), BATCH_SIZE)):
        batch = to_analyze[batch_start:batch_start + BATCH_SIZE]
        print(f"[Workflow] LLM batch {batch_idx+1}/{total_batches} ({len(batch)} diffs)")

        items_text = ""
        for idx, m in enumerate(batch):
            items_text += f"\n[{idx+1}] СТАРЫЙ: {truncate_text(m.get('old_text',''), 800)}\n    НОВЫЙ: {truncate_text(m.get('new_text',''), 800)}\n"

        prompt = f"""<|im_start|>system
Ты эксперт-юрист. Проанализируй {len(batch)} изменений в документе.<|im_end|>
<|im_start|>user
Для каждого из {len(batch)} изменений определи юридическую суть.
{items_text}
Ответ — JSON массив из ровно {len(batch)} объектов:
[{{"is_critical": true/false, "diff": "суть изменения", "impact": "последствия"}}]<|im_end|>
<|im_start|>assistant
"""
        try:
            payload = {"prompt": prompt, "max_tokens": 600, "temperature": 0.1, "echo": False}
            response = await ums_client.async_infer("qwen-14b-llm", payload)

            content = response.get("content", "")
            if not content and "choices" in response:
                content = response["choices"][0].get("text", "")
            elif not content and "result" in response and "choices" in response.get("result", {}):
                content = response["result"]["choices"][0].get("text", "")

            parsed = parse_json_garbage(content)
            if not isinstance(parsed, list):
                parsed = [parsed] if isinstance(parsed, dict) else []

            for idx, m in enumerate(batch):
                item_data = parsed[idx] if idx < len(parsed) else {}
                if item_data and (item_data.get("is_critical") or len(item_data.get("diff", " ")) > 5):
                    results.append({
                        "type": "MODIFIED",
                        "is_critical": item_data.get("is_critical"),
                        "diff": item_data.get("diff"),
                        "impact": item_data.get("impact"),
                        "old_text": m.get('old_text', ''),
                        "new_text": m.get('new_text', '')
                    })

        except Exception as e:
            print(f"[Workflow] Error in batch {batch_idx+1}: {e}")
            for m in batch:
                results.append({
                    "type": "MODIFIED",
                    "is_critical": False,
                    "diff": "Ошибка анализа",
                    "impact": "Требуется ручной анализ",
                    "old_text": m.get('old_text', ''),
                    "new_text": m.get('new_text', '')
                })

    return {"analysis_results": results}

async def generate_report_node(state: CompareState):
    """Формирует финальный Markdown отчет и сохраняет его."""
    import time
    import glob as glob_mod

    report = "# Отчет о сравнении документов\n\n"
    report += f"**Дата:** {time.strftime('%Y-%m-%d %H:%M')}\n"
    report += f"**Файлы:**\n- Старая версия: {os.path.basename(state['input_1'])}\n- Новая версия: {os.path.basename(state['input_2'])}\n\n"
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

    # Сохранение в файл (с проверкой дубликатов — Задача 2)
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        uploads_dir = os.path.join(base_dir, 'backend', 'open_webui_uploads')

        if not os.path.exists(uploads_dir):
            os.makedirs(uploads_dir, exist_ok=True)

        # Проверяем, не создан ли уже отчёт для этой пары файлов за последние 60 сек
        input_names = sorted([os.path.basename(state['input_1']), os.path.basename(state['input_2'])])
        now = time.time()
        current_count = len(state['analysis_results'])
        existing_reports = glob_mod.glob(os.path.join(uploads_dir, "Report_Compare_*.md"))
        for existing in existing_reports:
            if now - os.path.getmtime(existing) < 60:
                try:
                    with open(existing, "r", encoding="utf-8") as ef:
                        header = ef.read(500)
                    # Если оба имени файлов есть в заголовке — это дубль
                    if all(name in header for name in input_names):
                        # Извлекаем количество изменений из существующего отчёта
                        count_match = re.search(r'\*\*Найдено изменений:\*\*\s*(\d+)', header)
                        existing_count = int(count_match.group(1)) if count_match else 0

                        if current_count > existing_count:
                            # Новый отчёт полнее — заменяем старый
                            print(f"[Report] Replacing {os.path.basename(existing)} ({existing_count} -> {current_count} changes)")
                            os.remove(existing)
                            break
                        else:
                            # Старый отчёт не хуже — пропускаем
                            print(f"[Report] Duplicate skipped ({current_count} <= {existing_count}): {existing}")
                            report += f"\n---\n**Отчет уже сохранен:** `{os.path.basename(existing)}`"
                            return {"final_report": report}
                except Exception:
                    pass

        filename = f"Report_Compare_{int(time.time())}.md"
        filepath = os.path.join(uploads_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report)

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