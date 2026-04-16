from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Type
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from orchestrator.shared.report_utils import get_uploads_dir
from orchestrator.tool_catalog import get_tool_definition
from orchestrator.tool_schemas import (
    AcceptedToolResult,
    AnalyzeDocumentDeepRequest,
    AnalyzeDocumentFastRequest,
    AnalyzeEquipmentDeepRequest,
    AnalyzeEquipmentFastRequest,
    AskDocumentRequest,
    CompareDocumentsDeepRequest,
    CompareDocumentsFastRequest,
    CompletedToolResult,
    DocumentRef,
    ExecutionMetadata,
    ToolArtifact,
    ToolJobStatus,
    ToolRequest,
    ToolSource,
)
from orchestrator.tool_bindings import build_result_available_actions, summarize_tool_binding_catalog
from orchestrator.tool_execution import build_tool_job_status_response, cancel_tool_job, get_tool_job_result, get_tool_job_store

_TOOL_SERVER_BEARER = HTTPBearer(auto_error=False)
_TOOL_ROUTE_PREFIXES = ("/tools/", "/tool-jobs/")
_TOOL_SERVER_ALIAS_PREFIX = "/tool-server"
_REPORT_FILENAME_RE = re.compile(r"\*\*Отчет (?:сохранен|уже сохранен):\*\*\s*`([^`]+)`")
_ALLOWED_REPORT_EXTENSIONS = {".pdf", ".md"}


class ToolJobDeliveryRequest(BaseModel):
    result_message_id: str
    terminal_emitted_at: Optional[str] = None


def _env_flag(name: str, default: bool) -> bool:
    value = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _tool_server_enabled() -> bool:
    return _env_flag("OPENAPI_TOOL_SERVER_ENABLED", True)


def _tool_server_token() -> str:
    return str(os.getenv("OPENAPI_TOOL_SERVER_TOKEN") or "llm-tools-platform-tool-server-dev-token").strip()


def _allowed_origins() -> set[str]:
    raw = str(os.getenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS") or "").strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def _require_tool_server_access(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_TOOL_SERVER_BEARER),
) -> None:
    if not _tool_server_enabled():
        raise HTTPException(status_code=404, detail="tool-server-disabled")

    token = _tool_server_token()
    if credentials is None or credentials.scheme.lower() != "bearer" or credentials.credentials != token:
        raise HTTPException(
            status_code=401,
            detail="tool-server-auth-required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    allowed_origins = _allowed_origins()
    origin = str(request.headers.get("Origin") or "").strip()
    if allowed_origins and origin and origin not in allowed_origins:
        raise HTTPException(status_code=403, detail="tool-server-origin-denied")


def _require_tool_server_schema_access(request: Request) -> None:
    if not _tool_server_enabled():
        raise HTTPException(status_code=404, detail="tool-server-disabled")

    allowed_origins = _allowed_origins()
    origin = str(request.headers.get("Origin") or "").strip()
    if allowed_origins and origin and origin not in allowed_origins:
        raise HTTPException(status_code=403, detail="tool-server-origin-denied")


def _extract_ref_identity(document_ref: DocumentRef) -> Optional[str]:
    return document_ref.resolved_identity()


def _build_tool_message(tool_request: ToolRequest) -> str:
    if isinstance(tool_request, AskDocumentRequest):
        return tool_request.question
    if isinstance(tool_request, AnalyzeDocumentFastRequest):
        return tool_request.analysis_goal or "Сделай быстрый анализ документа"
    if isinstance(tool_request, AnalyzeDocumentDeepRequest):
        return tool_request.analysis_goal or "Сделай глубокий анализ документа"
    if isinstance(tool_request, CompareDocumentsFastRequest):
        return tool_request.comparison_goal or "Сравни документы по ключевым различиям"
    if isinstance(tool_request, CompareDocumentsDeepRequest):
        return tool_request.comparison_goal or "Сделай глубокое сравнение документов"
    if isinstance(tool_request, AnalyzeEquipmentFastRequest):
        return tool_request.equipment_query
    if isinstance(tool_request, AnalyzeEquipmentDeepRequest):
        return tool_request.equipment_query
    return str(tool_request.user_inputs.get("message") or "")


def _resolve_host_uploads_dir() -> Optional[str]:
    for candidate in (
        str(os.getenv("HOST_UPLOADS_DIR") or "").strip(),
        str(os.getenv("UPLOADS_DIR") or "").strip(),
    ):
        if candidate:
            return candidate
    repo_default = Path(__file__).resolve().parents[1] / "open_webui_uploads"
    return str(repo_default)


def _normalize_upload_path(path: Any) -> Any:
    normalized = str(path or "").strip()
    if not normalized:
        return path
    if os.path.exists(normalized):
        return normalized

    host_uploads_dir = _resolve_host_uploads_dir()
    if not host_uploads_dir:
        return normalized

    for container_root in (
        str(os.getenv("OPENWEBUI_UPLOADS_CONTAINER_DIR") or "/app/backend/data/uploads").strip(),
        "/app/uploads",
    ):
        if not container_root:
            continue
        normalized_root = os.path.normpath(container_root)
        normalized_path = os.path.normpath(normalized)
        if normalized_path == normalized_root or normalized_path.startswith(normalized_root + os.sep):
            relative = os.path.relpath(normalized_path, normalized_root)
            return os.path.normpath(os.path.join(host_uploads_dir, relative))
    return normalized


def _normalize_session_docs(raw_session_docs: Any) -> Dict[str, Any]:
    if not isinstance(raw_session_docs, dict):
        return {}
    normalized: Dict[str, Any] = {}
    for name, info in raw_session_docs.items():
        if not isinstance(info, dict):
            continue
        normalized[str(name)] = {
            **info,
            "path": _normalize_upload_path(info.get("path")),
        }
    return normalized


def _normalize_attachments_meta(raw_attachments_meta: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_attachments_meta, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in raw_attachments_meta:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                **item,
                "path": _normalize_upload_path(item.get("path")),
            }
        )
    return normalized


