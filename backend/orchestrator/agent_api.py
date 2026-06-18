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
from pathlib import Path
from contextlib import asynccontextmanager, suppress
from typing import Dict, Any, Optional, List, AsyncGenerator, Literal
from dotenv import load_dotenv
import numpy as np
import httpx

# Подавление предупреждений pynvml
warnings.filterwarnings("ignore", category=FutureWarning, module="pynvml")

# Загрузка переменных окружения
load_dotenv()
from fastapi import FastAPI, Request
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from services.model_manager.ums_client import create_ums_embed_fn, ums_client
from services.model_manager.model_selection import (
    resolve_execution_plan,
    resolve_model_selection,
    resolve_user_model_selection,
)
from services.model_manager.models_config import get_all_models
from services.observability import (
    inc_metric_counter,
    ObservabilityMiddleware,
    render_metrics_text,
)
from orchestrator.shared.http_client import get_shared_client
from orchestrator.document_binding_store import get_document_binding_store
from orchestrator.knowledge_base_ingestion import ingest_text_source_sync
from orchestrator.knowledge_base_store import get_knowledge_base_store
from orchestrator.execution_runtime import (
    ExecutionDependencies,
    _build_session_doc_list,
    build_control_plane_metadata,
    execute_orchestration,
    resolve_request_runtime_mode,
)
from orchestrator.tool_catalog import RoutingMode, ToolName
from orchestrator.openapi_tools_api import create_openapi_tools_router
from orchestrator.tool_execution import (
    apply_tool_contract_to_payload,
    build_accepted_tool_job_response,
    build_tool_job_status_response,
    cancel_tool_job,
    force_tool_job_transition,
    get_tool_job_result,
    get_tool_job_store,
    inject_tool_contract_metadata,
    reconcile_incomplete_tool_jobs,
    should_start_async_tool_job,
    submit_async_tool_job,
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
from orchestrator.operator_ui_api import router as operator_ui_router
from orchestrator.operator_ui_api import enforce_operator_localhost_only, is_operator_surface_path
from app.tool_server.server import create_tool_server_app

try:
    from services.resource_monitor import get_system_resources
except ImportError:
    def get_system_resources(): return {"error": "Resource monitor not found"}


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    reconcile_incomplete_tool_jobs()
    yield


app = FastAPI(title="llm-tools-platform Orchestrator", version="2.3.0", lifespan=app_lifespan)
logger = logging.getLogger("agent_api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ObservabilityMiddleware, service_name="agent_api", logger=logger)


@app.middleware("http")
async def restrict_operator_surface_to_localhost(request: Request, call_next):
    if is_operator_surface_path(request.url.path):
        try:
            enforce_operator_localhost_only(request)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 403)
            detail = getattr(exc, "detail", "operator-ui-access-denied")
            return JSONResponse(status_code=status_code, content={"detail": detail})
    return await call_next(request)


app.include_router(operator_ui_router)
app.mount(
    "/app-tools",
    create_tool_server_app(route_prefix="/app-tools"),
    name="clean-tool-server",
)

_OPERATOR_UI_DIR = Path(__file__).resolve().parents[2] / "prototype" / "operator-ui"
_OPERATOR_ASSETS_DIR = Path(__file__).resolve().parent / "public"
if _OPERATOR_UI_DIR.exists():
    app.mount("/operator-ui", StaticFiles(directory=str(_OPERATOR_UI_DIR), html=True), name="operator-ui")
if _OPERATOR_ASSETS_DIR.exists():
    app.mount("/operator-assets", StaticFiles(directory=str(_OPERATOR_ASSETS_DIR)), name="operator-assets")


class ToolJobTransitionPayload(BaseModel):
    status: Literal["running", "cancelling", "completed", "failed", "cancelled"]
    response: Optional[Dict[str, Any]] = None
    error_summary: Optional[str] = None


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


def _resolve_tool_job_route_prefix(http_request: Optional[Request]) -> str:
    if http_request is None:
        return ""
    path = str(http_request.url.path or "")
    if path.startswith("/tool-server/"):
        return "/tool-server"
    return ""

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
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    ui_locale: Optional[str] = None
    run_id: Optional[str] = None
    state_ref: Optional[str] = None
    state_version: Optional[int] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None
    pending_action: Optional[Dict[str, Any]] = None
    active_doc_ids: Optional[List[str]] = None
    document_bindings: Optional[List[Dict[str, Any]]] = None
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
    resolved_model_id: Optional[str] = None
    custom_system_prompt: Optional[str] = None
    tool_scope: Optional[Literal["chat", "coding", "agentic", "domain_tasks", "document_qa"]] = None
    ui_state: Optional[Dict[str, Any]] = None
    file_count: int = 0
    has_session_docs: bool = False
    session_docs: Optional[Dict[str, Any]] = None
    classifier_result: Optional[Dict[str, Any]] = None
    requested_tool: Optional[ToolName] = None
    routing_mode: RoutingMode = "explicit"
    job_mode: Literal["sync_if_possible", "force_async"] = "sync_if_possible"
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


def _serialize_document_binding(binding: Any) -> Dict[str, Any]:
    return {
        "document_id": str(binding.document_id),
        "version_id": str(binding.version_id),
        "thread_id": binding.thread_id,
        "upload_id": binding.upload_id,
        "file_id": binding.file_id,
        "display_name": str(binding.display_name),
        "path": binding.storage_path,
        "source_scope": str(binding.source_scope),
        "ingestion_status": str(binding.ingestion_status),
        "resolved_identity": str(binding.resolved_identity),
        "resolved_identity_kind": str(binding.resolved_identity_kind),
        "expires_at": binding.expires_at,
        "is_active": bool(binding.is_active),
    }


def _binding_has_identity(binding: Dict[str, Any]) -> bool:
    for key in ("document_id", "version_id", "upload_id", "file_id", "file_path", "path"):
        value = binding.get(key)
        if value is None:
            continue
        if str(value).strip():
            return True
    return False


def _collect_document_binding_inputs(request: OrchestrationRequest) -> List[Dict[str, Any]]:
    if request.document_bindings:
        return [
            dict(item)
            for item in request.document_bindings
            if isinstance(item, dict) and _binding_has_identity(item)
        ]
    ui_state = request.ui_state if isinstance(request.ui_state, dict) else {}
    ui_bindings = ui_state.get("document_ref_bindings")
    if isinstance(ui_bindings, list) and ui_bindings:
        return [dict(item) for item in ui_bindings if isinstance(item, dict) and _binding_has_identity(item)]

    bindings: List[Dict[str, Any]] = []
    for name, info in (request.session_docs or {}).items():
        if not isinstance(info, dict):
            continue
        candidate = {
            "label": str(info.get("display_name") or name),
            "document_id": info.get("document_id"),
            "version_id": info.get("version_id"),
            "upload_id": info.get("upload_id"),
            "file_id": info.get("file_id"),
            "file_path": info.get("path"),
            "ingestion_status": info.get("ingestion_status"),
        }
        if _binding_has_identity(candidate):
            bindings.append(candidate)
    return bindings


def _merge_session_docs_with_bindings(
    session_docs: Dict[str, Any],
    document_bindings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not session_docs:
        return session_docs
    docs_by_path = {}
    docs_by_label = {}
    docs_by_identity = {}
    for binding in document_bindings:
        if binding.get("path"):
            docs_by_path[str(binding["path"])] = binding
        if binding.get("display_name"):
            docs_by_label[str(binding["display_name"])] = binding
        for key in ("resolved_identity", "document_id", "upload_id", "file_id", "version_id"):
            if binding.get(key):
                docs_by_identity[str(binding[key])] = binding

    normalized: Dict[str, Any] = {}
    for name, info in session_docs.items():
        if not isinstance(info, dict):
            continue
        match = None
        for candidate in (
            info.get("document_id"),
            info.get("upload_id"),
            info.get("file_id"),
            info.get("version_id"),
            info.get("path"),
            info.get("display_name"),
            name,
        ):
            if candidate is None:
                continue
            text_candidate = str(candidate)
            match = (
                docs_by_identity.get(text_candidate)
                or docs_by_path.get(text_candidate)
                or docs_by_label.get(text_candidate)
            )
            if match is not None:
                break
        updated = dict(info)
        if match is not None:
            updated["document_id"] = match.get("document_id")
            updated["version_id"] = match.get("version_id")
            updated["display_name"] = match.get("display_name") or updated.get("display_name") or name
            updated["path"] = match.get("path") or updated.get("path")
            updated["thread_id"] = match.get("thread_id") or updated.get("thread_id")
            updated["expires_at"] = match.get("expires_at") or updated.get("expires_at")
            updated["source_scope"] = match.get("source_scope") or updated.get("source_scope")
            updated["ingestion_status"] = match.get("ingestion_status") or updated.get("ingestion_status")
        normalized[str(name)] = updated
    return normalized


def _prune_expired_document_context() -> int:
    binding_store = get_document_binding_store()
    expired = list(binding_store.list_expired_bindings_sync())
    if not expired:
        return 0
    kb_store = get_knowledge_base_store()
    for binding in expired:
        if not binding.thread_id:
            continue
        try:
            kb_store.delete_chunks_sync(
                collection_id=f"session:{binding.thread_id}",
                filters={
                    "source_scope": "session",
                    "thread_id": binding.thread_id,
                    "document_version_id": binding.version_id,
                },
            )
        except Exception:
            logger.warning("Failed to delete expired session chunks for %s", binding.version_id, exc_info=True)
    return binding_store.prune_expired_bindings_sync()


def _materialize_request_document_context(request: OrchestrationRequest) -> None:
    _prune_expired_document_context()
    binding_inputs = _collect_document_binding_inputs(request)
    binding_store = get_document_binding_store()
    resolved = []
    if binding_inputs:
        for item in binding_inputs:
            resolved.append(
                binding_store.register_document_binding_sync(
                    thread_id=request.thread_id,
                    binding=item,
                    source_scope=str(item.get("source_scope") or "session"),
                )
            )
    elif request.thread_id:
        resolved = binding_store.list_thread_bindings_sync(request.thread_id, active_only=True)

    if not resolved:
        return

    serialized = [_serialize_document_binding(item) for item in resolved]
    request.document_bindings = serialized
    request.active_doc_ids = [str(item["document_id"]) for item in serialized]
    request.has_session_docs = request.has_session_docs or bool(serialized)
    request.session_docs = _merge_session_docs_with_bindings(request.session_docs or {}, serialized)

    ui_state = dict(request.ui_state or {})
    ui_state["document_bindings"] = serialized
    ui_state["documents_by_id"] = {
        str(item["document_id"]): {
            "document_id": item["document_id"],
            "display_name": item["display_name"],
            "version": 1,
            "path": item["path"],
            "source_origin": item["source_scope"],
        }
        for item in serialized
    }
    request.ui_state = ui_state


def _collect_request_control_plane_with_payload_overrides(
    request: OrchestrationRequest,
    request_payload: Dict[str, Any],
) -> Dict[str, Any]:
    raw_control_plane = _collect_request_control_plane(request)
    for key in (
        "assistant_mode",
        "runtime_mode",
        "rag_scope",
        "knowledge_collection_id",
        "model_profile",
        "prompt_profile",
        "custom_system_prompt",
        "tool_scope",
    ):
        if key in request_payload and (
            key in request.model_fields_set or request_payload.get(key) != getattr(request, key, None)
        ):
            raw_control_plane[key] = request_payload[key]
    return raw_control_plane


def _normalize_runtime_model_override(value: Any) -> Optional[str]:
    model_id = str(value or "").strip()
    if not model_id or model_id == "llm-tools-platform":
        return None
    return model_id


def _apply_runtime_model_override(effective_settings: Dict[str, Any], model_id: Any) -> None:
    normalized_model_id = _normalize_runtime_model_override(model_id)
    if normalized_model_id:
        effective_settings["resolved_model_id"] = normalized_model_id


def _build_api_prompt(query: str, history: List[Dict[str, Any]], system_msg: str = "") -> str:
    if not system_msg:
        system_msg = "Ты помощник llm-tools-platform. Помогай пользователю."
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
    record_model_execution: Optional[Any] = None,
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
    model_id = str(
        effective_settings.get("resolved_model_id")
        or resolve_model_selection("llm.default_chat").resolved_model_id
    )
    resolved_device_mode = normalize_inference_device_mode(device_mode or effective_settings.get("device_mode"))
    response = await asyncio.to_thread(ums_client.infer, model_id, payload, resolved_device_mode)
    if callable(record_model_execution):
        try:
            if isinstance(response, dict) and isinstance(response.get("model_execution"), dict):
                record_model_execution(response["model_execution"])
        except Exception:
            logger.debug("Model execution recorder failed", exc_info=True)
    return _extract_content(response)


def _is_model_failover_blocked(exc: Exception) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return True
    if isinstance(exc, UMSBusyError):
        return True
    message = str(exc).lower()
    return "429" in message or "busy" in message or "cancel" in message


def _build_model_execution_event(
    *,
    selection: Any,
    used_model_id: str,
    fallback_used: bool,
    fallback_reason: Optional[str],
    attempt_count: int,
) -> Dict[str, Any]:
    payload = dict(selection.to_dict() if hasattr(selection, "to_dict") else selection or {})
    payload.update(
        {
            "primary_model_id": payload.get("resolved_model_id") or payload.get("primary_model_id"),
            "fallback_model_id": payload.get("fallback_model_id") if payload.get("fallback_available") else None,
            "used_model_id": used_model_id,
            "fallback_used": bool(fallback_used),
            "fallback_reason": fallback_reason,
            "attempt_count": max(1, int(attempt_count)),
            "status": "fallback_completed" if fallback_used else "completed",
        }
    )
    return payload


def _infer_with_model_failover(
    selection: Any,
    payload: Dict[str, Any],
    device_mode: str,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    primary_model_id = str(getattr(selection, "resolved_model_id", "") or "")
    fallback_model_id = str(getattr(selection, "fallback_model_id", "") or "")
    if not fallback_model_id or fallback_model_id == primary_model_id:
        fallback_model_id = ""
    attempts = [primary_model_id] + ([fallback_model_id] if fallback_model_id else [])
    last_error: Optional[Exception] = None
    for attempt_idx, model_id in enumerate(attempts, start=1):
        try:
            request_payload = dict(payload)
            if prompt is not None:
                request_payload["prompt"] = prompt
            response = ums_client.infer(model_id, request_payload, device_mode)
            return {
                "response": response,
                "model_execution": _build_model_execution_event(
                    selection=selection,
                    used_model_id=model_id,
                    fallback_used=attempt_idx > 1,
                    fallback_reason=str(last_error) if attempt_idx > 1 and last_error is not None else None,
                    attempt_count=attempt_idx,
                ),
            }
        except Exception as exc:
            if _is_model_failover_blocked(exc):
                raise
            last_error = exc
            if attempt_idx < len(attempts):
                logger.warning(
                    "Model failover retry model=%s fallback=%s error=%s",
                    primary_model_id,
                    fallback_model_id or None,
                    exc,
                    exc_info=True,
                )
                inc_metric_counter(
                    "llm_tools_platform_fallback_events_total",
                    labels={
                        "component": "agent_api",
                        "fallback": "model_failover_retry",
                        "source": "orchestrator",
                    },
                )
                continue
            raise


def _create_failover_embed_fn(
    selection: Any,
    *,
    record_model_execution: Optional[Any] = None,
) -> Optional[Any]:
    model_id = str(getattr(selection, "resolved_model_id", "") or "")
    if not model_id:
        return None
    embed_fn = create_ums_embed_fn(model_id=model_id)
    if embed_fn is None or not callable(record_model_execution):
        return embed_fn

    def _wrapped(texts: List[str]) -> np.ndarray:
        result = embed_fn(texts)
        event = getattr(embed_fn, "last_model_execution", None)
        if isinstance(event, dict):
            try:
                record_model_execution(event)
            except Exception:
                logger.debug("Model execution recorder failed", exc_info=True)
        setattr(_wrapped, "last_model_execution", event)
        return result

    setattr(_wrapped, "last_model_execution", getattr(embed_fn, "last_model_execution", None))
    return _wrapped


def _build_api_execution_dependencies(request: OrchestrationRequest, effective_settings: Dict[str, Any]) -> ExecutionDependencies:
    session_docs = request.session_docs or {}
    document_bindings = list(request.document_bindings or [])
    retrieval_embed_fn: Optional[Any] = None
    retrieval_embedder_model_id: Optional[str] = None
    model_execution_events: List[Dict[str, Any]] = []

    def _build_document_binding_doc_list() -> List[Dict[str, Any]]:
        if not document_bindings:
            return []
        docs: List[Dict[str, Any]] = []
        by_identity = {}
        by_path = {}
        for name, info in session_docs.items():
            if not isinstance(info, dict):
                continue
            if info.get("document_id"):
                by_identity[str(info["document_id"])] = (name, info)
            if info.get("path"):
                by_path[str(info["path"])] = (name, info)
        for index, binding in enumerate(document_bindings, start=1):
            session_name = str(binding.get("display_name") or "")
            session_info = {}
            matched = by_identity.get(str(binding.get("resolved_identity") or "")) or by_identity.get(
                str(binding.get("document_id") or "")
            ) or by_path.get(str(binding.get("path") or ""))
            if matched is not None:
                session_name, session_info = matched
            docs.append(
                {
                    "document_id": str(binding.get("document_id")),
                    "version_id": str(binding.get("version_id")),
                    "display_name": str(binding.get("display_name") or session_name or binding.get("document_id")),
                    "path": binding.get("path"),
                    "text": str((session_info or {}).get("text") or ""),
                    "report_generated": bool((session_info or {}).get("report_generated")),
                    "thread_id": binding.get("thread_id"),
                    "expires_at": binding.get("expires_at"),
                    "source_scope": str(binding.get("source_scope") or "session"),
                    "ingestion_status": str(binding.get("ingestion_status") or "registered"),
                    "order_index": index,
                }
            )
        return docs

    def _docs_list() -> List[Dict[str, Any]]:
        return _build_document_binding_doc_list() or _build_session_doc_list(session_docs)

    def _active_doc_ids() -> List[str]:
        if request.active_doc_ids:
            return list(request.active_doc_ids)
        return [str(doc["document_id"]) for doc in _docs_list() if doc.get("document_id")]

    def _active_docs() -> List[Dict[str, Any]]:
        docs = _docs_list()
        active_doc_ids = set(_active_doc_ids())
        if not active_doc_ids:
            return docs
        selected = [doc for doc in docs if str(doc.get("document_id")) in active_doc_ids]
        return selected or docs

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
            record_model_execution=_record_model_execution,
            allow_sync_retry=kwargs.get("allow_sync_retry"),
            raise_on_error=kwargs.get("raise_on_error"),
            summary_stage=kwargs.get("summary_stage"),
        )

    def _record_model_execution(event: Dict[str, Any]) -> None:
        if isinstance(event, dict):
            model_execution_events.append(dict(event))

    def _get_model_execution_events() -> List[Dict[str, Any]]:
        return [dict(event) for event in model_execution_events]

    async def _noop_async(*args: Any, **kwargs: Any) -> None:
        return None

    def _resolve_retrieval_selection(model_id: Optional[str] = None) -> Any:
        if model_id:
            return resolve_execution_plan(requested_model_id=str(model_id))
        retrieval_resolution = effective_settings.get("resolved_retrieval_embedder_resolution")
        if retrieval_resolution is not None and getattr(retrieval_resolution, "resolved_model_id", None):
            return retrieval_resolution
        if isinstance(retrieval_resolution, dict):
            resolved_model_id = str(retrieval_resolution.get("resolved_model_id") or "").strip()
            if resolved_model_id:
                return resolve_execution_plan(requested_model_id=resolved_model_id)
        effective_model_id = str(effective_settings.get("resolved_retrieval_embedder_model_id") or "").strip()
        if effective_model_id:
            return resolve_execution_plan(requested_model_id=effective_model_id)
        return resolve_model_selection("legal.embedder")

    def _get_retrieval_embed_fn() -> Any:
        nonlocal retrieval_embed_fn, retrieval_embedder_model_id
        if retrieval_embed_fn is not None:
            return retrieval_embed_fn
        retrieval_selection = _resolve_retrieval_selection()
        primary_model_id = str(getattr(retrieval_selection, "resolved_model_id", "") or "").strip()
        fallback_model_id = str(getattr(retrieval_selection, "fallback_model_id", "") or "").strip()
        candidate_model_ids: List[str] = []

        def _add_candidate(model_id: Optional[str]) -> None:
            candidate = str(model_id or "").strip()
            if candidate and candidate not in candidate_model_ids:
                candidate_model_ids.append(candidate)

        _add_candidate(primary_model_id)
        if fallback_model_id and fallback_model_id != primary_model_id:
            _add_candidate(fallback_model_id)
        _add_candidate(os.getenv("LEGAL_EMBEDDER_MODEL"))
        _add_candidate("labse-embedding")

        for candidate_model_id in candidate_model_ids:
            candidate_selection = (
                retrieval_selection
                if candidate_model_id == primary_model_id
                else _resolve_retrieval_selection(candidate_model_id)
            )
            candidate_embed_fn = _create_failover_embed_fn(
                candidate_selection,
                record_model_execution=_record_model_execution,
            )
            if candidate_embed_fn is None:
                continue
            retrieval_embed_fn = candidate_embed_fn
            retrieval_embedder_model_id = candidate_model_id
            if candidate_model_id != primary_model_id:
                effective_settings["resolved_retrieval_embedder_model_id"] = candidate_model_id
                effective_settings["resolved_retrieval_embedder_resolution"] = candidate_selection
                logger.warning(
                    "Retrieval embedder fallback activated primary=%s fallback=%s",
                    primary_model_id or None,
                    candidate_model_id,
                )
            return retrieval_embed_fn
        retrieval_embed_fn = None
        return None

    async def _ensure_rag_index_for_doc_ids(doc_ids: List[str], *args: Any, **kwargs: Any) -> bool:
        if not request.thread_id or not document_bindings:
            return False
        embed_fn = _get_retrieval_embed_fn()
        if embed_fn is None:
            return False

        docs_by_id = {
            str(doc.get("document_id")): doc
            for doc in _docs_list()
            if doc.get("document_id")
        }
        bindings_by_id = {
            str(binding.get("document_id")): binding
            for binding in document_bindings
            if binding.get("document_id")
        }
        target_doc_ids = [str(doc_id) for doc_id in (doc_ids or []) if str(doc_id).strip()]
        if not target_doc_ids:
            target_doc_ids = list(docs_by_id.keys())

        binding_store = get_document_binding_store()
        kb_store = get_knowledge_base_store()
        embedding_model_id = str(
            retrieval_embedder_model_id
            or effective_settings.get("resolved_retrieval_embedder_model_id")
            or getattr(_resolve_retrieval_selection(), "resolved_model_id", "")
            or "labse"
        )
        indexed_any = False
        for doc_id in target_doc_ids:
            doc = docs_by_id.get(doc_id)
            binding = bindings_by_id.get(doc_id)
            if doc is None or binding is None:
                continue
            text = str(doc.get("text") or "").strip()
            if not text:
                continue
            if str(doc.get("ingestion_status") or binding.get("ingestion_status") or "").lower() == "indexed":
                continue
            await asyncio.to_thread(
                ingest_text_source_sync,
                collection_id=f"session:{request.thread_id}",
                display_name=str(doc.get("display_name") or binding.get("display_name") or doc_id),
                text=text,
                store=kb_store,
                mime_type="text/plain",
                index_version="session_v1",
                embedding_model_id=embedding_model_id,
                chunking_version="session_v1",
                embed_fn=embed_fn,
                source_origin="session_upload",
                content_hash_override=str(binding.get("version_id") or doc_id),
                base_metadata={
                    "document_id": doc_id,
                    "document_version_id": binding.get("version_id"),
                    "thread_id": request.thread_id,
                    "source_scope": "session",
                    "expires_at": binding.get("expires_at"),
                    "storage_path": binding.get("path"),
                },
            )
            updated_binding = await asyncio.to_thread(
                binding_store.register_document_binding_sync,
                thread_id=request.thread_id,
                binding={
                    "document_id": binding.get("document_id"),
                    "version_id": binding.get("version_id"),
                    "upload_id": binding.get("upload_id"),
                    "file_id": binding.get("file_id"),
                    "label": binding.get("display_name"),
                    "file_path": binding.get("path"),
                    "ingestion_status": "indexed",
                },
                source_scope=str(binding.get("source_scope") or "session"),
            )
            serialized_binding = _serialize_document_binding(updated_binding)
            binding.update(serialized_binding)
            doc["ingestion_status"] = "indexed"
            doc["thread_id"] = serialized_binding.get("thread_id")
            doc["expires_at"] = serialized_binding.get("expires_at")
            indexed_any = True
        if indexed_any:
            request.document_bindings = [dict(item) for item in document_bindings]
        return indexed_any

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
        get_active_doc_ids=_active_doc_ids,
        get_all_docs=_docs_list,
        get_active_docs=_active_docs,
        get_report_docs=lambda: [doc for doc in _docs_list() if doc.get("report_generated")],
        resolve_target_doc_name=lambda query, docs: None,
        is_report_query=lambda query: False,
        ensure_rag_index_for_doc_ids=_ensure_rag_index_for_doc_ids,
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
        record_model_execution=_record_model_execution,
        get_model_execution_events=_get_model_execution_events,
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
        uploads_path = os.getenv("UPLOADS_DIR", os.path.join(base_dir, "uploads"))
        if not os.path.exists(uploads_path):
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


def _normalize_openwebui_forwarded_file_text(item: Dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    file_payload = item.get("file")
    if not isinstance(file_payload, dict):
        return ""
    file_data = file_payload.get("data")
    if not isinstance(file_data, dict):
        return ""
    embedded_content = file_data.get("content")
    if isinstance(embedded_content, str):
        return embedded_content.strip()
    return ""


def _extract_openwebui_forwarded_context(
    data: Dict[str, Any],
    request: Optional[Request] = None,
) -> Dict[str, Any]:
    raw_files = data.get("files")
    files = raw_files if isinstance(raw_files, list) else []
    handoff_state = data.get("openwebui_session_rag_handoff")
    handoff_payload = handoff_state if isinstance(handoff_state, dict) else {}

    thread_candidates = [
        data.get("thread_id"),
        data.get("session_id"),
        handoff_payload.get("chat_id"),
    ]
    if request is not None:
        headers = getattr(request, "headers", {}) or {}
        thread_candidates.extend(
            [
                headers.get("X-OpenWebUI-Chat-Id"),
                headers.get("x-openwebui-chat-id"),
            ]
        )
    thread_id = next((str(item).strip() for item in thread_candidates if str(item or "").strip()), None)

    session_docs: Dict[str, Any] = {}
    attachments_meta: List[Dict[str, Any]] = []
    active_doc_ids: List[str] = []
    dedup_tokens: List[str] = []

    for index, raw_item in enumerate(files, start=1):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        file_payload = item.get("file")
        file_meta = file_payload.get("meta") if isinstance(file_payload, dict) and isinstance(file_payload.get("meta"), dict) else {}
        file_data = file_payload.get("data") if isinstance(file_payload, dict) and isinstance(file_payload.get("data"), dict) else {}
        embedded_metadata = file_data.get("metadata") if isinstance(file_data.get("metadata"), dict) else {}

        display_name = str(
            item.get("name")
            or file_meta.get("name")
            or embedded_metadata.get("name")
            or f"openwebui-file-{index}"
        ).strip()
        document_id = str(
            item.get("id")
            or file_meta.get("id")
            or file_meta.get("file_id")
            or embedded_metadata.get("file_id")
            or display_name
        ).strip()
        text = _normalize_openwebui_forwarded_file_text(item)
        storage_path = str(
            file_meta.get("path")
            or embedded_metadata.get("path")
            or embedded_metadata.get("source")
            or ""
        ).strip()
        mime_type = str(
            item.get("content_type")
            or file_meta.get("content_type")
            or embedded_metadata.get("content_type")
            or item.get("type")
            or "application/octet-stream"
        ).strip()
        size_value = item.get("size")
        if size_value is None:
            size_value = file_meta.get("size")
        if size_value is None:
            size_value = len(text.encode("utf-8")) if text else 0
        try:
            size = int(size_value or 0)
        except (TypeError, ValueError):
            size = 0

        attachments_meta.append(
            {
                "name": display_name,
                "path": storage_path,
                "size": size,
                "type": mime_type,
                "document_id": document_id,
            }
        )
        active_doc_ids.append(document_id)

        session_doc_payload: Dict[str, Any] = {
            "document_id": document_id,
            "path": storage_path,
            "text": text,
        }
        if thread_id:
            session_doc_payload["thread_id"] = thread_id
        if item.get("id") is not None:
            session_doc_payload["file_id"] = item.get("id")
        session_docs[display_name] = session_doc_payload

        if text:
            text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
            dedup_tokens.append(f"{document_id}:{text_hash}:{len(text)}")
        else:
            dedup_tokens.append(f"{document_id}:{storage_path}:{size}")

    return {
        "thread_id": thread_id,
        "session_docs": session_docs,
        "attachments_meta": attachments_meta,
        "active_doc_ids": active_doc_ids,
        "dedup_tokens": dedup_tokens,
    }


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
    attachments_meta: Optional[List[Dict[str, Any]]] = None,
    active_doc_ids: Optional[List[str]] = None,
    thread_id: Optional[str] = None,
) -> OrchestrationRequest:
    target_model = str(data.get("model") or "llm-tools-platform")
    system_prompt = _extract_openai_system_prompt(messages)
    history = _normalize_openai_history(messages, latest_user_query=user_query)
    resolved_active_doc_ids = [str(item) for item in (active_doc_ids or []) if str(item).strip()]
    if not resolved_active_doc_ids:
        resolved_active_doc_ids = [att.name for att in attachments]
    if not resolved_active_doc_ids:
        for name, info in (session_docs or {}).items():
            if not isinstance(info, dict):
                continue
            resolved_active_doc_ids.append(str(info.get("document_id") or name))
    resolved_attachments_meta = attachments_meta if attachments_meta is not None else [att.model_dump() for att in attachments]

    request_payload: Dict[str, Any] = {
        "message": user_query,
        "history": history,
        "attachments_meta": resolved_attachments_meta,
        "active_doc_ids": resolved_active_doc_ids,
        "file_count": len(resolved_attachments_meta or resolved_active_doc_ids),
        "has_session_docs": bool(session_docs or resolved_attachments_meta),
        "session_docs": session_docs or {},
    }
    if thread_id:
        request_payload["thread_id"] = thread_id
    if system_prompt:
        request_payload["custom_system_prompt"] = system_prompt
    if target_model == "llm-tools-platform" and request_payload["has_session_docs"]:
        request_payload["assistant_mode"] = "specific_tasks"
        request_payload["rag_scope"] = "session_rag"

    if target_model != "llm-tools-platform":
        request_payload.update(
            {
                "assistant_mode": "general_chat",
                "runtime_mode": "chat_only",
                "model_profile": "default-chat",
                "tool_scope": "chat",
            }
        )

    return OrchestrationRequest(**request_payload)


def _compute_openai_dedup_key(
    *,
    target_model: str,
    user_query: str,
    attachments: List[FileAttachment],
    forwarded_file_tokens: Optional[List[str]] = None,
) -> str:
    if attachments:
        files_hash = _compute_files_hash([item.path for item in attachments])
    elif forwarded_file_tokens:
        digest = hashlib.md5()
        for token in sorted(str(item) for item in forwarded_file_tokens if str(item).strip()):
            digest.update(token.encode("utf-8"))
        files_hash = digest.hexdigest()
    else:
        files_hash = "no_files"
    query_hash = hashlib.md5((user_query or "").encode()).hexdigest()[:16]
    return f"{target_model}:{files_hash}:{query_hash}"


async def _stream_openai_compat_response(execution_response: Dict[str, Any]) -> AsyncGenerator[str, None]:
    yield "data: " + json.dumps({"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}) + "\n\n"
    text = _strip_openai_compat_telemetry_footer(str(execution_response.get("assistant_message") or ""))
    if text:
        yield "data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]}) + "\n\n"
    yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n"
    yield "data: [DONE]\n\n"


def _strip_openai_compat_telemetry_footer(text: str) -> str:
    marker = "\n\n---\nTiming / Quality"
    value = str(text or "")
    if marker not in value:
        return value
    return value.split(marker, 1)[0].rstrip()


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
                "message": {"role": "assistant", "content": _strip_openai_compat_telemetry_footer(text)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


_RAW_BUSY_MESSAGE = "Модель занята предыдущим тяжёлым запросом. Дождитесь освобождения слота или остановите активный запуск."


def _is_busy_http_error(exc: HTTPException) -> bool:
    if int(getattr(exc, "status_code", 0) or 0) != 429:
        return False
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return str(detail.get("status") or "").strip().lower() == "busy"
    return "busy" in str(detail).lower() or "429" in str(detail).lower()


def _extract_busy_message(detail: Any) -> str:
    if isinstance(detail, dict):
        message = str(detail.get("message") or "").strip()
        if message:
            return message
    return _RAW_BUSY_MESSAGE


def _build_raw_busy_non_stream_response(*, target_model: str, detail: Any) -> Dict[str, Any]:
    return _build_openai_chat_completion_response(
        target_model=target_model,
        text=_extract_busy_message(detail),
    )


def _build_raw_busy_stream_response(*, detail: Any) -> Dict[str, Any]:
    return {"assistant_message": _extract_busy_message(detail)}


_CHAT_VISIBLE_MODEL_STATUSES = {"ready", "running"}
_CHAT_VISIBLE_RUNTIME_TYPES = {"gguf", "gguf-vl"}


def _load_ums_model_catalog() -> Optional[Dict[str, Dict[str, Any]]]:
    try:
        response = httpx.get(f"{ums_client.base_url.rstrip('/')}/models", timeout=2.0)
        response.raise_for_status()
        models = response.json().get("models")
    except Exception as exc:
        logger.debug("failed to load UMS model catalog for OpenAI model listing: %s", exc)
        return None
    if not isinstance(models, list):
        return None
    catalog: Dict[str, Dict[str, Any]] = {}
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("model_id") or item.get("id") or "").strip()
        if model_id:
            catalog[model_id] = item
    return catalog


def _is_chat_visible_model(model_id: str, config: Dict[str, Any], *, require_catalog_status: bool) -> bool:
    if model_id == "llm-tools-platform":
        return False
    kind = str(config.get("kind") or "").strip()
    if kind not in {"llm", "vision"}:
        return False
    user_selectable = config.get("user_selectable")
    if user_selectable is None:
        user_selectable = True
    if not bool(user_selectable):
        return False

    status = str(config.get("status") or "").strip().lower()
    if require_catalog_status:
        return status in _CHAT_VISIBLE_MODEL_STATUSES
    if status and status not in _CHAT_VISIBLE_MODEL_STATUSES:
        return False

    runtime_type = str(config.get("runtime_type") or config.get("type") or "").strip().lower()
    if runtime_type not in _CHAT_VISIBLE_RUNTIME_TYPES:
        return False
    model_path = str(config.get("path") or config.get("resolved_path") or "").strip()
    if not model_path:
        return False
    if not require_catalog_status and not Path(model_path).exists():
        return False
    if not config.get("port"):
        return False
    return True


def _list_raw_chat_capable_models() -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    ums_catalog = _load_ums_model_catalog()
    catalog = ums_catalog if ums_catalog is not None else get_all_models()
    for model_id, config in catalog.items():
        if not _is_chat_visible_model(
            model_id,
            config,
            require_catalog_status=ums_catalog is not None,
        ):
            continue
        payload.append(
            {
                "id": model_id,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "llm-tools-platform-raw-provider",
            }
        )
    return payload


def _build_model_incompatibility_detail(
    *,
    requested_model_id: str,
    required_capabilities: List[str],
    missing_capabilities: List[str],
    capabilities: Dict[str, Any],
    user_selectable: bool,
) -> Dict[str, Any]:
    return {
        "status": "incompatible_model",
        "requested_model_id": requested_model_id,
        "required_capabilities": list(required_capabilities),
        "missing_capabilities": list(missing_capabilities),
        "user_selectable": bool(user_selectable),
        "capabilities": dict(capabilities),
    }


def _request_requires_vision(data: Dict[str, Any]) -> bool:
    for message in data.get("messages") or []:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if str(part.get("type") or "").strip() == "image_url":
                return True
    return False


def _resolve_default_user_model_id(*, available_ids: set[str]) -> str:
    with suppress(Exception):
        status = ums_client.get_status()
        active_model_id = str(
            (status or {}).get("active_model_id")
            or (status or {}).get("active_heavy_model")
            or ""
        ).strip()
        if active_model_id and active_model_id in available_ids:
            return active_model_id
    default_model_id = str(resolve_model_selection("llm.default_chat").resolved_model_id or "").strip()
    return default_model_id if default_model_id in available_ids else ""


def _resolve_user_facing_model_id(
    data: Dict[str, Any],
    *,
    required_capabilities: Optional[List[str]] = None,
) -> str:
    available_ids = {item["id"] for item in _list_raw_chat_capable_models()}
    requested_model = str(data.get("model") or "").strip()
    target_model = requested_model
    if not target_model or target_model == "llm-tools-platform":
        target_model = _resolve_default_user_model_id(available_ids=available_ids)
    if not target_model:
        raise HTTPException(status_code=503, detail="raw-model-provider-default-missing")

    resolution = resolve_user_model_selection(
        target_model,
        required_capabilities=required_capabilities or [],
    )
    if not resolution.exists_in_registry:
        raise HTTPException(status_code=404, detail=f"unknown-raw-model:{target_model}")
    if target_model not in available_ids or not resolution.compatible:
        raise HTTPException(
            status_code=422,
            detail=_build_model_incompatibility_detail(
                requested_model_id=target_model,
                required_capabilities=resolution.required_capabilities,
                missing_capabilities=resolution.missing_capabilities,
                capabilities=resolution.capabilities,
                user_selectable=resolution.user_selectable,
            ),
        )
    return resolution.resolved_model_id


def _resolve_raw_model_id(data: Dict[str, Any]) -> str:
    required_capabilities: List[str] = []
    if _request_requires_vision(data):
        required_capabilities.append("supports_vision")
    return _resolve_user_facing_model_id(data, required_capabilities=required_capabilities)


def _should_passthrough_native_tool_request(data: Dict[str, Any]) -> bool:
    tools = data.get("tools")
    return isinstance(tools, list) and len(tools) > 0


def _has_openai_tool_result_message(data: Dict[str, Any]) -> bool:
    messages = data.get("messages")
    if not isinstance(messages, list):
        return False
    return any(isinstance(item, dict) and item.get("role") == "tool" for item in messages)


def _resolve_native_tool_passthrough_model_id(data: Dict[str, Any]) -> str:
    required_capabilities = ["supports_tools"]
    if _request_requires_vision(data):
        required_capabilities.append("supports_vision")
    return _resolve_user_facing_model_id(data, required_capabilities=required_capabilities)


def _build_raw_openai_payload(data: Dict[str, Any], *, target_model: str) -> Dict[str, Any]:
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=422, detail="raw-chat-messages-required")
    payload = dict(data)
    payload["model"] = target_model
    return payload


def _sanitize_raw_openai_response(response: Dict[str, Any], *, target_model: str) -> Dict[str, Any]:
    sanitized = dict(response)
    sanitized.pop("model_execution", None)
    sanitized["model"] = target_model
    return sanitized


def _extract_raw_ums_error_detail(response: Optional[httpx.Response]) -> Any:
    if response is None:
        return None
    with suppress(Exception):
        payload = response.json()
        if isinstance(payload, dict) and "detail" in payload:
            return payload["detail"]
        return payload
    with suppress(Exception):
        text = response.text
        if text:
            return text
    return None


def _raise_raw_ums_http_error(
    *,
    response: Optional[httpx.Response],
    exc: Exception,
    stream: bool,
) -> None:
    status_code = getattr(response, "status_code", None)
    if status_code == 429:
        detail = _extract_raw_ums_error_detail(response)
        raise HTTPException(
            status_code=429,
            detail=detail or ("raw-model-stream-saturated" if stream else "raw-model-infer-saturated"),
        ) from exc
    if status_code in {409, 503}:
        detail = _extract_raw_ums_error_detail(response)
        raise HTTPException(status_code=int(status_code), detail=detail or f"raw-model-unavailable:{status_code}") from exc
    raise HTTPException(
        status_code=502,
        detail=f"{'raw-model-stream-failed' if stream else 'raw-model-infer-failed'}:{exc}",
    ) from exc


async def _request_raw_openai_infer(
    *,
    target_model: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    client = await get_shared_client()
    request_body = {
        "model_id": target_model,
        "payload": payload,
        "device_mode": "hybrid",
        "priority": "normal",
    }
    try:
        response = await client.post(f"{ums_client.base_url}/infer", json=request_body)
        response.raise_for_status()
        response_payload = response.json()
    except httpx.HTTPStatusError as exc:
        _raise_raw_ums_http_error(response=exc.response, exc=exc, stream=False)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"raw-model-infer-failed:{exc}") from exc
    if response_payload.get("status") == "success":
        result = response_payload.get("result", {})
        return result if isinstance(result, dict) else {"value": result}
    raise HTTPException(status_code=502, detail=f"raw-model-infer-failed:unexpected-payload:{response_payload}")


async def _open_raw_openai_stream(*, target_model: str, payload: Dict[str, Any]) -> httpx.Response:
    client = await get_shared_client()
    request_body = {
        "model_id": target_model,
        "payload": payload,
        "device_mode": "hybrid",
        "priority": "normal",
        "stream": True,
    }
    response: httpx.Response | None = None
    try:
        request = client.build_request("POST", f"{ums_client.base_url}/infer", json=request_body)
        response = await client.send(request, stream=True)
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        response_obj = exc.response if exc.response is not None else response
        if response_obj is not None:
            with suppress(Exception):
                await response_obj.aclose()
        _raise_raw_ums_http_error(response=exc.response, exc=exc, stream=True)
    except Exception as exc:
        if response is not None:
            with suppress(Exception):
                await response.aclose()
        raise HTTPException(status_code=502, detail=f"raw-model-stream-failed:{exc}") from exc


async def _proxy_raw_openai_stream(response: httpx.Response) -> AsyncGenerator[bytes, None]:
    try:
        async for chunk in response.aiter_raw():
            if chunk:
                yield chunk
    except Exception as exc:
        logger.warning("Raw model stream interrupted: %s", exc)
    finally:
        with suppress(Exception):
            await response.aclose()


async def _proxy_raw_openai_completion(
    data: Dict[str, Any],
    *,
    target_model: str,
    response_model: Optional[str] = None,
):
    payload = _build_raw_openai_payload(data, target_model=target_model)
    stream_mode = bool(data.get("stream", True))
    visible_model = response_model or target_model

    if not stream_mode:
        try:
            response = await _request_raw_openai_infer(target_model=target_model, payload=payload)
        except HTTPException as exc:
            if _is_busy_http_error(exc):
                return _build_raw_busy_non_stream_response(target_model=visible_model, detail=exc.detail)
            raise
        return _sanitize_raw_openai_response(response, target_model=visible_model)

    try:
        stream_response = await _open_raw_openai_stream(target_model=target_model, payload=payload)
    except HTTPException as exc:
        if _is_busy_http_error(exc):
            return StreamingResponse(
                _stream_openai_compat_response(_build_raw_busy_stream_response(detail=exc.detail)),
                media_type="text/event-stream",
            )
        raise
    return StreamingResponse(_proxy_raw_openai_stream(stream_response), media_type="text/event-stream")

# === Health ===

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return PlainTextResponse(render_metrics_text(), media_type="text/plain; version=0.0.4; charset=utf-8")


async def orchestrate(request: OrchestrationRequest, http_request: Optional[Request] = None):
    request.trace_id = request.trace_id or _resolve_http_trace_id(http_request)
    _materialize_request_document_context(request)
    payload = request.model_dump(exclude_none=True)
    apply_tool_contract_to_payload(payload)
    payload.setdefault("execution_surface", "agent_mode")
    effective_settings = resolve_effective_settings(_collect_request_control_plane_with_payload_overrides(request, payload))
    _apply_runtime_model_override(effective_settings, payload.get("resolved_model_id"))
    response = decide_orchestration(
        query=str(payload.get("message", request.message)),
        trace_id=request.trace_id,
        runtime_mode=resolve_request_runtime_mode(payload, effective_settings),
        rag_scope=str(effective_settings.get("rag_scope") or "off"),
        knowledge_collection_id=effective_settings.get("knowledge_collection_id"),
        file_count=int(payload.get("file_count", request.file_count)),
        has_session_docs=bool(payload.get("has_session_docs", request.has_session_docs)),
        session_docs=payload.get("session_docs") or request.session_docs or {},
        classifier_result=payload.get("classifier_result") or request.classifier_result,
        new_files=payload.get("attachments_meta") or request.attachments_meta or [],
        active_doc_ids=payload.get("active_doc_ids") or request.active_doc_ids or [],
        forced_route=payload.get("forced_route") or request.forced_route,
    )
    response.update(build_control_plane_metadata(payload, effective_settings))
    response["effective_settings"] = effective_settings
    inject_tool_contract_metadata(response, payload)
    inc_metric_counter(
        "llm_tools_platform_agent_api_orchestration_requests_total",
        labels={"endpoint": "/orchestrate", "result": str(response.get("route") or "unknown")},
    )
    return response


@app.post("/orchestrate")
async def orchestrate_route(request: OrchestrationRequest, http_request: Request):
    return await orchestrate(request, http_request)


async def execute_orchestration_api(request: OrchestrationRequest, http_request: Optional[Request] = None):
    request.trace_id = request.trace_id or _resolve_http_trace_id(http_request)
    _materialize_request_document_context(request)
    payload = request.model_dump(exclude_none=True)
    apply_tool_contract_to_payload(payload)
    payload.setdefault("execution_surface", "agent_mode")
    effective_settings = resolve_effective_settings(_collect_request_control_plane_with_payload_overrides(request, payload))
    _apply_runtime_model_override(effective_settings, payload.get("resolved_model_id"))
    deps = _build_api_execution_dependencies(request, effective_settings)
    payload["runtime_mode"] = resolve_request_runtime_mode(payload, effective_settings)
    payload["effective_settings"] = effective_settings
    if should_start_async_tool_job(payload):
        job = submit_async_tool_job(
            request_payload=payload,
            deps=deps,
            execute_fn=execute_orchestration,
            route_prefix=_resolve_tool_job_route_prefix(http_request),
        )
        return build_accepted_tool_job_response(job, payload)
    response = await execute_orchestration(payload, deps=deps)
    inject_tool_contract_metadata(response, payload)
    inc_metric_counter(
        "llm_tools_platform_agent_api_orchestration_requests_total",
        labels={"endpoint": "/execute_orchestration", "result": str(response.get("route") or "unknown")},
    )
    return response


@app.post("/execute_orchestration")
async def execute_orchestration_route(request: OrchestrationRequest, http_request: Request):
    response = await execute_orchestration_api(request, http_request)
    if response.get("status") == "accepted":
        return JSONResponse(status_code=202, content=response)
    return response


async def get_tool_job_status_route(job_id: str):
    job = get_tool_job_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown-tool-job:{job_id}")
    return build_tool_job_status_response(job)


async def get_tool_job_result_route(job_id: str):
    try:
        return get_tool_job_result(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown-tool-job:{job_id}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def cancel_tool_job_route(job_id: str):
    try:
        job = cancel_tool_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown-tool-job:{job_id}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return build_tool_job_status_response(job)


def _test_mode_enabled() -> bool:
    return str(os.getenv("LLM_TOOLS_PLATFORM_TEST_MODE", "0")).strip() == "1"


@app.post("/debug/test/tool-jobs/{job_id}/transition")
async def debug_transition_tool_job_route(job_id: str, payload: ToolJobTransitionPayload):
    if not _test_mode_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        job = force_tool_job_transition(
            job_id,
            status=payload.status,
            response=payload.response,
            error_summary=payload.error_summary,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown-tool-job:{job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return build_tool_job_status_response(job)


async def _execute_openapi_tool_request(
    request: OrchestrationRequest,
    http_request: Optional[Request] = None,
):
    return await execute_orchestration_api(request, http_request)


app.include_router(
    create_openapi_tools_router(
        app=app,
        orchestration_request_model=OrchestrationRequest,
        execute_orchestration_request=_execute_openapi_tool_request,
    )
)


# === OpenAI Compatible API ===

@app.get("/raw/v1/models")
def list_raw_models():
    return {"object": "list", "data": _list_raw_chat_capable_models()}


@app.post("/raw/v1/chat/completions")
async def raw_openai_completions(request: Request):
    data = await request.json()
    target_model = _resolve_raw_model_id(data)
    return await _proxy_raw_openai_completion(data, target_model=target_model)

@app.get("/v1/models")
def list_models():
    """Возвращает список пользовательских моделей из канонического backend-каталога."""
    return {"object": "list", "data": _list_raw_chat_capable_models()}

@app.post("/v1/chat/completions")
async def openai_completions(request: Request):
    data = await request.json()
    stream_mode = bool(data.get("stream", True))
    requested_target_model = str(data.get("model") or "").strip()

    if _should_passthrough_native_tool_request(data):
        raw_target_model = _resolve_native_tool_passthrough_model_id(data)
        visible_model = (
            requested_target_model
            if requested_target_model and requested_target_model != "llm-tools-platform"
            else raw_target_model
        )
        return await _proxy_raw_openai_completion(
            data,
            target_model=raw_target_model,
            response_model=visible_model,
        )

    if requested_target_model != "llm-tools-platform" or _has_openai_tool_result_message(data):
        raw_target_model = _resolve_raw_model_id(data)
        return await _proxy_raw_openai_completion(data, target_model=raw_target_model)

    messages = data.get("messages", [])
    target_model = "llm-tools-platform"

    user_query = _extract_latest_user_query(messages)
    found_files: List[FileAttachment] = []
    dedup_key = _compute_openai_dedup_key(
        target_model=target_model,
        user_query=user_query,
        attachments=found_files,
    )
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
            "llm_tools_platform_agent_api_openai_dedup_hits_total",
            labels={"model_family": "llm-tools-platform" if target_model == "llm-tools-platform" else "direct"},
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

    if not stream_mode:
        try:
            compat_request = _build_openai_compat_request(
                data=data,
                messages=messages,
                user_query=user_query,
                attachments=found_files,
                session_docs={},
            )
            _materialize_request_document_context(compat_request)
            effective_settings = resolve_effective_settings(_collect_request_control_plane(compat_request))
            if target_model != "llm-tools-platform":
                effective_settings["resolved_model_id"] = target_model
            payload = compat_request.model_dump(exclude_none=True)
            payload["execution_surface"] = "agent_mode" if target_model == "llm-tools-platform" else "compat_chat"
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
                session_docs={},
            )
            _materialize_request_document_context(compat_request)
            effective_settings = resolve_effective_settings(_collect_request_control_plane(compat_request))
            if target_model != "llm-tools-platform":
                effective_settings["resolved_model_id"] = target_model
            payload = compat_request.model_dump(exclude_none=True)
            payload["execution_surface"] = "agent_mode" if target_model == "llm-tools-platform" else "compat_chat"
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
