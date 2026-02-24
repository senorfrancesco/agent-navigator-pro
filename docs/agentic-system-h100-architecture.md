# Архитектура агентной системы на 8×H100

> Руководство по построению локальной агентной системы уровня Claude Code / ChatGPT
> на базе 8×NVIDIA H100 (640GB VRAM, ~1TB RAM).

---

## Оглавление

1. [Обзор и цели](#1-обзор-и-цели)
2. [Аппаратная платформа](#2-аппаратная-платформа)
3. [Выбор модели](#3-выбор-модели)
4. [Inference-сервер](#4-inference-сервер)
5. [Агентный Runtime (ReAct Loop)](#5-агентный-runtime-react-loop)
6. [Tool Use и Function Calling](#6-tool-use-и-function-calling)
7. [Многоагентная система](#7-многоагентная-система)
8. [RAG и память](#8-rag-и-память)
9. [Оркестрация и графы](#9-оркестрация-и-графы)
10. [UI и интерфейсы](#10-ui-и-интерфейсы)
11. [Масштабирование и продакшен](#11-масштабирование-и-продакшен)
12. [Эталонная архитектура](#12-эталонная-архитектура)
13. [Поэтапный план запуска](#13-поэтапный-план-запуска)
14. [Стоимость](#14-стоимость)
15. [Сравнение с Agent Navigator Pro (2×RTX 2070)](#15-сравнение-с-agent-navigator-pro-2rtx-2070)

---

## 1. Обзор и цели

### Что строим

Локальную агентную систему, которая:
- Принимает задачу от пользователя на естественном языке
- Сама планирует последовательность шагов
- Вызывает инструменты (bash, файлы, API, базы данных, поиск)
- Анализирует результаты и решает, что делать дальше
- Поддерживает многоагентность (параллельные под-задачи)
- Работает полностью локально — данные не покидают инфраструктуру

### Аналоги в продакшене

| Система | Компания | Модель | Архитектура |
|---------|----------|--------|-------------|
| Claude Code | Anthropic | Claude Opus/Sonnet | ReAct loop + Tools + Sub-agents |
| ChatGPT + Code Interpreter | OpenAI | GPT-4o | ReAct loop + Sandbox |
| Devin | Cognition | Fine-tuned | Multi-agent + Planning |
| Cursor Agent | Cursor | Claude/GPT | ReAct loop + LSP |

### Принципиальная архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                        UI Layer                              │
│         (Open WebUI / Custom Web / CLI / API)                │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                    Agent Runtime                             │
│                                                              │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐     │
│  │ ReAct    │  │ Multi-Agent  │  │ Memory / State     │     │
│  │ Loop     │  │ Orchestrator │  │ Manager            │     │
│  └────┬─────┘  └──────┬───────┘  └────────┬───────────┘     │
│       │               │                   │                  │
│  ┌────▼───────────────▼───────────────────▼──────────────┐  │
│  │                  Tool Registry                         │  │
│  │  [bash] [read] [write] [search] [sql] [web] [mcp...]  │  │
│  └────────────────────────┬──────────────────────────────┘  │
└───────────────────────────┼──────────────────────────────────┘
                            │
                   OpenAI-compatible API
                            │
┌───────────────────────────▼──────────────────────────────────┐
│                  Inference Server                             │
│                                                               │
│  vLLM / SGLang / TensorRT-LLM                                │
│  DeepSeek-V3-671B / Llama-405B / Qwen-72B                   │
│  Tensor Parallel: 8×H100 (NVLink/NVSwitch)                  │
│  PagedAttention + Continuous Batching                         │
│  Throughput: 50-100 tok/s gen, 10k tok/s prefill              │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Аппаратная платформа

### Минимальная конфигурация

| Компонент | Спецификация | Назначение |
|-----------|-------------|------------|
| GPU | 8× NVIDIA H100 80GB SXM5 | LLM inference, tensor parallel |
| CPU | 2× AMD EPYC 9654 (96 cores) или Intel Xeon w9-3595X | Preprocessing, tool execution |
| RAM | 1 TB DDR5 ECC | KV cache overflow, RAG индексы |
| NVLink | NVSwitch (900 GB/s bisection) | GPU-GPU communication для tensor parallel |
| Storage | 4 TB NVMe SSD (RAID 0) | Модели, данные, логи |
| Network | 100 GbE или InfiniBand | Если multi-node |

### Варианты серверных платформ

| Платформа | GPU | Цена (покупка) | Цена (аренда/мес) |
|-----------|-----|----------------|-------------------|
| NVIDIA DGX H100 | 8×H100 SXM | ~$300k | — |
| Supermicro AS-8125GS-TNHR | 8×H100 SXM | ~$250k | — |
| Lambda Cloud | 8×H100 SXM | — | ~$18-20k |
| RunPod | 8×H100 SXM | — | ~$15-18k |
| Vast.ai | 8×H100 (varies) | — | ~$10-15k |
| Yandex Cloud (GPU) | 8×A100/H100 | — | Запросить |

### Важные нюансы

**NVLink vs PCIe:**
- H100 SXM (NVLink) — 900 GB/s между GPU. Обязательно для tensor parallel на 8 GPU.
- H100 PCIe — 128 GB/s. Будет бутылочное горлышко при TP=8. Максимум TP=2-4.

**Cooling:**
- H100 SXM потребляет 700W per GPU → 5.6 kW только GPU → ~8-10 kW total.
- Требуется серверная стойка с адекватным охлаждением или liquid cooling.

---

## 3. Выбор модели

### Карта моделей по размеру и возможностям

| Модель | Параметры | VRAM (FP16) | VRAM (FP8/INT8) | Tool Use | Агентность | Язык |
|--------|-----------|-------------|-----------------|----------|-----------|------|
| Qwen2.5-72B-Instruct | 72B | 144 GB | 72 GB | Хороший | Средняя | Мультиязычный |
| Llama-3.1-405B-Instruct | 405B | 810 GB | 405 GB | Хороший | Высокая | EN, средний RU |
| DeepSeek-V3 | 671B (MoE, 37B active) | ~350 GB | ~180 GB | Отличный | Высокая | Мультиязычный |
| DeepSeek-R1 | 671B (MoE) | ~350 GB | ~180 GB | Отличный | Очень высокая (reasoning) | Мультиязычный |
| Qwen3-235B-A22B (MoE) | 235B (22B active) | ~470 GB | ~235 GB | Отличный | Высокая | Мультиязычный |
| Mistral Large 2 | 123B | 246 GB | 123 GB | Хороший | Средняя | Мультиязычный |

### Рекомендация для 8×H100 (640GB)

**Основная модель (агент):** DeepSeek-V3-671B в FP8
- 37B активных параметров (MoE) → быстрый inference
- ~180-200 GB VRAM → помещается с запасом
- Отличный tool use, coding, рассуждения
- Хороший русский язык

**Запасная:** Qwen2.5-72B-Instruct в FP16
- Помещается на 2×H100 (144 GB)
- Оставляет 6×H100 свободных для параллельных задач
- Отличный русский язык

**Эмбеддинги (RAG):** отдельная модель
- `intfloat/multilingual-e5-large-instruct` (560M) — GPU не нужен отдельный
- Или `BAAI/bge-m3` — мультиязычный, dense + sparse
- Можно оставить на CPU или выделить 1 GPU

### Квантизация

| Формат | Качество | Размер (405B) | Скорость |
|--------|----------|---------------|----------|
| FP16 | 100% | 810 GB | Базовая |
| FP8 (E4M3) | ~99.5% | 405 GB | +20-40% |
| INT8 (W8A8) | ~99% | 405 GB | +20-30% |
| GPTQ-INT4 | ~97% | 203 GB | +50-80% |
| AWQ-INT4 | ~97.5% | 203 GB | +50-80% |
| GGUF Q4_K_M | ~96% | 203 GB | llama.cpp only |

**Рекомендация:** FP8 — минимальная потеря качества, значительный выигрыш в скорости и VRAM.

---

## 4. Inference-сервер

### Сравнение движков

| Движок | Tensor Parallel | Continuous Batching | PagedAttention | Tool Use | Production-ready |
|--------|----------------|---------------------|----------------|----------|-----------------|
| **vLLM** | ✅ до 8 GPU | ✅ | ✅ v2 | ✅ | ✅ |
| **SGLang** | ✅ до 8 GPU | ✅ | ✅ | ✅ | ✅ |
| **TensorRT-LLM** | ✅ до 8 GPU | ✅ | ✅ | ✅ | ✅ (NVIDIA) |
| llama.cpp | ✅ до 8 GPU | ❌ (4 slots) | ❌ | Ограниченно | Десктоп |
| Ollama | ❌ (1 GPU) | ❌ | ❌ | ❌ | Десктоп |

**Рекомендация:** vLLM или SGLang. Для NVIDIA-only стека — TensorRT-LLM.

### vLLM — запуск DeepSeek-V3 на 8×H100

```bash
# Установка
pip install vllm

# Запуск с tensor parallel на 8 GPU
python -m vllm.entrypoints.openai.api_server \
    --model deepseek-ai/DeepSeek-V3 \
    --tensor-parallel-size 8 \
    --dtype float16 \
    --quantization fp8 \
    --max-model-len 65536 \
    --gpu-memory-utilization 0.90 \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    --host 0.0.0.0 \
    --port 8000
```

Ключевые параметры:
- `--tensor-parallel-size 8` — распределить модель по всем GPU
- `--enable-auto-tool-choice` — включить native function calling
- `--tool-call-parser hermes` — формат tool calls (DeepSeek/Qwen используют hermes)
- `--max-model-len 65536` — контекстное окно (можно до 128k для DeepSeek-V3)
- `--gpu-memory-utilization 0.90` — использовать 90% VRAM

### SGLang — альтернатива (часто быстрее для MoE)

```bash
pip install sglang[all]

python -m sglang.launch_server \
    --model deepseek-ai/DeepSeek-V3 \
    --tp 8 \
    --dtype float16 \
    --quantization fp8 \
    --context-length 65536 \
    --host 0.0.0.0 \
    --port 8000
```

### OpenAI-compatible API

Оба движка предоставляют стандартный API:

```
POST /v1/chat/completions
POST /v1/completions
POST /v1/embeddings
GET  /v1/models
```

Это значит, что **любой** клиент, работающий с OpenAI API — работает и с локальным сервером. Включая Open WebUI, LangChain, LangGraph, и собственный Agent Runtime.

### Производительность (ожидаемая)

| Метрика | DeepSeek-V3 (8×H100, FP8) | Llama-405B (8×H100, FP8) |
|---------|---------------------------|--------------------------|
| Time to first token | ~0.5-1s | ~1-2s |
| Generation speed | 50-80 tok/s | 30-50 tok/s |
| Prefill speed | 5,000-10,000 tok/s | 3,000-5,000 tok/s |
| Concurrent users | 20-50 | 10-30 |
| Context window | 128k tokens | 128k tokens |

Для сравнения: Qwen-14B-Q4 на 2×RTX 2070 даёт ~2-5 tok/s.

---

## 5. Агентный Runtime (ReAct Loop)

### Концепция

ReAct (Reasoning + Acting) — модель рассуждает, решает какой инструмент вызвать, анализирует результат, и повторяет до завершения задачи.

```
User: "Найди все файлы с SQL-инъекциями в проекте и исправь их"

Agent думает: "Нужно найти файлы с raw SQL запросами"
Agent вызывает: grep("SELECT.*%s", type="py")
Agent получает: [file1.py:23, file2.py:45, file3.py:112]

Agent думает: "Нашел 3 файла. Прочитаю каждый, чтобы понять контекст"
Agent вызывает: read("file1.py")
Agent получает: [содержимое файла]

Agent думает: "Строка 23 использует f-string в SQL — уязвимость. Заменю на параметризованный запрос"
Agent вызывает: edit("file1.py", old="f'SELECT * FROM users WHERE id = {user_id}'",
                                  new="'SELECT * FROM users WHERE id = %s', (user_id,)")

... продолжает для остальных файлов ...

Agent: "Исправил SQL-инъекции в 3 файлах: file1.py, file2.py, file3.py"
```

### Минимальная реализация (Python)

```python
"""
Minimal ReAct Agent Runtime.
Требует: OpenAI-compatible API сервер (vLLM/SGLang).
"""

import json
import httpx
from typing import Any

LLM_URL = "http://localhost:8000/v1/chat/completions"
MAX_TURNS = 50

# Реестр инструментов
TOOLS_REGISTRY = {}

def tool(name: str, description: str, parameters: dict):
    """Декоратор для регистрации инструмента."""
    def decorator(func):
        TOOLS_REGISTRY[name] = {
            "function": func,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters
                }
            }
        }
        return func
    return decorator

# Определение инструментов
@tool("bash", "Execute a bash command", {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "The command to execute"}
    },
    "required": ["command"]
})
def bash_tool(command: str) -> str:
    import subprocess
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
    return result.stdout + result.stderr

@tool("read_file", "Read a file from disk", {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path to read"}
    },
    "required": ["path"]
})
def read_file_tool(path: str) -> str:
    with open(path, "r") as f:
        return f.read()

@tool("write_file", "Write content to a file", {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path"},
        "content": {"type": "string", "description": "Content to write"}
    },
    "required": ["path", "content"]
})
def write_file_tool(path: str, content: str) -> str:
    with open(path, "w") as f:
        f.write(content)
    return f"Written {len(content)} bytes to {path}"

@tool("grep", "Search for pattern in files", {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Regex pattern"},
        "path": {"type": "string", "description": "Directory to search", "default": "."}
    },
    "required": ["pattern"]
})
def grep_tool(pattern: str, path: str = ".") -> str:
    import subprocess
    result = subprocess.run(
        ["grep", "-rn", pattern, path],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout[:10000]  # Ограничиваем выход


def execute_tool(name: str, arguments: dict) -> str:
    """Выполняет инструмент по имени."""
    if name not in TOOLS_REGISTRY:
        return f"Error: Unknown tool '{name}'"
    try:
        func = TOOLS_REGISTRY[name]["function"]
        return func(**arguments)
    except Exception as e:
        return f"Error executing {name}: {str(e)}"


async def run_agent(user_message: str, system_prompt: str = None) -> str:
    """
    Основной агентный цикл (ReAct Loop).

    Модель получает сообщение + доступные инструменты.
    Если модель вызывает инструмент — выполняем, добавляем результат, повторяем.
    Если модель отвечает текстом — возвращаем ответ.
    """

    messages = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    messages.append({"role": "user", "content": user_message})

    tools_schema = [t["schema"] for t in TOOLS_REGISTRY.values()]

    async with httpx.AsyncClient(timeout=300.0) as client:
        for turn in range(MAX_TURNS):
            # Запрос к LLM
            response = await client.post(LLM_URL, json={
                "model": "deepseek-v3",
                "messages": messages,
                "tools": tools_schema,
                "tool_choice": "auto",
                "temperature": 0.1,
                "max_tokens": 4096,
            })

            result = response.json()
            choice = result["choices"][0]
            message = choice["message"]

            # Добавляем ответ модели в историю
            messages.append(message)

            # Если нет tool calls — финальный ответ
            if not message.get("tool_calls"):
                return message["content"]

            # Выполняем все tool calls
            for tool_call in message["tool_calls"]:
                func_name = tool_call["function"]["name"]
                func_args = json.loads(tool_call["function"]["arguments"])

                print(f"[Agent] Calling tool: {func_name}({func_args})")

                tool_result = execute_tool(func_name, func_args)

                # Добавляем результат инструмента в историю
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": str(tool_result)
                })

    return "Agent reached maximum turns without completing the task."


# Точка входа
if __name__ == "__main__":
    import asyncio

    result = asyncio.run(run_agent(
        "Найди все Python файлы в текущей директории и посчитай общее количество строк",
        system_prompt="Ты полезный ассистент. Используй инструменты для выполнения задач."
    ))
    print(result)
```

### Как это масштабируется

```
Простой агент (выше)          Продвинутый агент
─────────────────────         ─────────────────────
1 ReAct loop                  Планировщик + Executor
Все инструменты в одном       Разделение на под-агентов
Один контекст                 Shared memory + State
Синхронное выполнение         Параллельные tool calls
Нет retry/fallback            Retry + error recovery
```

---

## 6. Tool Use и Function Calling

### Native Function Calling

Модели 70B+ поддерживают **native tool use** — это не prompt engineering, а часть формата ответа:

```json
// Запрос к LLM
{
    "messages": [{"role": "user", "content": "Какая погода в Москве?"}],
    "tools": [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"}
                },
                "required": ["city"]
            }
        }
    }]
}

// Ответ LLM (модель сама решает вызвать инструмент)
{
    "choices": [{
        "message": {
            "role": "assistant",
            "tool_calls": [{
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": "{\"city\": \"Moscow\"}"
                }
            }]
        }
    }]
}
```

### MCP (Model Context Protocol)

MCP — стандарт Anthropic для подключения инструментов. Поддерживается Open WebUI, Claude Code, и может быть интегрирован в собственный Agent Runtime.

```
Agent Runtime
    │
    ├── MCP Client ──→ MCP Server: Document Analysis
    │                      ├── tool: load_document
    │                      ├── tool: chunk_text
    │                      └── tool: extract_tables
    │
    ├── MCP Client ──→ MCP Server: Database
    │                      ├── tool: sql_query
    │                      └── tool: describe_table
    │
    └── MCP Client ──→ MCP Server: Web Search
                           ├── tool: search
                           └── tool: fetch_page
```

### Категории инструментов

| Категория | Инструменты | Описание |
|-----------|-------------|----------|
| **Файловая система** | read, write, edit, glob, grep | Работа с файлами и кодом |
| **Системные** | bash, process_list, kill | Выполнение команд |
| **Поиск** | web_search, rag_query | Поиск информации |
| **Данные** | sql_query, api_call, http_request | Доступ к данным |
| **Документы** | load_pdf, parse_docx, ocr | Обработка документов |
| **Код** | lint, test, compile, deploy | Разработка |
| **Коммуникация** | send_email, slack_message | Уведомления |
| **Специализированные** | compare_documents, analyze_estimate | Бизнес-логика |

---

## 7. Многоагентная система

### Паттерны многоагентности

#### Паттерн 1: Main Agent + Sub-Agents (как Claude Code)

```
                    Main Agent
                   (координатор)
                   /     |     \
                  /      |      \
           Sub-Agent  Sub-Agent  Sub-Agent
           (research) (coding)   (testing)
              │          │          │
           [search]   [edit]     [bash]
           [read]     [write]    [test]
```

Main Agent получает задачу, декомпозирует, делегирует Sub-Agents.
Каждый Sub-Agent — отдельный ReAct loop с изолированным контекстом.

```python
async def main_agent(task: str):
    """Main agent — планирует и делегирует."""

    plan = await llm.chat([
        {"role": "system", "content": "Разбей задачу на подзадачи для параллельного выполнения."},
        {"role": "user", "content": task}
    ])

    # Параллельный запуск под-агентов
    sub_tasks = parse_plan(plan)
    results = await asyncio.gather(*[
        sub_agent(sub_task, tools=sub_task.tools)
        for sub_task in sub_tasks
    ])

    # Синтез результатов
    final = await llm.chat([
        {"role": "system", "content": "Объедини результаты под-агентов в финальный ответ."},
        {"role": "user", "content": format_results(results)}
    ])

    return final
```

#### Паттерн 2: Pipeline (последовательный)

```
Analyst Agent → Coder Agent → Reviewer Agent → Deployer Agent
     │               │              │                │
  "Изучи задачу"  "Напиши код"  "Проверь код"   "Деплой"
```

Каждый агент передаёт результат следующему. Похоже на LangGraph, но каждая нода — полноценный агент с ReAct loop.

#### Паттерн 3: Debate / Consensus

```
Agent A (решение 1) ──┐
                       ├──→ Judge Agent ──→ Финальное решение
Agent B (решение 2) ──┘
```

Два агента независимо решают задачу, третий (Judge) выбирает лучшее решение или синтезирует.

### Выбор паттерна

| Задача | Рекомендуемый паттерн |
|--------|----------------------|
| Исследование + код | Main + Sub-Agents (параллельно) |
| Сравнение документов | Pipeline (последовательно) |
| Код-ревью | Debate (два агента + judge) |
| Сложная задача с неизвестной структурой | Main Agent с динамическими sub-agents |

---

## 8. RAG и память

### Уровни памяти

```
┌─────────────────────────────────────────────┐
│           Контекстное окно (128k)            │  ← Рабочая память
│  System prompt + History + Tool results      │     (текущий диалог)
├─────────────────────────────────────────────┤
│           RAG / Vector Store                 │  ← Долгосрочная память
│  ChromaDB / Milvus / Qdrant / FAISS         │     (документы, код)
│  Эмбеддинги: E5-large / BGE-M3              │
├─────────────────────────────────────────────┤
│           Persistent Memory                  │  ← Память между сессиями
│  Файлы (MEMORY.md), SQLite, Redis           │     (паттерны, решения)
└─────────────────────────────────────────────┘
```

### RAG Pipeline

```
Документ загружен
       │
       ▼
  Чанкирование (512-1024 токенов, с overlap)
       │
       ▼
  Эмбеддинги (E5-large / BGE-M3 / LaBSE)
       │
       ▼
  Индексация (ChromaDB / Milvus / FAISS)
       │
       ▼
  ═══════════════════════════════════════
       │
  Пользовательский запрос
       │
       ▼
  Эмбеддинг запроса
       │
       ▼
  Поиск top-K чанков (cosine similarity)
       │
       ▼
  Reranking (cross-encoder, опционально)
       │
       ▼
  Чанки добавляются в промпт как контекст
       │
       ▼
  LLM генерирует ответ с цитированием
```

### Выбор эмбеддинг-модели

| Модель | Размер | Русский | Скорость | MTEB Score |
|--------|--------|---------|----------|------------|
| `intfloat/multilingual-e5-large-instruct` | 560M | Хорошо | Быстро | 67.3 |
| `BAAI/bge-m3` | 570M | Хорошо | Быстро | 68.1 |
| `sentence-transformers/LaBSE` | 470M | Отлично | Быстро | 64.2 |
| `intfloat/e5-mistral-7b-instruct` | 7B | Хорошо | Медленно | 72.1 |

### Векторные БД

| БД | Масштаб | Фильтрация | Persistence | Рекомендация |
|----|---------|-----------|-------------|--------------|
| **FAISS** | Миллионы | Ограниченная | Файл | Прототип |
| **ChromaDB** | Сотни тысяч | Metadata | SQLite | Open WebUI default |
| **Milvus** | Миллиарды | Полная | Distributed | Продакшен |
| **Qdrant** | Миллиарды | Полная | Disk + RAM | Продакшен |
| **Weaviate** | Миллиарды | Полная | Distributed | Продакшен |

---

## 9. Оркестрация и графы

### Когда нужны жёсткие графы, а когда ReAct

| Модель | Подход | Почему |
|--------|--------|--------|
| Qwen-14B (маленькая) | LangGraph — жёсткий граф | Модель не умеет планировать |
| Qwen-72B (средняя) | Гибрид: ReAct + fallback на граф | Умеет, но ненадёжно |
| DeepSeek-V3 / 405B (большая) | ReAct loop с инструментами | Модель сама планирует |

### LangGraph — когда всё же нужен

Даже с мощной моделью, жёсткие графы полезны для:
- **Критические процессы** (финансы, юридика) — нужен предсказуемый pipeline
- **Оптимизация** — граф не тратит токены на планирование
- **Параллелизм** — LangGraph может запускать ноды параллельно
- **Human-in-the-loop** — точки прерывания для одобрения человеком

```python
# Гибридный подход: граф для структуры + ReAct внутри нод
from langgraph.graph import StateGraph

class CompareState(TypedDict):
    documents: list
    analysis: list
    report: str

async def analyze_node(state: CompareState) -> dict:
    """Нода использует ReAct loop внутри."""
    agent = ReActAgent(tools=[read_file, grep, semantic_search])
    result = await agent.run(f"Проанализируй различия: {state['documents']}")
    return {"analysis": result}

graph = StateGraph(CompareState)
graph.add_node("load", load_documents_node)      # Детерминированная нода
graph.add_node("analyze", analyze_node)            # ReAct-нода (агент внутри)
graph.add_node("report", generate_report_node)     # Детерминированная нода
graph.add_edge("load", "analyze")
graph.add_edge("analyze", "report")
```

---

## 10. UI и интерфейсы

### Варианты

| UI | Тип | Плюсы | Минусы |
|----|-----|-------|--------|
| **Open WebUI** | Web (Docker) | RAG, MCP, Tools, history, auth | Ограниченная кастомизация графов |
| **LobeChat** | Web | Красивый, plugins | Меньше возможностей RAG |
| **Chainlit** | Web (Python) | Глубокая интеграция с LangChain/LangGraph | Нужно кодить UI |
| **Gradio** | Web (Python) | Быстрый прототип | Не для продакшена |
| **Custom React** | Web | Полный контроль | Долго разрабатывать |
| **CLI** | Terminal | Быстро, как Claude Code | Нет визуализации |

### Рекомендация

**Этап 1 (сейчас):** Open WebUI — уже работает, есть RAG, MCP, история.

**Этап 2 (после стабилизации):** Open WebUI + Pipelines — кастомная логика внутри Open WebUI через Python pipes.

**Этап 3 (если нужно больше):** Chainlit или Custom React — полный контроль над UX, визуализация графов, стриминг промежуточных шагов.

---

## 11. Масштабирование и продакшен

### Оркестрация сервисов

```yaml
# docker-compose.production.yml
services:
  vllm:
    image: vllm/vllm-openai:latest
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 8
              capabilities: [gpu]
    command: >
      --model deepseek-ai/DeepSeek-V3
      --tensor-parallel-size 8
      --dtype float16
      --quantization fp8
      --max-model-len 65536
      --enable-auto-tool-choice
    ports:
      - "8000:8000"
    volumes:
      - /models:/models

  embedding-server:
    image: ghcr.io/huggingface/text-embeddings-inference:latest
    command: --model-id intfloat/multilingual-e5-large-instruct
    ports:
      - "8001:80"

  agent-runtime:
    build: ./agent
    environment:
      - LLM_URL=http://vllm:8000/v1
      - EMBEDDING_URL=http://embedding-server:80
      - VECTOR_DB_URL=http://qdrant:6333
    ports:
      - "8080:8080"

  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage

  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    environment:
      - OPENAI_API_BASE_URL=http://agent-runtime:8080/v1
      - RAG_EMBEDDING_ENGINE=openai
      - RAG_OPENAI_API_BASE_URL=http://embedding-server:80
    ports:
      - "3000:8080"

  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3001:3000"

volumes:
  qdrant_data:
```

### Мониторинг

| Метрика | Цель | Инструмент |
|---------|------|-----------|
| GPU utilization | >80% | nvidia-smi + Prometheus |
| VRAM usage | <95% | nvidia-smi |
| Tokens/sec throughput | >50 tok/s | vLLM metrics endpoint |
| Request latency P99 | <10s | Prometheus + Grafana |
| Queue depth | <10 | vLLM metrics |
| Error rate | <1% | Application logs |
| Agent turns per task | Avg <10 | Custom metrics |

### Безопасность

| Слой | Меры |
|------|------|
| Сеть | VPN / private network, TLS для всех сервисов |
| Аутентификация | Open WebUI встроенная + LDAP/OAuth |
| Инструменты | Sandbox для bash (Docker/gVisor), whitelist команд |
| Данные | Шифрование at rest, audit log всех tool calls |
| Модель | Prompt injection protection, output filtering |

---

## 12. Эталонная архитектура

```
┌──────────────────────────────────────────────────────────────────┐
│                         Clients                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ Open     │  │ Custom   │  │   CLI    │  │ API Consumers    │ │
│  │ WebUI    │  │ Web App  │  │  Client  │  │ (webhooks, bots) │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘ │
└───────┼──────────────┼────────────┼──────────────────┼───────────┘
        │              │            │                  │
        └──────────────┴────────────┴──────────────────┘
                                │
                          Load Balancer
                          (nginx/traefik)
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│                      Agent Gateway (FastAPI)                      │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  OpenAI-compatible API (/v1/chat/completions, /v1/models)   │ │
│  │  Session Manager (conversations, state)                      │ │
│  │  Auth + Rate Limiting                                        │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
│                             │                                     │
│  ┌──────────────────────────▼──────────────────────────────────┐ │
│  │                    Agent Runtime                              │ │
│  │                                                               │ │
│  │  ┌──────────┐  ┌──────────────┐  ┌────────────────────────┐ │ │
│  │  │ ReAct    │  │ Sub-Agent    │  │ LangGraph Workflows    │ │ │
│  │  │ Loop     │  │ Spawner      │  │ (complex pipelines)    │ │ │
│  │  └────┬─────┘  └──────┬───────┘  └────────────┬───────────┘ │ │
│  │       │               │                       │              │ │
│  │  ┌────▼───────────────▼───────────────────────▼────────────┐ │ │
│  │  │                Tool Registry (MCP + Native)              │ │ │
│  │  │                                                          │ │ │
│  │  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌───────┐ │ │ │
│  │  │  │  bash  │ │  file  │ │  web   │ │  sql   │ │ custom│ │ │ │
│  │  │  │sandbox │ │  ops   │ │ search │ │ query  │ │ tools │ │ │ │
│  │  │  └────────┘ └────────┘ └────────┘ └────────┘ └───────┘ │ │ │
│  │  └─────────────────────────────────────────────────────────┘ │ │
│  └──────────────────────────────────────────────────────────────┘ │
└───────────────────────────┬──────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
    ┌─────────▼────┐ ┌──────▼─────┐ ┌─────▼──────┐
    │   vLLM       │ │ Embedding  │ │  Vector DB │
    │ DeepSeek-V3  │ │  Server    │ │  Qdrant    │
    │ 8×H100 TP=8  │ │ E5-large   │ │            │
    │              │ │            │ │            │
    │ /v1/chat/    │ │ /v1/embed  │ │ /search    │
    │ completions  │ │            │ │ /upsert    │
    └──────────────┘ └────────────┘ └────────────┘
```

---

## 13. Поэтапный план запуска

### Фаза 1: Inference (1-2 недели)

- [ ] Установить и настроить серверное оборудование (8×H100)
- [ ] Установить NVIDIA drivers, CUDA toolkit, Docker + NVIDIA Container Toolkit
- [ ] Скачать модель (DeepSeek-V3 или Llama-405B)
- [ ] Запустить vLLM с tensor parallel на 8 GPU
- [ ] Проверить: `/v1/chat/completions` работает, tool use работает
- [ ] Бенчмарк: tokens/sec, latency, concurrent users

### Фаза 2: Базовый Agent (1-2 недели)

- [ ] Реализовать ReAct loop (Python, ~200 строк)
- [ ] Подключить базовые инструменты: bash, read, write, grep
- [ ] Обернуть в FastAPI с OpenAI-compatible API
- [ ] Подключить Open WebUI как UI
- [ ] Тест: агент может читать файлы, выполнять команды, отвечать на вопросы

### Фаза 3: RAG + Memory (1-2 недели)

- [ ] Запустить embedding server (TEI или свой)
- [ ] Развернуть Qdrant или Milvus
- [ ] Подключить Open WebUI RAG к embedding server
- [ ] Реализовать persistent memory (MEMORY.md или SQLite)
- [ ] Тест: загрузка документа → вопрос по документу → цитата с %

### Фаза 4: Многоагентность (2-4 недели)

- [ ] Реализовать Sub-Agent spawner
- [ ] Добавить параллельные tool calls
- [ ] Интегрировать LangGraph для сложных workflows
- [ ] Реализовать MCP-серверы для специализированных инструментов
- [ ] Тест: сложная задача → агент декомпозирует → параллельные sub-agents → результат

### Фаза 5: Продакшен (2-4 недели)

- [ ] Docker Compose для всех сервисов
- [ ] Мониторинг: Prometheus + Grafana
- [ ] Безопасность: sandbox для bash, auth, TLS
- [ ] Логирование: все tool calls, все промпты, все ответы
- [ ] Нагрузочное тестирование: 20-50 concurrent users
- [ ] Документация и runbooks

---

## 14. Стоимость

### Покупка оборудования

| Компонент | Стоимость |
|-----------|-----------|
| 8×H100 SXM (DGX или сборка) | $200,000 - $300,000 |
| Серверная платформа (CPU, RAM, NVMe, PSU) | $20,000 - $40,000 |
| Стойка + cooling | $5,000 - $15,000 |
| Сеть (коммутатор, кабели) | $2,000 - $5,000 |
| **Итого** | **$230,000 - $360,000** |

### Аренда (облако)

| Провайдер | Конфигурация | Цена/час | Цена/мес (24/7) |
|-----------|-------------|----------|-----------------|
| Lambda Cloud | 8×H100 SXM | ~$25/hr | ~$18,000 |
| RunPod | 8×H100 SXM | ~$22/hr | ~$16,000 |
| Vast.ai | 8×H100 (varies) | ~$15-20/hr | ~$12,000 |
| AWS p5.48xlarge | 8×H100 SXM | ~$98/hr | ~$70,000 |
| GCP a3-highgpu-8g | 8×H100 | ~$80/hr | ~$58,000 |

### Операционные расходы

| Статья | Стоимость/мес |
|--------|---------------|
| Электричество (10 kW × $0.10/kWh) | ~$720 |
| Интернет (серверный) | ~$100-500 |
| Администрирование | Зависит от команды |
| Бэкапы и мониторинг | ~$50-200 |

### Сравнение с API

| Объём | API (Claude/GPT-4) | Свой сервер (8×H100) |
|-------|---------------------|----------------------|
| 100 запросов/день | ~$50-150/мес | $12,000-18,000/мес |
| 1,000 запросов/день | ~$500-1,500/мес | $12,000-18,000/мес |
| 10,000 запросов/день | ~$5,000-15,000/мес | $12,000-18,000/мес |
| 100,000 запросов/день | ~$50,000-150,000/мес | $12,000-18,000/мес |

**Точка безубыточности:** ~5,000-10,000 запросов/день. Ниже — дешевле API. Выше — дешевле свой сервер.

**Другие причины для своего сервера:**
- Данные не покидают инфраструктуру (compliance, NDA, гостайна)
- Нет rate limits
- Полный контроль над моделью (fine-tuning, prompt caching)
- Нет зависимости от внешнего провайдера

---

## 15. Сравнение с Agent Navigator Pro (2×RTX 2070)

| Аспект | Текущая система (2×RTX 2070) | Целевая система (8×H100) |
|--------|-------------------------------|--------------------------|
| VRAM | 16 GB | 640 GB |
| Модель | Qwen-14B-Q4 | DeepSeek-V3-671B FP8 |
| Скорость | 2-5 tok/s | 50-100 tok/s |
| Агентность | Жёсткие LangGraph графы | Полный ReAct loop |
| Tool Use | Keyword matching в коде | Native function calling |
| RAG | Через Open WebUI (MiniLM) | E5-large/BGE-M3 + Qdrant |
| Concurrent users | 1 | 20-50 |
| Роутинг | `if "сравни" in query` | Модель сама решает |
| Многоагентность | Нет | Sub-agents с параллельным выполнением |
| Стоимость | ~$1,500 (железо) | ~$250,000+ (железо) или $15k/мес (cloud) |

### Что переиспользуется

- **LangGraph workflows** — можно оставить для критических процессов
- **Микросервисы** (Doc Server, Legal Server) — становятся MCP-серверами
- **Open WebUI** — остаётся как UI
- **Архитектура** — разделение на inference/orchestration/tools сохраняется

### Что заменяется

- **llama.cpp** → vLLM/SGLang
- **Keyword routing** → Native tool use
- **Context stuffing** → RAG с vector DB
- **Синхронный инференс** → Continuous batching

---

## Ссылки и ресурсы

- [vLLM Documentation](https://docs.vllm.ai/)
- [SGLang Documentation](https://sgl-project.github.io/)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Open WebUI Documentation](https://docs.openwebui.com/)
- [MCP Specification](https://modelcontextprotocol.io/)
- [DeepSeek-V3 Technical Report](https://arxiv.org/abs/2412.19437)
- [ReAct Paper](https://arxiv.org/abs/2210.03629)
- [NVIDIA H100 Datasheet](https://www.nvidia.com/en-us/data-center/h100/)
