from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from orchestrator.tool_catalog import ToolDefinition, get_tool_definition, is_known_tool
from orchestrator.tool_schemas import AcceptedToolResult, ExecutionMetadata, ToolJobStatus


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolJobRecord:
    job_id: str
    tool_name: str
    status: str
    submitted_at: str
    status_url: str
    result_ref: str
    current_stage: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    run_id: Optional[str] = None
    state_ref: Optional[str] = None
    error_summary: Optional[str] = None
    result_preview: Optional[str] = None
    response: Optional[Dict[str, Any]] = None


class ToolJobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, ToolJobRecord] = {}

    def create_job(self, *, tool_name: str) -> ToolJobRecord:
        job_id = str(uuid.uuid4())
        job = ToolJobRecord(
            job_id=job_id,
            tool_name=tool_name,
            status="queued",
            submitted_at=utc_now(),
            status_url=f"/tool-jobs/{job_id}",
            result_ref=f"/tool-jobs/{job_id}/result",
            current_stage="queued",
        )
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[ToolJobRecord]:
        return self._jobs.get(job_id)

    def mark_running(self, job_id: str) -> ToolJobRecord:
        job = self._require(job_id)
        job.status = "running"
        job.current_stage = "running"
        job.started_at = utc_now()
        return job

    def finish_completed(self, job_id: str, response: Dict[str, Any]) -> ToolJobRecord:
        job = self._require(job_id)
        job.status = "completed"
        job.current_stage = "completed"
        job.completed_at = utc_now()
        job.response = copy.deepcopy(response)
        job.run_id = response.get("run_id")
        job.state_ref = response.get("state_ref")
        assistant_message = str(response.get("assistant_message") or "").strip()
        job.result_preview = assistant_message[:280] or None
        return job

    def finish_failed(self, job_id: str, error_summary: str) -> ToolJobRecord:
        job = self._require(job_id)
        job.status = "failed"
        job.current_stage = "failed"
        job.completed_at = utc_now()
        job.error_summary = error_summary
        return job

    def _require(self, job_id: str) -> ToolJobRecord:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job


_TOOL_JOB_STORE = ToolJobStore()


def get_tool_job_store() -> ToolJobStore:
    return _TOOL_JOB_STORE


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

    if str(request_payload.get("runtime_mode") or "auto") == "auto":
        request_payload["runtime_mode"] = "specialized_tasks"

    if tool_definition.name == "ask_document" and not request_payload.get("rag_scope"):
        if str(request_payload.get("knowledge_collection_id") or "").strip():
            request_payload["rag_scope"] = "knowledge_base_rag"
        elif (
            bool(request_payload.get("has_session_docs"))
            or int(request_payload.get("file_count") or 0) > 0
            or bool(request_payload.get("active_doc_ids"))
        ):
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


def build_accepted_tool_job_response(job: ToolJobRecord, request_payload: Dict[str, Any]) -> Dict[str, Any]:
    result = AcceptedToolResult(
        tool_name=str(request_payload.get("requested_tool") or request_payload.get("tool_name") or ""),
        job_id=job.job_id,
        status_url=job.status_url,
        submitted_at=job.submitted_at,
        result_preview=job.result_preview,
        execution_metadata=ExecutionMetadata(
            requested_tool=request_payload.get("requested_tool"),
            routing_mode=str(request_payload.get("routing_mode") or "explicit"),
            execution_mode="async",
            trace_id=request_payload.get("trace_id"),
            runtime_mode=request_payload.get("runtime_mode"),
            model_profile=request_payload.get("model_profile"),
        ),
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
    if job.status != "completed" or job.response is None:
        raise RuntimeError(f"job-not-ready:{job_id}")
    return copy.deepcopy(job.response)


def submit_async_tool_job(
    *,
    request_payload: Dict[str, Any],
    deps: Any,
    execute_fn: Callable[..., Awaitable[Dict[str, Any]]],
) -> ToolJobRecord:
    store = get_tool_job_store()
    job = store.create_job(tool_name=str(request_payload.get("requested_tool") or request_payload.get("tool_name") or ""))
    payload_copy = copy.deepcopy(request_payload)
    payload_copy.setdefault("idempotency_key", f"tool-job:{job.job_id}")

    async def _runner() -> None:
        store.mark_running(job.job_id)
        try:
            response = await execute_fn(payload_copy, deps=deps)
        except Exception as exc:
            store.finish_failed(job.job_id, str(exc))
            return
        store.finish_completed(job.job_id, response)

    asyncio.create_task(_runner())
    return job
