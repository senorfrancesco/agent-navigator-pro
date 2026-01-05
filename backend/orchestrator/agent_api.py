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
from typing import Dict, Any, Optional, List
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
UMS_URL = "http://localhost:8090"
DOCUMENT_SERVER_URL = "http://localhost:8001"
LEGAL_SERVER_URL = "http://localhost:8002"

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
    type: str  # thought, action, observation, final_answer
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
                return result.get("text", result.get("content", ""))
    except Exception as e:
        # Fallback: возвращаем демо-ответ для тестирования UI
        return f"[Demo mode] Не удалось подключиться к UMS: {str(e)}"
    
    return "[Error] Не удалось получить ответ от LLM"


def build_react_prompt(query: str, history: List[Dict], tools: Dict) -> str:
    """Построение промпта для ReAct агента."""
    tools_desc = "\n".join([
        f"- {name}: {info['description']}"
        for name, info in tools.items()
    ])
    
    history_text = ""
    for item in history[-10:]:  # Последние 10 элементов истории
        if item["type"] == "thought":
            history_text += f"Thought: {item['content']}\n"
        elif item["type"] == "action":
            history_text += f"Action: {item['tool_name']}\nAction Input: {json.dumps(item.get('tool_params', {}), ensure_ascii=False)}\n"
        elif item["type"] == "observation":
            history_text += f"Observation: {item['content']}\n"
    
    prompt = f"""Ты — интеллектуальный ассистент с доступом к инструментам. Используй формат ReAct:

Доступные инструменты:
{tools_desc}

Формат ответа:
Thought: <твои рассуждения>
Action: <имя_инструмента>
Action Input: <параметры в JSON>

ИЛИ если готов дать финальный ответ:
Thought: <финальные рассуждения>
Final Answer: <ответ пользователю>

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
    
    lines = response.strip().split("\n")
    current_key = None
    current_value = []
    
    for line in lines:
        line_lower = line.lower().strip()
        
        if line_lower.startswith("thought:"):
            if current_key and current_value:
                result[current_key] = "\n".join(current_value).strip()
            current_key = "thought"
            current_value = [line.split(":", 1)[1].strip() if ":" in line else ""]
        elif line_lower.startswith("action:"):
            if current_key and current_value:
                result[current_key] = "\n".join(current_value).strip()
            current_key = "action"
            current_value = [line.split(":", 1)[1].strip() if ":" in line else ""]
        elif line_lower.startswith("action input:"):
            if current_key and current_value:
                result[current_key] = "\n".join(current_value).strip()
            current_key = "action_input"
            current_value = [line.split(":", 1)[1].strip() if ":" in line else ""]
        elif line_lower.startswith("final answer:"):
            if current_key and current_value:
                result[current_key] = "\n".join(current_value).strip()
            current_key = "final_answer"
            current_value = [line.split(":", 1)[1].strip() if ":" in line else ""]
        else:
            current_value.append(line)
    
    if current_key and current_value:
        result[current_key] = "\n".join(current_value).strip()
    
    # Парсинг action_input как JSON
    if result["action_input"]:
        try:
            result["action_input"] = json.loads(result["action_input"])
        except json.JSONDecodeError:
            pass
    
    return result


async def run_react_agent(
    session_id: str,
    query: str,
    attachments: Optional[List[FileAttachment]] = None,
    model_settings: Optional[Dict[str, Any]] = None,
    max_iterations: int = 10
):
    """Запуск ReAct агента с генерацией шагов."""
    
    history: List[Dict] = []
    
    # Добавляем информацию о вложениях в запрос
    if attachments:
        files_info = ", ".join([f"{a.name} ({a.path})" for a in attachments])
        query = f"{query}\n\nПрикрепленные файлы: {files_info}"
    
    for iteration in range(max_iterations):
        # Генерируем промпт
        prompt = build_react_prompt(query, history, TOOLS)
        
        # Получаем ответ LLM
        start_time = time.time()
        llm_response = await get_llm_response(prompt, model_settings)
        duration_ms = (time.time() - start_time) * 1000
        
        # Парсим ответ
        parsed = parse_llm_response(llm_response)
        
        # Генерируем Thought step
        if parsed["thought"]:
            thought_step = AgentStep(
                id=str(uuid.uuid4()),
                type="thought",
                content=parsed["thought"],
                timestamp=time.time(),
                duration_ms=duration_ms
            )
            history.append({"type": "thought", "content": parsed["thought"]})
            
            # Сохраняем в сессию и отправляем
            if session_id in sessions:
                sessions[session_id]["steps"].append(thought_step.model_dump())
            
            yield thought_step
        
        # Проверяем на финальный ответ
        if parsed["final_answer"]:
            final_step = AgentStep(
                id=str(uuid.uuid4()),
                type="final_answer",
                content=parsed["final_answer"],
                timestamp=time.time()
            )
            
            if session_id in sessions:
                sessions[session_id]["steps"].append(final_step.model_dump())
                sessions[session_id]["status"] = "completed"
            
            yield final_step
            return
        
        # Выполняем action если есть
        if parsed["action"]:
            action_step = AgentStep(
                id=str(uuid.uuid4()),
                type="action",
                content=f"Вызов {parsed['action']}",
                timestamp=time.time(),
                tool_name=parsed["action"],
                tool_params=parsed["action_input"] if isinstance(parsed["action_input"], dict) else {}
            )
            history.append({
                "type": "action",
                "tool_name": parsed["action"],
                "tool_params": action_step.tool_params
            })
            
            if session_id in sessions:
                sessions[session_id]["steps"].append(action_step.model_dump())
            
            yield action_step
            
            # Вызываем инструмент
            start_time = time.time()
            tool_result = await call_tool(parsed["action"], action_step.tool_params)
            duration_ms = (time.time() - start_time) * 1000
            
            # Генерируем Observation
            observation_content = json.dumps(tool_result, ensure_ascii=False, indent=2)
            observation_step = AgentStep(
                id=str(uuid.uuid4()),
                type="observation",
                content=observation_content[:500] + "..." if len(observation_content) > 500 else observation_content,
                timestamp=time.time(),
                duration_ms=duration_ms,
                raw_json=tool_result
            )
            history.append({"type": "observation", "content": observation_content})
            
            if session_id in sessions:
                sessions[session_id]["steps"].append(observation_step.model_dump())
            
            yield observation_step
    
    # Превышено максимальное количество итераций
    error_step = AgentStep(
        id=str(uuid.uuid4()),
        type="final_answer",
        content="Превышено максимальное количество итераций. Пожалуйста, уточните запрос.",
        timestamp=time.time()
    )
    
    if session_id in sessions:
        sessions[session_id]["steps"].append(error_step.model_dump())
        sessions[session_id]["status"] = "error"
    
    yield error_step


# === FastAPI Application ===

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager для приложения."""
    print("🚀 Agent API запущен")
    yield
    print("👋 Agent API остановлен")


