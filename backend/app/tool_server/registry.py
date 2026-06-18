from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Tuple


ToolName = Literal["echo", "analyze_equipment_fast", "analyze_equipment_deep"]


@dataclass(frozen=True)
class ToolDefinition:
    name: ToolName
    summary: str
    description: str


TOOLS: Tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="echo",
        summary="Echo",
        description="Проверочный инструмент нового backend/app контура.",
    ),
    ToolDefinition(
        name="analyze_equipment_fast",
        summary="Быстрый анализ оборудования",
        description=(
            "Быстрый текстовый анализ оборудования через внешний OpenAI-compatible LLM provider. "
            "Не запускает старый orchestrator, UMS, RAG или document workflow."
        ),
    ),
    ToolDefinition(
        name="analyze_equipment_deep",
        summary="Глубокий анализ оборудования",
        description=(
            "Проверочный deep-job контракт нового backend/app контура. "
            "Создаёт accepted job для проверки polling, result и cancel routes."
        ),
    ),
)


def get_tool_definition(name: ToolName) -> ToolDefinition:
    for definition in TOOLS:
        if definition.name == name:
            return definition
    raise KeyError(name)
