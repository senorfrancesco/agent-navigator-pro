from __future__ import annotations

from app.clients.openai_compatible import build_chat_client_config, chat_completion
from app.tool_server.schemas import (
    AnalyzeEquipmentFastRequest,
    AnalyzeEquipmentFastResponse,
    EchoToolRequest,
    EchoToolResponse,
)


_EQUIPMENT_FAST_SYSTEM_PROMPT = (
    "Ты анализируешь оборудование, спецификации и краткие технические описания. "
    "Отвечай кратко, по делу, с явным выводом и рисками, если они есть."
)


def _strip_service_footer(text: str) -> str:
    marker = "\n\n---\nTiming / Quality"
    value = str(text or "")
    if marker not in value:
        return value.strip()
    return value.split(marker, 1)[0].rstrip()


async def run_echo_tool(request: EchoToolRequest) -> EchoToolResponse:
    config = build_chat_client_config(current_model_id=request.current_model_id)
    return EchoToolResponse(
        assistant_message=request.message,
        model_id=config.model_id,
    )


async def run_analyze_equipment_fast(
    request: AnalyzeEquipmentFastRequest,
) -> AnalyzeEquipmentFastResponse:
    config = build_chat_client_config(current_model_id=request.current_model_id)
    assistant_message = await chat_completion(
        prompt=request.equipment_query,
        current_model_id=request.current_model_id,
        system_prompt=_EQUIPMENT_FAST_SYSTEM_PROMPT,
        max_tokens=900,
        temperature=0.1,
    )
    return AnalyzeEquipmentFastResponse(
        assistant_message=_strip_service_footer(assistant_message),
        model_id=config.model_id,
    )