app = FastAPI(
    title="ReAct Agent API",
    description="API для взаимодействия с ReAct агентом",
    version="1.0.0",
    lifespan=lifespan
)

# CORS для доступа из UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Проверка здоровья всех сервисов."""
    services = {
        "agent": {"status": "connected"},
        "ums": await check_service_health(UMS_URL),
        "document_server": await check_service_health(DOCUMENT_SERVER_URL),
        "legal_server": await check_service_health(LEGAL_SERVER_URL),
    }
    
    overall_status = "healthy" if all(
        s.get("status") == "connected" for s in services.values()
    ) else "degraded"
    
    return HealthResponse(
        status=overall_status,
        services=services,
        timestamp=datetime.now().isoformat()
    )


@app.get("/status", response_model=StatusResponse)
async def get_status():
    """Получение статуса системы с реальными ресурсами."""
    # Получаем реальные ресурсы через resource_monitor
    resources = get_system_resources()
    
    # Получаем статус UMS (активная модель и т.д.)
    ums_status = await get_ums_status()
    
    # Проверяем MCP серверы
    mcp_servers = [
        {"name": "Document Server", "port": 8001, **await check_service_health(DOCUMENT_SERVER_URL)},
        {"name": "Legal Server", "port": 8002, **await check_service_health(LEGAL_SERVER_URL)},
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