def _normalize_document_refs(document_refs: List[DocumentRef]) -> List[DocumentRef]:
    normalized_refs: List[DocumentRef] = []
    for item in document_refs:
        payload = item.model_dump()
        payload["file_path"] = _normalize_upload_path(payload.get("file_path"))
        normalized_refs.append(DocumentRef(**payload))
    return normalized_refs


def _build_document_ref_bindings(document_refs: List[DocumentRef]) -> List[Dict[str, Any]]:
    bindings: List[Dict[str, Any]] = []
    for item in document_refs:
        bindings.append(
            {
                "label": item.label,
                "document_id": item.document_id,
                "upload_id": item.upload_id,
                "file_id": item.file_id,
                "version_id": item.version_id,
                "session_file_ref": item.session_file_ref,
                "file_path": item.file_path,
                "resolved_identity": item.resolved_identity(),
                "resolved_identity_kind": item.resolved_identity_kind(),
                "has_canonical_identity": item.has_canonical_identity(),
            }
        )
    return bindings


def _extract_forwarded_header(request: Request, *names: str) -> Optional[str]:
    for name in names:
        value = str(request.headers.get(name) or "").strip()
        if value:
            return value
    return None


def _extract_forwarded_openwebui_context(request: Request) -> Dict[str, str]:
    chat_id = _extract_forwarded_header(
        request,
        "X-OpenWebUI-Chat-Id",
        "X-Open-WebUI-Chat-Id",
    )
    message_id = _extract_forwarded_header(
        request,
        "X-OpenWebUI-Message-Id",
        "X-Open-WebUI-Message-Id",
    )
    context: Dict[str, str] = {}
    if chat_id:
        context["chat_id"] = chat_id
    if message_id:
        context["message_id"] = message_id
    return context


