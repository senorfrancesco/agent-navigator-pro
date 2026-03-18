"""
Agent API - Умный оркестратор на базе LangGraph.
Поддерживает микросервисную архитектуру, динамический список моделей и OpenAI-совместимый API.
"""

import asyncio
import json
import hashlib
import time
import sys
import os
import re
import uuid
import logging
import warnings
from typing import Dict, Any, Optional, List, AsyncGenerator, Literal
from dotenv import load_dotenv

# Подавление предупреждений pynvml
warnings.filterwarnings("ignore", category=FutureWarning, module="pynvml")

# Загрузка переменных окружения
load_dotenv()
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from services.model_manager.ums_client import ums_client
from services.model_manager.model_selection import resolve_model_selection
from services.observability import (
    inc_metric_counter,
    ObservabilityMiddleware,
    render_metrics_text,
)
from orchestrator.shared.http_client import get_shared_client
from orchestrator.knowledge_base_store import get_knowledge_base_store
from orchestrator.execution_runtime import (
    ExecutionDependencies,
    _build_session_doc_list,
    build_control_plane_metadata,
    execute_orchestration,
    resolve_request_runtime_mode,
)
from orchestrator.doc_question_heuristics import (
    build_doc_question_deterministic_fallback,
    citations_are_valid,
    compute_confidence_v1,
    extract_citation_ids,
    has_sufficient_evidence_v1,
)
from orchestrator.orchestration_runtime import decide_orchestration
from orchestrator.ui_control_plane import (
    get_prompt_profile_system_message,
    normalize_inference_device_mode,
    resolve_effective_settings,
)

try:
    from services.resource_monitor import get_system_resources
except ImportError:
    def get_system_resources(): return {"error": "Resource monitor not found"}

