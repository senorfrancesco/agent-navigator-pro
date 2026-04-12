from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional, Tuple

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
OpenWebUIToolStatus = Literal["enabled", "deferred"]


@dataclass(frozen=True)
class ToolDefinition:
    name: ToolName
    label: str
    category: ToolCategory
    execution_mode: ExecutionMode
    summary: str
    use_when: str
    input_summary: str
    output_summary: str
    legacy_executor: str
    openwebui_status: OpenWebUIToolStatus = "enabled"
    availability_note: Optional[str] = None
    requires_document_context: bool = False
    requires_min_documents: int = 0
    supported_routing_modes: Tuple[RoutingMode, ...] = ("explicit", "assisted", "auto")

    @property
    def openapi_description(self) -> str:
        lines = [
            self.summary,
            "",
            f"Когда использовать: {self.use_when}",
            f"Вход: {self.input_summary}",
            f"Результат: {self.output_summary}",
        ]
        if self.requires_document_context:
            if self.requires_min_documents > 1:
                lines.append(f"Требования к контексту: минимум {self.requires_min_documents} документа в document_refs.")
            else:
                lines.append("Требования к контексту: нужен хотя бы один document_ref.")
        if self.openwebui_status == "deferred":
            note = self.availability_note or "Инструмент пока не materialize'ится как named tool в Open WebUI."
            lines.append(f"Статус в Open WebUI: deferred. {note}")
        else:
            lines.append("Статус в Open WebUI: enabled.")
        return "\n".join(lines)

    def to_catalog_entry(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "category": self.category,
            "execution_mode": self.execution_mode,
            "summary": self.summary,
            "use_when": self.use_when,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "openwebui_status": self.openwebui_status,
            "availability_note": self.availability_note,
            "requires_document_context": self.requires_document_context,
            "requires_min_documents": self.requires_min_documents,
        }


