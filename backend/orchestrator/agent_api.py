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

ИНСТРУКЦИЯ ДЛЯ ФАЙЛОВ:
1. Если пользователь просит проанализировать или сравнить файлы, ТЫ ДОЛЖЕН СРАЗУ ИСПОЛЬЗОВАТЬ ИНСТРУМЕНТЫ.
2. НЕ ОБЪЯСНЯЙ, как ты будешь это делать.
3. НЕ ПИШИ вводных фраз типа "Для анализа мне нужно...".
4. СРАЗУ пиши: Thought: Мне нужно загрузить файлы для анализа.
5. Затем пиши: Action: document_server.load_document(path="...")
{% endif %}

ВАЖНЫЕ ПРАВИЛА:
- Отвечай КРАТКО и ПО ДЕЛУ.
- НИКАКОЙ БОЛТОВНИ перед использованием инструментов.
- ВСЕГДА используй Markdown для оформления кода и таблиц.
- НЕ имитируй диалог и НЕ придумывай вопросы.
- Не используй эмодзи.

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

# === Tool Definitions (HTTP Clients) ===

MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
MCP_LEGAL_SERVER_URL = os.getenv("MCP_LEGAL_SERVER_URL", "http://localhost:8002")

async def document_server_load_document(path: str) -> str:
    """Загружает документ через HTTP-запрос к Document Server."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MCP_DOCUMENT_SERVER_URL}/load_document",
                json={"path": path, "extract_tables": False, "use_ocr": False},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                return data.get("text", "Document loaded but no text returned.")
            return f"Error loading document: {data.get('error', 'Unknown error')}"
    except Exception as e:
        return f"Failed to connect to Document Server: {str(e)}"

async def legal_server_compare_chunks(old_text: str, new_text: str) -> str:
    """Сравнивает два текста через HTTP-запрос к Legal Server."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MCP_LEGAL_SERVER_URL}/compare_chunks",
                json={"old_text": old_text, "new_text": new_text, "threshold": 0.72},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                return json.dumps({"differences": data.get("differences", [])})
            return json.dumps({"error": data.get("error", "Unknown error")})
    except Exception as e:
        return json.dumps({"error": str(e)})

async def legal_server_analyze_impact(differences: str) -> str:
    """Анализирует юридическую значимость различий через Legal Server."""
    try:
        if isinstance(differences, str):
            try:
                diff_data = json.loads(differences)
                differences_list = diff_data.get("differences", [])
            except:
                differences_list = []
        else:
            differences_list = differences
            
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MCP_LEGAL_SERVER_URL}/analyze_impact",
                json={"differences": differences_list},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                analysis = data.get("analysis", [])
                result = "Impact Analysis:\n"
                for item in analysis:
                    result += f"- {item.get('difference', 'Unknown')}: {'CRITICAL' if item.get('is_critical') else 'NON-CRITICAL'} - {item.get('impact_description', 'No description')}\n"
                return result
            return f"Error analyzing impact: {data.get('error', 'Unknown error')}"
    except Exception as e:
        return f"Failed to connect to Legal Server: {str(e)}"

