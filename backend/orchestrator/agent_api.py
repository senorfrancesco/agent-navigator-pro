"""
Agent API - Умный оркестратор на базе LangGraph.
Поддерживает микросервисную архитектуру и OpenAI-совместимый API.
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

app = FastAPI(title="Agent Navigator Pro Orchestrator", version="2.0.0")

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

async def run_workflow_stream(session_id: str, query: str, attachments: List[FileAttachment]):
    """Запускает нужный LangGraph в зависимости от контекста."""
    
    # 1. Логика выбора воркфлоу
    if len(attachments) >= 2 and any(kw in query.lower() for kw in ["сравни", "различия", "изменения"]):
        workflow = create_compare_graph()
        initial_state = {
            "input_1": attachments[0].path,
            "input_2": attachments[1].path,
            "chunks_old": [], "chunks_new": [], "matches": [],
            "analysis_results": [], "final_report": "", "errors": []
        }
        task_name = "Сравнение документов"
    elif any(kw in query.lower() for kw in ["смета", "оборудование", "тз", "закупка"]):
        workflow = create_equipment_graph()
        # Пытаемся определить где ТЗ, а где Смета по имени файла
        tz_path = next((a.path for a in attachments if "тз" in a.name.lower() or "req" in a.name.lower()), attachments[0].path if attachments else "")
        smeta_path = next((a.path for a in attachments if "смет" in a.name.lower() or "offer" in a.name.lower()), attachments[1].path if len(attachments) > 1 else "")
        
        initial_state = {
            "input_tz": tz_path,
            "input_smeta": smeta_path,
            "requirements": [], "offers": [], "matches": [],
            "final_report": "", "errors": []
        }
        task_name = "Анализ оборудования"
    else:
        # Режим простого чата (General Chat), если не выбраны спец. навыки
        yield {"type": "thought", "content": "Это общий запрос. Отвечаю как ассистент."}
        
        from services.model_manager.ums_client import ums_client
        
        # Формируем контекст (историю пока не храним в этом MVP, только текущий запрос)
        prompt = f"""<|im_start|>system
