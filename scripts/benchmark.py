#!/usr/bin/env python3
"""
llm-tools-platform — Benchmark / Stress-test скрипт.

Отправляет запросы к запущенной системе и измеряет время ответа.
Предназначен для сравнения CPU / GPU / гибридного режимов
через изменение параметров в .env и перезапуск системы.

Использование:
    # Базовый запуск (все сценарии)
    python scripts/benchmark.py

    # Только чат-сценарии (без файлов)
    python scripts/benchmark.py --scenarios chat

    # Только workflow с файлами
    python scripts/benchmark.py --scenarios doc_question,compare,equipment

    # Задать число повторов для усреднения
    python scripts/benchmark.py --repeats 3

    # Сохранить результаты в файл
    python scripts/benchmark.py --output results/benchmark_gpu.json

    # Указать другой адрес API
    python scripts/benchmark.py --api-url http://192.168.1.100:8000

Сценарии:
    health       — health-check всех сервисов (latency baseline)
    chat         — простой чат без документов (LLM inference)
    doc_question — вопрос по одному документу (RAG pipeline)
    compare      — сравнение двух юридических документов (LangGraph workflow)
    equipment    — анализ ТЗ + КП (LangGraph workflow)
    ums_status   — статус UMS (загруженные модели, runtime profile)
    embedding    — embedding одного текста (CPU embedder latency)
    prompt_cache_probe — identical repeat probe для prompt-cache на прямом UMS LLM path
"""

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:
    print("httpx не установлен. Установите: pip install httpx")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Конфигурация по умолчанию
# ---------------------------------------------------------------------------

AGENT_API_URL = os.getenv("AGENT_API_URL", "http://localhost:8000")
DOC_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
LEGAL_SERVER_URL = os.getenv("MCP_LEGAL_SERVER_URL", "http://localhost:8002")
UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")
CHAINLIT_URL = os.getenv("CHAINLIT_URL", "http://localhost:3000")

# Файлы для тестирования (относительно project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOADS_DIR = PROJECT_ROOT / "backend" / "open_webui_uploads"

# Тестовые документы — используем оригиналы без UUID-префиксов
TEST_FILES = {
    "single_legal": "H12100110_1621890000.pdf",
    "contract_v1": "contract_v1.pdf",
    "contract_v2": "contract_v2.pdf",
    "requirements": "Requirements.pdf",
    "quotation": "Quotation.pdf",
}

REQUEST_TIMEOUT = 300.0  # 5 минут — workflows могут быть долгими на CPU


# ---------------------------------------------------------------------------
# Результаты
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    scenario: str
    description: str
    status: str  # "ok", "error", "timeout", "skip"
    elapsed_sec: float = 0.0
    response_size: int = 0
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkReport:
    timestamp: str = ""
    api_url: str = ""
    backend_mode: str = ""
    ums_status: Dict[str, Any] = field(default_factory=dict)
    runtime_metadata: Dict[str, Any] = field(default_factory=dict)
    env_snapshot: Dict[str, str] = field(default_factory=dict)
    results: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _resolve_file(key: str) -> Optional[str]:
    """Resolves test file path, returns absolute path or None."""
    name = TEST_FILES.get(key)
    if not name:
        return None
    path = UPLOADS_DIR / name
    if path.exists():
        return str(path)
    return None


def _pretty_time(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds/60:.1f}min"


def _print_result(result: BenchmarkResult, idx: int) -> None:
    status_icon = {"ok": "\033[32m✓\033[0m", "error": "\033[31m✗\033[0m",
                   "timeout": "\033[33m⏱\033[0m", "skip": "\033[90m⊘\033[0m"}
    icon = status_icon.get(result.status, "?")
    time_str = _pretty_time(result.elapsed_sec) if result.elapsed_sec > 0 else "-"
    print(f"  {icon} [{idx}] {result.scenario:<20s} {time_str:>10s}  {result.description}")
    if result.error:
        print(f"       \033[31m{result.error[:120]}\033[0m")


