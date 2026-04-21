from __future__ import annotations

import asyncio
import copy
import os
import re
from dataclasses import is_dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import quote

from orchestrator.tool_catalog import (
    ToolDefinition,
    get_tool_definition,
    get_tool_display_label,
    is_known_tool,
)
from orchestrator.tool_bindings import build_result_available_actions
from orchestrator.tool_job_store import ToolJobRecord, get_tool_job_store
from orchestrator.tool_schemas import AcceptedToolResult, ExecutionMetadata, ToolJobStatus


_ACTIVE_TOOL_JOB_TASKS: Dict[str, asyncio.Task[Any]] = {}
_EXPLICIT_TOOL_JOB_START_DELAY_ENV = "OPENWEBUI_EXPLICIT_TOOL_JOB_START_DELAY_S"
_GENERIC_DEEP_JOB_ENABLED_ENV = "OPENWEBUI_GENERIC_DEEP_JOB_ENABLED"
_GENERIC_DEEP_JOB_ENABLED_TOOLS_ENV = "OPENWEBUI_GENERIC_DEEP_JOB_ENABLED_TOOLS"
_GENERIC_DEEP_JOB_POLL_AFTER_MS_ENV = "OPENWEBUI_GENERIC_DEEP_JOB_POLL_AFTER_MS"
_REPORT_FILENAME_RE = re.compile(
    r"\*\*(?:Отчет|Отчёт|Report) (?:сохранен|сохранён|уже сохранен|уже сохранён|saved|already saved):\*\*\s*`([^`]+)`",
    re.IGNORECASE,
)
_DEFAULT_GENERIC_DEEP_JOB_TOOLS = {
    "analyze_document_deep",
    "analyze_equipment_deep",
    "compare_documents_deep",
}
_JOB_STATUS_TEXTS = {
    "ru": {
        "queued": "Задача поставлена в очередь.",
        "running": "Выполняется обработка.",
        "cancelling": "Останавливается выполнение.",
        "completed": "Выполнение завершено.",
        "failed": "Выполнение завершилось с ошибкой.",
        "cancelled": "Выполнение отменено.",
        "step_completed": "Шаг завершён.",
        "download_report": "Скачать отчёт",
    },
    "en": {
        "queued": "The task has been queued.",
        "running": "Processing is in progress.",
        "cancelling": "Stopping execution.",
        "completed": "Execution completed.",
        "failed": "Execution failed.",
        "cancelled": "Execution was cancelled.",
        "step_completed": "Step completed.",
        "download_report": "Download report",
    },
}


