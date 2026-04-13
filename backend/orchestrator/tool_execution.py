from __future__ import annotations

import asyncio
import copy
import os
from typing import Any, Awaitable, Callable, Dict, Optional

from orchestrator.tool_catalog import ToolDefinition, get_tool_definition, is_known_tool
from orchestrator.tool_bindings import build_result_available_actions
from orchestrator.tool_job_store import ToolJobRecord, get_tool_job_store
from orchestrator.tool_schemas import AcceptedToolResult, ExecutionMetadata, ToolJobStatus


_ACTIVE_TOOL_JOB_TASKS: Dict[str, asyncio.Task[Any]] = {}
_EXPLICIT_TOOL_JOB_START_DELAY_ENV = "OPENWEBUI_EXPLICIT_TOOL_JOB_START_DELAY_S"


def _resolve_document_context_count(request_payload: Dict[str, Any]) -> int:
    counts = [
        int(request_payload.get("file_count") or 0),
        len(request_payload.get("active_doc_ids") or []),
        len(request_payload.get("session_docs") or {}),
        len(request_payload.get("attachments_meta") or []),
    ]
    return max(counts)


def apply_tool_contract_to_payload(request_payload: Dict[str, Any]) -> Optional[ToolDefinition]:
    requested_tool = str(request_payload.get("requested_tool") or "").strip()
    if not requested_tool or not is_known_tool(requested_tool):
        return None

    tool_definition = get_tool_definition(requested_tool)
    routing_mode = str(request_payload.get("routing_mode") or "explicit").strip() or "explicit"

    request_payload["requested_tool"] = tool_definition.name
    request_payload["routing_mode"] = routing_mode
    request_payload["forced_route"] = tool_definition.legacy_executor
    request_payload["tool_execution_mode"] = tool_definition.execution_mode
    request_payload["execution_surface"] = "explicit_tool"
    request_payload["runtime_mode"] = "specialized_tasks"

    document_context_count = _resolve_document_context_count(request_payload)

    if tool_definition.name == "ask_document" and not request_payload.get("rag_scope"):
        if str(request_payload.get("knowledge_collection_id") or "").strip():
            request_payload["rag_scope"] = "knowledge_base_rag"
        elif (
            bool(request_payload.get("has_session_docs"))
            or int(request_payload.get("file_count") or 0) > 0
            or bool(request_payload.get("active_doc_ids"))
        ):
            request_payload["rag_scope"] = "session_rag"

    if tool_definition.name == "analyze_equipment_deep" and document_context_count == 1:
        request_payload["forced_route"] = "document_question"
        if not request_payload.get("rag_scope"):
            request_payload["rag_scope"] = "session_rag"

    return tool_definition


def inject_tool_contract_metadata(response: Dict[str, Any], request_payload: Dict[str, Any]) -> Dict[str, Any]:
    requested_tool = str(request_payload.get("requested_tool") or "").strip()
    if not requested_tool:
        return response

    response["requested_tool"] = requested_tool
    response["routing_mode"] = str(request_payload.get("routing_mode") or "explicit")
    response["tool_execution_mode"] = request_payload.get("tool_execution_mode")
    return response


def should_start_async_tool_job(request_payload: Dict[str, Any]) -> bool:
    if str(request_payload.get("tool_execution_mode") or "") == "async":
        return True
    return str(request_payload.get("job_mode") or "") == "force_async"


def _resolve_async_tool_job_start_delay_s(request_payload: Dict[str, Any]) -> float:
    if str(request_payload.get("execution_surface") or "") != "explicit_tool":
        return 0.0
    if (
        str(request_payload.get("tool_execution_mode") or "") != "async"
        and str(request_payload.get("job_mode") or "") != "force_async"
    ):
        return 0.0
    raw_value = str(os.getenv(_EXPLICIT_TOOL_JOB_START_DELAY_ENV, "0")).strip()
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return 0.0


