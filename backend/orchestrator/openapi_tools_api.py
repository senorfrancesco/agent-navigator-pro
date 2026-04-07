from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Type

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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

_TOOL_SERVER_BEARER = HTTPBearer(auto_error=False)
_TOOL_ROUTE_PREFIXES = ("/tools/", "/tool-jobs/")


def _env_flag(name: str, default: bool) -> bool:
    value = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _tool_server_enabled() -> bool:
    return _env_flag("OPENAPI_TOOL_SERVER_ENABLED", True)


def _tool_server_token() -> str:
    return str(os.getenv("OPENAPI_TOOL_SERVER_TOKEN") or "agent-navigator-tool-server-dev-token").strip()


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


def _extract_ref_identity(document_ref: DocumentRef) -> Optional[str]:
    for candidate in (
        document_ref.document_id,
        document_ref.upload_id,
        document_ref.file_id,
        document_ref.version_id,
        document_ref.session_file_ref,
        document_ref.file_path,
    ):
        if candidate:
            return str(candidate)
    return None


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


def _build_orchestration_payload(tool_request: ToolRequest) -> Dict[str, Any]:
    active_doc_ids = [ref_id for ref_id in (_extract_ref_identity(item) for item in tool_request.document_refs) if ref_id]
    user_inputs = dict(tool_request.user_inputs or {})
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
    return artifacts


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
        title="Agent Navigator OpenAPI Tool Server",
        version=str(app.version),
        description="Tool-only OpenAPI surface for Open WebUI integration.",
        routes=_tool_only_routes(app),
    )


def create_openapi_tools_router(
    *,
    app: FastAPI,
    orchestration_request_model: Type[Any],
    execute_orchestration_request: Callable[[Any, Optional[Request]], Awaitable[Dict[str, Any]]],
) -> APIRouter:
    router = APIRouter()

    async def _execute_tool(tool_request: ToolRequest, http_request: Request) -> Dict[str, Any]:
        payload = _build_orchestration_payload(tool_request)
        orchestration_request = orchestration_request_model(**payload)
        response = await execute_orchestration_request(orchestration_request, http_request)
        if response.get("status") == "accepted":
            return response
        return _build_completed_tool_result(tool_request, response)

    @router.get(
        "/tool-server/openapi.json",
        dependencies=[Depends(_require_tool_server_access)],
        include_in_schema=False,
    )
    async def tool_server_openapi_schema() -> Dict[str, Any]:
        return _build_tool_server_openapi(app)

    @router.get(
        "/tool-jobs/{job_id}",
        response_model=ToolJobStatus,
        operation_id="get_tool_job_status",
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def tool_job_status(job_id: str, request: Request) -> Dict[str, Any]:
        from orchestrator.tool_execution import build_tool_job_status_response, get_tool_job_store

        job = get_tool_job_store().get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown-tool-job:{job_id}")
        return build_tool_job_status_response(job)

    @router.get(
        "/tool-jobs/{job_id}/result",
        operation_id="get_tool_job_result",
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def tool_job_result(job_id: str, request: Request) -> Dict[str, Any]:
        from orchestrator.tool_execution import get_tool_job_result

        try:
            return get_tool_job_result(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown-tool-job:{job_id}") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post(
        "/tools/ask_document",
        response_model=CompletedToolResult,
        responses={202: {"model": AcceptedToolResult}},
        operation_id="ask_document",
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
        dependencies=[Depends(_require_tool_server_access)],
    )
    async def analyze_equipment_deep(tool_request: AnalyzeEquipmentDeepRequest, request: Request):
        response = await _execute_tool(tool_request, request)
        if response.get("status") == "accepted":
            return JSONResponse(status_code=202, content=response)
        return response

    return router