app = FastAPI(title="Agent Navigator Pro Orchestrator", version="2.3.0")
logger = logging.getLogger("agent_api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ObservabilityMiddleware, service_name="agent_api", logger=logger)


def _get_request_trace_id(request: Request) -> str:
    state_trace_id = str(getattr(request.state, "trace_id", "") or "").strip()
    if state_trace_id:
        return state_trace_id
    header_trace_id = str(request.headers.get("X-Trace-Id") or "").strip()
    if header_trace_id:
        request.state.trace_id = header_trace_id
        return header_trace_id
    generated = str(uuid.uuid4())[:8]
    request.state.trace_id = generated
    return generated


def _resolve_http_trace_id(http_request: Optional[Request]) -> str:
    if http_request is None:
        return str(uuid.uuid4())[:8]
    return _get_request_trace_id(http_request)

# Дедупликация параллельных запросов от Open WebUI
# Хранит dedup_key -> timestamp начала обработки
_active_workflows: Dict[str, float] = {}
DEDUP_WINDOW_SEC = 30  # Окно дедупликации (workflow может длиться >60 сек)

# === Pydantic Models ===

class FileAttachment(BaseModel):
    name: str
    path: str
    size: int
    type: str

class GenerationOverrides(BaseModel):
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None


class OrchestrationRequest(BaseModel):
    message: str
    run_id: Optional[str] = None
    state_ref: Optional[str] = None
    state_version: Optional[int] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None
    pending_action: Optional[Dict[str, Any]] = None
    active_doc_ids: Optional[List[str]] = None
    attachments_meta: Optional[List[Dict[str, Any]]] = None
    runtime_mode: Literal["auto", "chat_only", "specialized_tasks"] = "auto"
    assistant_mode: Optional[Literal["general_chat", "coding", "agentic", "specific_tasks", "rag_qa"]] = None
    rag_scope: Optional[Literal["off", "session_rag", "knowledge_base_rag"]] = None
    knowledge_collection_id: Optional[str] = None
    model_profile: Optional[
        Literal[
            "default-chat",
            "long-context",
            "legal-compare",
            "low-vram",
            "coder",
            "agentic",
            "analyst",
        ]
    ] = None
    prompt_profile: Optional[
        Literal[
            "default-assistant",
            "coding-assistant",
            "tool-using-agent",
            "task-router",
            "strict-grounded-doc-qa",
        ]
    ] = None
    generation_overrides: Optional[GenerationOverrides] = None
    custom_system_prompt: Optional[str] = None
    tool_scope: Optional[Literal["chat", "coding", "agentic", "domain_tasks", "document_qa"]] = None
    ui_state: Optional[Dict[str, Any]] = None
    file_count: int = 0
    has_session_docs: bool = False
    session_docs: Optional[Dict[str, Any]] = None
    classifier_result: Optional[Dict[str, Any]] = None
    trace_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    forced_route: Optional[str] = None


def _collect_request_control_plane(request: OrchestrationRequest) -> Dict[str, Any]:
    raw_control_plane: Dict[str, Any] = {}
    if "assistant_mode" in request.model_fields_set:
        raw_control_plane["assistant_mode"] = request.assistant_mode
    if "runtime_mode" in request.model_fields_set:
        raw_control_plane["runtime_mode"] = request.runtime_mode
    if "rag_scope" in request.model_fields_set:
        raw_control_plane["rag_scope"] = request.rag_scope
    if "knowledge_collection_id" in request.model_fields_set:
        raw_control_plane["knowledge_collection_id"] = request.knowledge_collection_id
    if "model_profile" in request.model_fields_set:
        raw_control_plane["model_profile"] = request.model_profile
    if "prompt_profile" in request.model_fields_set:
        raw_control_plane["prompt_profile"] = request.prompt_profile
    if "generation_overrides" in request.model_fields_set and request.generation_overrides is not None:
        raw_control_plane["generation_overrides"] = request.generation_overrides.model_dump(exclude_none=True)
    if "custom_system_prompt" in request.model_fields_set:
        raw_control_plane["custom_system_prompt"] = request.custom_system_prompt
    if "tool_scope" in request.model_fields_set:
        raw_control_plane["tool_scope"] = request.tool_scope
    return raw_control_plane


def _build_api_prompt(query: str, history: List[Dict[str, Any]], system_msg: str = "") -> str:
    if not system_msg:
        system_msg = "Ты помощник Agent Navigator. Помогай пользователю."
    prompt = f"<|im_start|>system\n{system_msg}<|im_end|>\n"
    for msg in history[-10:]:
        role = str(msg.get("role", "user"))
        content = str(msg.get("content", ""))
        prompt += f"<|im_start|>{role}\n{content}<|im_end|>\n"
    prompt += f"<|im_start|>user\n{query}<|im_end|>\n<|im_start|>assistant\n"
    return prompt


async def _infer_with_effective_settings(
    effective_settings: Dict[str, Any],
    prompt: str,
    *,
    enforced_overrides: Optional[Dict[str, Any]] = None,
    device_mode: Optional[str] = None,
    **_: Any,
) -> str:
    generation = dict((effective_settings.get("generation") or {}))
    if enforced_overrides:
        generation.update({k: v for k, v in enforced_overrides.items() if v is not None})
    payload = {
        "prompt": prompt,
        "temperature": generation.get("temperature", 0.7),
        "top_p": generation.get("top_p", 0.9),
        "max_tokens": generation.get("max_tokens", 2048),
    }
    model_id = (
        effective_settings.get("resolved_model_id")
        or resolve_model_selection("llm.default_chat").resolved_model_id
    )
    resolved_device_mode = normalize_inference_device_mode(device_mode or effective_settings.get("device_mode"))
    response = await asyncio.to_thread(ums_client.infer, model_id, payload, resolved_device_mode)
    return _extract_content(response)


def _build_api_execution_dependencies(request: OrchestrationRequest, effective_settings: Dict[str, Any]) -> ExecutionDependencies:
    session_docs = request.session_docs or {}
    retrieval_embed_fn: Optional[Any] = None

    def _docs_list() -> List[Dict[str, Any]]:
        return _build_session_doc_list(session_docs)

    def _profile_prompt() -> str:
        custom = (effective_settings.get("custom_system_prompt") or "").strip()
        if custom:
            return custom
        return get_prompt_profile_system_message(effective_settings.get("prompt_profile"))

    async def _infer(prompt: str, **kwargs: Any) -> str:
        return await _infer_with_effective_settings(
            effective_settings,
            prompt,
            enforced_overrides=kwargs.get("enforced_overrides"),
            device_mode=kwargs.get("device_mode"),
            allow_sync_retry=kwargs.get("allow_sync_retry"),
            raise_on_error=kwargs.get("raise_on_error"),
            summary_stage=kwargs.get("summary_stage"),
        )

    async def _noop_async(*args: Any, **kwargs: Any) -> None:
        return None

    def _get_retrieval_embed_fn() -> Any:
        nonlocal retrieval_embed_fn
        if retrieval_embed_fn is not None:
            return retrieval_embed_fn
        retrieval_embedder_model_id = str(
            effective_settings.get("resolved_retrieval_embedder_model_id")
            or resolve_model_selection("legal.embedder").resolved_model_id
        )
        try:
            from services.model_manager.ums_client import create_ums_embed_fn

            retrieval_embed_fn = create_ums_embed_fn(model_id=retrieval_embedder_model_id)
        except Exception:
            retrieval_embed_fn = None
        return retrieval_embed_fn

    def _has_sufficient_evidence(**kwargs: Any) -> bool:
        return has_sufficient_evidence_v1(**kwargs)

    def _compute_confidence_v1(
        sources: List[Dict[str, Any]],
        cited_ids: List[int],
        answer_mode: str,
        **kwargs: Any,
    ) -> tuple[float, str]:
        query = str(kwargs.get("query") or request.message)
        return compute_confidence_v1(sources, cited_ids, answer_mode, query=query)

    def _doc_question_fallback(**kwargs: Any) -> Dict[str, Any]:
        fallback_type = kwargs.get("fallback_type", "insufficient_evidence")
        fallback_reason = kwargs.get("fallback_reason")
        if fallback_type == "retrieval_unavailable":
            answer_text = (
                fallback_reason
                or "Для этого API-compatible пути backend retrieval adapter пока не подключён. "
                "Execution выполняется через unified core, но session-document retrieval для этого "
                "adapter ещё не реализован."
            )
            answer_mode = "retrieval_unavailable"
            confidence = 0.0
            confidence_label = "low"
        else:
            answer_text = fallback_reason or "Недостаточно проверяемых данных."
            answer_mode = "insufficient_evidence"
            confidence = 0.1
            confidence_label = "low"

        if fallback_type == "retrieval_unavailable":
            return {
                "answer_text": answer_text,
                "sources": kwargs.get("sources", []),
                "answer_mode": answer_mode,
                "fallback_type": fallback_type,
                "fallback_reason": fallback_reason,
                "confidence": confidence,
                "confidence_label": confidence_label,
                "confidence_method": "heuristic_v1",
                "confidence_version": "1",
            }
        return build_doc_question_deterministic_fallback(
            query=request.message,
            sources=kwargs.get("sources", []),
            fallback_type=fallback_type,
            fallback_reason=fallback_reason or "Недостаточно проверяемых данных.",
            source_scope_summary=kwargs.get("source_scope_summary", "off"),
        )

    return ExecutionDependencies(
        infer_assistant_text=_infer,
        build_prompt=_build_api_prompt,
        get_profile_system_prompt=_profile_prompt,
        has_retrieval_adapter=lambda: _get_retrieval_embed_fn() is not None,
        get_retrieval_embed_fn=_get_retrieval_embed_fn,
        get_knowledge_base_store=get_knowledge_base_store,
        get_active_doc_ids=lambda: list(request.active_doc_ids or []),
        get_all_docs=_docs_list,
        get_active_docs=_docs_list,
        get_report_docs=lambda: [doc for doc in _docs_list() if doc.get("report_generated")],
        resolve_target_doc_name=lambda query, docs: None,
        is_report_query=lambda query: False,
        ensure_rag_index_for_doc_ids=_noop_async,
        get_rag_pipeline=lambda: None,
        build_sources_from_rag_result=lambda rag_result, rag_pipeline, max_sources=5: [],
        reindex_sources=lambda sources: sources,
        build_doc_question_deterministic_fallback=_doc_question_fallback,
        render_doc_question_markdown=lambda payload: payload["answer_text"],
        build_doc_question_prompt_with_sources=lambda query, history, sources: _build_api_prompt(
            query,
            history,
            "Ты grounded document QA ассистент. Отвечай только по источникам.",
        ),
        citations_are_valid=citations_are_valid,
        needs_doc_question_regen=lambda answer_text, has_session_docs: False,
        extract_citation_ids=extract_citation_ids,
        has_sufficient_evidence=_has_sufficient_evidence,
        compute_confidence_v1=_compute_confidence_v1,
        strip_model_source_sections=lambda answer_text: answer_text,
        to_host_path=lambda path: path,
        active_set_status_line=lambda: "",
        attach_and_register_report=_noop_async,
        update_progress_box=_noop_async,
        clear_progress_box=_noop_async,
        is_cancelled=lambda: False,
    )

def _compute_files_hash(file_paths: List[str]) -> str:
    """Вычисляет хеш на основе путей и размеров файлов для дедупликации."""
    h = hashlib.md5()
    for p in sorted(file_paths):
        h.update(p.encode())
        try:
            h.update(str(os.path.getsize(p)).encode())
        except OSError:
            pass
    return h.hexdigest()

def _extract_content(response):
    if isinstance(response, dict):
        if "content" in response: return response["content"]
        if "choices" in response and len(response["choices"]) > 0:
            choice = response["choices"][0]
            if isinstance(choice, dict):
                return choice.get("text", "") or choice.get("message", {}).get("content", "")
    return str(response).strip()


def _extract_openai_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")).strip()
            for part in content
            if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text", "")).strip()
        ).strip()
    if content is None:
        return ""
    return str(content).strip()


