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
        # Fallback на простой чат (ReAct или просто ответ)
        yield {"type": "final_answer", "content": "Я могу сравнить юридические документы или проанализировать смету. Пожалуйста, прикрепите файлы и уточните задачу."}
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

@app.post("/v1/chat/completions")
async def openai_completions(request: Request):
    data = await request.json()
    messages = data.get("messages", [])
    user_query = messages[-1].get("content", "") if messages else ""
    
    # В Open WebUI файлы могут приходить как URL или текст в контексте.
    # Здесь мы будем парсить контекст для поиска путей к файлам.
    # Для MVP: просто запускаем воркфлоу.
    
    async def generate():
        # Притворяемся OpenAI стримингом
        yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': 'Анализирую ваш запрос...\n\n'}}]})}

"
        # Запускаем воркфлоу (без аттачментов в этом примере, нужно доработать парсинг)
        async for step in run_workflow_stream("openai-session", user_query, []):
            if step["type"] == "thought":
                content = f"*[Thought: {step['content']}]*\n"
            elif step["type"] == "final_answer":
                content = step["content"]
            else:
                content = f"\n{step['content']}\n"
                
            chunk = {
                "choices": [{
                    "delta": {"content": content},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(chunk)}

"
            
        yield f"data: [DONE]

"

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