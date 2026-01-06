"""
Agent API - FastAPI wrapper для ReAct-агента с SSE стримингом.
Поддерживает сквозной стриминг генерации текста.
ИСПРАВЛЕНО: Расширен /status эндпоинт для полноценного мониторинга ресурсов.
"""

import asyncio
import json
import uuid
import time
import sys
import os
import re
import warnings
import logging
from typing import Dict, Any, Optional, List, AsyncGenerator
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    from resource_monitor import get_system_resources, get_primary_gpu_stats, check_cuda
    RESOURCE_MONITOR_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Resource monitor not available: {e}")
    RESOURCE_MONITOR_AVAILABLE = False
    
    def get_system_resources():
        return {
            "ram_total_gb": 0, "ram_used_gb": 0, "ram_free_gb": 0, "ram_percent": 0,
            "cpu_percent": 0, "cpu_count": 0,
            "cuda_available": False, "cuda_version": None, "driver_version": None,
            "vram_total_gb": 0, "vram_used_gb": 0, "vram_free_gb": 0, "gpus": []
        }
    
    def get_primary_gpu_stats():
        return {"temperature": None, "utilization": None}
    
    def check_cuda():
        return {"cuda_available": False, "cuda_version": None, "driver_version": None, "gpu_count": 0, "gpus": []}

# Конфигурация из .env
UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")
BACKEND_MODE = os.getenv("BACKEND_MODE", "llama-cpp-python")
DOC_SERVER_URL = os.getenv("DOC_SERVER_URL", "http://localhost:8001")
LEGAL_SERVER_URL = os.getenv("LEGAL_SERVER_URL", "http://localhost:8002")

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

class MCPServerInfo(BaseModel):
    name: str
    port: int
    status: str

class StatusResponse(BaseModel):
    # Ресурсы
    vram_used_gb: float
    vram_total_gb: float
    vram_free_gb: float
    ram_used_gb: float
    ram_total_gb: float
    ram_free_gb: float
    ram_percent: float
    cpu_percent: float
    cpu_count: int
    
    # CUDA
    cuda_available: bool
    cuda_version: Optional[str]
    driver_version: Optional[str]
    
    # GPU stats
    gpu_temperature: Optional[int]
    gpu_utilization: Optional[int]
    
    # Модель и бэкенд
    active_model: Optional[str]
    backend_mode: str
    queue_size: int
    
    # MCP серверы
    mcp_servers: List[MCPServerInfo]

# === Helper Functions ===

async def check_mcp_server(name: str, url: str, port: int) -> MCPServerInfo:
    """Проверка доступности MCP-сервера."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{url}/health")
            if response.status_code == 200:
                return MCPServerInfo(name=name, port=port, status="connected")
    except Exception as e:
        logger.debug(f"MCP server {name} not available: {e}")
    return MCPServerInfo(name=name, port=port, status="disconnected")


async def get_ums_status() -> Dict[str, Any]:
    """Получение статуса UMS (активная модель, очередь)."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{UMS_URL}/status")
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        logger.debug(f"UMS not available: {e}")
    return {"active_model": None, "queue_size": 0}