def _extract_latest_user_query(messages: List[Dict[str, Any]]) -> str:
    for msg in reversed(messages or []):
        if str(msg.get("role", "")).lower() != "user":
            continue
        text = _extract_openai_text_content(msg.get("content"))
        if text:
            return text
    return ""


def _extract_openai_system_prompt(messages: List[Dict[str, Any]]) -> Optional[str]:
    system_messages = []
    for msg in messages or []:
        if str(msg.get("role", "")).lower() != "system":
            continue
        text = _extract_openai_text_content(msg.get("content"))
        if text:
            system_messages.append(text)
    if not system_messages:
        return None
    return "\n\n".join(system_messages)


def _normalize_openai_history(messages: List[Dict[str, Any]], latest_user_query: str) -> List[Dict[str, Any]]:
    history: List[Dict[str, Any]] = []
    normalized: List[Dict[str, Any]] = []
    for msg in messages or []:
        role = str(msg.get("role", "")).lower()
        if role not in {"user", "assistant", "system"}:
            continue
        text = _extract_openai_text_content(msg.get("content"))
        if not text:
            continue
        normalized.append({"role": role, "content": text})

    for msg in normalized:
        if msg["role"] == "system":
            continue
        history.append(msg)

    if history and history[-1]["role"] == "user" and history[-1]["content"] == latest_user_query:
        history = history[:-1]
    return history