def _build_runtime_metadata(ums_status: Dict[str, Any]) -> Dict[str, Any]:
    placements = ums_status.get("placements") if isinstance(ums_status, dict) else {}
    return {
        "backend_mode": str((ums_status or {}).get("backend_mode") or os.getenv("BACKEND_MODE", "unknown")),
        "runtime_profile": (ums_status or {}).get("runtime_profile"),
        "active_heavy_model": (ums_status or {}).get("active_heavy_model"),
        "running_models": (ums_status or {}).get("running"),
        "effective_context_tokens": (ums_status or {}).get("effective_context_tokens"),
        "retrieved_context_tokens_budget": (ums_status or {}).get("retrieved_context_tokens_budget"),
        "generation_tokens_reserve": (ums_status or {}).get("generation_tokens_reserve"),
        "prompt_cache_policy": (ums_status or {}).get("prompt_cache_policy"),
        "placements": placements if isinstance(placements, dict) else {},
        "agent_api_url": AGENT_API_URL,
        "ums_url": UMS_URL,
        "doc_server_url": DOC_SERVER_URL,
        "legal_server_url": LEGAL_SERVER_URL,
    }


def _extract_telemetry_details(payload: Dict[str, Any]) -> Dict[str, Any]:
    telemetry = payload.get("telemetry") if isinstance(payload, dict) else None
    if not isinstance(telemetry, dict):
        return {}
    return {
        "telemetry_elapsed_ms": telemetry.get("elapsed_ms"),
        "telemetry_llm_ms": telemetry.get("llm_ms"),
        "telemetry_embedding_ms": telemetry.get("embedding_ms"),
        "telemetry_service_ms": telemetry.get("service_ms"),
        "telemetry_report_ms": telemetry.get("report_ms"),
        "telemetry_quality_summary": telemetry.get("quality_summary"),
        "telemetry_stage_count": len(telemetry.get("stage_timings") or []),
        "telemetry_tool_count": len(telemetry.get("tool_timings") or []),
    }


# ---------------------------------------------------------------------------
# Сценарии
# ---------------------------------------------------------------------------

def scenario_health(client: httpx.Client) -> BenchmarkResult:
    """Health-check всех сервисов — baseline latency."""
    services = [
        ("Agent API", f"{AGENT_API_URL}/health"),
        ("Doc Server", f"{DOC_SERVER_URL}/health"),
        ("Legal Server", f"{LEGAL_SERVER_URL}/health"),
        ("UMS", f"{UMS_URL}/health"),
        ("Chainlit", f"{CHAINLIT_URL}/"),
    ]
    details: Dict[str, Any] = {}
    total_start = time.monotonic()
    all_ok = True
    for name, url in services:
        try:
            start = time.monotonic()
            resp = client.get(url, timeout=10.0)
            elapsed = time.monotonic() - start
            details[name] = {"status": resp.status_code, "latency_ms": round(elapsed * 1000)}
            if resp.status_code >= 500:
                all_ok = False
        except Exception as e:
            details[name] = {"status": "error", "error": str(e)[:100]}
            all_ok = False

    total = time.monotonic() - total_start
    return BenchmarkResult(
        scenario="health",
        description="Health-check всех сервисов",
        status="ok" if all_ok else "error",
        elapsed_sec=total,
        details=details,
    )


def scenario_ums_status(client: httpx.Client) -> BenchmarkResult:
    """Получение статуса UMS — загруженные модели и runtime profile."""
    start = time.monotonic()
    try:
        resp = client.get(f"{UMS_URL}/status", timeout=15.0)
        elapsed = time.monotonic() - start
        data = resp.json() if resp.status_code == 200 else {}
        return BenchmarkResult(
            scenario="ums_status",
            description="UMS status + runtime profile",
            status="ok",
            elapsed_sec=elapsed,
            response_size=len(resp.content),
            details=data,
        )
    except Exception as e:
        return BenchmarkResult(
            scenario="ums_status", description="UMS status",
            status="error", elapsed_sec=time.monotonic() - start, error=str(e)[:200],
        )