def _build_orchestration_payload(tool_request: ToolRequest) -> Dict[str, Any]:
    normalized_document_refs = _normalize_document_refs(tool_request.document_refs)
    active_doc_ids = [ref_id for ref_id in (_extract_ref_identity(item) for item in normalized_document_refs) if ref_id]
    active_canonical_doc_ids = [
        ref_id for ref_id in (item.canonical_identity() for item in normalized_document_refs) if ref_id
    ]
    ui_state = dict(tool_request.ui_hints or {})
    if normalized_document_refs:
        ui_state.update(
            {
                "document_ref_bindings": _build_document_ref_bindings(normalized_document_refs),
                "active_canonical_doc_ids": active_canonical_doc_ids,
                "has_canonical_document_refs": bool(active_canonical_doc_ids),
                "document_ref_resolution_mode": "canonical_preferred",
            }
        )
    user_inputs = dict(tool_request.user_inputs or {})
    user_inputs["session_docs"] = _normalize_session_docs(user_inputs.get("session_docs"))
    user_inputs["attachments_meta"] = _normalize_attachments_meta(user_inputs.get("attachments_meta"))
    payload: Dict[str, Any] = {
        "message": _build_tool_message(tool_request),
        "requested_tool": tool_request.tool_name,
        "routing_mode": tool_request.routing_mode,
        "job_mode": tool_request.job_mode,
        "thread_id": tool_request.thread_id,
        "session_id": tool_request.session_id,
        "active_doc_ids": active_doc_ids,
        "file_count": len(active_doc_ids),
        "has_session_docs": bool(active_doc_ids),
        "ui_state": ui_state or None,
    }

    for key in (
        "session_docs",
        "attachments_meta",
        "knowledge_collection_id",
        "rag_scope",
        "model_profile",
        "prompt_profile",
        "runtime_mode",
        "assistant_mode",
        "custom_system_prompt",
        "tool_scope",
    ):
        if key in user_inputs and user_inputs[key] is not None:
            payload[key] = user_inputs[key]
    return payload


def _normalize_tool_sources(raw_sources: Iterable[Dict[str, Any]]) -> List[ToolSource]:
    normalized: List[ToolSource] = []
    for idx, source in enumerate(raw_sources, start=1):
        source_origin = str(source.get("source_origin") or "document")
        source_type = "knowledge_base" if source_origin == "knowledge_base" else "document"
        source_id = str(source.get("source_id") or source.get("document_id") or idx)
        metadata = {
            key: source.get(key)
            for key in (
                "document_id",
                "chunk_id",
                "collection_id",
                "source_origin",
                "page",
                "section",
                "normalized_score",
                "raw_score",
                "quote",
            )
            if source.get(key) is not None
        }
        normalized.append(
            ToolSource(
                source_id=source_id,
                source_type=source_type,
                title=str(source.get("display_name") or source.get("title") or source.get("document_id") or source_id),
                citation_ids=[source_id],
                metadata=metadata,
            )
        )
    return normalized


def _normalize_tool_artifacts(response: Dict[str, Any]) -> List[ToolArtifact]:
    artifacts: List[ToolArtifact] = []
    generated_report = ((response.get("ui_effects") or {}).get("generated_report") if isinstance(response.get("ui_effects"), dict) else None)
    if isinstance(generated_report, dict):
        artifact_id = str(generated_report.get("path") or generated_report.get("name") or "generated-report")
        artifacts.append(
            ToolArtifact(
                artifact_id=artifact_id,
                artifact_type="report",
                url=generated_report.get("path"),
                title=generated_report.get("name") or "Generated report",
                metadata={key: value for key, value in generated_report.items() if value is not None},
            )
        )
    elif isinstance(generated_report, str):
        report_filename = _extract_saved_report_filename(generated_report)
        if report_filename:
            report_ext = Path(report_filename).suffix.lower().lstrip(".")
            artifacts.append(
                ToolArtifact(
                    artifact_id=report_filename,
                    artifact_type="report",
                    url=f"/tool-server/tool-reports/{quote(report_filename, safe='')}",
                    title="Скачать отчёт",
                    metadata={
                        "filename": report_filename,
                        "format": report_ext or None,
                        "download_label": "Скачать отчёт",
                    },
                )
            )
    return artifacts


def _extract_saved_report_filename(report_text: str) -> Optional[str]:
    match = _REPORT_FILENAME_RE.search(str(report_text or ""))
    if not match:
        return None
    filename = os.path.basename(match.group(1).strip())
    return filename or None


