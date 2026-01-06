"""
Agent API - FastAPI wrapper для ReAct-агента с SSE стримингом.

Endpoints:
- POST /agent/chat - отправка запроса агенту
- GET /agent/stream/{session_id} - SSE стриминг шагов
- GET /health - статус всех сервисов
- GET /status - информация о моделях и ресурсах
- GET /resources - детальная информация о системных ресурсах
- GET /cuda - проверка поддержки CUDA
"""

import asyncio
import json
import uuid
import time
import sys
import os
import re
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

# Загрузка переменных окружения из .env файла
load_dotenv()
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx

# Добавляем путь к services для импорта resource_monitor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'services'))

from resource_monitor import get_resource_monitor, get_system_resources, check_cuda

# Конфигурация сервисов
UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")
DOCUMENT_SERVER_URL = os.getenv("DOCUMENT_SERVER_URL", "http://localhost:8001")
LEGAL_SERVER_URL = os.getenv("LEGAL_SERVER_URL", "http://localhost:8002")

# Хранилище сессий (в production использовать Redis)
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


class ChatResponse(BaseModel):
    session_id: str
    status: str
    message: str


class HealthResponse(BaseModel):
    status: str
    services: Dict[str, Dict[str, Any]]
    timestamp: str


class StatusResponse(BaseModel):
    active_model: Optional[str]
    backend_mode: str
    vram_used_gb: float
    vram_total_gb: float
    vram_free_gb: float
    ram_used_gb: float
    ram_total_gb: float
    ram_free_gb: float
    ram_percent: float
    cpu_percent: float
    cpu_count: int
    cuda_available: bool
    cuda_version: Optional[str]
    driver_version: Optional[str]
    gpu_temperature: Optional[int]
    gpu_utilization: Optional[int]
    queue_size: int
    mcp_servers: List[Dict[str, Any]]


class AgentStep(BaseModel):
    id: str
    type: str  # thought, action, observation, final_answer, error
    content: str
    timestamp: float
    duration_ms: Optional[float] = None
    tool_name: Optional[str] = None
    tool_params: Optional[Dict[str, Any]] = None
    raw_json: Optional[Dict[str, Any]] = None


# === Helper Functions ===

async def check_service_health(url: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Проверка здоровья сервиса."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{url}/health")
            if response.status_code == 200:
                return {"status": "connected", "details": response.json()}
            return {"status": "error", "details": f"HTTP {response.status_code}"}
    except httpx.TimeoutException:
        return {"status": "timeout", "details": "Connection timeout"}
    except Exception as e:
        return {"status": "disconnected", "details": str(e)}