def _build_execution_metadata_payload(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    return ExecutionMetadata(
        requested_tool=request_payload.get("requested_tool"),
        routing_mode=str(request_payload.get("routing_mode") or "explicit"),
        execution_mode="async",
        trace_id=request_payload.get("trace_id"),
        runtime_mode=request_payload.get("runtime_mode"),
        model_profile=request_payload.get("model_profile"),
    ).model_dump()


def _resolve_async_terminal_status(response: Dict[str, Any]) -> str:
    execution_metadata = response.get("execution_metadata") if isinstance(response, dict) else None
    if isinstance(execution_metadata, dict):
        status = str(execution_metadata.get("status") or "").strip().lower()
        if status:
            return status
    status = str(response.get("status") or "").strip().lower() if isinstance(response, dict) else ""
    return status or "completed"


def _resolve_async_terminal_error_summary(response: Dict[str, Any], *, fallback: str) -> str:
    execution_metadata = response.get("execution_metadata") if isinstance(response, dict) else None
    if isinstance(execution_metadata, dict):
        reason = str(execution_metadata.get("reason") or "").strip()
        if reason:
            return reason
    assistant_message = str(response.get("assistant_message") or "").strip() if isinstance(response, dict) else ""
    if assistant_message:
        return assistant_message
    return fallback


def build_accepted_tool_job_response(job: ToolJobRecord, request_payload: Dict[str, Any]) -> Dict[str, Any]:
    execution_metadata = job.execution_metadata or _build_execution_metadata_payload(request_payload)
    tool_name = str(request_payload.get("requested_tool") or request_payload.get("tool_name") or "")
    result = AcceptedToolResult(
        tool_name=tool_name,
        job_id=job.job_id,
        status_url=job.status_url,
        submitted_at=job.submitted_at,
        result_preview=job.result_preview,
        available_actions=build_result_available_actions(tool_name, status_url=job.status_url),
        execution_metadata=ExecutionMetadata(**execution_metadata),
    )
    return result.model_dump()


def build_tool_job_status_response(job: ToolJobRecord) -> Dict[str, Any]:
    payload = ToolJobStatus(
        job_id=job.job_id,
        status=str(job.status),
        current_stage=job.current_stage,
        submitted_at=job.submitted_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result_ref=job.result_ref if job.status == "completed" else None,
        error_summary=job.error_summary,
    ).model_dump()
    if job.run_id:
        payload["run_id"] = job.run_id
    if job.state_ref:
        payload["state_ref"] = job.state_ref
    if job.result_preview:
        payload["result_preview"] = job.result_preview
    return payload


def get_tool_job_result(job_id: str) -> Dict[str, Any]:
    job = get_tool_job_store().get(job_id)
    if job is None:
        raise KeyError(job_id)
    if job.status == "completed" and job.response is not None:
        return copy.deepcopy(job.response)
    if job.status in {"failed", "cancelled"}:
        raise RuntimeError(f"job-terminal-without-result:{job_id}:{job.status}")
    raise RuntimeError(f"job-not-ready:{job_id}")


def cancel_tool_job(job_id: str) -> ToolJobRecord:
    store = get_tool_job_store()
    job = store.get(job_id)
    if job is None:
        raise KeyError(job_id)
    if job.status in {"completed", "failed", "cancelled"}:
        return job

    task = _ACTIVE_TOOL_JOB_TASKS.get(job_id)
    if job.status == "cancelling":
        if task is None or task.done():
            return store.finish_cancelled(job_id, error_summary="cancelled-by-request")
        return job
    if task is None or task.done():
        return store.finish_cancelled(job_id, error_summary="cancelled-by-request")

    updated_job = store.mark_cancelling(job_id)
    task.cancel()
    return updated_job


def force_tool_job_transition(
    job_id: str,
    *,
    status: str,
    response: Optional[Dict[str, Any]] = None,
    error_summary: Optional[str] = None,
) -> ToolJobRecord:
    store = get_tool_job_store()
    job = store.get(job_id)
    if job is None:
        raise KeyError(job_id)

    task = _ACTIVE_TOOL_JOB_TASKS.pop(job_id, None)
    if task is not None and not task.done():
        task.cancel()

    normalized_status = str(status).strip().lower()
    if normalized_status == "running":
        return store.mark_running(job_id)
    if normalized_status == "cancelling":
        return store.mark_cancelling(job_id)
    if normalized_status == "completed":
        payload = copy.deepcopy(response or {})
        if not payload:
            payload = {"assistant_message": "Synthetic completed result"}
        return store.finish_completed(job_id, payload)
    if normalized_status == "failed":
        return store.finish_failed(job_id, error_summary or "synthetic-failure")
    if normalized_status == "cancelled":
        return store.finish_cancelled(job_id, error_summary=error_summary or "synthetic-cancelled")
    raise ValueError(f"unsupported-tool-job-status:{status}")


def reconcile_incomplete_tool_jobs() -> int:
    _ACTIVE_TOOL_JOB_TASKS.clear()
    return get_tool_job_store().reconcile_incomplete_jobs()


def reset_tool_job_runtime_state() -> None:
    _ACTIVE_TOOL_JOB_TASKS.clear()


def submit_async_tool_job(
    *,
    request_payload: Dict[str, Any],
    deps: Any,
    execute_fn: Callable[..., Awaitable[Dict[str, Any]]],
    route_prefix: Optional[str] = None,
) -> ToolJobRecord:
    store = get_tool_job_store()
    payload_copy = copy.deepcopy(request_payload)
    start_delay_s = _resolve_async_tool_job_start_delay_s(payload_copy)
    execution_metadata = _build_execution_metadata_payload(payload_copy)
    job = store.create_job(
        tool_name=str(payload_copy.get("requested_tool") or payload_copy.get("tool_name") or ""),
        route_prefix=route_prefix,
        request_payload=payload_copy,
        execution_metadata=execution_metadata,
    )
    payload_copy.setdefault("idempotency_key", f"tool-job:{job.job_id}")

    async def _runner() -> None:
        try:
            if start_delay_s > 0:
                await asyncio.sleep(start_delay_s)
            store.mark_running(job.job_id)
            response = await execute_fn(payload_copy, deps=deps)
        except asyncio.CancelledError:
            current_job = store.get(job.job_id)
            if current_job is not None and current_job.status in {"completed", "failed", "cancelled"}:
                return
            if current_job is not None and current_job.status == "cancelling":
                store.finish_cancelled(job.job_id)
            else:
                store.finish_failed(job.job_id, "interrupted:task-cancelled", current_stage="interrupted")
            raise
        except Exception as exc:
            current_job = store.get(job.job_id)
            if current_job is not None and current_job.status in {"completed", "failed", "cancelled"}:
                return
            store.finish_failed(job.job_id, str(exc))
            return
        current_job = store.get(job.job_id)
        if current_job is not None and current_job.status in {"completed", "failed", "cancelled"}:
            return
        terminal_status = _resolve_async_terminal_status(response)
        if terminal_status in {"busy", "failed"}:
            store.finish_failed(
                job.job_id,
                _resolve_async_terminal_error_summary(response, fallback=terminal_status),
                current_stage="busy" if terminal_status == "busy" else "failed",
            )
            return
        if terminal_status == "cancelled":
            store.finish_cancelled(
                job.job_id,
                error_summary=_resolve_async_terminal_error_summary(response, fallback="cancelled-by-request"),
            )
            return
        store.finish_completed(job.job_id, response)

    task = asyncio.create_task(_runner(), name=f"tool-job:{job.job_id}")
    _ACTIVE_TOOL_JOB_TASKS[job.job_id] = task

    def _cleanup(_task: asyncio.Task[Any]) -> None:
        _ACTIVE_TOOL_JOB_TASKS.pop(job.job_id, None)

    task.add_done_callback(_cleanup)
    return job
