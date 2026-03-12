"""
Workflow: Single-Document Analysis

Линейный конвейер для структурного анализа одного документа:
  classify → extract → summarize → report → END

Классификация документа (keyword scoring, без LLM).
Двухпроходная экстракция позиций (таблицы + LLM текст).
Map-reduce суммаризация с адаптивным промптом по типу документа.
"""

import asyncio
from contextlib import asynccontextmanager
import glob as glob_mod
import json
import os
import re
import time
import httpx
from typing import TypedDict, List, Dict, Any, Annotated, Optional, AsyncIterator
import operator
from langgraph.graph import StateGraph, END

# Абсолютные импорты пакета (TD-5 Fix)
from services.model_manager.ums_client import ums_client
from orchestrator.utils import parse_json_garbage
from orchestrator.shared.http_client import get_shared_client
from orchestrator.workflows.equipment import (
    _extract_tables_from_doc,
    _extract_items_llm,
    _dedup_items,
    _chunk_text,
    truncate_text,
    _polish_items_specs_llm,
)

# URLs серверов
MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")

MAX_TEXT_FOR_LLM = 8000  # Снижено для стабильности KV-кэша GPU


# === State Definition ===

class DocumentAnalysisState(TypedDict):
    input_path: str
    doc_name: str
    doc_type: str                      # "tz"|"smeta"|"kp"|"legal"|"other"
    doc_metadata: Dict[str, Any]       # pages, chars, tables_count, format
    items: List[Dict[str, Any]]        # Извлечённые позиции
    full_text: str
    summary: str                       # LLM-сводка ключевых требований
    final_report: str
    errors: Annotated[List[str], operator.add]


# === Classification ===

_DOC_TYPE_KEYWORDS = {
    "tz": ["техническое задание", "предмет закупки", "требования к поставляемому",
           "тз на", "объект закупки", "техзадание"],
    "smeta": ["сметная документация", "сметный расчет", "сметный расчёт",
              "стоимость работ", "расценк", "локальная смета"],
    "kp": ["коммерческое предложение", "прайс-лист", "ценовое предложение",
           "прайс лист"],
    "legal": ["договор", "контракт", "соглашение", "предмет договора",
              "стороны договора"],
}


def _extract_llm_content(response: Dict[str, Any]) -> str:
    """Normalize common UMS/LLM response shapes to plain text."""
    if not isinstance(response, dict):
        return str(response).strip()

    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] or {}
        return (
            choice.get("text", "")
            or choice.get("message", {}).get("content", "")
            or choice.get("delta", {}).get("content", "")
        ).strip()

    content = response.get("content")
    if isinstance(content, str):
        return content.strip()

    return str(response).strip()


def classify_doc_type(text: str) -> str:
    """Keyword scoring для определения типа документа (без LLM)."""
    text_lower = text[:5000].lower()
    scores = {}
    for doc_type, keywords in _DOC_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[doc_type] = score
    if not scores:
        return "other"
    return max(scores, key=scores.get)


@asynccontextmanager
async def _optional_chainlit_step(name: str, step_type: str) -> AsyncIterator[Any]:
    """Открывает Chainlit step только при активном UI context."""
    try:
        import chainlit as cl
        from chainlit.context import get_context

        get_context()
    except Exception:
        yield None
        return

    async with cl.Step(name=name, type=step_type) as step:
        yield step


def _build_summary_chunk_prompt(type_prompt: str, chunk: str, index: int, total: int) -> str:
    return f"""<|im_start|>system
Ты аналитик документов. Извлекай структурированную информацию из текстов.<|im_end|>
<|im_start|>user
{type_prompt}
Отвечай кратко, по пунктам. Если информация отсутствует — пропусти пункт.

Текст (фрагмент {index} из {total}):
{truncate_text(chunk, MAX_TEXT_FOR_LLM)}<|im_end|>
<|im_start|>assistant
"""


# === Node 1: Classify and Load ===