def _normalize_ui_locale(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("ru"):
        return "ru"
    return "en"


def _job_text(locale: Any, key: str) -> str:
    return _JOB_STATUS_TEXTS[_normalize_ui_locale(locale)][key]


def _resolve_job_locale(job: ToolJobRecord | Dict[str, Any] | None) -> str:
    if isinstance(job, dict):
        return _normalize_ui_locale(job.get("ui_locale"))
    request_payload = getattr(job, "request_payload", None) or {}
    return _normalize_ui_locale(request_payload.get("ui_locale"))


def _resolve_document_context_count(request_payload: Dict[str, Any]) -> int:
    counts = [
        int(request_payload.get("file_count") or 0),
        len(request_payload.get("active_doc_ids") or []),
        len(request_payload.get("session_docs") or {}),
        len(request_payload.get("attachments_meta") or []),
    ]
    return max(counts)


def _resolve_tool_label(tool_name: Any, ui_locale: Any) -> str | None:
    normalized_tool_name = str(tool_name or "").strip()
    if not normalized_tool_name or not is_known_tool(normalized_tool_name):
        return None
    return get_tool_display_label(normalized_tool_name, _normalize_ui_locale(ui_locale))


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


def _extract_saved_report_filename(report_text: str) -> str | None:
    match = _REPORT_FILENAME_RE.search(str(report_text or ""))
    if not match:
        return None
    filename = os.path.basename(match.group(1).strip())
    return filename or None


def _normalize_async_result_artifacts(response: Dict[str, Any], *, ui_locale: str) -> Dict[str, Any]:
    normalized_response = copy.deepcopy(response or {})
    artifacts = list(normalized_response.get("artifacts") or [])
    generated_report = normalized_response.get("generated_report")

    if isinstance(generated_report, str):
        report_filename = _extract_saved_report_filename(generated_report)
        if report_filename and not any(
            str(artifact.get("artifact_id") or "").strip() == report_filename
            for artifact in artifacts
            if isinstance(artifact, dict)
        ):
            report_format = Path(report_filename).suffix.lower().lstrip(".")
            download_label = _job_text(ui_locale, "download_report")
            artifacts.append(
                {
                    "artifact_id": report_filename,
                    "artifact_type": "report",
                    "url": f"/tool-server/tool-reports/{quote(report_filename, safe='')}",
                    "title": download_label,
                    "metadata": {
                        "filename": report_filename,
                        "format": report_format or None,
                        "download_label": download_label,
                    },
                }
            )

    if artifacts:
        normalized_response["artifacts"] = artifacts

    return normalized_response


def _generic_deep_job_enabled(tool_name: str) -> bool:
    raw_enabled = str(os.getenv(_GENERIC_DEEP_JOB_ENABLED_ENV, "0")).strip().lower()
    if raw_enabled in {"", "0", "false", "no", "off"}:
        return False
    allowlist_raw = str(os.getenv(_GENERIC_DEEP_JOB_ENABLED_TOOLS_ENV, "")).strip()
    if not allowlist_raw:
        return tool_name in _DEFAULT_GENERIC_DEEP_JOB_TOOLS
    allowlist = {
        item.strip()
        for item in allowlist_raw.split(",")
        if item.strip()
    }
    return tool_name in allowlist


def _resolve_generic_deep_job_poll_after_ms() -> int:
    raw_value = str(os.getenv(_GENERIC_DEEP_JOB_POLL_AFTER_MS_ENV, "1500")).strip()
    try:
        return max(250, int(raw_value))
    except ValueError:
        return 1500


def _resolve_status_text(job: ToolJobRecord, *, ui_locale: Any = None) -> Optional[str]:
    locale = _normalize_ui_locale(ui_locale) if ui_locale is not None else _resolve_job_locale(job)
    defaults = {
        "queued": _job_text(locale, "queued"),
        "running": _job_text(locale, "running"),
        "cancelling": _job_text(locale, "cancelling"),
        "completed": _job_text(locale, "completed"),
        "failed": _job_text(locale, "failed"),
        "cancelled": _job_text(locale, "cancelled"),
    }
    legacy_defaults = {
        "deep-job выполняется.",
        "deep-job готовится к отмене.",
        "deep-job завершён.",
        "deep-job завершён со статусом failed.",
        "deep-job отменён.",
    }
    for localized_defaults in _JOB_STATUS_TEXTS.values():
        legacy_defaults.update(localized_defaults.values())
    payload = job.status_payload or {}
    status_text = str(payload.get("status_text") or "").strip()
    if status_text and status_text not in legacy_defaults:
        return status_text
    return defaults.get(str(job.status))


class _ToolJobProgressReporter:
    def __init__(self, *, job_id: str):
        self.job_id = job_id
        self._store = get_tool_job_store()
        self._entries_by_key: Dict[str, Dict[str, Any]] = {}
        self._entry_order: list[str] = []

    def _status_history(self) -> list[Dict[str, Any]]:
        return [dict(self._entries_by_key[key]) for key in self._entry_order if key in self._entries_by_key]

    def _progress_payload(self) -> Optional[Dict[str, Any]]:
        if not self._entry_order:
            return None
        latest = self._entries_by_key.get(self._entry_order[-1]) or {}
        phase = str(latest.get("key") or "").strip()
        if not phase:
            return None
        progress = {"phase": phase}
        title = str(latest.get("title") or "").strip()
        content = str(latest.get("content") or "").strip()
        if title:
            progress["title"] = title
        if content:
            progress["content"] = content
        return progress

    async def update_progress_box(self, *, key: str, title: str, content: str) -> None:
        stage_key = str(key or title or "progress").strip() or "progress"
        if stage_key not in self._entry_order:
            self._entry_order.append(stage_key)
        self._entries_by_key[stage_key] = {
            "key": stage_key,
            "title": str(title or stage_key).strip(),
            "content": str(content or title or "").strip(),
            "updated_at": asyncio.get_running_loop().time(),
        }
        self._store.update_job_status(
            self.job_id,
            current_stage=stage_key,
            status_text=str(
                content or title or _job_text(_resolve_job_locale(self._store.get(self.job_id)), "running")
            ).strip(),
            progress=self._progress_payload(),
            status_history=self._status_history(),
        )

    async def clear_progress_box(self, *, key: str) -> None:
        stage_key = str(key or "").strip()
        if not stage_key or stage_key not in self._entries_by_key:
            return
        entry = dict(self._entries_by_key[stage_key])
        entry["cleared_at"] = asyncio.get_running_loop().time()
        self._entries_by_key[stage_key] = entry
        self._store.update_job_status(
            self.job_id,
            current_stage=stage_key,
            status_text=str(
                entry.get("content")
                or entry.get("title")
                or _job_text(_resolve_job_locale(self._store.get(self.job_id)), "step_completed")
            ).strip(),
            progress=self._progress_payload(),
            status_history=self._status_history(),
        )

    def is_cancelled(self) -> bool:
        job = self._store.get(self.job_id)
        return bool(job is not None and job.status in {"cancelling", "cancelled"})

    def finalize_completed(self, response: Dict[str, Any]) -> None:
        self._store.update_job_status(
            self.job_id,
            status_text=_job_text(_resolve_job_locale(self._store.get(self.job_id)), "completed"),
            progress=self._progress_payload(),
            status_history=self._status_history(),
            embeds=list(response.get("embeds") or []),
            sources=list(response.get("sources") or []),
            artifacts=list(response.get("artifacts") or []),
            result_preview=str(response.get("assistant_message") or "").strip()[:280] or None,
        )


def _attach_job_reporter(deps: Any, *, job: ToolJobRecord, reporter: _ToolJobProgressReporter) -> Any:
    tool_name = str(job.tool_name or "").strip()
    if not _generic_deep_job_enabled(tool_name):
        return deps
    if not is_dataclass(deps):
        return deps
    return replace(
        deps,
        update_progress_box=reporter.update_progress_box,
        clear_progress_box=reporter.clear_progress_box,
        is_cancelled=reporter.is_cancelled,
    )


def build_accepted_tool_job_response(job: ToolJobRecord, request_payload: Dict[str, Any]) -> Dict[str, Any]:
    execution_metadata = job.execution_metadata or _build_execution_metadata_payload(request_payload)
    tool_name = str(request_payload.get("requested_tool") or request_payload.get("tool_name") or "")
    ui_locale = request_payload.get("ui_locale")
    if ui_locale is None:
        ui_locale = (job.request_payload or {}).get("ui_locale")
    tool_label = str(
        request_payload.get("tool_label")
        or (job.request_payload or {}).get("tool_label")
        or _resolve_tool_label(tool_name, ui_locale)
        or ""
    ).strip() or None
    result = AcceptedToolResult(
        tool_name=tool_name,
        tool_label=tool_label,
        job_id=job.job_id,
        status_url=job.status_url,
        submitted_at=job.submitted_at,
        job_status=str(job.status),
        status_text=_resolve_status_text(job, ui_locale=ui_locale),
        poll_after_ms=_resolve_generic_deep_job_poll_after_ms() if _generic_deep_job_enabled(tool_name) else None,
        result_preview=job.result_preview,
        available_actions=build_result_available_actions(tool_name, status_url=job.status_url),
        execution_metadata=ExecutionMetadata(**execution_metadata),
    )
    return result.model_dump()


def build_tool_job_status_response(job: ToolJobRecord) -> Dict[str, Any]:
    status_payload = dict(job.status_payload or {})
    request_payload = dict(job.request_payload or {})
    tool_name = str(job.tool_name or request_payload.get("requested_tool") or request_payload.get("tool_name") or "")
    ui_locale = request_payload.get("ui_locale")
    payload = ToolJobStatus(
        job_id=job.job_id,
        status=str(job.status),
        tool_label=str(
            status_payload.get("tool_label")
            or request_payload.get("tool_label")
            or _resolve_tool_label(tool_name, ui_locale)
            or ""
        ).strip()
        or None,
        current_stage=job.current_stage,
        submitted_at=job.submitted_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result_ref=job.result_ref if job.status in {"completed", "failed", "cancelled"} and (job.result_payload or job.response) is not None else None,
        error_summary=job.error_summary,
        status_text=str(status_payload.get("status_text") or _resolve_status_text(job) or "").strip() or None,
        status_history=list(status_payload.get("status_history") or []),
        progress=status_payload.get("progress"),
        artifacts=list(status_payload.get("artifacts") or []),
        sources=list(status_payload.get("sources") or []),
        embeds=list(status_payload.get("embeds") or []),
        result_preview=str(status_payload.get("result_preview") or job.result_preview or "").strip() or None,
    ).model_dump()
    if job.run_id:
        payload["run_id"] = job.run_id
    if job.state_ref:
        payload["state_ref"] = job.state_ref
    return payload


def get_tool_job_result(job_id: str) -> Dict[str, Any]:
    job = get_tool_job_store().get(job_id)
    if job is None:
        raise KeyError(job_id)
    result_payload = job.result_payload or job.response
    if job.status in {"completed", "failed", "cancelled"} and result_payload is not None:
        return copy.deepcopy(result_payload)
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
    payload_copy.setdefault(
        "tool_label",
        _resolve_tool_label(
            payload_copy.get("requested_tool") or payload_copy.get("tool_name"),
            payload_copy.get("ui_locale"),
        ),
    )
    start_delay_s = _resolve_async_tool_job_start_delay_s(payload_copy)
    execution_metadata = _build_execution_metadata_payload(payload_copy)
    job = store.create_job(
        tool_name=str(payload_copy.get("requested_tool") or payload_copy.get("tool_name") or ""),
        route_prefix=route_prefix,
        request_payload=payload_copy,
        execution_metadata=execution_metadata,
    )
    reporter = _ToolJobProgressReporter(job_id=job.job_id)
    job_deps = _attach_job_reporter(deps, job=job, reporter=reporter)
    payload_copy.setdefault("idempotency_key", f"tool-job:{job.job_id}")

    async def _runner() -> None:
        try:
            if start_delay_s > 0:
                await asyncio.sleep(start_delay_s)
            store.mark_running(job.job_id)
            response = await execute_fn(payload_copy, deps=job_deps)
            response = _normalize_async_result_artifacts(
                response,
                ui_locale=_normalize_ui_locale(payload_copy.get("ui_locale")),
            )
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
        if _generic_deep_job_enabled(str(job.tool_name or "")):
            reporter.finalize_completed(response)

    task = asyncio.create_task(_runner(), name=f"tool-job:{job.job_id}")
    _ACTIVE_TOOL_JOB_TASKS[job.job_id] = task

    def _cleanup(_task: asyncio.Task[Any]) -> None:
        _ACTIVE_TOOL_JOB_TASKS.pop(job.job_id, None)

    task.add_done_callback(_cleanup)
    return job
