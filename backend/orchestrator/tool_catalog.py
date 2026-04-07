from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Tuple

ToolName = Literal[
    "ask_document",
    "analyze_document_fast",
    "analyze_document_deep",
    "compare_documents_fast",
    "compare_documents_deep",
    "analyze_equipment_fast",
    "analyze_equipment_deep",
]
RoutingMode = Literal["explicit", "assisted", "auto"]
ExecutionMode = Literal["sync", "async"]
ToolCategory = Literal["document_qa", "document_analysis", "document_compare", "equipment_analysis"]


@dataclass(frozen=True)
class ToolDefinition:
    name: ToolName
    category: ToolCategory
    execution_mode: ExecutionMode
    summary: str
    legacy_executor: str
    supported_routing_modes: Tuple[RoutingMode, ...] = ("explicit", "assisted", "auto")


TOOL_DEFINITIONS: Dict[ToolName, ToolDefinition] = {
    "ask_document": ToolDefinition(
        name="ask_document",
        category="document_qa",
        execution_mode="sync",
        summary="Grounded Q&A по одному или нескольким документам.",
        legacy_executor="document_question",
    ),
    "analyze_document_fast": ToolDefinition(
        name="analyze_document_fast",
        category="document_analysis",
        execution_mode="sync",
        summary="Быстрый обзор и извлечение ключевых моментов из документа.",
        legacy_executor="document_analysis",
    ),
    "analyze_document_deep": ToolDefinition(
        name="analyze_document_deep",
        category="document_analysis",
        execution_mode="async",
        summary="Глубокий анализ документа с long-running execution contract.",
        legacy_executor="document_analysis",
    ),
    "compare_documents_fast": ToolDefinition(
        name="compare_documents_fast",
        category="document_compare",
        execution_mode="sync",
        summary="Быстрое сравнение документов по ключевым различиям.",
        legacy_executor="compare_documents",
    ),
    "compare_documents_deep": ToolDefinition(
        name="compare_documents_deep",
        category="document_compare",
        execution_mode="async",
        summary="Глубокое сравнение документов с rich result/report contract.",
        legacy_executor="compare_documents",
    ),
    "analyze_equipment_fast": ToolDefinition(
        name="analyze_equipment_fast",
        category="equipment_analysis",
        execution_mode="sync",
        summary="Быстрый анализ оборудования, спецификации или карточки.",
        legacy_executor="equipment_analysis",
    ),
    "analyze_equipment_deep": ToolDefinition(
        name="analyze_equipment_deep",
        category="equipment_analysis",
        execution_mode="async",
        summary="Глубокий анализ оборудования с long-running execution contract.",
        legacy_executor="equipment_analysis",
    ),
}


def list_tool_definitions() -> Tuple[ToolDefinition, ...]:
    return tuple(TOOL_DEFINITIONS.values())


def is_known_tool(name: str) -> bool:
    return name in TOOL_DEFINITIONS


def get_tool_definition(name: ToolName) -> ToolDefinition:
    return TOOL_DEFINITIONS[name]