async def classify_and_load_node(state: DocumentAnalysisState) -> dict:
    """Node 1: Загрузка документа, определение типа, сбор метаданных."""
    path = state["input_path"]
    doc_name = state.get("doc_name") or os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    errors = []

    print(f"[DocAnalysis] Loading: {doc_name}")

    full_text = ""
    pages = 0
    tables_count = 0

    client = await get_shared_client()
    # Загрузка текста
    try:
        resp = await client.post(
            f"{MCP_DOCUMENT_SERVER_URL}/load_document",
            json={"path": path},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "error":
            errors.append(f"Document load error: {data.get('error')}")
        else:
            full_text = data.get("text", "")
    except Exception as e:
        errors.append(f"Failed to load document: {e}")

    # Количество страниц (PDF)
    if ext == ".pdf":
        try:
            resp = await client.post(
                f"{MCP_DOCUMENT_SERVER_URL}/load_pages",
                json={"path": path},
            )
            resp.raise_for_status()
            data = resp.json()
            pages = data.get("total_pages", 0)
        except Exception as e:
            errors.append(f"Failed to load pages: {e}")

    # Количество таблиц
    try:
        resp = await client.post(
            f"{MCP_DOCUMENT_SERVER_URL}/extract_tables",
            json={"path": path},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "error":
            tables_count = len(data.get("tables", []))
    except Exception:
        pass  # Таблицы опциональны

    # Классификация
    doc_type = classify_doc_type(full_text)

    doc_metadata = {
        "pages": pages,
        "chars": len(full_text),
        "tables_count": tables_count,
        "format": ext.lstrip(".").upper() or "unknown",
    }

    print(f"  [DocAnalysis] Type: {doc_type}, pages: {pages}, chars: {len(full_text)}, tables: {tables_count}")
    return {
        "doc_name": doc_name,
        "doc_type": doc_type,
        "doc_metadata": doc_metadata,
        "full_text": full_text,
        "errors": errors,
    }


# === Node 2: Extract Positions ===

async def extract_positions_node(state: DocumentAnalysisState) -> dict:
    """Node 2: Двухпроходная экстракция позиций (таблицы + LLM)."""
    path = state["input_path"]
    errors = []

    print(f"[DocAnalysis] Extracting positions from: {os.path.basename(path)}")

    # Pass 1: Структурные таблицы
    items_table = []
    try:
        items_table = await _extract_tables_from_doc(path)
        print(f"  [DocAnalysis] Tables: {len(items_table)} items")
    except Exception as e:
        errors.append(f"Table extraction failed: {e}")

    # Pass 2: LLM текстовые позиции (Условный запуск)
    items_text = []
    if len(items_table) >= 2:
        print(f"  [DocAnalysis] Skipping LLM text extraction: {len(items_table)} items already found in structured tables.")
    else:
        try:
            already_names = [it["name"] for it in items_table]
            items_text = await _extract_items_llm(path, already_names)
            print(f"  [DocAnalysis] LLM text: {len(items_text)} items")
        except Exception as e:
            errors.append(f"LLM extraction failed: {e}")

    items = _dedup_items(items_table + items_text)
    
    # TD-LLM-Polisher: Очищаем сырые характеристики через LLM
    await _polish_items_specs_llm(items)
    
    print(f"  [DocAnalysis] Total after dedup: {len(items)}")

    return {"items": items, "errors": errors}


# === Node 3: Summarize ===

_SUMMARY_PROMPTS = {
    "tz": (
        "Извлеки ключевые требования из технического задания:\n"
        "- Предмет закупки\n"
        "- Сроки поставки\n"
        "- Гарантия\n"
        "- Условия приёмки\n"
        "- Адрес поставки\n"
        "- Особые требования\n"
    ),
    "smeta": (
        "Извлеки ключевую информацию из сметного расчёта:\n"
        "- Итоговая стоимость\n"
        "- Основные категории затрат\n"
        "- Накладные расходы\n"
        "- Сметная прибыль\n"
        "- НДС\n"
    ),
    "kp": (
        "Извлеки ключевую информацию из коммерческого предложения:\n"
        "- Условия оплаты\n"
        "- Сроки действия предложения\n"
        "- Сроки поставки\n"
        "- Гарантия\n"
        "- Общая стоимость\n"
    ),
    "legal": (
        "Извлеки ключевую информацию из договора:\n"
        "- Стороны договора\n"
        "- Предмет договора\n"
        "- Сроки исполнения\n"
        "- Стоимость и порядок оплаты\n"
        "- Штрафные санкции\n"
        "- Срок действия\n"
    ),
    "other": (
        "Извлеки ключевую информацию из документа:\n"
        "- Основная тема\n"
        "- Ключевые положения\n"
        "- Важные детали\n"
    ),
}


async def summarize_node(state: DocumentAnalysisState) -> dict:
    """Node 3: Map-reduce суммаризация с адаптивным промптом по типу документа."""
    full_text = state.get("full_text", "")
    doc_type = state.get("doc_type", "other")
    errors = []

    if not full_text.strip():
        return {"summary": "Текст документа пуст.", "errors": errors}

    type_prompt = _SUMMARY_PROMPTS.get(doc_type, _SUMMARY_PROMPTS["other"])

    print(f"[DocAnalysis] Summarizing (type={doc_type})")

    # Map-reduce: разбиваем на чанки, суммаризируем каждый, объединяем
    chunks = await _chunk_text(full_text)

    print(f"  [DocAnalysis] {len(chunks)} chunk(s) for summarization")

    chunk_summaries = []
    for idx, chunk in enumerate(chunks):
        prompt = _build_summary_chunk_prompt(type_prompt, chunk, idx + 1, len(chunks))
        step_name = f"Суммаризация чанка {idx+1}/{len(chunks)}"
        print(f"    [DocAnalysis] Processing chunk {idx+1}/{len(chunks)}...")
        try:
            async with _optional_chainlit_step(step_name, "tool") as step:
                response = await ums_client.async_infer("qwen-14b-llm", {
                    "prompt": prompt, "temperature": 0.1, "max_tokens": 1500
                })

                content = _extract_llm_content(response)
                if content.strip():
                    chunk_summaries.append(content.strip())
                    if step is not None:
                        step.output = f"Успешно: {len(content)} симв."

                await asyncio.sleep(1.0)
        except Exception as e:
            print(f"    [DocAnalysis] Chunk {idx+1} failed: {e}")
            errors.append(f"Summarize chunk {idx+1} failed: {e}")

    if not chunk_summaries:
        return {"summary": "Не удалось выполнить суммаризацию.", "errors": errors}

    # Reduce: если несколько чанков — объединяем через ещё один LLM-вызов
    if len(chunk_summaries) == 1:
        summary = chunk_summaries[0]
    else:
        combined = "\n\n---\n\n".join(chunk_summaries)
        reduce_prompt = f"""<|im_start|>system
Ты аналитик документов. Объедини фрагменты анализа в единую сводку.<|im_end|>
<|im_start|>user
Объедини следующие фрагменты анализа документа в единую структурированную сводку. Убери дублирование, сохрани все важные детали.

{truncate_text(combined, MAX_TEXT_FOR_LLM)}<|im_end|>
<|im_start|>assistant
"""
        try:
            response = await ums_client.async_infer("qwen-14b-llm", {
                "prompt": reduce_prompt, "temperature": 0.1, "max_tokens": 2000
            })
            content = response.get("content", "")
            if not content and "choices" in response:
                content = response["choices"][0].get("text", "")
            summary = content.strip() if content.strip() else "\n\n".join(chunk_summaries)
        except Exception as e:
            errors.append(f"Reduce summarization failed: {e}")
            summary = "\n\n".join(chunk_summaries)

    return {"summary": summary, "errors": errors}


# === Node 4: Generate Report ===

_DOC_TYPE_LABELS = {
    "tz": "Техническое задание",
    "smeta": "Сметная документация",
    "kp": "Коммерческое предложение",
    "legal": "Юридический / нормативный документ",
    "other": "Документ",
}


async def generate_analysis_report_node(state: DocumentAnalysisState) -> dict:
    """Node 4: Генерация Markdown отчёта с сохранением и дедупликацией."""
    doc_name = state.get("doc_name", "unknown")
    doc_type = state.get("doc_type", "other")
    doc_metadata = state.get("doc_metadata", {})
    items = state.get("items", [])
    summary = state.get("summary", "")
    errors = state.get("errors", [])

    type_label = _DOC_TYPE_LABELS.get(doc_type, doc_type)
    pages = doc_metadata.get("pages", 0)
    chars = doc_metadata.get("chars", 0)
    fmt = doc_metadata.get("format", "?")
    tables_count = doc_metadata.get("tables_count", 0)

    # Формат страниц
    pages_str = f"{pages} стр." if pages else ""
    format_parts = [fmt]
    if pages_str:
        format_parts.append(pages_str)
    format_parts.append(f"{chars} симв.")
    format_info = ", ".join(format_parts)

    report = f"# Анализ документа: {doc_name}\n\n"
    report += f"**Дата:** {time.strftime('%Y-%m-%d %H:%M')}  "
    report += f"**Тип:** {type_label}  "
    report += f"**Формат:** {format_info}\n\n"

    # Ошибки
    if errors:
        report += "## Предупреждения\n\n"
        for e in errors:
            report += f"- {e}\n"
        report += "\n"

    # Извлечённые позиции
    if items:
        report += f"## Извлеченные позиции ({len(items)} шт.)\n\n"
        report += "| # | Наименование | Характеристики | Кол-во | Цена | Источник |\n"
        report += "| :---: | :--- | :--- | :---: | :---: | :---: |\n"
        for idx, item in enumerate(items, 1):
            name = item.get("name", "-")[:60]
            specs = truncate_text(item.get("specs", "-"), 180)
            specs = specs.replace("|", "\\|")
            qty = item.get("quantity", "-")
            price = item.get("price", "-")
            source = item.get("source", "-")
            report += f"| {idx} | {name} | {specs} | {qty} | {price} | {source} |\n"
        report += "\n"
    else:
        report += "## Извлеченные позиции\n\n"
        report += "Позиции оборудования/товаров не обнаружены в документе.\n\n"

    # Ключевые требования / сводка
    if summary:
        section_title = {
            "tz": "Ключевые требования",
            "smeta": "Сводка по смете",
            "kp": "Условия предложения",
            "legal": "Основные условия договора",
        }.get(doc_type, "Сводка")
        report += f"## {section_title}\n\n{summary}\n\n"

    # Метаданные
    report += "## Метаданные\n\n"
    report += f"- **Таблиц обнаружено:** {tables_count}\n"
    report += f"- **Позиций извлечено:** {len(items)}\n"
    report += f"- **Символов в документе:** {chars}\n"

    # Сохранение в файл через общую утилиту
    try:
        from orchestrator.shared.report_utils import save_report_with_dedup
        final_report_text = save_report_with_dedup(
            report_text=report,
            prefix="Report_Analysis",
            input_names=[doc_name],
            current_metric=len(items),
            metric_marker="Позиций извлечено:**"
        )
        return {"final_report": final_report_text}
    except Exception as e:
        report += f"\n---\n**Ошибка сохранения отчета:** {e}"
        return {"final_report": report}


# === Build Graph ===

def create_analysis_graph():
    workflow = StateGraph(DocumentAnalysisState)

    workflow.add_node("classify", classify_and_load_node)
    workflow.add_node("extract", extract_positions_node)
    workflow.add_node("summarize", summarize_node)
    workflow.add_node("report", generate_analysis_report_node)

    workflow.set_entry_point("classify")
    workflow.add_edge("classify", "extract")
    workflow.add_edge("extract", "summarize")
    workflow.add_edge("summarize", "report")
    workflow.add_edge("report", END)

    return workflow.compile()