def _discover_recent_uploaded_files(max_age_seconds: int = 600) -> List[FileAttachment]:
    found_files: List[FileAttachment] = []
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        uploads_path = os.path.join(base_dir, "open_webui_uploads")
        if not os.path.exists(uploads_path):
            return found_files
        recent: List[str] = []
        now = time.time()
        for root, _, files in os.walk(uploads_path):
            for filename in files:
                path = os.path.join(root, filename)
                if now - os.path.getmtime(path) < max_age_seconds and not filename.startswith("Report_"):
                    recent.append(path)
        recent.sort(key=os.path.getmtime, reverse=True)
        for path in recent:
            found_files.append(
                FileAttachment(
                    name=os.path.basename(path),
                    path=path,
                    size=os.path.getsize(path),
                    type="webui_upload",
                )
            )
    except Exception as exc:
        print(f"Error scanning uploads: {exc}")
    return found_files


def _discover_manual_path_attachments(user_query: str, existing_paths: Optional[set[str]] = None) -> List[FileAttachment]:
    found_files: List[FileAttachment] = []
    known_paths = existing_paths or set()
    try:
        path_pattern = r'(?:/[\w\-. /]+|\bbackend/[\w\-. /]+)'
        matches = re.findall(path_pattern, user_query or "")
        for match in matches:
            candidate = match.strip()
            if not candidate or candidate in known_paths or not os.path.exists(candidate):
                continue
            found_files.append(
                FileAttachment(
                    name=os.path.basename(candidate),
                    path=candidate,
                    size=os.path.getsize(candidate) if os.path.exists(candidate) else 0,
                    type="manual",
                )
            )
            known_paths.add(candidate)
    except Exception:
        return found_files
    return found_files


