"""
Agent API - FastAPI wrapper для ReAct-агента с SSE стримингом.
Поддерживает сквозной стриминг генерации текста.
ИСПРАВЛЕНО: Восстановлен мониторинг ресурсов, улучшена защита от галлюцинаций.
ДОБАВЛЕНО: Шаблонизация промптов и улучшенная передача контекста файлов (по совету помощника).
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

app = FastAPI(title="Agent Navigator Pro API", version="1.3.0")

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

# === Prompt Templates (Jinja2-like) ===

SYSTEM_PROMPT_TEMPLATE = """Ты — ассистент Agent Navigator Pro. 

{% if files %}
ТЕБЕ ДОСТУПНЫ СЛЕДУЮЩИЕ ФАЙЛЫ:
{% for file in files %}
- {{ file.name }} (Путь: {{ file.path }}, Тип: {{ file.type }})
{% endfor %}
Используй инструменты для анализа этих файлов, если это необходимо для ответа на запрос.
{% endif %}

ВАЖНЫЕ ПРАВИЛА:
- Ответь ТОЛЬКО на текущий запрос пользователя
- ВСЕГДА используй Markdown для оформления кода и таблиц
- НЕ придумывай дополнительные вопросы или ответы
- НЕ имитируй диалог с пользователем
- НЕ добавляй фразы типа "Если у вас есть вопросы" или "Пожалуйста, уточните"
- НЕ используй фразы "Корректный ответ" или "Ответ окончен"
- Закончи ответ сразу после того, как ответишь на вопрос
- Не используй эмодзи

Запрос пользователя: {{ query }}

Твой ответ:"""

REACT_PROMPT_TEMPLATE = """Ты — AI-агент для анализа документов. Твоя задача — выполнить запрос пользователя, используя доступные инструменты.

{% if files %}
ДОСТУПНЫЕ ФАЙЛЫ:
{% for file in files %}
- {{ file.name }} (Путь: {{ file.path }})
{% endfor %}
{% endif %}

ИНСТРУМЕНТЫ:
1. document_server.load_document(path: str) -> str
2. legal_server.compare_chunks(old_text: str, new_text: str) -> str
3. legal_server.analyze_impact(differences: str) -> str

ФОРМАТ ОТВЕТА:
Thought: <твоё рассуждение о следующем шаге>
Action: <tool_name(param1="value")> ИЛИ Final Answer: <ответ пользователю>

Запрос: {{ query }}
Thought: {{ thought }}

Final Answer:"""

def render_template(template: str, **kwargs) -> str:
    """Простая реализация шаблонизатора (замена Jinja2 для минимизации зависимостей)."""
    result = template
    
    # Обработка условий {% if var %} ... {% endif %}
    if "{% if files %}" in result:
        if kwargs.get("files"):
            result = result.replace("{% if files %}", "").replace("{% endif %}", "")
            # Обработка цикла {% for file in files %} ... {% endfor %}
            if "{% for file in files %}" in result:
                start_tag = "{% for file in files %}"
                end_tag = "{% endfor %}"
                start_idx = result.find(start_tag)
                end_idx = result.find(end_tag)
                loop_content = result[start_idx + len(start_tag):end_idx].strip()
                
                rendered_files = []
                for file in kwargs["files"]:
                    file_item = loop_content
                    file_item = file_item.replace("{{ file.name }}", file.name)
                    file_item = file_item.replace("{{ file.path }}", file.path)
                    file_item = file_item.replace("{{ file.type }}", file.type if hasattr(file, 'type') else "unknown")
                    rendered_files.append(file_item)
                
                result = result[:start_idx] + "\n".join(rendered_files) + result[end_idx + len(end_tag):]
        else:
            # Удаляем весь блок if, если файлов нет
            start_idx = result.find("{% if files %}")
            end_idx = result.find("{% endif %}") + len("{% endif %}")
            result = result[:start_idx] + result[end_idx:]
            
    # Простые замены переменных
    for key, value in kwargs.items():
        if not isinstance(value, list):
            result = result.replace("{{ " + key + " }}", str(value))
            
    return result.strip()

# === Helper Functions ===

def clean_response(text: str) -> str:
    """
    Очистка ответа от артефактов и самодиалога.
    """
    # Удаление служебной разметки
    text = re.sub(r'```plaintext.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'```\s*```', '', text, flags=re.DOTALL)
    
    # Исправление подсветки синтаксиса: проверка на незакрытые блоки кода
    code_blocks = text.count("```")
    if code_blocks % 2 != 0:
        text += "\n```"
    
    # Нормализация ПЕРЕД обрезкой
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    
    # Обрезка при обнаружении самодиалога и галлюцинаций
    stop_patterns = [
        r'\n+(User:|Human:|Вопрос:|Понял[,\s]|Спасибо[,\s])',
        r'\n+Ответ:',
        r'\n+###',
        r'\n+Если у вас есть',
        r'\n+Пожалуйста, уточните',
        r'Корректированный ответ:',
        r'Корректный ответ:',
        r'Ответ окончен\.',
        r'Прекращаю отвечать\.',
        r'Твой ответ:'
    ]
    
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
    """Получение потокового ответа от LLM через UMS."""
    settings = model_settings or {}
    
    stop_sequences = [
        "Observation:", "Human:", "\n\nHuman:", "User:", "\n\nUser:",
        "Вопрос:", "###", "\n\n\n", "Понял, спасибо", "```plaintext",
        "Если у вас есть", "Пожалуйста, уточните", "Корректный ответ:", "Ответ окончен."
    ]
    
    payload = {
        "prompt": prompt,
        "max_tokens": settings.get("maxTokens", 2048),
        "temperature": settings.get("temperature", 0.7),
        "frequency_penalty": settings.get("frequency_penalty", 0.5),
        "presence_penalty": settings.get("presence_penalty", 0.5),
        "repetition_penalty": settings.get("repetition_penalty", 1.2),
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
    """Цикл ReAct агента с поддержкой стриминга."""
    
    # Проверка на неопределенные запросы
    ambiguous_queries = ["что", "что?", "как", "как?", "почему", "почему?", "зачем", "зачем?", "где", "где?"]
    if query.strip().lower() in ambiguous_queries:
        yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content="Ваш запрос слишком неопределён. Пожалуйста, уточните, что именно вас интересует.", timestamp=time.time())
        return
    
    is_simple = not attachments and len(query.split()) < 10
    
    if is_simple:
        prompt = render_template(SYSTEM_PROMPT_TEMPLATE, query=query, files=attachments)
        full_content = ""
        async for chunk in get_llm_stream(prompt, model_settings):
            full_content += chunk
            yield AgentStep(id=str(uuid.uuid4()), type="chunk", content=chunk, timestamp=time.time())
        
        cleaned_content = clean_response(full_content)
        yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content=cleaned_content, timestamp=time.time())
        return

    thought = "Мне нужно проанализировать ваш запрос."
    yield AgentStep(id=str(uuid.uuid4()), type="thought", content=thought, timestamp=time.time())
    
    prompt = render_template(REACT_PROMPT_TEMPLATE, query=query, thought=thought, files=attachments)
    
    full_content = ""
    async for chunk in get_llm_stream(prompt, model_settings):
        full_content += chunk
        yield AgentStep(id=str(uuid.uuid4()), type="chunk", content=chunk, timestamp=time.time())
    
    cleaned_content = clean_response(full_content)
    yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content=cleaned_content, timestamp=time.time())

# === API Endpoints ===

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time(), "version": "1.3.0"}

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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
