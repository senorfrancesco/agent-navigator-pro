"""
Agent API - FastAPI wrapper для ReAct-агента с SSE стримингом.
Поддерживает сквозной стриминг генерации текста.
"""

import asyncio
import json
import uuid
import time
import sys
import os
import re
from typing import Dict, Any, Optional, List, AsyncGenerator
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx

app = FastAPI(title="Agent Navigator Pro API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'services'))
from resource_monitor import get_system_resources

UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")
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

class AgentStep(BaseModel):
    id: str
    type: str  # thought, action, observation, final_answer, error, chunk
    content: str
    timestamp: float
    tool_name: Optional[str] = None
    tool_params: Optional[Dict[str, Any]] = None

    def to_json(self) -> str:
        if hasattr(self, "model_dump_json"): return self.model_dump_json()
        return self.json()

# === Core Logic ===

async def get_llm_stream(prompt: str, model_settings: Optional[Dict[str, Any]] = None) -> AsyncGenerator[str, None]:
    """Получение потокового ответа от LLM через UMS."""
    settings = model_settings or {}
    payload = {
        "prompt": prompt,
        "max_tokens": settings.get("maxTokens", 2048),
        "temperature": settings.get("temperature", 0.7),
        "stop": ["Observation:", "Human:", "\n\nHuman:"]
    }
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", 
                f"{UMS_URL}/infer", 
                json={"model_id": "qwen-14b-llm", "payload": payload, "stream": True}
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]": break
                        try:
                            data = json.loads(data_str)
                            # Обработка формата llama-server
                            chunk = ""
                            if "choices" in data:
                                chunk = data["choices"][0].get("text", data["choices"][0].get("delta", {}).get("content", ""))
                            elif "content" in data:
                                chunk = data["content"]
                            
                            if chunk: yield chunk
                        except: continue
    except Exception as e:
        yield f"\n[Ошибка стриминга: {str(e)}]\n"

async def run_react_agent(session_id: str, query: str, attachments: Optional[List[FileAttachment]] = None, model_settings: Optional[Dict[str, Any]] = None):
    """Цикл ReAct агента с поддержкой стриминга финального ответа."""
    history = []
    
    # Простейшая классификация: если нет вложений и запрос короткий - отвечаем сразу
    is_simple = not attachments and len(query.split()) < 10
    
    if is_simple:
        # Стриминг прямого ответа
        prompt = f"Ты — ассистент Agent Navigator Pro. Ответь на запрос пользователя: {query}\n\nОтвет:"
        full_content = ""
        async for chunk in get_llm_stream(prompt, model_settings):
            full_content += chunk
            yield AgentStep(id=str(uuid.uuid4()), type="chunk", content=chunk, timestamp=time.time())
        
        yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content=full_content, timestamp=time.time())
        return

    # ReAct цикл (упрощенно для примера стриминга)
    # В реальности здесь будет логика Thought -> Action -> Observation
    thought = "Мне нужно проанализировать ваш запрос."
    yield AgentStep(id=str(uuid.uuid4()), type="thought", content=thought, timestamp=time.time())
    
    prompt = f"Запрос: {query}\nThought: {thought}\nFinal Answer:"
    full_content = ""
    async for chunk in get_llm_stream(prompt, model_settings):
        full_content += chunk
        yield AgentStep(id=str(uuid.uuid4()), type="chunk", content=chunk, timestamp=time.time())
    
    yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content=full_content, timestamp=time.time())

# === API Endpoints ===

@app.get("/status")
async def get_status():
    return get_system_resources()

@app.post("/agent/chat")
async def start_chat(request: ChatRequest):
    session_id = request.session_id or str(uuid.uuid4())
    sessions[session_id] = {
        "query": request.query,
        "attachments": [a.model_dump() for a in request.attachments] if request.attachments else [],
        "model_settings": request.model_settings or {},
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
        async for step in run_react_agent(
            session_id=session_id,
            query=session["query"],
            attachments=attachments if attachments else None,
            model_settings=session.get("model_settings")
        ):
            yield f"data: {step.to_json()}\n\n"
            await asyncio.sleep(0.01)
        
        yield f"data: {json.dumps({'type': 'end', 'session_id': session_id})}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