async def get_ums_status() -> Dict[str, Any]:
    """Получение статуса UMS."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{UMS_URL}/status")
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass
    return {
        "active_model": None,
        "vram_used_gb": 0,
        "vram_total_gb": 12,
        "ram_used_gb": 0,
        "ram_total_gb": 32,
        "queue_size": 0
    }


# === ReAct Agent Logic ===

TOOLS = {
    "document_server.load_document": {
        "description": "Загрузка и извлечение текста из документа (PDF, DOCX, TXT, изображения)",
        "params": {"path": "string"}
    },
    "document_server.smart_chunk": {
        "description": "Умное разбиение текста на чанки с перекрытием",
        "params": {"text": "string", "max_tokens": "int", "overlap": "int"}
    },
    "document_server.extract_tables": {
        "description": "Извлечение таблиц из PDF документа",
        "params": {"path": "string"}
    },
    "legal_server.compare_chunks": {
        "description": "Сравнение двух текстовых фрагментов",
        "params": {"old_text": "string", "new_text": "string"}
    },
    "legal_server.analyze_impact": {
        "description": "Анализ влияния различий между документами",
        "params": {"differences": "list"}
    },
    "legal_server.generate_report": {
        "description": "Генерация отчета по анализу",
        "params": {"analysis": "object", "format": "string"}
    }
}


async def call_tool(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Вызов инструмента через соответствующий MCP-сервер."""
    try:
        if tool_name.startswith("document_server."):
            endpoint = tool_name.replace("document_server.", "")
            url = f"{DOCUMENT_SERVER_URL}/{endpoint}"
        elif tool_name.startswith("legal_server."):
            endpoint = tool_name.replace("legal_server.", "")
            url = f"{LEGAL_SERVER_URL}/{endpoint}"
        else:
            return {"error": f"Unknown tool: {tool_name}"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=params)
            if response.status_code == 200:
                return {"success": True, "result": response.json()}
            return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_llm_response(prompt: str, model_settings: Optional[Dict[str, Any]] = None) -> str:
    """Получение ответа от LLM через UMS."""
    settings = model_settings or {}
    payload = {
        "prompt": prompt,
        "max_tokens": settings.get("maxTokens", 2048),
        "temperature": settings.get("temperature", 0.7),
        "stop": ["Observation:", "Human:", "\n\nHuman:"]
    }
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{UMS_URL}/infer",
                json={
                    "model_id": "qwen-14b-llm",
                    "payload": payload,
                    "device_mode": "hybrid"
                }
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "success":
                    inner_result = result.get("result", {})
                    if "choices" in inner_result:
                        return inner_result["choices"][0].get("text", inner_result["choices"][0].get("message", {}).get("content", ""))
                    return inner_result.get("text", inner_result.get("content", ""))
                return f"[UMS Error] {result.get('detail', 'Unknown error')}"
            else:
                return f"[UMS HTTP {response.status_code}] {response.text}"
    except Exception as e:
        return f"[Connection Error] Не удалось подключиться к UMS: {str(e)}"


def build_react_prompt(query: str, history: List[Dict], tools: Dict) -> str:
    """Построение промпта для ReAct агента."""
    tools_desc = "\n".join([
        f"- {name}: {info['description']}"
        for name, info in tools.items()
    ])
    
    history_text = ""
    for item in history[-10:]:
        if item["type"] == "thought":
            history_text += f"Thought: {item['content']}\n"
        elif item["type"] == "action":
            history_text += f"Action: {item['tool_name']}\nAction Input: {json.dumps(item.get('tool_params', {}), ensure_ascii=False)}\n"
        elif item["type"] == "observation":
            history_text += f"Observation: {item['content']}\n"
    
    prompt = f"""Ты — интеллектуальный ассистент Agent Navigator Pro. Твоя задача — помогать пользователю с анализом документов и юридических текстов.

ИНСТРУКЦИЯ:
1. Если запрос пользователя — это простое приветствие, светская беседа или вопрос общего характера, на который ты можешь ответить сам, используй формат 'Final Answer' сразу.
2. Если для ответа требуются инструменты (анализ файлов, сравнение текстов), используй формат ReAct.
3. Если инструментов для решения задачи нет, честно скажи об этом в 'Final Answer'.

Доступные инструменты:
{tools_desc}

Формат ответа:
Thought: <твои рассуждения>
Action: <имя_инструмента>
Action Input: <параметры в JSON>

ИЛИ если готов дать финальный ответ:
Thought: <финальные рассуждения>
Final Answer: <ответ пользователю на русском языке>

{history_text}

Запрос пользователя: {query}

Thought:"""
    
    return prompt


def parse_llm_response(response: str) -> Dict[str, Any]:
    """Парсинг ответа LLM в структурированный формат."""
    result = {
        "thought": None,
        "action": None,
        "action_input": None,
        "final_answer": None
    }
    
    # Пытаемся найти блоки через регулярные выражения для большей надежности
    thought_match = re.search(r"Thought:(.*?)(?=Action:|Final Answer:|$)", response, re.DOTALL | re.IGNORECASE)
    action_match = re.search(r"Action:(.*?)(?=Action Input:|$)", response, re.DOTALL | re.IGNORECASE)
    action_input_match = re.search(r"Action Input:(.*?)(?=Observation:|Final Answer:|$)", response, re.DOTALL | re.IGNORECASE)
    final_answer_match = re.search(r"Final Answer:(.*)", response, re.DOTALL | re.IGNORECASE)
    
    if thought_match:
        result["thought"] = thought_match.group(1).strip()
    if action_match:
        result["action"] = action_match.group(1).strip()
    if action_input_match:
        result["action_input"] = action_input_match.group(1).strip()
    if final_answer_match:
        result["final_answer"] = final_answer_match.group(1).strip()
        
    # Если ничего не распарсилось, но текст есть — считаем это финальным ответом
    if not any([result["action"], result["final_answer"]]) and response.strip():
        result["final_answer"] = response.strip()
        
    return result