def _resolve_report_download_path(filename: str) -> Optional[Path]:
    normalized = os.path.basename(str(filename or "").strip())
    if not normalized or normalized in {".", ".."}:
        return None

    extension = Path(normalized).suffix.lower()
    if extension not in _ALLOWED_REPORT_EXTENSIONS:
        return None

    uploads_dir = Path(get_uploads_dir()).resolve()
    candidate = (uploads_dir / normalized).resolve()
    try:
        candidate.relative_to(uploads_dir)
    except ValueError:
        return None

    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _build_completed_tool_result(tool_request: ToolRequest, response: Dict[str, Any]) -> Dict[str, Any]:
    telemetry = response.get("telemetry") if isinstance(response.get("telemetry"), dict) else None
    raw_execution_metadata = response.get("execution_metadata") if isinstance(response.get("execution_metadata"), dict) else {}
    warnings: List[str] = []
    raw_status = str(raw_execution_metadata.get("status") or "").strip()
    if raw_status and raw_status not in {"completed"}:
        warnings.append(f"legacy_status:{raw_status}")

    structured_result = {
        key: response.get(key)
        for key in ("route", "source_scope_summary", "run_id", "state_ref")
        if response.get(key) is not None
    }
    if telemetry:
        structured_result["telemetry"] = telemetry

    result = CompletedToolResult(
        tool_name=tool_request.tool_name,
        assistant_message=str(response.get("assistant_message") or ""),
        structured_result=structured_result,
        sources=_normalize_tool_sources(response.get("sources") or []),
        artifacts=_normalize_tool_artifacts(response),
        available_actions=build_result_available_actions(tool_request.tool_name),
        execution_metadata=ExecutionMetadata(
            requested_tool=tool_request.tool_name,
            routing_mode=tool_request.routing_mode,
            execution_mode="sync",
            trace_id=response.get("trace_id"),
            runtime_mode=response.get("mode"),
            model_profile=response.get("model_profile"),
            latency_ms=int(telemetry.get("elapsed_ms", 0)) if telemetry and telemetry.get("elapsed_ms") is not None else None,
            warnings=warnings,
        ),
    )
    return result.model_dump()


def _tool_only_routes(app: FastAPI) -> List[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and any(route.path.startswith(prefix) for prefix in _TOOL_ROUTE_PREFIXES)
    ]


def _build_tool_server_openapi(app: FastAPI) -> Dict[str, Any]:
    return get_openapi(
        title="llm-tools-platform OpenAPI Tool Server",
        version=str(app.version),
        description="Tool-only OpenAPI surface for Open WebUI integration.",
        routes=_tool_only_routes(app),
    )


def _build_tool_server_config_payload() -> Dict[str, Any]:
    return {
        "features": {
            "system": False,
        },
        "server": {
            "name": "llm-tools-platform OpenAPI Tool Server",
        },
        "toolUx": summarize_tool_binding_catalog(),
    }


def _tool_openapi_summary(tool_name: str) -> str:
    return get_tool_definition(tool_name).label


def _tool_openapi_description(tool_name: str) -> str:
    return get_tool_definition(tool_name).openapi_description