TOOL_DEFINITIONS: Dict[ToolName, ToolDefinition] = {
    "ask_document": ToolDefinition(
        name="ask_document",
        label="Вопрос по документу",
        category="document_qa",
        execution_mode="sync",
        summary="Точечный вопрос по документу с опорой на document_ref и retrieval по релевантным фрагментам.",
        use_when="Когда пользователь хочет уточнить конкретную деталь документа и задать новый вопрос по тому же document_ref без запуска отдельного глубокого анализа.",
        input_summary="question + хотя бы один document_ref; вопрос определяет, какие фрагменты будут извлечены для ответа.",
        output_summary="Короткий ответ по содержимому документа с источниками и execution metadata; результат относится к текущему question, а не заменяет состояние документа целиком.",
        legacy_executor="document_question",
        openwebui_status="deferred",
        availability_note="Роль ask_document будет подтверждена отдельным решением M3.9 относительно Open WebUI Knowledge.",
        requires_document_context=True,
    ),
    "analyze_document_fast": ToolDefinition(
        name="analyze_document_fast",
        label="Быстрый анализ документа",
        category="document_analysis",
        execution_mode="sync",
        summary="Быстрый обзор документа с извлечением ключевых пунктов и рисков.",
        use_when="Когда нужен компактный обзор одного документа без long-running job, но с возможностью сместить акцент через analysis_goal.",
        input_summary="analysis_goal опционален; нужен хотя бы один document_ref; новый analysis_goal должен трактоваться как новый запуск анализа.",
        output_summary="Синхронный assistant_message с краткими выводами, источниками и structured_result для текущей цели анализа.",
        legacy_executor="document_analysis",
        openwebui_status="deferred",
        availability_note="Named tool будет включён после backend-owned upload/document binding (`M2.1/M2.2`).",
        requires_document_context=True,
    ),
    "analyze_document_deep": ToolDefinition(
        name="analyze_document_deep",
        label="Глубокий анализ документа",
        category="document_analysis",
        execution_mode="async",
        summary="Глубокий анализ документа для длинных разборов и rich result/report contract.",
        use_when="Когда нужен развёрнутый анализ документа под конкретную цель; новый analysis_goal по тому же document_ref считается новым запуском, а не повторным чтением старого отчёта.",
        input_summary="analysis_goal опционален; нужен хотя бы один document_ref; deep path по умолчанию работает как accepted job с отдельным состоянием запуска.",
        output_summary="Accepted job с job_id/status_url, а затем completed result через tool-jobs contract; результат относится к конкретному запуску анализа, а не становится постоянным состоянием документа.",
        legacy_executor="document_analysis",
        openwebui_status="deferred",
        availability_note="Named tool будет включён после backend-owned upload/document binding (`M2.1/M2.2`).",
        requires_document_context=True,
    ),
    "compare_documents_fast": ToolDefinition(
        name="compare_documents_fast",
        label="Сравнение документов",
        category="document_compare",
        execution_mode="sync",
        summary="Быстрое сравнение документов по ключевым различиям и конфликтам.",
        use_when="Когда пользователь хочет быстро сопоставить минимум два документа без запуска deep async job.",
        input_summary="comparison_goal опционален; нужно минимум два document_ref.",
        output_summary="Синхронный assistant_message со сравнением, основными отличиями и источниками.",
        legacy_executor="compare_documents",
        openwebui_status="deferred",
        availability_note="Named tool будет включён после multi-document binding (`M2.2`).",
        requires_document_context=True,
        requires_min_documents=2,
    ),
    "compare_documents_deep": ToolDefinition(
        name="compare_documents_deep",
        label="Глубокое сравнение документов",
        category="document_compare",
        execution_mode="async",
        summary="Глубокое сравнение документов с rich result/report contract и long-running execution.",
        use_when="Когда нужно детальное сравнение нескольких документов с async job lifecycle и последующим refresh/result path.",
        input_summary="comparison_goal опционален; нужно минимум два document_ref; deep path идёт как accepted job.",
        output_summary="Accepted job с job_id/status_url, затем completed comparison result или report artifact.",
        legacy_executor="compare_documents",
        openwebui_status="deferred",
        availability_note="Named tool будет включён после multi-document binding (`M2.2`).",
        requires_document_context=True,
        requires_min_documents=2,
    ),
    "analyze_equipment_fast": ToolDefinition(
        name="analyze_equipment_fast",
        label="Быстрый анализ оборудования",
        category="equipment_analysis",
        execution_mode="sync",
        summary="Быстрый one-shot анализ оборудования, спецификации или краткой карточки.",
        use_when="Когда нужен быстрый ответ по одному описанию оборудования без long-running job.",
        input_summary="equipment_query с текстовым описанием, моделью, серией или короткой спецификацией.",
        output_summary="Синхронный assistant_message с кратким анализом, выводами и optional sources.",
        legacy_executor="equipment_analysis",
    ),
    "analyze_equipment_deep": ToolDefinition(
        name="analyze_equipment_deep",
        label="Глубокий анализ оборудования",
        category="equipment_analysis",
        execution_mode="async",
        summary="Глубокий анализ оборудования с async job contract для тяжёлых запросов.",
        use_when="Когда нужен развёрнутый анализ оборудования, который может идти заметно дольше обычного ответа.",
        input_summary="equipment_query с подробным описанием оборудования; deep path запускается как accepted job.",
        output_summary="Accepted job с job_id/status_url и последующим completed result через tool-jobs contract.",
        legacy_executor="equipment_analysis",
    ),
}


def list_tool_definitions() -> Tuple[ToolDefinition, ...]:
    return tuple(TOOL_DEFINITIONS.values())


def list_openwebui_enabled_tool_definitions() -> Tuple[ToolDefinition, ...]:
    return tuple(definition for definition in list_tool_definitions() if definition.openwebui_status == "enabled")


def list_openwebui_deferred_tool_definitions() -> Tuple[ToolDefinition, ...]:
    return tuple(definition for definition in list_tool_definitions() if definition.openwebui_status == "deferred")


def is_known_tool(name: str) -> bool:
    return name in TOOL_DEFINITIONS


def get_tool_definition(name: ToolName) -> ToolDefinition:
    return TOOL_DEFINITIONS[name]