async def run_react_agent(session_id: str, query: str, attachments: Optional[List[FileAttachment]] = None, model_settings: Optional[Dict[str, Any]] = None):
    """Основной цикл ReAct агента."""
    history = []
    max_steps = 5
    
    # Если есть вложения, добавляем информацию о них в первый Thought
    if attachments:
        file_info = ", ".join([f"{a.name} ({a.path})" for a in attachments])
        history.append({
            "type": "thought",
            "content": f"Пользователь предоставил файлы: {file_info}. Мне нужно проанализировать их содержимое."
        })

    for step_idx in range(max_steps):
        prompt = build_react_prompt(query, history, TOOLS)
        llm_response = await get_llm_response(prompt, model_settings)
        
        parsed = parse_llm_response(llm_response)
        
        # 1. Thought
        if parsed["thought"]:
            thought_step = AgentStep(
                id=str(uuid.uuid4()),
                type="thought",
                content=parsed["thought"],
                timestamp=time.time()
            )
            yield thought_step
            history.append({"type": "thought", "content": parsed["thought"]})

        # 2. Final Answer
        if parsed["final_answer"]:
            final_step = AgentStep(
                id=str(uuid.uuid4()),
                type="final_answer",
                content=parsed["final_answer"],
                timestamp=time.time()
            )
            yield final_step
            break

        # 3. Action
        if parsed["action"]:
            tool_name = parsed["action"]
            tool_params = {}
            
            try:
                if parsed["action_input"]:
                    # Очистка JSON от возможных артефактов разметки
                    clean_json = re.sub(r"```json\s*|\s*```", "", parsed["action_input"]).strip()
                    tool_params = json.loads(clean_json)
            except Exception as e:
                error_step = AgentStep(
                    id=str(uuid.uuid4()),
                    type="error",
                    content=f"Ошибка парсинга параметров инструмента: {str(e)}",
                    timestamp=time.time()
                )
                yield error_step
                history.append({"type": "observation", "content": f"Error parsing params: {str(e)}"})
                continue

            action_step = AgentStep(
                id=str(uuid.uuid4()),
                type="action",
                content=f"Вызываю {tool_name}...",
                timestamp=time.time(),
                tool_name=tool_name,
                tool_params=tool_params
            )
            yield action_step
            history.append({"type": "action", "tool_name": tool_name, "tool_params": tool_params})

            # Вызов инструмента
            tool_result = await call_tool(tool_name, tool_params)
            
            observation_content = ""
            if tool_result.get("success"):
                observation_content = json.dumps(tool_result["result"], ensure_ascii=False, indent=2)
            else:
                observation_content = f"Ошибка: {tool_result.get('error')}"

            observation_step = AgentStep(
                id=str(uuid.uuid4()),
                type="observation",
                content=observation_content,
                timestamp=time.time()
            )
            yield observation_step
            history.append({"type": "observation", "content": observation_content})
        
        # Если нет ни действия, ни ответа — прерываемся
        if not parsed["action"] and not parsed["final_answer"]:
            yield AgentStep(
                id=str(uuid.uuid4()),
                type="final_answer",
                content="Извините, я не смог определить следующий шаг. Попробуйте переформулировать запрос.",
                timestamp=time.time()
            )
            break


# === API Endpoints ===

@app.get("/health", response_model=HealthResponse)
async def health():
    """Проверка здоровья всех сервисов."""
    services = {
        "ums": await check_service_health(UMS_URL),
        "document_server": await check_service_health(DOCUMENT_SERVER_URL),
        "legal_server": await check_service_health(LEGAL_SERVER_URL)
    }
    
    all_connected = all(s["status"] == "connected" for s in services.values())
    
    return HealthResponse(
        status="healthy" if all_connected else "degraded",
        services=services,
        timestamp=datetime.now().isoformat()
    )