TOOLS = {
    "document_server.load_document": document_server_load_document,
    "legal_server.compare_chunks": legal_server_compare_chunks,
    "legal_server.analyze_impact": legal_server_analyze_impact,
}

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
    """Цикл ReAct агента с поддержкой стриминга и реальным вызовом инструментов."""
    
    # Мапа для исправления blob URL
    path_map = {}
    if attachments:
        for att in attachments:
            if att.path.startswith("blob:"):
                # Пытаемся найти локальный путь в /tmp или текущей папке по имени файла
                # В реальной системе фронтенд должен присылать корректный путь после загрузки
                local_path = os.path.join("/tmp", att.name)
                if os.path.exists(local_path):
                    path_map[att.path] = local_path
                else:
                    # Если файла нет в /tmp, используем имя как есть (предполагая, что сервер знает где искать)
                    path_map[att.path] = att.name
            else:
                path_map[att.path] = att.path

    # Проверка на неопределенные запросы
    ambiguous_queries = ["что", "что?", "как", "как?", "почему", "почему?", "зачем", "зачем?", "где", "где?"]
    if query.strip().lower() in ambiguous_queries:
        yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content="Ваш запрос слишком неопределён. Пожалуйста, уточните, что именно вас интересует.", timestamp=time.time())
        return
    
    chat_history = []
    max_iterations = 5
    
    for i in range(max_iterations):
        # Формируем промпт для текущего шага
        if not chat_history:
            prompt = render_template(REACT_PROMPT_TEMPLATE, query=query, thought="", files=attachments)
        else:
            history_str = "\n".join(chat_history)
            prompt = f"{render_template(REACT_PROMPT_TEMPLATE, query=query, thought='', files=attachments)}\n{history_str}\nThought:"

        full_response = ""
        async for chunk in get_llm_stream(prompt, model_settings):
            full_response += chunk
            yield AgentStep(id=str(uuid.uuid4()), type="chunk", content=chunk, timestamp=time.time())
        
        # Парсинг ответа
        response = full_response.strip()
        
        # Извлекаем Thought
        thought = ""
        if "Thought:" in response:
            thought = response.split("Thought:")[1].split("Action:")[0].split("Final Answer:")[0].strip()
        elif not any(k in response for k in ["Action:", "Final Answer:"]):
            thought = response
            
        if thought:
            yield AgentStep(id=str(uuid.uuid4()), type="thought", content=thought, timestamp=time.time())
            chat_history.append(f"Thought: {thought}")

        # Извлекаем Action
        if "Action:" in response:
            action_str = response.split("Action:")[1].strip()
            try:
                tool_name = action_str.split("(")[0].strip()
                params_str = action_str.split("(")[1].split(")")[0]
                
                # Упрощенный парсинг параметров (key="value")
                params = {}
                for part in re.split(r',\s*(?=[a-zA-Z_][a-zA-Z0-9_]*\s*=)', params_str):
                    if '=' in part:
                        key, value = part.split('=', 1)
                        val = value.strip().strip('"').strip("'")
                        # Исправляем blob URL в параметрах
                        if val in path_map: val = path_map[val]
                        params[key.strip()] = val
                
                yield AgentStep(
                    id=str(uuid.uuid4()), 
                    type="action", 
                    content=f"Выполнение {tool_name}...", 
                    tool_name=tool_name,
                    tool_params=params,
                    timestamp=time.time()
                )
                
                # Реальный вызов инструмента
                if tool_name in TOOLS:
                    observation = await TOOLS[tool_name](**params)
                else:
                    observation = f"Error: Tool {tool_name} not found."
                
                yield AgentStep(id=str(uuid.uuid4()), type="observation", content=str(observation), timestamp=time.time())
                chat_history.append(f"Action: {tool_name}({params_str})")
                chat_history.append(f"Observation: {observation}")
                continue # Переходим к следующей итерации ReAct
            except Exception as e:
                error_msg = f"Error parsing or calling tool: {str(e)}"
                yield AgentStep(id=str(uuid.uuid4()), type="error", content=error_msg, timestamp=time.time())
                break

        # Извлекаем Final Answer
        if "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[1].strip()
            yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content=final_answer, timestamp=time.time())
            return
        
        # Если нет ни Action, ни Final Answer, но есть текст - считаем это финальным ответом
        if not "Action:" in response and not "Final Answer:" in response:
            yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content=clean_response(response), timestamp=time.time())
            return

    yield AgentStep(id=str(uuid.uuid4()), type="final_answer", content="Превышено количество итераций анализа.", timestamp=time.time())

# === API Endpoints ===

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time(), "version": "1.3.0"}

@app.get("/status")
async def get_status():
    try:
        resources = get_system_resources()
    except Exception as e:
        print(f"Error getting resources: {e}")
        resources = {}
        
    # Адаптация под формат фронтенда (StatusResponse в agentApi.ts)
    gpus = resources.get("gpus", [])
    primary_gpu = gpus[0] if gpus else {}
    
    # Гарантируем наличие всех полей с правильными типами
    return {
        "active_model": "qwen-14b-llm",
        "backend_mode": "llama-cpp-python",
        "vram_used_gb": float(resources.get("vram_used_gb", 0.0)),
        "vram_total_gb": float(resources.get("vram_total_gb", 0.0)),
        "vram_free_gb": float(resources.get("vram_free_gb", 0.0)),
        "ram_used_gb": float(resources.get("ram_used_gb", 0.0)),
        "ram_total_gb": float(resources.get("ram_total_gb", 0.0)),
        "ram_free_gb": float(resources.get("ram_free_gb", 0.0)),
        "ram_percent": float(resources.get("ram_percent", 0.0)),
        "cpu_percent": float(resources.get("cpu_percent", 0.0)),
        "cpu_count": int(resources.get("cpu_count", 0)),
        "cuda_available": bool(resources.get("cuda_available", False)),
        "cuda_version": str(resources.get("cuda_version", "")) if resources.get("cuda_version") else None,
        "driver_version": str(resources.get("driver_version", "")) if resources.get("driver_version") else None,
        "gpu_temperature": float(primary_gpu.get("temperature")) if primary_gpu.get("temperature") is not None else None,
        "gpu_utilization": float(primary_gpu.get("utilization")) if primary_gpu.get("utilization") is not None else None,
        "queue_size": 0,
        "mcp_servers": [
            {"name": "document_server", "port": 8001, "status": "connected"},
            {"name": "legal_server", "port": 8002, "status": "connected"}
        ]
    }

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
