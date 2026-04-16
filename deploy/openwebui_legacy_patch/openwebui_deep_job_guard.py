from __future__ import annotations

import copy
import functools
import json
import logging
import os
from typing import Any

from fastapi.responses import StreamingResponse


DEEP_TOOL_NAMES = frozenset(
    {
        "analyze_equipment_deep",
        "analyze_document_deep",
    }
)
DEEP_JOB_LAUNCH_ERROR = (
    "Не удалось запустить deep-job.\n"
    "Инструмент не вернул подтверждение запуска (`job_id` и `status_url`). "
    "Повторите запрос и проверьте, что инструмент включён в текущем чате."
)

_LOG = logging.getLogger(__name__)
_PATCH_APPLIED_ATTR = "_llm_tools_platform_deep_job_guard_applied"
_SESSION_RAG_HANDOFF_MARKER = "llm_tools_platform_session_rag_handoff"
_SESSION_RAG_HANDOFF_MODEL = "llm-tools-platform"


def _session_rag_handoff_mode() -> str:
    raw = str(os.environ.get("OPENWEBUI_SESSION_RAG_HANDOFF", "preferred") or "").strip().lower()
    if raw in {"", "preferred", "required"}:
        return raw or "preferred"
    if raw in {"0", "false", "no", "off", "disabled"}:
        return "off"
    return "preferred"