def create_openapi_tools_router(
    *,
    app: FastAPI,
    orchestration_request_model: Type[Any],
    execute_orchestration_request: Callable[[Any, Optional[Request]], Awaitable[Dict[str, Any]]],
) -> APIRouter:
    router = APIRouter()

    def _register_tool_server_alias(
        path: str,
        endpoint: Callable[..., Awaitable[Any]],
        *,
        methods: List[str],
        response_model: Optional[Type[Any]] = None,
        responses: Optional[Dict[int, Dict[str, Any]]] = None,
    ) -> None:
        alias_name = f"tool_server_alias_{endpoint.__name__}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
        router.add_api_route(
            path,
            endpoint,
            methods=methods,
            response_model=response_model,
            responses=responses,
            dependencies=[Depends(_require_tool_server_access)],
            include_in_schema=False,
            name=alias_name,
        )

    async def _execute_tool(tool_request: ToolRequest, http_request: Request) -> Dict[str, Any]:
        payload = _build_orchestration_payload(tool_request)
        payload.update(_extract_forwarded_openwebui_context(http_request))
        orchestration_request = orchestration_request_model(**payload)
        response = await execute_orchestration_request(orchestration_request, http_request)
        if response.get("status") == "accepted":
            return response
        return _build_completed_tool_result(tool_request, response)

    @router.get(
        "/tool-reports/{filename:path}",
        include_in_schema=False,
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def tool_report_download(filename: str, request: Request):
        report_path = _resolve_report_download_path(filename)
        if report_path is None:
            raise HTTPException(status_code=404, detail=f"unknown-tool-report:{filename}")

        media_type = "application/pdf" if report_path.suffix.lower() == ".pdf" else "text/markdown; charset=utf-8"
        return FileResponse(
            path=report_path,
            media_type=media_type,
            filename=report_path.name,
        )

    @router.get(
        "/tool-server/openapi.json",
        dependencies=[Depends(_require_tool_server_schema_access)],
        include_in_schema=False,
    )
    async def tool_server_openapi_schema() -> Dict[str, Any]:
        return _build_tool_server_openapi(app)

    @router.get(
        "/tool-server/api/config",
        include_in_schema=False,
    )
    async def tool_server_config() -> Dict[str, Any]:
        if not _tool_server_enabled():
            raise HTTPException(status_code=404, detail="tool-server-disabled")
        return _build_tool_server_config_payload()

    @router.get(
        "/tool-jobs/{job_id}",
        response_model=ToolJobStatus,
        operation_id="get_tool_job_status",
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def tool_job_status(job_id: str, request: Request) -> Dict[str, Any]:
        job = get_tool_job_store().get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown-tool-job:{job_id}")
        return build_tool_job_status_response(job)

    @router.get(
        "/tool-jobs/active/chat/{chat_id}",
        operation_id="get_active_tool_job_for_chat",
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def active_tool_job_for_chat(chat_id: str, request: Request) -> Dict[str, Any]:
        job = get_tool_job_store().find_latest_active_by_chat_id(chat_id)
        if job is None:
            return {"job": None}
        payload = build_tool_job_status_response(job)
        payload["result_message_id"] = job.result_message_id
        payload["terminal_emitted_at"] = job.terminal_emitted_at
        return {"job": payload}

    @router.get(
        "/tool-jobs/{job_id}/result",
        operation_id="get_tool_job_result",
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def tool_job_result(job_id: str, request: Request) -> Dict[str, Any]:
        try:
            return get_tool_job_result(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown-tool-job:{job_id}") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post(
        "/tool-jobs/{job_id}/delivery",
        operation_id="record_tool_job_delivery",
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def tool_job_delivery(job_id: str, payload: ToolJobDeliveryRequest, request: Request) -> Dict[str, Any]:
        store = get_tool_job_store()
        try:
            job = store.record_terminal_delivery(
                job_id,
                result_message_id=payload.result_message_id,
                terminal_emitted_at=payload.terminal_emitted_at,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown-tool-job:{job_id}") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response = build_tool_job_status_response(job)
        response["result_message_id"] = job.result_message_id
        response["terminal_emitted_at"] = job.terminal_emitted_at
        return response

    @router.post(
        "/tool-jobs/{job_id}/cancel",
        response_model=ToolJobStatus,
        operation_id="cancel_tool_job",
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def tool_job_cancel(job_id: str, request: Request) -> Dict[str, Any]:
        try:
            job = cancel_tool_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown-tool-job:{job_id}") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return build_tool_job_status_response(job)

    @router.post(
        "/tools/ask_document",
        response_model=CompletedToolResult,
        responses={202: {"model": AcceptedToolResult}},
        operation_id="ask_document",
        summary=_tool_openapi_summary("ask_document"),
        description=_tool_openapi_description("ask_document"),
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def ask_document(tool_request: AskDocumentRequest, request: Request):
        response = await _execute_tool(tool_request, request)
        if response.get("status") == "accepted":
            return JSONResponse(status_code=202, content=response)
        return response

    @router.post(
        "/tools/analyze_document_fast",
        response_model=CompletedToolResult,
        responses={202: {"model": AcceptedToolResult}},
        operation_id="analyze_document_fast",
        summary=_tool_openapi_summary("analyze_document_fast"),
        description=_tool_openapi_description("analyze_document_fast"),
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def analyze_document_fast(tool_request: AnalyzeDocumentFastRequest, request: Request):
        response = await _execute_tool(tool_request, request)
        if response.get("status") == "accepted":
            return JSONResponse(status_code=202, content=response)
        return response

    @router.post(
        "/tools/analyze_document_deep",
        response_model=AcceptedToolResult,
        operation_id="analyze_document_deep",
        summary=_tool_openapi_summary("analyze_document_deep"),
        description=_tool_openapi_description("analyze_document_deep"),
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def analyze_document_deep(tool_request: AnalyzeDocumentDeepRequest, request: Request):
        response = await _execute_tool(tool_request, request)
        if response.get("status") == "accepted":
            return JSONResponse(status_code=202, content=response)
        return response

    @router.post(
        "/tools/compare_documents_fast",
        response_model=CompletedToolResult,
        responses={202: {"model": AcceptedToolResult}},
        operation_id="compare_documents_fast",
        summary=_tool_openapi_summary("compare_documents_fast"),
        description=_tool_openapi_description("compare_documents_fast"),
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def compare_documents_fast(tool_request: CompareDocumentsFastRequest, request: Request):
        response = await _execute_tool(tool_request, request)
        if response.get("status") == "accepted":
            return JSONResponse(status_code=202, content=response)
        return response

    @router.post(
        "/tools/compare_documents_deep",
        response_model=AcceptedToolResult,
        operation_id="compare_documents_deep",
        summary=_tool_openapi_summary("compare_documents_deep"),
        description=_tool_openapi_description("compare_documents_deep"),
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def compare_documents_deep(tool_request: CompareDocumentsDeepRequest, request: Request):
        response = await _execute_tool(tool_request, request)
        if response.get("status") == "accepted":
            return JSONResponse(status_code=202, content=response)
        return response

    @router.post(
        "/tools/analyze_equipment_fast",
        response_model=CompletedToolResult,
        responses={202: {"model": AcceptedToolResult}},
        operation_id="analyze_equipment_fast",
        summary=_tool_openapi_summary("analyze_equipment_fast"),
        description=_tool_openapi_description("analyze_equipment_fast"),
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def analyze_equipment_fast(tool_request: AnalyzeEquipmentFastRequest, request: Request):
        response = await _execute_tool(tool_request, request)
        if response.get("status") == "accepted":
            return JSONResponse(status_code=202, content=response)
        return response

    @router.post(
        "/tools/analyze_equipment_deep",
        response_model=AcceptedToolResult,
        operation_id="analyze_equipment_deep",
        summary=_tool_openapi_summary("analyze_equipment_deep"),
        description=_tool_openapi_description("analyze_equipment_deep"),
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def analyze_equipment_deep(tool_request: AnalyzeEquipmentDeepRequest, request: Request):
        response = await _execute_tool(tool_request, request)
        if response.get("status") == "accepted":
            return JSONResponse(status_code=202, content=response)
        return response

    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tool-reports/{{filename:path}}",
        tool_report_download,
        methods=["GET"],
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tool-jobs/active/chat/{{chat_id}}",
        active_tool_job_for_chat,
        methods=["GET"],
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tool-jobs/{{job_id}}",
        tool_job_status,
        methods=["GET"],
        response_model=ToolJobStatus,
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tool-jobs/{{job_id}}/result",
        tool_job_result,
        methods=["GET"],
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tool-jobs/{{job_id}}/delivery",
        tool_job_delivery,
        methods=["POST"],
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tool-jobs/{{job_id}}/cancel",
        tool_job_cancel,
        methods=["POST"],
        response_model=ToolJobStatus,
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tools/ask_document",
        ask_document,
        methods=["POST"],
        response_model=CompletedToolResult,
        responses={202: {"model": AcceptedToolResult}},
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tools/analyze_document_fast",
        analyze_document_fast,
        methods=["POST"],
        response_model=CompletedToolResult,
        responses={202: {"model": AcceptedToolResult}},
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tools/analyze_document_deep",
        analyze_document_deep,
        methods=["POST"],
        response_model=AcceptedToolResult,
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tools/compare_documents_fast",
        compare_documents_fast,
        methods=["POST"],
        response_model=CompletedToolResult,
        responses={202: {"model": AcceptedToolResult}},
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tools/compare_documents_deep",
        compare_documents_deep,
        methods=["POST"],
        response_model=AcceptedToolResult,
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tools/analyze_equipment_fast",
        analyze_equipment_fast,
        methods=["POST"],
        response_model=CompletedToolResult,
        responses={202: {"model": AcceptedToolResult}},
    )
    _register_tool_server_alias(
        f"{_TOOL_SERVER_ALIAS_PREFIX}/tools/analyze_equipment_deep",
        analyze_equipment_deep,
        methods=["POST"],
        response_model=AcceptedToolResult,
    )

    return router