Ты — полезный AI-ассистент Agent Navigator. Ты помогаешь пользователю, отвечаешь на вопросы и поддерживаешь диалог.
Твои спец. навыки (сравнение документов, анализ смет) активируются автоматически при наличии файлов.
Сейчас файлов нет, просто ответь пользователю.
<|im_end|>
<|im_start|>user
{query}
<|im_end|>
<|im_start|>assistant
"""
        try:
            # Стримим ответ от Qwen через UMS
            # Примечание: ums_client.infer сейчас не поддерживает стриминг генератором, 
            # поэтому получим ответ целиком и отдадим как один чанк (для MVP).
            response = ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.7})
            
            # Умный парсинг ответа
            content = ""
            if isinstance(response, dict):
                if "content" in response:
                    content = response["content"]
                elif "choices" in response and len(response["choices"]) > 0:
                    choice = response["choices"][0]
                    if isinstance(choice, dict):
                        if "text" in choice:
                            content = choice["text"]
                        elif "message" in choice and "content" in choice["message"]:
                            content = choice["message"]["content"]
            elif isinstance(response, str):
                content = response
            
            content = str(content).strip()
            
            if content:
                yield {"type": "final_answer", "content": content}
            else:
                # Отладочная информация, если ответ пустой
                yield {"type": "final_answer", "content": f"Извините, я получил пустой ответ от модели. Raw: {str(response)[:200]}"}
                
        except Exception as e:
            yield {"type": "error", "content": f"Ошибка генерации: {str(e)}"}
        
        return

    yield {"type": "thought", "content": f"Запускаю процесс: {task_name}..."}

    # 2. Выполнение графа
    try:
        # LangGraph invoke или stream
        async for event in workflow.astream(initial_state):
            # Извлекаем текущий узел и его результат
            for node_name, output in event.items():
                if "final_report" in output and output["final_report"]:
                    yield {"type": "final_answer", "content": output["final_report"]}
                elif "errors" in output and output["errors"]:
                    yield {"type": "error", "content": "; ".join(output["errors"])}
                else:
                    yield {"type": "thought", "content": f"Завершено: {node_name}"}
    except Exception as e:
        yield {"type": "error", "content": f"Ошибка воркфлоу: {str(e)}"}

# === API Endpoints ===

@app.post("/agent/chat")
async def start_chat(request: ChatRequest):
    session_id = request.session_id or str(uuid.uuid4())
    sessions[session_id] = {
        "query": request.query,
        "attachments": [a.model_dump() for a in request.attachments] if request.attachments else [],
        "created_at": time.time()
    }
    return {"session_id": session_id, "status": "processing"}

@app.get("/agent/stream/{session_id}")
async def stream_agent_steps(session_id: str):
    if session_id not in sessions: raise HTTPException(status_code=404)
    session = sessions[session_id]
    
    async def event_generator():
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id})}\n\n"
        attachments = [FileAttachment(**a) for a in session.get("attachments", [])]
        
        async for step in run_workflow_stream(session_id, session["query"], attachments):
            # Добавляем timestamp и id для совместимости с фронтендом
            step["id"] = str(uuid.uuid4())
            step["timestamp"] = time.time()
            yield f"data: {json.dumps(step)}\n\n"
            await asyncio.sleep(0.01)
            
        yield f"data: {json.dumps({'type': 'end', 'session_id': session_id})}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# === OpenAI Compatible API (for Open WebUI) ===

@app.get("/v1/models")
async def list_models():
    """Эндпоинт для списка моделей (OpenAI compatible)."""
    return {
        "object": "list",
        "data": [
            {
                "id": "agent-navigator",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "agent-navigator-pro"
            }
        ]
    }

@app.post("/v1/chat/completions")
async def openai_completions(request: Request):
    print(">>> NEW REQUEST (V2.1 - Chat Enabled) <<<")
    data = await request.json()
    messages = data.get("messages", [])
    user_query = messages[-1].get("content", "") if messages else ""
    
    # В Open WebUI файлы могут приходить как URL или текст в контексте.
    # Здесь мы будем парсить контекст для поиска путей к файлам.
    
    found_files = []
    
    try:
        # ПРАВИЛЬНЫЙ ПУТЬ к монтированной папке uploads
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        WEBUI_UPLOADS_PATH = os.path.join(base_dir, 'open_webui_uploads')
        
        # 1. Ищем файлы, которые были загружены недавно (или просто есть в папке)
        if os.path.exists(WEBUI_UPLOADS_PATH):
            recent_files = []
            current_time = time.time()
            for root, dirs, files in os.walk(WEBUI_UPLOADS_PATH):
                for file in files:
                    full_path = os.path.join(root, file)
                    try:
                        # Берем файлы, измененные за последние 10 минут (600 сек)
                        if current_time - os.path.getmtime(full_path) < 600:
                            recent_files.append(full_path)
                    except OSError: pass
            
            recent_files.sort(key=os.path.getmtime, reverse=True)
            for rf in recent_files:
                found_files.append(FileAttachment(
                    name=os.path.basename(rf),
                    path=rf,
                    size=os.path.getsize(rf),
                    type="webui_upload"
                ))
    except Exception as e:
        print(f"Error scanning WebUI uploads: {e}")

    # 2. Парсинг путей из текста (если пользователь указал путь явно)
    try:
        path_pattern = r'(?:/[\w\-. /]+|\bbackend/[\w\-. /]+)'
        potential_paths = re.findall(path_pattern, user_query)
        for p in potential_paths:
            clean_path = p.strip()
            if any(f.path == clean_path for f in found_files): continue
            if "." in os.path.basename(clean_path) and os.path.exists(clean_path): 
                 found_files.append(FileAttachment(
                     name=os.path.basename(clean_path),
                     path=clean_path, size=0, type="manual_path"
                 ))
    except Exception as e:
        print(f"Error parsing paths: {e}")

    async def generate():
        # Притворяемся OpenAI стримингом
        start_chunk = {
            "choices": [{
                "delta": {"role": "assistant", "content": "Анализирую ваш запрос...\n\n"}
            }]
        }
        yield "data: " + json.dumps(start_chunk) + "\n\n"
        
        # Запускаем воркфлоу с найденными файлами
        async for step in run_workflow_stream("openai-session", user_query, found_files):
            if step["type"] == "thought":
                content = "*[Thought: " + step["content"] + "]*\n"
            elif step["type"] == "final_answer":
                content = step["content"]
            else:
                content = "\n" + step["content"] + "\n"
                
            chunk = {
                "choices": [{
                    "delta": {"content": content},
                    "finish_reason": None
                }]
            }
            yield "data: " + json.dumps(chunk) + "\n\n"
            
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

@app.get("/status")
async def get_status():
    resources = get_system_resources()
    return {
        "status": "online",
        "active_workflows": ["compare", "equipment"],
        "resources": resources
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)