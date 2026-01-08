"""
Agent API - Умный оркестратор на базе LangGraph.
Поддерживает микросервисную архитектуру, динамический список моделей и OpenAI-совместимый API.
"""

import asyncio
import json
import uuid
import time
import sys
import os
import re
import warnings
from typing import Dict, Any, Optional, List, AsyncGenerator
from dotenv import load_dotenv

# Подавление предупреждений pynvml
warnings.filterwarnings("ignore", category=FutureWarning, module="pynvml")

# Загрузка переменных окружения
load_dotenv()
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx

# Подключаем пути
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from orchestrator.workflows.compare import create_compare_graph
from orchestrator.workflows.equipment import create_equipment_graph

try:
    from services.resource_monitor import get_system_resources
except ImportError:
    def get_system_resources(): return {"error": "Resource monitor not found"}

app = FastAPI(title="Agent Navigator Pro Orchestrator", version="2.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions: Dict[str, Dict[str, Any]] = {}

# === Pydantic Models ===

class FileAttachment(BaseModel):
    name: str
    path: str
    size: int
    type: str

class ChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    attachments: Optional[List[FileAttachment]] = None
    model_settings: Optional[Dict[str, Any]] = None

# === Workflow Dispatcher ===

async def run_workflow_stream(session_id: str, query: str, attachments: List[FileAttachment], target_model: str = "agent-navigator"):
    """Запускает нужный LangGraph или прямой инференс в зависимости от контекста и модели."""
    
    # 1. Прямой доступ к модели (Direct Model Access)
    if target_model != "agent-navigator":
        yield {"type": "thought", "content": f"Использую прямую модель: {target_model}"}
        
        from services.model_manager.ums_client import ums_client
        prompt = f"<|im_start|>user\n{query}\n<|im_end|>\n<|im_start|>assistant\n"
        
        try:
            response = ums_client.infer(target_model, {"prompt": prompt, "temperature": 0.7})
            content = _extract_content(response)
            yield {"type": "final_answer", "content": content}
        except Exception as e:
            yield {"type": "error", "content": f"Ошибка инференса {target_model}: {str(e)}"}
        return

    # 2. Логика АГЕНТА (agent-navigator) - Soft ReAct Router
    query_lower = query.lower()
    is_compare_intent = any(kw in query_lower for kw in ["сравни", "различия", "изменения"])
    is_equipment_intent = any(kw in query_lower for kw in ["смета", "оборудование", "тз", "закупка"])
    file_count = len(attachments)
    
    workflow = None
    initial_state = {}
    task_name = "Чат"
    
    # Сценарий A: 2+ файла и интент -> Графы (Compare или Equipment)
    if file_count >= 2 and (is_compare_intent or is_equipment_intent):
        active_attachments = attachments[:2]
        
        if is_compare_intent:
            workflow = create_compare_graph()
            initial_state = {
                "input_1": active_attachments[0].path, "input_2": active_attachments[1].path,
                "chunks_old": [], "chunks_new": [], "matches": [],
                "analysis_results": [], "final_report": "", "errors": []
            }
            task_name = "Сравнение документов"
        elif is_equipment_intent:
            workflow = create_equipment_graph()
            tz_path = next((a.path for a in active_attachments if "тз" in a.name.lower() or "req" in a.name.lower()), active_attachments[0].path)
            smeta_path = next((a.path for a in active_attachments if "смет" in a.name.lower() or "offer" in a.name.lower()), active_attachments[1].path)
            initial_state = {
                "input_tz": tz_path, "input_smeta": smeta_path,
                "requirements": [], "offers": [], "matches": [],
                "final_report": "", "errors": []
            }
            task_name = "Анализ оборудования"

    # Сценарий B: 1 файл и интент "Смета" -> Извлечение позиций через Map-Reduce
    elif file_count == 1 and is_equipment_intent:
        yield {"type": "thought", "content": "Вижу запрос на анализ сметы, но файл один. Извлекаю данные..."}
        file_path = attachments[0].path
        MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": file_path})
                if resp.status_code == 200:
                    text = resp.json().get("text", "")
                    chunk_size = 10000
                    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
                    all_items = []
                    for i, chunk in enumerate(chunks):
                        yield {"type": "thought", "content": f"Извлекаю позиции ({i+1}/{len(chunks)})..."}
                        prompt = f"<|im_start|>system\nТы аналитик смет. Извлеки товары в формате JSON: [{{'item': '...', 'price': '...', 'qty': '...'}}]. Отвечай только JSON.\n<|im_end|>\n<|im_start|>user\n{chunk}\n<|im_end|>\n<|im_start|>assistant\n["
                        from services.model_manager.ums_client import ums_client
                        response = ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.1})
                        all_items.append(_extract_content(response))
                    
                    final_data = "".join(all_items)
                    yield {"type": "final_answer", "content": f"**Данные извлечены из сметы:**\n\n{final_data}"}
                    return
            except Exception as e:
                yield {"type": "error", "content": f"Ошибка анализа сметы: {e}"}
                return

    # Сценарий C: 1 файл и нет интента -> Глубокий анализ (Map-Reduce) / Vision
    elif file_count == 1:
        file_path = attachments[0].path
        ext = os.path.splitext(file_path)[1].lower()
        if ext in ['.jpg', '.jpeg', '.png', '.webp']:
            yield {"type": "final_answer", "content": f"Обнаружено изображение {os.path.basename(file_path)}. Vision-анализ в разработке."}
            return

        yield {"type": "thought", "content": "Запускаю глубокий анализ документа..."}
        MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": file_path})
                text = resp.json().get("text", "")
                if not text:
                    yield {"type": "final_answer", "content": "Не удалось извлечь текст из документа."}; return
                
                chunk_size = 8000
                chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
                
                if len(chunks) == 1:
                    prompt = f"<|im_start|>system\nТы аналитик. Сделай подробное резюме документа.\n<|im_end|>\n<|im_start|>user\n{text}\n<|im_end|>\n<|im_start|>assistant\n"
                    from services.model_manager.ums_client import ums_client
                    response = ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.3})
                    yield {"type": "final_answer", "content": _extract_content(response)}
                    return

                summaries = []
                for i, chunk in enumerate(chunks):
                    yield {"type": "thought", "content": f"Анализ части {i+1}/{len(chunks)}..."}
                    prompt = f"<|im_start|>system\nСделай подробный конспект фрагмента документа. Сохрани важные детали.\n<|im_end|>\n<|im_start|>user\n{chunk}\n<|im_end|>\n<|im_start|>assistant\n"
                    from services.model_manager.ums_client import ums_client
                    summaries.append(_extract_content(ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.3})))
                
                yield {"type": "thought", "content": "Финализация общего отчета..."}
                combined = "\n\n".join(summaries)
                prompt = f"<|im_start|>system\nОбъедини эти конспекты в единый связный подробный отчет.\n<|im_end|>\n<|im_start|>user\n{combined}\n<|im_end|>\n<|im_start|>assistant\n"
                from services.model_manager.ums_client import ums_client
                yield {"type": "final_answer", "content": _extract_content(ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.3}))}
                return
            except Exception as e:
                yield {"type": "error", "content": f"Ошибка: {str(e)}"}; return

    # Запуск графа (Compare/Equipment) или Чат
    if workflow:
        yield {"type": "thought", "content": f"Запускаю процесс: {task_name}..."}
        try:
            async for event in workflow.astream(initial_state):
                for node_name, output in event.items():
                    if "final_report" in output and output["final_report"]:
                        yield {"type": "final_answer", "content": output["final_report"]}
                    elif "errors" in output and output["errors"]:
                        yield {"type": "error", "content": "; ".join(output["errors"])}
                    else:
                        yield {"type": "thought", "content": f"Завершено: {node_name}"}
        except Exception as e: yield {"type": "error", "content": f"Ошибка графа: {e}"}
    else:
        from services.model_manager.ums_client import ums_client
        prompt = f"<|im_start|>system\nТы помощник Agent Navigator. Помогай пользователю.\n<|im_end|>\n<|im_start|>user\n{query}\n<|im_end|>\n<|im_start|>assistant\n"
        try:
            response = ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.7})
            yield {"type": "final_answer", "content": _extract_content(response)}
        except Exception as e: yield {"type": "error", "content": f"Ошибка генерации: {e}"}