def _extract_metadata_files(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        return []
    files = metadata.get("files")
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def _extract_top_level_files(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    files = body.get("files")
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def _extract_body_files(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    metadata_files = _extract_metadata_files(body)
    if metadata_files:
        return metadata_files
    return _extract_top_level_files(body)


def _should_enable_session_rag_handoff(body: dict[str, Any] | None) -> bool:
    return _session_rag_handoff_mode() in {"preferred", "required"} and bool(_extract_body_files(body))


def _build_session_rag_handoff_payload(body: dict[str, Any]) -> dict[str, Any]:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    files = _extract_body_files(body)
    original_model = str(body.get("model") or "").strip() or None
    return {
        "enabled": True,
        "mode": _session_rag_handoff_mode(),
        "chat_id": str(metadata.get("chat_id") or "").strip() or None,
        "message_id": str(metadata.get("message_id") or "").strip() or None,
        "file_count": len(files),
        "original_model": original_model,
        "routed_model": _SESSION_RAG_HANDOFF_MODEL,
    }


def _annotate_body_with_session_rag_handoff(body: dict[str, Any]) -> dict[str, Any]:
    metadata = body.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        body["metadata"] = metadata
    if "files" not in metadata:
        body_files = _extract_top_level_files(body)
        if body_files:
            metadata["files"] = [copy.deepcopy(item) for item in body_files]
    metadata[_SESSION_RAG_HANDOFF_MARKER] = _build_session_rag_handoff_payload(body)
    return body


def _prepare_openai_form_data_for_session_rag_handoff(form_data: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(form_data)
    metadata = prepared.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    else:
        metadata = dict(metadata)
    prepared["metadata"] = metadata
    if "files" not in metadata:
        body_files = _extract_top_level_files(prepared)
        if body_files:
            metadata["files"] = [copy.deepcopy(item) for item in body_files]

    body_for_marker = {"metadata": metadata, "files": prepared.get("files"), "model": prepared.get("model")}
    handoff_payload = _build_session_rag_handoff_payload(body_for_marker)
    metadata[_SESSION_RAG_HANDOFF_MARKER] = handoff_payload

    files = prepared.get("files")
    if not isinstance(files, list) or not files:
        metadata_files = _extract_metadata_files({"metadata": metadata, "files": prepared.get("files")})
        if metadata_files:
            prepared["files"] = [copy.deepcopy(item) for item in metadata_files]
    if handoff_payload.get("chat_id") and not str(prepared.get("thread_id") or "").strip():
        prepared["thread_id"] = str(handoff_payload["chat_id"])
    if handoff_payload.get("chat_id") and not str(prepared.get("session_id") or "").strip():
        prepared["session_id"] = str(handoff_payload["chat_id"])
    original_model = str(prepared.get("model") or "").strip()
    if original_model and original_model != _SESSION_RAG_HANDOFF_MODEL:
        prepared["model"] = _SESSION_RAG_HANDOFF_MODEL
    prepared["openwebui_session_rag_handoff"] = handoff_payload
    return prepared


def _extract_tool_call_name(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function", {})
    if isinstance(function, dict):
        return str(function.get("name") or "").strip()
    return ""


def _normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if text is None:
                continue
            chunks.append(str(text))
        return "".join(chunks).strip()
    if content is None:
        return ""
    return str(content).strip()


def _normalize_output_content(output: Any) -> str:
    if isinstance(output, str):
        return output.strip()
    if isinstance(output, list):
        chunks: list[str] = []
        for part in output:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if text is None:
                continue
            chunks.append(str(text))
        return "".join(chunks).strip()
    if output is None:
        return ""
    return str(output).strip()


def _is_confirmed_deep_job_text(text: str) -> bool:
    normalized = str(text or "").strip()
    return "job_id:" in normalized and "status_url:" in normalized


def _is_tracked_deep_tool_name(name: str) -> bool:
    return str(name or "").strip() in DEEP_TOOL_NAMES


def detect_unconfirmed_deep_job_launch(messages: list[dict[str, Any]] | None) -> dict[str, str] | None:
    if not isinstance(messages, list):
        return None

    call_lookup: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip() != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            call_id = str(tool_call.get("id") or "").strip()
            if not call_id:
                continue
            call_lookup[call_id] = _extract_tool_call_name(tool_call)

    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip() != "tool":
            continue
        call_id = str(message.get("tool_call_id") or "").strip()
        if not call_id or not _is_tracked_deep_tool_name(call_lookup.get(call_id, "")):
            continue
        content = _normalize_message_content(message.get("content"))
        if _is_confirmed_deep_job_text(content):
            return None
        return {"call_id": call_id, "content": content, "message": DEEP_JOB_LAUNCH_ERROR}
    return None


def detect_unconfirmed_deep_job_output(output: list[dict[str, Any]] | None) -> dict[str, str] | None:
    if not isinstance(output, list):
        return None

    call_lookup: dict[str, str] = {}
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call":
            continue
        call_id = str(item.get("call_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if call_id:
            call_lookup[call_id] = name

    for item in reversed(output):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call_output":
            continue
        call_id = str(item.get("call_id") or "").strip()
        if not call_id or not _is_tracked_deep_tool_name(call_lookup.get(call_id, "")):
            continue
        content = _normalize_output_content(item.get("output"))
        if _is_confirmed_deep_job_text(content):
            return None
        return {"call_id": call_id, "content": content, "message": DEEP_JOB_LAUNCH_ERROR}
    return None


def _build_synthetic_error_stream(message: str) -> StreamingResponse:
    async def _iterator():
        yield (
            "data: "
            + json.dumps(
                {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]},
                ensure_ascii=False,
            )
            + "\n\n"
        )
        yield (
            "data: "
            + json.dumps(
                {"choices": [{"delta": {"content": message}, "finish_reason": None}]},
                ensure_ascii=False,
            )
            + "\n\n"
        )
        yield (
            "data: "
            + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}, ensure_ascii=False)
            + "\n\n"
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(_iterator(), media_type="text/event-stream")


def apply_patches() -> None:
    import open_webui.utils.middleware as ow_middleware
    try:
        import open_webui.routers.openai as ow_openai
    except ModuleNotFoundError:
        ow_openai = None
    try:
        import open_webui.utils.chat as ow_chat
    except ModuleNotFoundError:
        ow_chat = None

    if getattr(ow_middleware, _PATCH_APPLIED_ATTR, False):
        return

    original_generate_chat_completion = ow_middleware.generate_chat_completion
    original_serialize_output = ow_middleware.serialize_output
    original_chat_completion_files_handler = getattr(ow_middleware, "chat_completion_files_handler", None)
    original_openai_generate_chat_completion = getattr(ow_openai, "generate_chat_completion", None) if ow_openai else None
    original_chat_openai_generate_chat_completion = (
        getattr(ow_chat, "generate_openai_chat_completion", None) if ow_chat else None
    )

    @functools.wraps(original_generate_chat_completion)
    async def patched_generate_chat_completion(*args, **kwargs):
        form_data = kwargs.get("form_data")
        if form_data is None and len(args) >= 2:
            form_data = args[1]

        launch_state = detect_unconfirmed_deep_job_launch((form_data or {}).get("messages"))
        if launch_state is not None:
            _LOG.warning(
                "Blocked follow-up model turn for unconfirmed deep-job launch: call_id=%s",
                launch_state["call_id"],
            )
            return _build_synthetic_error_stream(launch_state["message"])

        return await original_generate_chat_completion(*args, **kwargs)

    @functools.wraps(original_serialize_output)
    def patched_serialize_output(output):
        launch_state = detect_unconfirmed_deep_job_output(output)
        if launch_state is not None:
            return launch_state["message"]
        return original_serialize_output(output)

    if callable(original_chat_completion_files_handler):

        @functools.wraps(original_chat_completion_files_handler)
        async def patched_chat_completion_files_handler(request, body, extra_params, user):
            if _should_enable_session_rag_handoff(body):
                _annotate_body_with_session_rag_handoff(body)
                return body, {"sources": []}
            return await original_chat_completion_files_handler(request, body, extra_params, user)

        ow_middleware.chat_completion_files_handler = patched_chat_completion_files_handler

    if ow_openai is not None and callable(original_openai_generate_chat_completion):

        @functools.wraps(original_openai_generate_chat_completion)
        async def patched_openai_generate_chat_completion(*args, **kwargs):
            form_data = kwargs.get("form_data")
            if form_data is None and len(args) >= 2:
                form_data = args[1]

            async def _invoke_with_form_data(payload: dict[str, Any]):
                if "form_data" in kwargs:
                    call_kwargs = dict(kwargs)
                    call_kwargs["form_data"] = payload
                    return await original_openai_generate_chat_completion(*args, **call_kwargs)
                call_args = list(args)
                call_args[1] = payload
                return await original_openai_generate_chat_completion(*tuple(call_args), **kwargs)

            if isinstance(form_data, dict) and _should_enable_session_rag_handoff(form_data):
                prepared = _prepare_openai_form_data_for_session_rag_handoff(form_data)
                try:
                    return await _invoke_with_form_data(prepared)
                except Exception as exc:
                    handoff_payload = prepared.get("openwebui_session_rag_handoff") or {}
                    original_model = str(handoff_payload.get("original_model") or "").strip()
                    if (
                        str(handoff_payload.get("mode") or "").strip().lower() != "preferred"
                        or not original_model
                        or original_model == prepared.get("model")
                    ):
                        raise
                    _LOG.warning(
                        "Session RAG handoff fallback to original model after backend wrapper failure: original_model=%s error=%s",
                        original_model,
                        exc,
                    )
                    return await _invoke_with_form_data(form_data)
            return await _invoke_with_form_data(form_data)

        ow_openai.generate_chat_completion = patched_openai_generate_chat_completion
        if ow_chat is not None and callable(original_chat_openai_generate_chat_completion):
            ow_chat.generate_openai_chat_completion = patched_openai_generate_chat_completion

    ow_middleware.generate_chat_completion = patched_generate_chat_completion
    ow_middleware.serialize_output = patched_serialize_output
    setattr(ow_middleware, _PATCH_APPLIED_ATTR, True)
    _LOG.info("Applied legacy Open WebUI deep-job launch guard patch")
