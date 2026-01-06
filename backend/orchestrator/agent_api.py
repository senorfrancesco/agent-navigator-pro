"""
Agent API - FastAPI wrapper для ReAct-агента с SSE стримингом.
Поддерживает сквозной стриминг генерации текста.
ИСПРАВЛЕНО: Восстановлен мониторинг ресурсов и улучшена защита от галлюцинаций.
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
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx

app = FastAPI(title="Agent Navigator Pro API", version="1.2.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем путь к сервисам для мониторинга ресурсов
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'services'))
try:
    from resource_monitor import get_system_resources
except ImportError:
    def get_system_resources():
        return {"error": "Resource monitor not found"}

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

# === Helper Functions ===

def clean_response(text: str) -> str:
    """
    Очистка ответа от артефактов и самодиалога.
    Удаляет служебную разметку и обрезает текст при обнаружении самодиалога.
    """
    # Удаление служебной разметки
    text = re.sub(r'```plaintext.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'```\s*```', '', text, flags=re.DOTALL)
    
    # Обрезка при обнаружении самодиалога и галлюцинаций
    stop_patterns = [
        r'\n+(User:|Human:|Вопрос:|Понял[,\s]|Спасибо[,\s])',
        r'\n+Ответ:',
        r'\n+###',
        r'\n{3,}',
        r'\n+Если у вас есть',
        r'\n+Пожалуйста, уточните',
        r'Корректированный ответ:',
        r'Корректный ответ:',
        r'Ответ окончен\.',
        r'Прекращаю отвечать\.',
        r'Твой ответ:'
    ]
    
    # Удаление множественных пробелов и переносов ПЕРЕД обрезкой
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)

    earliest_match = len(text)
    for pattern in stop_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match and match.start() < earliest_match:
            earliest_match = match.start()
    
    if earliest_match < len(text):
        text = text[:earliest_match]
    
    return text.strip()

# === Core Logic ===

async def get_llm_stream(prompt: str, model_settings: Optional[Dict[str, Any]] = None) -> AsyncGenerator[str, None]:
    """
    Получение потокового ответа от LLM через UMS.
    """
    settings = model_settings or {}
    
    # Расширенный список стоп-токенов
    stop_sequences = [
        "Observation:",
        "Human:",
        "\n\nHuman:",
        "User:",
        "\n\nUser:",
        "Вопрос:",
        "\n\nВопрос:",
        "###",
        "\n\n\n",
        "Понял, спасибо",
        "```plaintext",
        "Если у вас есть",
        "Пожалуйста, уточните",
        "Корректный ответ:",
        "Ответ окончен."
    ]
    
    payload = {
        "prompt": prompt,
        "max_tokens": settings.get("maxTokens", 2048),
        "temperature": settings.get("temperature", 0.7),
        "frequency_penalty": settings.get("frequency_penalty", 0.5), # Увеличено для борьбы с повторами
        "presence_penalty": settings.get("presence_penalty", 0.5),   # Увеличено
        "repetition_penalty": settings.get("repetition_penalty", 1.2), # Увеличено
        "stop": stop_sequences
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
    """
    Цикл ReAct агента с поддержкой стриминга финального ответа.
    """
    
    # Проверка на неопределенные запросы
    ambiguous_queries = ["что", "что?", "как", "как?", "почему", "почему?", "зачем", "зачем?", "где", "где?"]
    if query.strip().lower() in ambiguous_queries:
        yield AgentStep(
            id=str(uuid.uuid4()), 
            type="final_answer", 
            content="Ваш запрос слишком неопределён. Пожалуйста, уточните, что именно вас интересует. Например, вы можете спросить о конкретной теме, технологии или задаче.", 
            timestamp=time.time()
        )
        return
    
    is_simple = not attachments and len(query.split()) < 10
    
    if is_simple:
        # Улучшенный системный промпт
        prompt = f"""Ты — ассистент Agent Navigator Pro. 

ВАЖНЫЕ ПРАВИЛА:
- Ответь ТОЛЬКО на текущий запрос пользователя
- НЕ придумывай дополнительные вопросы или ответы
- НЕ имитируй диалог с пользователем
- НЕ добавляй фразы типа "Если у вас есть вопросы" или "Пожалуйста, уточните"
- НЕ используй фразы "Корректный ответ" или "Ответ окончен"
- Закончи ответ сразу после того, как ответишь на вопрос
- Не используй эмодзи

Запрос пользователя: {query}

Твой ответ:"""
        
        full_content = ""
        async for chunk in get_llm_stream(prompt, model_settings):
            full_content += chunk
            yield AgentStep(id=str(uuid.uuid4()), type="chunk", content=chunk, timestamp=time.time())
        
        cleaned_content = clean_response(full_content)
        yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content=cleaned_content, timestamp=time.time())
        return

    thought = "Мне нужно проанализировать ваш запрос."
    yield AgentStep(id=str(uuid.uuid4()), type="thought", content=thought, timestamp=time.time())
    
    prompt = f"""Ты — ассистент Agent Navigator Pro.

ВАЖНЫЕ ПРАВИЛА:
- Ответь ТОЛЬКО на текущий запрос пользователя
- НЕ придумывай дополнительные вопросы или ответы
- НЕ имитируй диалог с пользователем
- Закончи ответ сразу после того, как ответишь на вопрос
- Не используй эмодзи

Запрос: {query}
Thought: {thought}

Final Answer:"""
    
    full_content = ""
    async for chunk in get_llm_stream(prompt, model_settings):
        full_content += chunk
        yield AgentStep(id=str(uuid.uuid4()), type="chunk", content=chunk, timestamp=time.time())
    
    cleaned_content = clean_response(full_content)
    yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content=cleaned_content, timestamp=time.time())

# === API Endpoints ===

@app.get("/health")
async def health():
    """Проверка работоспособности API."""
    return {"status": "ok", "timestamp": time.time(), "version": "1.2.1"}

@app.get("/status")
async def get_status():
    """Получение статуса системных ресурсов."""
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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