def clean_response(text: str) -> str:
    """
    Очистка ответа от артефактов и самодиалога.
    Удаляет служебную разметку и обрезает текст при обнаружении самодиалога.
    """
    # Удаление служебной разметки
    text = re.sub(r'```plaintext.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'```\s*```', '', text, flags=re.DOTALL)
    
    # Расширенный список стоп-паттернов для борьбы с галлюцинациями
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
        r'Твой ответ:',
        r'\n+Кстати[,\s]',
        r'\n+Также[,\s]хочу',
        r'\[INST\]',
        r'\[/INST\]',
        r'<\|im_start\|>',
        r'<\|im_end\|>',
        r'<\|endoftext\|>',
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
        "Ответ окончен.",
        "<|im_start|>",
        "<|im_end|>",
    ]
    
    # Сниженная температура по умолчанию для уменьшения галлюцинаций
    payload = {
        "prompt": prompt,
        "max_tokens": settings.get("maxTokens", 2048),
        "temperature": settings.get("temperature", 0.5),  # Снижено с 0.7
        "top_p": settings.get("top_p", 0.9),
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
        # Улучшенный системный промпт для борьбы с галлюцинациями
        prompt = f"""Ты — ассистент Agent Navigator Pro. 

ВАЖНЫЕ ПРАВИЛА:
- Ответь ТОЛЬКО на текущий запрос пользователя
- НЕ придумывай дополнительные вопросы или ответы
- НЕ имитируй диалог с пользователем
- НЕ добавляй фразы типа "Если у вас есть вопросы" или "Пожалуйста, уточните"
- НЕ используй фразы "Корректный ответ" или "Ответ окончен"
- Если ты не знаешь ответа, честно скажи "Я не знаю" или "У меня нет информации по этому вопросу"
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
- Если ты не знаешь ответа, честно скажи "Я не знаю"
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
    """Проверка работоспособности API и всех сервисов."""
    services = {
        "agent": {"status": "ok"}
    }
    
    # Проверяем UMS
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{UMS_URL}/health")
            services["ums"] = {"status": "ok" if response.status_code == 200 else "error"}
    except:
        services["ums"] = {"status": "disconnected"}
    
    # Проверяем Document Server
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{DOC_SERVER_URL}/health")
            services["document_server"] = {"status": "ok" if response.status_code == 200 else "error"}
    except:
        services["document_server"] = {"status": "disconnected"}
    
    # Проверяем Legal Server
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{LEGAL_SERVER_URL}/health")
            services["legal_server"] = {"status": "ok" if response.status_code == 200 else "error"}
    except:
        services["legal_server"] = {"status": "disconnected"}
    
    # Определяем общий статус
    all_ok = all(s.get("status") == "ok" for s in services.values())
    any_ok = any(s.get("status") == "ok" for s in services.values())
    
    overall_status = "healthy" if all_ok else ("degraded" if any_ok else "error")
    
    return {
        "status": overall_status,
        "services": services,
        "timestamp": datetime.now().isoformat(),
        "version": "1.3.0"
    }


@app.get("/status", response_model=StatusResponse)
async def get_status():
    """Получение полного статуса системы: ресурсы, модель, MCP-серверы."""
    
    # Получаем системные ресурсы
    resources = get_system_resources()
    gpu_stats = get_primary_gpu_stats()
    
    # Проверяем MCP-серверы параллельно
    mcp_checks = await asyncio.gather(
        check_mcp_server("Document", DOC_SERVER_URL, 8001),
        check_mcp_server("Legal", LEGAL_SERVER_URL, 8002),
        check_mcp_server("UMS", UMS_URL, 8090),
    )
    
    # Получаем статус UMS
    ums_status = await get_ums_status()
    
    return StatusResponse(
        # Ресурсы из resource_monitor
        vram_used_gb=resources.get("vram_used_gb", 0),
        vram_total_gb=resources.get("vram_total_gb", 0),
        vram_free_gb=resources.get("vram_free_gb", 0),
        ram_used_gb=resources.get("ram_used_gb", 0),
        ram_total_gb=resources.get("ram_total_gb", 0),
        ram_free_gb=resources.get("ram_free_gb", 0),
        ram_percent=resources.get("ram_percent", 0),
        cpu_percent=resources.get("cpu_percent", 0),
        cpu_count=resources.get("cpu_count", 0),
        
        # CUDA
        cuda_available=resources.get("cuda_available", False),
        cuda_version=resources.get("cuda_version"),
        driver_version=resources.get("driver_version"),
        
        # GPU stats от первого GPU
        gpu_temperature=gpu_stats.get("temperature"),
        gpu_utilization=gpu_stats.get("utilization"),
        
        # Модель и бэкенд
        active_model=ums_status.get("active_model"),
        backend_mode=BACKEND_MODE,
        queue_size=ums_status.get("queue_size", 0),
        
        # MCP серверы
        mcp_servers=list(mcp_checks)
    )


@app.get("/cuda")
async def get_cuda_info():
    """Получение информации о CUDA."""
    return check_cuda()


@app.get("/resources")
async def get_resources():
    """Получение детальной информации о системных ресурсах."""
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
    host = os.getenv("AGENT_API_HOST", "0.0.0.0")
    port = int(os.getenv("AGENT_API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