@app.get("/status", response_model=StatusResponse)
async def get_status():
    """Получение расширенного статуса системы."""
    ums_status = await get_ums_status()
    resources = get_system_resources()
    
    # Список MCP серверов
    mcp_servers = [
        {"id": "document_server", "name": "Document Processor", "url": DOCUMENT_SERVER_URL},
        {"id": "legal_server", "name": "Legal Analyzer", "url": LEGAL_SERVER_URL}
    ]
    
    # Получаем температуру и загрузку GPU (если есть)
    gpu_temp = None
    gpu_util = None
    if resources.get("gpus") and len(resources["gpus"]) > 0:
        gpu_temp = resources["gpus"][0].get("temperature")
        gpu_util = resources["gpus"][0].get("utilization")
    
    return StatusResponse(
        active_model=ums_status.get("active_model"),
        backend_mode=ums_status.get("backend_mode", "llama-cpp-python"),
        vram_used_gb=resources.get("vram_used_gb", 0),
        vram_total_gb=resources.get("vram_total_gb", 0),
        vram_free_gb=resources.get("vram_free_gb", 0),
        ram_used_gb=resources.get("ram_used_gb", 0),
        ram_total_gb=resources.get("ram_total_gb", 0),
        ram_free_gb=resources.get("ram_free_gb", 0),
        ram_percent=resources.get("ram_percent", 0),
        cpu_percent=resources.get("cpu_percent", 0),
        cpu_count=resources.get("cpu_count", 0),
        cuda_available=resources.get("cuda_available", False),
        cuda_version=resources.get("cuda_version"),
        driver_version=resources.get("driver_version"),
        gpu_temperature=gpu_temp,
        gpu_utilization=gpu_util,
        queue_size=ums_status.get("queue_size", 0),
        mcp_servers=mcp_servers
    )


@app.get("/resources")
async def get_resources():
    """Получение детальной информации о системных ресурсах."""
    return get_system_resources()


@app.get("/cuda")
async def get_cuda_info():
    """Проверка поддержки CUDA."""
    return check_cuda()


@app.post("/agent/chat", response_model=ChatResponse)
async def start_chat(request: ChatRequest, background_tasks: BackgroundTasks):
    """Начало диалога с агентом."""
    session_id = request.session_id or str(uuid.uuid4())
    
    # Создаем сессию
    sessions[session_id] = {
        "query": request.query,
        "attachments": [a.model_dump() for a in request.attachments] if request.attachments else [],
        "model_settings": request.model_settings or {},
        "steps": [],
        "status": "processing",
        "created_at": time.time()
    }
    
    return ChatResponse(
        session_id=session_id,
        status="processing",
        message="Запрос принят. Подключитесь к /agent/stream/{session_id} для получения результатов."
    )


@app.get("/agent/stream/{session_id}")
async def stream_agent_steps(session_id: str):
    """SSE стриминг шагов агента."""
    
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = sessions[session_id]
    
    async def event_generator():
        # Отправляем начальное событие
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id})}\n\n"
        
        # Запускаем агента
        attachments = [FileAttachment(**a) for a in session.get("attachments", [])]
        
        async for step in run_react_agent(
            session_id=session_id,
            query=session["query"],
            attachments=attachments if attachments else None,
            model_settings=session.get("model_settings")
        ):
            yield f"data: {step.model_dump_json()}\n\n"
            await asyncio.sleep(0.01)  # Небольшая задержка для flush
        
        # Отправляем завершающее событие
        yield f"data: {json.dumps({'type': 'end', 'session_id': session_id})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/agent/session/{session_id}")
async def get_session(session_id: str):
    """Получение информации о сессии."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return sessions[session_id]


@app.delete("/agent/session/{session_id}")
async def delete_session(session_id: str):
    """Удаление сессии."""
    if session_id in sessions:
        del sessions[session_id]
    return {"status": "deleted"}


# === App Initialization ===

app = FastAPI(title="Agent Navigator Pro API", version="1.0.0")

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