def _discover_openai_attachments(user_query: str) -> List[FileAttachment]:
    enable_recent_uploads = os.getenv("OPENAI_COMPAT_ENABLE_UPLOAD_DISCOVERY", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    found_files = _discover_recent_uploaded_files() if enable_recent_uploads else []
    existing_paths = {item.path for item in found_files}
    found_files.extend(_discover_manual_path_attachments(user_query, existing_paths))
    return found_files


async def _load_openai_session_docs(attachments: List[FileAttachment]) -> Dict[str, Any]:
    if not attachments:
        return {}

    doc_server = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
    client = await get_shared_client()
    loaded: Dict[str, Any] = {}
    for attachment in attachments:
        text = ""
        error = None
        try:
            response = await client.post(
                f"{doc_server}/load_document",
                json={"path": attachment.path},
                timeout=60.0,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "error":
                    error = data.get("error")
                else:
                    text = str(data.get("text", "") or "")
            else:
                error = f"HTTP {response.status_code}"
        except Exception as exc:
            error = str(exc)

        loaded[attachment.name] = {
            "document_id": attachment.name,
            "path": attachment.path,
            "text": text,
            "error": error,
        }
    return loaded


def _build_openai_compat_request(
    *,
    data: Dict[str, Any],
    messages: List[Dict[str, Any]],
    user_query: str,
    attachments: List[FileAttachment],
    session_docs: Optional[Dict[str, Any]] = None,
) -> OrchestrationRequest:
    target_model = str(data.get("model") or "agent-navigator")
    system_prompt = _extract_openai_system_prompt(messages)
    history = _normalize_openai_history(messages, latest_user_query=user_query)
    active_doc_ids = [att.name for att in attachments]

    request_payload: Dict[str, Any] = {
        "message": user_query,
        "history": history,
        "attachments_meta": [att.model_dump() for att in attachments],
        "active_doc_ids": active_doc_ids,
        "file_count": len(attachments),
        "has_session_docs": bool(attachments),
        "session_docs": session_docs or {},
    }
    if system_prompt:
        request_payload["custom_system_prompt"] = system_prompt

    if target_model != "agent-navigator":
        request_payload.update(
            {
                "assistant_mode": "general_chat",
                "runtime_mode": "chat_only",
                "model_profile": "default-chat",
                "tool_scope": "chat",
            }
        )

    return OrchestrationRequest(**request_payload)


def _compute_openai_dedup_key(*, target_model: str, user_query: str, attachments: List[FileAttachment]) -> str:
    files_hash = _compute_files_hash([item.path for item in attachments]) if attachments else "no_files"
    query_hash = hashlib.md5((user_query or "").encode()).hexdigest()[:16]
    return f"{target_model}:{files_hash}:{query_hash}"


async def _stream_openai_compat_response(execution_response: Dict[str, Any]) -> AsyncGenerator[str, None]:
    yield "data: " + json.dumps({"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}) + "\n\n"
    text = str(execution_response.get("assistant_message") or "")
    if text:
        yield "data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]}) + "\n\n"
    yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n"
    yield "data: [DONE]\n\n"


def _build_openai_chat_completion_response(*, target_model: str, text: str) -> Dict[str, Any]:
    now_ts = int(time.time())
    return {
        "id": f"chatcmpl-{now_ts}",
        "object": "chat.completion",
        "created": now_ts,
        "model": target_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

# === Health ===

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return PlainTextResponse(render_metrics_text(), media_type="text/plain; version=0.0.4; charset=utf-8")


async def orchestrate(request: OrchestrationRequest, http_request: Optional[Request] = None):
    request.trace_id = request.trace_id or _resolve_http_trace_id(http_request)
    effective_settings = resolve_effective_settings(_collect_request_control_plane(request))
    response = decide_orchestration(
        query=request.message,
        trace_id=request.trace_id,
        runtime_mode=resolve_request_runtime_mode(request.model_dump(exclude_none=True), effective_settings),
        rag_scope=str(effective_settings.get("rag_scope") or "off"),
        knowledge_collection_id=effective_settings.get("knowledge_collection_id"),
        file_count=request.file_count,
        has_session_docs=request.has_session_docs,
        session_docs=request.session_docs or {},
        classifier_result=request.classifier_result,
        new_files=request.attachments_meta or [],
        active_doc_ids=request.active_doc_ids or [],
        forced_route=request.forced_route,
    )
    response.update(build_control_plane_metadata(request.model_dump(exclude_none=True), effective_settings))
    response["effective_settings"] = effective_settings
    inc_metric_counter(
        "agent_nav_agent_api_orchestration_requests_total",
        labels={"endpoint": "/orchestrate", "result": str(response.get("route") or "unknown")},
    )
    return response


@app.post("/orchestrate")
async def orchestrate_route(request: OrchestrationRequest, http_request: Request):
    return await orchestrate(request, http_request)


async def execute_orchestration_api(request: OrchestrationRequest, http_request: Optional[Request] = None):
    request.trace_id = request.trace_id or _resolve_http_trace_id(http_request)
    effective_settings = resolve_effective_settings(_collect_request_control_plane(request))
    deps = _build_api_execution_dependencies(request, effective_settings)
    payload = request.model_dump(exclude_none=True)
    payload["runtime_mode"] = resolve_request_runtime_mode(payload, effective_settings)
    payload["effective_settings"] = effective_settings
    response = await execute_orchestration(payload, deps=deps)
    inc_metric_counter(
        "agent_nav_agent_api_orchestration_requests_total",
        labels={"endpoint": "/execute_orchestration", "result": str(response.get("route") or "unknown")},
    )
    return response


@app.post("/execute_orchestration")
async def execute_orchestration_route(request: OrchestrationRequest, http_request: Request):
    return await execute_orchestration_api(request, http_request)


# === OpenAI Compatible API ===

@app.get("/v1/models")
def list_models():
    """Возвращает динамический список доступных GGUF моделей."""
    models = [{"id": "agent-navigator", "object": "model", "created": int(time.time()), "owned_by": "agent-navigator-pro"}]
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        models_dir = os.path.join(base_dir, 'models', 'gguf')
        if os.path.exists(models_dir):
            for root, dirs, files in os.walk(models_dir):
                for file in files:
                    if file.endswith(".gguf"):
                        mid = os.path.splitext(file)[0]
                        models.append({"id": mid, "object": "model", "created": int(os.path.getctime(os.path.join(root, file))), "owned_by": "local-fs"})
    except Exception as e: print(f"Error scanning models: {e}")
    return {"object": "list", "data": models}

@app.post("/v1/chat/completions")
async def openai_completions(request: Request):
    data = await request.json()
    stream_mode = bool(data.get("stream", True))
    messages = data.get("messages", [])
    target_model = data.get("model", "agent-navigator")
    user_query = _extract_latest_user_query(messages)
    found_files = _discover_openai_attachments(user_query)
    dedup_key = _compute_openai_dedup_key(target_model=target_model, user_query=user_query, attachments=found_files)
    now_ts = time.time()
    if dedup_key in _active_workflows and now_ts - _active_workflows[dedup_key] < DEDUP_WINDOW_SEC:
        logger.info(
            "openai_dedup_hit trace_id=%s model=%s stream=%s dedup_key=%s",
            _get_request_trace_id(request),
            target_model,
            stream_mode,
            dedup_key,
        )
        inc_metric_counter(
            "agent_nav_agent_api_openai_dedup_hits_total",
            labels={"model_family": "agent-navigator" if target_model == "agent-navigator" else "direct"},
        )
        if not stream_mode:
            return _build_openai_chat_completion_response(target_model=target_model, text="")

        async def _empty():
            yield "data: [DONE]\n\n"

        return StreamingResponse(_empty(), media_type="text/event-stream")
    _active_workflows[dedup_key] = now_ts
    for k in list(_active_workflows.keys()):
        if now_ts - _active_workflows[k] > 600:
            del _active_workflows[k]

    session_docs = await _load_openai_session_docs(found_files)
    if not stream_mode:
        try:
            compat_request = _build_openai_compat_request(
                data=data,
                messages=messages,
                user_query=user_query,
                attachments=found_files,
                session_docs=session_docs,
            )
            effective_settings = resolve_effective_settings(_collect_request_control_plane(compat_request))
            if target_model != "agent-navigator":
                effective_settings["resolved_model_id"] = target_model
            payload = compat_request.model_dump(exclude_none=True)
            payload["idempotency_key"] = dedup_key
            payload["runtime_mode"] = resolve_request_runtime_mode(payload, effective_settings)
            payload["effective_settings"] = effective_settings
            response = await execute_orchestration(
                payload,
                deps=_build_api_execution_dependencies(compat_request, effective_settings),
            )
            return _build_openai_chat_completion_response(
                target_model=target_model,
                text=str(response.get("assistant_message") or ""),
            )
        finally:
            _active_workflows.pop(dedup_key, None)

    async def generate():
        try:
            compat_request = _build_openai_compat_request(
                data=data,
                messages=messages,
                user_query=user_query,
                attachments=found_files,
                session_docs=session_docs,
            )
            effective_settings = resolve_effective_settings(_collect_request_control_plane(compat_request))
            if target_model != "agent-navigator":
                effective_settings["resolved_model_id"] = target_model
            payload = compat_request.model_dump(exclude_none=True)
            payload["idempotency_key"] = dedup_key
            payload["runtime_mode"] = resolve_request_runtime_mode(payload, effective_settings)
            payload["effective_settings"] = effective_settings
            response = await execute_orchestration(
                payload,
                deps=_build_api_execution_dependencies(compat_request, effective_settings),
            )
            async for chunk in _stream_openai_compat_response(response):
                yield chunk
        finally:
            _active_workflows.pop(dedup_key, None)

    return StreamingResponse(generate(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
