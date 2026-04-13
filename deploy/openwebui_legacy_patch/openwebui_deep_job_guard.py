from __future__ import annotations

import functools
import json
import logging
from typing import Any

from fastapi.responses import StreamingResponse


DEEP_TOOL_NAME = "analyze_equipment_deep"
DEEP_JOB_LAUNCH_ERROR = (
    "Не удалось запустить deep-job.\n"
    "Инструмент не вернул подтверждение запуска (`job_id` и `status_url`). "
    "Повторите запрос и проверьте, что инструмент включён в текущем чате."
)

_LOG = logging.getLogger(__name__)
_PATCH_APPLIED_ATTR = "_llm_tools_platform_deep_job_guard_applied"


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
        if not call_id or call_lookup.get(call_id) != DEEP_TOOL_NAME:
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
        if not call_id or call_lookup.get(call_id) != DEEP_TOOL_NAME:
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

    if getattr(ow_middleware, _PATCH_APPLIED_ATTR, False):
        return

    original_generate_chat_completion = ow_middleware.generate_chat_completion
    original_serialize_output = ow_middleware.serialize_output

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

    ow_middleware.generate_chat_completion = patched_generate_chat_completion
    ow_middleware.serialize_output = patched_serialize_output
    setattr(ow_middleware, _PATCH_APPLIED_ATTR, True)
    _LOG.info("Applied legacy Open WebUI deep-job launch guard patch")