def _extract_content(response):
    if isinstance(response, dict):
        if "content" in response: return response["content"]
        if "choices" in response and len(response["choices"]) > 0:
            choice = response["choices"][0]
            if isinstance(choice, dict):
                return choice.get("text", "") or choice.get("message", {}).get("content", "")
    return str(response).strip()

# === OpenAI Compatible API ===

@app.get("/v1/models")
def list_models():
    """Возвращает динамический список доступных GGUF моделей."""
    models = [{"id": "agent-navigator", "object": "model", "created": int(time.time()), "owned_by": "agent-navigator-pro"}]
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        models_dir = os.path.join(base_dir, 'models', 'gguf')
        if os.path.exists(models_dir):
            for root, dirs, files in os.walk(models_dir):
                for file in files:
                    if file.endswith(".gguf"):
                        mid = os.path.splitext(file)[0]
                        models.append({"id": mid, "object": "model", "created": int(os.path.getctime(os.path.join(root, file))), "owned_by": "local-fs"})
    except Exception as e: print(f"Error scanning models: {e}")
    return {"object": "list", "data": models}

@app.post("/v1/chat/completions")
async def openai_completions(request: Request):
    print(">>> NEW REQUEST (V2.2 - Multi-Model) <<<")
    data = await request.json()
    messages = data.get("messages", [])
    target_model = data.get("model", "agent-navigator")
    
    # Парсинг текста запроса
    user_query = ""
    if messages:
        content = messages[-1].get("content", "")
        if isinstance(content, str): user_query = content
        elif isinstance(content, list):
            user_query = " ".join([p.get("text", "") for p in content if p.get("type") == "text"])
    
    # Поиск файлов в папке uploads
    found_files = []
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        WEBUI_PATH = os.path.join(base_dir, 'open_webui_uploads')
        if os.path.exists(WEBUI_PATH):
            recent = []
            now = time.time()
            for root, _, files in os.walk(WEBUI_PATH):
                for f in files:
                    p = os.path.join(root, f)
                    if now - os.path.getmtime(p) < 600 and not f.startswith("Report_"):
                        recent.append(p)
            recent.sort(key=os.path.getmtime, reverse=True)
            for rf in recent:
                found_files.append(FileAttachment(name=os.path.basename(rf), path=rf, size=os.path.getsize(rf), type="webui_upload") )
    except Exception as e: print(f"Error scanning uploads: {e}")

    # Парсинг путей из текста
    try:
        path_pattern = r'(?:/[\w\-. /]+|\bbackend/[\w\-. /]+)'
        matches = re.findall(path_pattern, user_query)
        for m in matches:
            cp = m.strip()
            if cp and os.path.exists(cp) and not any(f.path == cp for f in found_files):
                found_files.insert(0, FileAttachment(name=os.path.basename(cp), path=cp, size=0, type="manual"))
    except: pass

    async def generate():
        if target_model == "agent-navigator":
            yield "data: " + json.dumps({"choices": [{"delta": {"role": "assistant", "content": "Анализирую ваш запрос...\n\n"}}]}) + "\n\n"
        
        async for step in run_workflow_stream("session", user_query, found_files, target_model):
            txt = step.get("content", "")
            if step["type"] == "thought" and target_model == "agent-navigator":
                txt = f"*[Thought: {txt}]*\n"
            
            if txt:
                chunk = {"choices": [{"delta": {"content": txt}, "finish_reason": None}]}
                yield "data: " + json.dumps(chunk) + "\n\n"
        
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