def scenario_embedding(client: httpx.Client) -> BenchmarkResult:
    """Embedding одного текста через UMS — CPU embedder latency."""
    payload = {
        "input": "Требования к системе кондиционирования для серверного помещения",
        "model": "labse-embedding",
    }
    start = time.monotonic()
    try:
        resp = client.post(f"{UMS_URL}/v1/embeddings", json=payload, timeout=30.0)
        elapsed = time.monotonic() - start
        data = resp.json() if resp.status_code == 200 else {}
        emb_dim = 0
        if "data" in data and data["data"]:
            emb_dim = len(data["data"][0].get("embedding", []))
        return BenchmarkResult(
            scenario="embedding",
            description=f"LaBSE embedding (dim={emb_dim})",
            status="ok",
            elapsed_sec=elapsed,
            response_size=len(resp.content),
            details={"embedding_dim": emb_dim},
        )
    except Exception as e:
        return BenchmarkResult(
            scenario="embedding", description="Embedding request",
            status="error", elapsed_sec=time.monotonic() - start, error=str(e)[:200],
        )


def scenario_chat(client: httpx.Client) -> BenchmarkResult:
    """Простой чат без документов — чистый LLM inference."""
    payload = {
        "model": "llm-tools-platform",
        "messages": [{"role": "user", "content": "Объясни кратко, что такое тендерная документация."}],
        "stream": False,
    }
    start = time.monotonic()
    try:
        resp = client.post(
            f"{AGENT_API_URL}/v1/chat/completions",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        elapsed = time.monotonic() - start
        if resp.status_code != 200:
            return BenchmarkResult(
                scenario="chat", description="Чат (LLM inference)",
                status="error", elapsed_sec=elapsed, error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return BenchmarkResult(
            scenario="chat",
            description=f"Чат (LLM inference), ответ {len(text)} символов",
            status="ok",
            elapsed_sec=elapsed,
            response_size=len(text),
            details={"response_preview": text[:150]},
        )
    except httpx.ReadTimeout:
        return BenchmarkResult(
            scenario="chat", description="Чат (LLM inference)",
            status="timeout", elapsed_sec=REQUEST_TIMEOUT, error="Timeout",
        )
    except Exception as e:
        return BenchmarkResult(
            scenario="chat", description="Чат (LLM inference)",
            status="error", elapsed_sec=time.monotonic() - start, error=str(e)[:200],
        )


def scenario_prompt_cache_probe(client: httpx.Client) -> BenchmarkResult:
    """Повторяет один и тот же direct-LLM запрос через UMS и меряет cold/warm delta."""
    prompt = "Кратко перечисли три ключевых пункта тендерной документации."
    prompt_fingerprint = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    payload = {
        "model_id": "qwen-14b-llm",
        "payload": {
            "prompt": prompt,
            "temperature": 0.1,
        },
        "stream": False,
    }
    try:
        status_before_resp = client.get(f"{UMS_URL}/status", timeout=15.0)
        status_before = status_before_resp.json() if status_before_resp.status_code == 200 else {}
    except Exception:
        status_before = {}

    def _run_once() -> tuple[float, Optional[httpx.Response], Optional[str]]:
        start = time.monotonic()
        try:
            resp = client.post(f"{UMS_URL}/infer", json=payload, timeout=REQUEST_TIMEOUT)
            return time.monotonic() - start, resp, None
        except Exception as exc:
            return time.monotonic() - start, None, str(exc)[:200]

    cold_elapsed, cold_resp, cold_error = _run_once()
    if cold_resp is None:
        return BenchmarkResult(
            scenario="prompt_cache_probe",
            description="Prompt-cache probe",
            status="error",
            elapsed_sec=cold_elapsed,
            error=f"cold request failed: {cold_error}",
            details={"prompt_prefix_fingerprint": prompt_fingerprint},
        )
    if cold_resp.status_code != 200:
        return BenchmarkResult(
            scenario="prompt_cache_probe",
            description="Prompt-cache probe",
            status="error",
            elapsed_sec=cold_elapsed,
            error=f"cold request HTTP {cold_resp.status_code}: {cold_resp.text[:200]}",
            details={"prompt_prefix_fingerprint": prompt_fingerprint},
        )

    warm_elapsed, warm_resp, warm_error = _run_once()
    if warm_resp is None:
        return BenchmarkResult(
            scenario="prompt_cache_probe",
            description="Prompt-cache probe",
            status="error",
            elapsed_sec=cold_elapsed + warm_elapsed,
            error=f"warm request failed: {warm_error}",
            details={"prompt_prefix_fingerprint": prompt_fingerprint},
        )
    if warm_resp.status_code != 200:
        return BenchmarkResult(
            scenario="prompt_cache_probe",
            description="Prompt-cache probe",
            status="error",
            elapsed_sec=cold_elapsed + warm_elapsed,
            error=f"warm request HTTP {warm_resp.status_code}: {warm_resp.text[:200]}",
            details={"prompt_prefix_fingerprint": prompt_fingerprint},
        )

    try:
        status_after_resp = client.get(f"{UMS_URL}/status", timeout=15.0)
        status_after = status_after_resp.json() if status_after_resp.status_code == 200 else {}
    except Exception:
        status_after = {}

    speedup = cold_elapsed / warm_elapsed if warm_elapsed > 0 else None
    delta_pct = ((cold_elapsed - warm_elapsed) / cold_elapsed * 100.0) if cold_elapsed > 0 else None
    details = {
        "prompt_prefix_fingerprint": prompt_fingerprint,
        "cold_elapsed_sec": round(cold_elapsed, 4),
        "warm_elapsed_sec": round(warm_elapsed, 4),
        "speedup": round(speedup, 4) if speedup is not None else None,
        "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
        "same_process_expected": True,
        "status_before": {
            "active_heavy_model": status_before.get("active_heavy_model"),
            "backend_mode": status_before.get("backend_mode"),
            "prompt_cache_policy": status_before.get("prompt_cache_policy"),
        },
        "status_after": {
            "active_heavy_model": status_after.get("active_heavy_model"),
            "backend_mode": status_after.get("backend_mode"),
            "prompt_cache_policy": status_after.get("prompt_cache_policy"),
        },
    }
    return BenchmarkResult(
        scenario="prompt_cache_probe",
        description=f"Prompt-cache probe speedup={details['speedup'] or 0:.2f}x",
        status="ok",
        elapsed_sec=cold_elapsed + warm_elapsed,
        response_size=len(cold_resp.content) + len(warm_resp.content),
        details=details,
    )


def scenario_doc_question(client: httpx.Client) -> BenchmarkResult:
    """Вопрос по документу — RAG pipeline (загрузка + retrieval + generation)."""
    file_path = _resolve_file("single_legal")
    if not file_path:
        return BenchmarkResult(
            scenario="doc_question", description="RAG pipeline",
            status="skip", error="Файл H12100110_1621890000.pdf не найден",
        )

    # Шаг 1: загрузка документа через Document Server
    start = time.monotonic()
    try:
        load_resp = client.post(
            f"{DOC_SERVER_URL}/load_document",
            json={"path": file_path},
            timeout=60.0,
        )
        doc_load_time = time.monotonic() - start
        if load_resp.status_code != 200:
            return BenchmarkResult(
                scenario="doc_question", description="RAG pipeline",
                status="error", elapsed_sec=doc_load_time,
                error=f"Document Server: HTTP {load_resp.status_code}",
            )
    except Exception as e:
        return BenchmarkResult(
            scenario="doc_question", description="RAG pipeline",
            status="error", elapsed_sec=time.monotonic() - start, error=f"Doc load: {e}",
        )

    # Шаг 2: orchestrate запрос с документом
    orchestrate_payload = {
        "message": "Какие основные условия описаны в этом документе?",
        "runtime_mode": "auto",
        "file_count": 1,
        "has_session_docs": True,
        "session_docs": {
            os.path.basename(file_path): {
                "document_id": os.path.basename(file_path),
                "path": file_path,
                "text": load_resp.json().get("text", "")[:8000],
            }
        },
        "active_doc_ids": [os.path.basename(file_path)],
    }

    step2_start = time.monotonic()
    try:
        resp = client.post(
            f"{AGENT_API_URL}/execute_orchestration",
            json=orchestrate_payload,
            timeout=REQUEST_TIMEOUT,
        )
        total_elapsed = time.monotonic() - start
        step2_time = time.monotonic() - step2_start
        if resp.status_code != 200:
            return BenchmarkResult(
                scenario="doc_question", description="RAG pipeline",
                status="error", elapsed_sec=total_elapsed,
                error=f"Execute: HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        answer = str(data.get("assistant_message", ""))
        return BenchmarkResult(
            scenario="doc_question",
            description=f"RAG pipeline, ответ {len(answer)} символов",
            status="ok",
            elapsed_sec=total_elapsed,
            response_size=len(answer),
            details={
                "doc_load_sec": round(doc_load_time, 2),
                "orchestration_sec": round(step2_time, 2),
                "route": data.get("route", "unknown"),
                "response_preview": answer[:150],
                **_extract_telemetry_details(data),
            },
        )
    except httpx.ReadTimeout:
        return BenchmarkResult(
            scenario="doc_question", description="RAG pipeline",
            status="timeout", elapsed_sec=REQUEST_TIMEOUT, error="Timeout",
        )
    except Exception as e:
        return BenchmarkResult(
            scenario="doc_question", description="RAG pipeline",
            status="error", elapsed_sec=time.monotonic() - start, error=str(e)[:200],
        )


def scenario_compare(client: httpx.Client) -> BenchmarkResult:
    """Сравнение двух юридических документов — LangGraph compare workflow."""
    file1 = _resolve_file("contract_v1")
    file2 = _resolve_file("contract_v2")
    if not file1 or not file2:
        return BenchmarkResult(
            scenario="compare", description="Compare workflow",
            status="skip", error="contract_v1.pdf или contract_v2.pdf не найдены",
        )

    start = time.monotonic()
    session_docs = {}
    for fpath in [file1, file2]:
        fname = os.path.basename(fpath)
        try:
            resp = client.post(
                f"{DOC_SERVER_URL}/load_document",
                json={"path": fpath},
                timeout=60.0,
            )
            text = resp.json().get("text", "") if resp.status_code == 200 else ""
            session_docs[fname] = {
                "document_id": fname,
                "path": fpath,
                "text": text[:12000],
            }
        except Exception as e:
            return BenchmarkResult(
                scenario="compare", description="Compare workflow",
                status="error", elapsed_sec=time.monotonic() - start,
                error=f"Doc load {fname}: {e}",
            )

    doc_load_time = time.monotonic() - start

    orchestrate_payload = {
        "message": "Сравни эти два документа",
        "runtime_mode": "auto",
        "file_count": 2,
        "has_session_docs": True,
        "session_docs": session_docs,
        "active_doc_ids": list(session_docs.keys()),
    }

    step2_start = time.monotonic()
    try:
        resp = client.post(
            f"{AGENT_API_URL}/execute_orchestration",
            json=orchestrate_payload,
            timeout=REQUEST_TIMEOUT,
        )
        total_elapsed = time.monotonic() - start
        step2_time = time.monotonic() - step2_start
        if resp.status_code != 200:
            return BenchmarkResult(
                scenario="compare", description="Compare workflow",
                status="error", elapsed_sec=total_elapsed,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        answer = str(data.get("assistant_message", ""))
        return BenchmarkResult(
            scenario="compare",
            description=f"Compare workflow, ответ {len(answer)} символов",
            status="ok",
            elapsed_sec=total_elapsed,
            response_size=len(answer),
            details={
                "doc_load_sec": round(doc_load_time, 2),
                "workflow_sec": round(step2_time, 2),
                "route": data.get("route", "unknown"),
                "response_preview": answer[:150],
                **_extract_telemetry_details(data),
            },
        )
    except httpx.ReadTimeout:
        return BenchmarkResult(
            scenario="compare", description="Compare workflow",
            status="timeout", elapsed_sec=REQUEST_TIMEOUT, error="Timeout",
        )
    except Exception as e:
        return BenchmarkResult(
            scenario="compare", description="Compare workflow",
            status="error", elapsed_sec=time.monotonic() - start, error=str(e)[:200],
        )


def scenario_equipment(client: httpx.Client) -> BenchmarkResult:
    """Анализ ТЗ + КП — LangGraph equipment workflow."""
    file_tz = _resolve_file("requirements")
    file_kp = _resolve_file("quotation")
    if not file_tz or not file_kp:
        return BenchmarkResult(
            scenario="equipment", description="Equipment workflow",
            status="skip", error="Requirements.pdf или Quotation.pdf не найдены",
        )

    start = time.monotonic()
    session_docs = {}
    for fpath in [file_tz, file_kp]:
        fname = os.path.basename(fpath)
        try:
            resp = client.post(
                f"{DOC_SERVER_URL}/load_document",
                json={"path": fpath},
                timeout=60.0,
            )
            text = resp.json().get("text", "") if resp.status_code == 200 else ""
            session_docs[fname] = {
                "document_id": fname,
                "path": fpath,
                "text": text[:12000],
            }
        except Exception as e:
            return BenchmarkResult(
                scenario="equipment", description="Equipment workflow",
                status="error", elapsed_sec=time.monotonic() - start,
                error=f"Doc load {fname}: {e}",
            )

    doc_load_time = time.monotonic() - start

    orchestrate_payload = {
        "message": "Проанализируй ТЗ и коммерческое предложение",
        "runtime_mode": "auto",
        "file_count": 2,
        "has_session_docs": True,
        "session_docs": session_docs,
        "active_doc_ids": list(session_docs.keys()),
    }

    step2_start = time.monotonic()
    try:
        resp = client.post(
            f"{AGENT_API_URL}/execute_orchestration",
            json=orchestrate_payload,
            timeout=REQUEST_TIMEOUT,
        )
        total_elapsed = time.monotonic() - start
        step2_time = time.monotonic() - step2_start
        if resp.status_code != 200:
            return BenchmarkResult(
                scenario="equipment", description="Equipment workflow",
                status="error", elapsed_sec=total_elapsed,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        answer = str(data.get("assistant_message", ""))
        return BenchmarkResult(
            scenario="equipment",
            description=f"Equipment workflow, ответ {len(answer)} символов",
            status="ok",
            elapsed_sec=total_elapsed,
            response_size=len(answer),
            details={
                "doc_load_sec": round(doc_load_time, 2),
                "workflow_sec": round(step2_time, 2),
                "route": data.get("route", "unknown"),
                "response_preview": answer[:150],
                **_extract_telemetry_details(data),
            },
        )
    except httpx.ReadTimeout:
        return BenchmarkResult(
            scenario="equipment", description="Equipment workflow",
            status="timeout", elapsed_sec=REQUEST_TIMEOUT, error="Timeout",
        )
    except Exception as e:
        return BenchmarkResult(
            scenario="equipment", description="Equipment workflow",
            status="error", elapsed_sec=time.monotonic() - start, error=str(e)[:200],
        )


# ---------------------------------------------------------------------------
# Реестр сценариев
# ---------------------------------------------------------------------------

SCENARIOS = {
    "health": scenario_health,
    "ums_status": scenario_ums_status,
    "embedding": scenario_embedding,
    "prompt_cache_probe": scenario_prompt_cache_probe,
    "chat": scenario_chat,
    "doc_question": scenario_doc_question,
    "compare": scenario_compare,
    "equipment": scenario_equipment,
}

DEFAULT_ORDER = ["health", "ums_status", "embedding", "prompt_cache_probe", "chat", "doc_question", "compare", "equipment"]


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

def collect_env_snapshot() -> Dict[str, str]:
    """Собираем ключевые .env параметры для сравнения конфигураций."""
    keys = [
        "N_GPU_LAYERS_QWEN14B", "CONTEXT_SIZE_QWEN14B",
        "TIER_OVERRIDE", "N_GPU_LAYERS_OVERRIDE",
        "INTENT_CLASSIFIER_MODE", "INTENT_CLASSIFIER_EMBEDDER_MODEL",
        "UMS_PORT", "AGENT_API_PORT",
        "MODEL_PATH_LLM", "MODEL_PATH_VLM",
        "MODEL_PATH_EMBEDDING_INTENT", "MODEL_PATH_EMBEDDING_RETRIEVAL",
        "MODEL_PATH_QWEN14B", "MODEL_PATH_QWENVL",
        "MODEL_PATH_LABSE", "MODEL_PATH_QWEN3_EMBEDDING_06B",
        "BACKEND_MODE", "VLLM_BASE_URL", "VLLM_MODEL_ID_QWEN_14B_LLM",
        "UMS_RUNTIME_PROFILE", "ACTIVE_MODEL_ID",
    ]
    env_file = PROJECT_ROOT / "backend" / ".env"
    env_values: Dict[str, str] = {}

    # Читаем из .env файла
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k in keys:
                    env_values[k] = v

    # Переопределения из текущего окружения
    for k in keys:
        val = os.getenv(k)
        if val is not None:
            env_values[k] = val

    return env_values


def run_benchmark(scenarios: List[str], repeats: int = 1, api_url: Optional[str] = None) -> BenchmarkReport:
    global AGENT_API_URL
    if api_url:
        AGENT_API_URL = api_url

    report = BenchmarkReport(
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        api_url=AGENT_API_URL,
        backend_mode=str(os.getenv("BACKEND_MODE", "unknown")),
        env_snapshot=collect_env_snapshot(),
    )

    client = httpx.Client(timeout=REQUEST_TIMEOUT)

    # UMS status для отчёта
    try:
        ums_resp = client.get(f"{UMS_URL}/status", timeout=15.0)
        if ums_resp.status_code == 200:
            report.ums_status = ums_resp.json()
            report.backend_mode = str(report.ums_status.get("backend_mode") or report.backend_mode)
    except Exception:
        pass
    report.runtime_metadata = _build_runtime_metadata(report.ums_status)
    report.backend_mode = str(report.runtime_metadata.get("backend_mode") or report.backend_mode)

    print()
    print(f"\033[1;34m{'='*65}\033[0m")
    print(f"\033[1;34m  llm-tools-platform — Benchmark\033[0m")
    print(f"\033[1;34m{'='*65}\033[0m")
    print(f"  API:       {AGENT_API_URL}")
    print(f"  Сценарии:  {', '.join(scenarios)}")
    print(f"  Повторы:   {repeats}")

    gpu_layers = report.env_snapshot.get("N_GPU_LAYERS_QWEN14B", "?")
    ctx_size = report.env_snapshot.get("CONTEXT_SIZE_QWEN14B", "?")
    print(f"  GPU layers: {gpu_layers}  |  Context: {ctx_size}")
    print(f"  Backend mode: {report.backend_mode}")

    if report.ums_status:
        loaded = report.runtime_metadata.get("running_models") or report.ums_status.get("loaded_models", [])
        profile = report.ums_status.get("runtime_profile", "?")
        print(f"  UMS models: {', '.join(str(m) for m in loaded) if loaded else 'none'}")
        print(f"  Runtime profile: {profile}")

    print(f"\033[1;34m{'-'*65}\033[0m")
    print()

    all_results: List[BenchmarkResult] = []
    idx = 0

    for scenario_name in scenarios:
        fn = SCENARIOS.get(scenario_name)
        if not fn:
            print(f"  \033[33m⚠ Неизвестный сценарий: {scenario_name}\033[0m")
            continue

        times: List[float] = []
        last_result: Optional[BenchmarkResult] = None

        for rep in range(repeats):
            if repeats > 1:
                print(f"  \033[90m[повтор {rep+1}/{repeats}]\033[0m", end=" ")
            result = fn(client)
            times.append(result.elapsed_sec)
            last_result = result
            idx += 1
            _print_result(result, idx)

        if last_result and repeats > 1 and len(times) > 1:
            avg = sum(times) / len(times)
            min_t, max_t = min(times), max(times)
            last_result.details["avg_sec"] = round(avg, 2)
            last_result.details["min_sec"] = round(min_t, 2)
            last_result.details["max_sec"] = round(max_t, 2)
            last_result.elapsed_sec = avg
            print(f"       \033[90mavg={_pretty_time(avg)} min={_pretty_time(min_t)} max={_pretty_time(max_t)}\033[0m")

        if last_result:
            all_results.append(last_result)

    client.close()

    # Сводка
    report.results = [asdict(r) for r in all_results]
    ok_count = sum(1 for r in all_results if r.status == "ok")
    err_count = sum(1 for r in all_results if r.status == "error")
    skip_count = sum(1 for r in all_results if r.status == "skip")
    timeout_count = sum(1 for r in all_results if r.status == "timeout")
    total_time = sum(r.elapsed_sec for r in all_results if r.status == "ok")

    report.summary = {
        "total_scenarios": len(all_results),
        "ok": ok_count,
        "error": err_count,
        "skip": skip_count,
        "timeout": timeout_count,
        "total_time_sec": round(total_time, 2),
        "repeats": repeats,
    }

    print()
    print(f"\033[1;34m{'-'*65}\033[0m")
    print(f"  \033[1mИтого:\033[0m {ok_count} ok, {err_count} error, {skip_count} skip, {timeout_count} timeout")
    print(f"  \033[1mОбщее время (ok):\033[0m {_pretty_time(total_time)}")

    # Таблица ключевых метрик
    key_scenarios = ["chat", "doc_question", "compare", "equipment", "embedding"]
    key_results = {r.scenario: r for r in all_results if r.scenario in key_scenarios}
    if key_results:
        print()
        print(f"  \033[1mПроизводительность:\033[0m")
        print(f"  {'Сценарий':<20s} {'Время':>10s}  {'Статус':>8s}")
        print(f"  {'-'*42}")
        for name in key_scenarios:
            if name in key_results:
                r = key_results[name]
                print(f"  {name:<20s} {_pretty_time(r.elapsed_sec):>10s}  {r.status:>8s}")

    print(f"\033[1;34m{'='*65}\033[0m")
    print()

    return report


def main():
    parser = argparse.ArgumentParser(
        description="llm-tools-platform — Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python scripts/benchmark.py                           # Все сценарии
  python scripts/benchmark.py --scenarios chat           # Только чат
  python scripts/benchmark.py --repeats 3               # 3 повтора для усреднения
  python scripts/benchmark.py --output gpu_results.json  # Сохранить результаты

Workflow для сравнения CPU/GPU/Hybrid:
  1. Установить N_GPU_LAYERS_QWEN14B=-1 в .env  → перезапустить → benchmark
  2. Установить N_GPU_LAYERS_QWEN14B=0 в .env   → перезапустить → benchmark
  3. Сравнить результаты в JSON-файлах
        """,
    )
    parser.add_argument(
        "--scenarios", type=str, default=None,
        help=f"Сценарии через запятую. Доступные: {', '.join(DEFAULT_ORDER)}",
    )
    parser.add_argument("--repeats", type=int, default=1, help="Число повторов каждого сценария (default: 1)")
    parser.add_argument("--output", "-o", type=str, default=None, help="Путь для сохранения JSON-результатов")
    parser.add_argument("--api-url", type=str, default=None, help="URL Agent API (default: http://localhost:8000)")

    args = parser.parse_args()

    if args.scenarios:
        scenarios = [s.strip() for s in args.scenarios.split(",")]
    else:
        scenarios = DEFAULT_ORDER

    report = run_benchmark(scenarios=scenarios, repeats=args.repeats, api_url=args.api_url)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str))
        print(f"  Результаты сохранены: {output_path}")
        print()


if __name__ == "__main__":
    main()
