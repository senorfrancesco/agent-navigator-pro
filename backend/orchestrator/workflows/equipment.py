"""
Workflow: Analyze Equipment (Equipment vs Specs)
Логика анализа смет и ТЗ с использованием Map-Reduce (постраничное извлечение).
"""

import json
import os
import re
import httpx
from typing import TypedDict, List, Dict, Any, Annotated, Optional
import operator
from langgraph.graph import StateGraph, END

# Настройки URL серверов
MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")

try:
    from services.model_manager.ums_client import ums_client
except ImportError:
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
    from services.model_manager.ums_client import ums_client

# === State Definition ===

class EquipmentState(TypedDict):
    input_tz: str  # Путь к ТЗ
    input_smeta: str # Путь к смете
    requirements: List[Dict[str, Any]]
    offers: List[Dict[str, Any]]
    matches: List[Dict[str, Any]]
    final_report: str
    errors: List[str]

# === Nodes ===

async def extract_data_map_node(state: EquipmentState):
    """
    MAP phase: Постраничное извлечение данных.
    Итерируется по страницам ТЗ и Сметы, извлекая структурированные данные.
    """
    print(f"[Workflow] Extraction phase (Map-Reduce started)")
    
    all_requirements = []
    all_offers = []
    
    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            # 1. Обработка ТЗ
            print(f"   - Обработка ТЗ: {state['input_tz']}")
            resp_tz = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_pages", json={"path": state['input_tz']})
            pages_tz = resp_tz.json().get("pages", [])
            
            for i, page_text in enumerate(pages_tz):
                if not page_text.strip(): continue
                print(f"     [ТЗ] Страница {i+1}/{len(pages_tz)}")
                
                prompt = f"""Ты — аналитик закупок. Твоя задача: извлечь перечень закупаемого оборудования из фрагмента ТЗ.
ОТВЕТЬ ТОЛЬКО JSON МАССИВОМ ОБЪЕКТОВ.
Формат: [{{"item": "название товара", "specs": "технические требования"}}]
Если на странице нет товаров, верни [].

Текст страницы:
{page_text}"""
                
                res = ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.1})
                # Упрощенный парсинг (в реальности нужен regex для извлечения JSON блока)
                try:
                    content = res.get("content", "[]")
                    # Ищем JSON блок
                    json_match = re.search(r'[[.*]]', content, re.DOTALL)
                    if json_match:
                        items = json.loads(json_match.group())
                        all_requirements.extend(items)
                except: continue

            # 2. Обработка Сметы
            print(f"   - Обработка Сметы: {state['input_smeta']}")
            resp_sm = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_pages", json={"path": state['input_smeta']})
            pages_sm = resp_sm.json().get("pages", [])
            
            for i, page_text in enumerate(pages_sm):
                if not page_text.strip(): continue
                print(f"     [Смета] Страница {i+1}/{len(pages_sm)}")
                
                prompt = f"""Извлеки товары из сметы. 
ОТВЕТЬ ТОЛЬКО JSON МАССИВОМ.
Формат: [{{"name": "полное наименование", "price": "цена", "specs": "характеристики"}}]
Если товаров нет, верни [].

Текст:
{page_text}"""
                
                res = ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.1})
                try:
                    content = res.get("content", "[]")
                    json_match = re.search(r'[[.*]]', content, re.DOTALL)
                    if json_match:
                        items = json.loads(json_match.group())
                        all_offers.extend(items)
                except: continue
                
        except Exception as e:
            print(f"Error in Map-Reduce: {e}")
            return {"errors": [f"Extraction failed: {str(e)}"]}
            
    print(f"   ✅ Извлечено: требований={len(all_requirements)}, предложений={len(all_offers)}")
    return {"requirements": all_requirements, "offers": all_offers}

async def match_and_evaluate_node(state: EquipmentState):
    """
    REDUCE phase: Сопоставление и оценка.
    """
    print(f"[Workflow] Matching and evaluating {len(state['requirements'])} items")
    
    matches = []
    # Для каждого требования ищем лучший товар (упрощенно)
    for req in state['requirements']:
        # В реальности здесь вызов эмбеддингов LaBSE через UMS
        # И проверка соответствия через LLM
        prompt_eval = f"""Сравни требование и предложение. Подходит ли товар?
ТРЕБОВАНИЕ: {json.dumps(req)}
ПРЕДЛОЖЕНИЕ: {json.dumps(state['offers'][0] if state['offers'] else {{}})}
Ответи JSON: {{"pass": true/false, "reason": "почему"}}"""
        
        try:
            res_eval = ums_client.infer("qwen-14b-llm", {"prompt": prompt_eval, "temperature": 0.1})
            # ИСПРАВЛЕНО: Безопасное получение контента с простым дефолтным JSON
            content_str = res_eval.get("content", '{"pass": false}')
            eval_data = json.loads(content_str)
            
            matches.append({
                "requirement": req,
                "offer": state['offers'][0] if state['offers'] else None,
                "status": "OK" if eval_data.get("pass") else "FAIL",
                "reason": eval_data.get("reason", "")
            })
        except:
            continue
            
    return {"matches": matches}

async def generate_equipment_report_node(state: EquipmentState):
    """Формирует итоговый отчет-сравнение и сохраняет его в файл."""
    import time
    
    report = "# Анализ соответствия оборудования\n\n"
    report += f"**Дата:** {time.strftime('%Y-%m-%d %H:%M')}\n"
    report += f"**ТЗ:** {os.path.basename(state['input_tz'])}\n"
    report += f"**Смета:** {os.path.basename(state['input_smeta'])}\n\n"
    
    report += "| Статус | Требование | Предложение | Примечание |\n"
    report += "| :--- | :--- | :--- | :--- |\n"
    
    for m in state['matches']:
        icon = "✅" if m['status'] == "OK" else "❌"
        report += f"| {icon} | {m['requirement'].get('item')} | {m['offer'].get('name') if m['offer'] else '-'} | {m['reason']} |\n"
        
    # Сохранение в файл
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        uploads_dir = os.path.join(base_dir, 'backend', 'open_webui_uploads')
        os.makedirs(uploads_dir, exist_ok=True)
            
        filename = f"Report_Equipment_{int(time.time())}.md"
        filepath = os.path.join(uploads_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report)
            
        report += f"\n\n---\n**Отчет сохранен:** `{filename}`"
    except Exception as e:
        report += f"\n\n---\n**Ошибка сохранения:** {e}"
        
    return {"final_report": report}

# === Build Graph ===

def create_equipment_graph():
    workflow = StateGraph(EquipmentState)
    
    workflow.add_node("extract", extract_data_map_node)
    workflow.add_node("evaluate", match_and_evaluate_node)
    workflow.add_node("report", generate_equipment_report_node)
    
    workflow.set_entry_point("extract")
    workflow.add_edge("extract", "evaluate")
    workflow.add_edge("evaluate", "report")
    workflow.add_edge("report", END)
    
    return workflow.compile()