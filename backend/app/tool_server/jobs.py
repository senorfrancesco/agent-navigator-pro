from __future__ import annotations

from typing import Any, Dict

from orchestrator.tool_job_store import ToolJobRecord, get_tool_job_store

from app.tool_server.schemas import AcceptedToolResult, ToolJobResult, ToolJobStatus


def get_store():
    return get_tool_job_store()


def _request_payload_for_deep_equipment(*, equipment_query: str, current_model_id: str | None, ui_locale: str | None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "requested_tool": "analyze_equipment_deep",
        "tool_name": "analyze_equipment_deep",
        "equipment_query": equipment_query,
    }
    if current_model_id:
        payload["current_model_id"] = current_model_id
    if ui_locale:
        payload["ui_locale"] = ui_locale
    return payload


def create_analyze_equipment_deep_job(
    *,
    equipment_query: str,
    current_model_id: str | None = None,
    ui_locale: str | None = None,
    route_prefix: str | None = None,
) -> AcceptedToolResult:
    job = get_store().create_job(
        tool_name="analyze_equipment_deep",
        route_prefix=route_prefix,
        request_payload=_request_payload_for_deep_equipment(
            equipment_query=equipment_query,
            current_model_id=current_model_id,
            ui_locale=ui_locale,
        ),
        execution_metadata={"execution_mode": "async"},
    )
    return AcceptedToolResult(
        tool_name="analyze_equipment_deep",
        job_id=job.job_id,
        status_url=job.status_url,
        result_ref=job.result_ref,
    )


def _to_status(job: ToolJobRecord) -> ToolJobStatus:
    status_payload = job.status_payload or {}
    return ToolJobStatus(
        job_id=job.job_id,
        status=job.status,
        tool_name=job.tool_name,
        status_url=job.status_url,
        result_ref=job.result_ref,
        status_text=status_payload.get("status_text") or job.error_summary,
        result_preview=job.result_preview,
    )


def get_job_status(job_id: str) -> ToolJobStatus:
    job = get_store().get(job_id)
    if job is None:
        raise KeyError(job_id)
    return _to_status(job)


def get_job_result(job_id: str) -> ToolJobResult:
    job = get_store().get(job_id)
    if job is None:
        raise KeyError(job_id)

    result_payload = job.result_payload or job.response
    if job.status in {"completed", "failed", "cancelled"} and isinstance(result_payload, dict):
        return ToolJobResult(
            assistant_message=str(result_payload.get("assistant_message") or ""),
            payload=dict(result_payload),
        )
    raise RuntimeError(f"tool-job-not-ready:{job_id}")


def cancel_job(job_id: str) -> ToolJobStatus:
    store = get_store()
    job = store.get(job_id)
    if job is None:
        raise KeyError(job_id)
    if job.status in {"completed", "failed", "cancelled"}:
        return _to_status(job)
    return _to_status(store.finish_cancelled(job_id, error_summary="cancelled-by-request"))
