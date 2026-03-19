"""
Workflow: Single-Document Analysis

Линейный конвейер для структурного анализа одного документа:
  classify → extract → summarize → report → END

Классификация документа (keyword scoring, без LLM).
Двухпроходная экстракция позиций (таблицы + LLM текст).
Map-reduce суммаризация с адаптивным промптом по типу документа.
"""

import asyncio
import copy
import glob as glob_mod
import inspect
import json
import logging
import os
import re
import time
import httpx
from typing import TypedDict, List, Dict, Any, Annotated, Optional
import operator
from langgraph.graph import StateGraph, END

# Абсолютные импорты пакета (TD-5 Fix)
from services.model_manager.model_selection import resolve_model_selection
from services.model_manager.ums_client import ums_client
from services.observability import inc_metric_counter
from orchestrator.utils import parse_json_garbage
from orchestrator.shared.http_client import get_shared_client
from orchestrator.summary_reduce_policy import (
    resolve_fast_final_merge_enabled,
    resolve_reduce_strategy,
)
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
DOCUMENT_ANALYSIS_SUMMARIZE_TEMPERATURE = float(
    os.getenv("DOCUMENT_ANALYSIS_SUMMARIZE_TEMPERATURE", "0.1")
)
DOCUMENT_ANALYSIS_SUMMARIZE_MAX_TOKENS = int(
    os.getenv("DOCUMENT_ANALYSIS_SUMMARIZE_MAX_TOKENS", "1500")
)
DOCUMENT_ANALYSIS_REDUCE_MAX_TOKENS = int(
    os.getenv("DOCUMENT_ANALYSIS_REDUCE_MAX_TOKENS", "2000")
)
SUMMARY_FINAL_RESERVE_RATIO = float(os.getenv("SUMMARY_FINAL_RESERVE_RATIO", "0.15"))
SUMMARY_FINAL_RESERVE_TOKENS = int(os.getenv("SUMMARY_FINAL_RESERVE_TOKENS", "128"))
DOCUMENT_ANALYSIS_SUMMARIZE_SLEEP_S = float(
    os.getenv("DOCUMENT_ANALYSIS_SUMMARIZE_SLEEP_S", "1.0")
)
logger = logging.getLogger("document_analysis_workflow")
_LEGAL_COMPARE_SELECTION = resolve_model_selection("llm.legal_compare")


async def _infer_document_analysis_with_failover(
    *,
    stage: str,
    prompt: str,
    payload: Dict[str, Any],
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    response = await ums_client.async_infer(
        "llm.legal_compare",
        {
            **payload,
            "prompt": prompt,
        },
    )
    model_execution = response.get("model_execution") if isinstance(response, dict) else None
    if not isinstance(model_execution, dict):
        model_execution = {
            "role_key": _LEGAL_COMPARE_SELECTION.role_key,
            "primary_model_id": _LEGAL_COMPARE_SELECTION.primary_model_id,
            "fallback_model_id": _LEGAL_COMPARE_SELECTION.fallback_model_id,
            "used_model_id": _LEGAL_COMPARE_SELECTION.resolved_model_id,
            "fallback_stage": stage,
            "fallback_used": False,
            "status": "completed",
            "source": "workflow",
        }
    elif stage and not model_execution.get("fallback_stage"):
        model_execution = {**model_execution, "fallback_stage": stage}
    return response, model_execution


# === State Definition ===

class DocumentAnalysisState(TypedDict):
    input_path: str
    doc_name: str
    doc_type: str                      # "tz"|"smeta"|"kp"|"legal"|"other"
    doc_metadata: Dict[str, Any]       # pages, chars, tables_count, format
    items: List[Dict[str, Any]]        # Извлечённые позиции
    full_text: str
    summary: str                       # LLM-сводка ключевых требований
    summary_metadata: Dict[str, Any]
    final_report: str
    runtime_context: Dict[str, Any]
    model_execution: Annotated[List[Dict[str, Any]], operator.add]
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


def _get_runtime_context(state: DocumentAnalysisState) -> Dict[str, Any]:
    raw = state.get("runtime_context") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _format_elapsed_seconds(elapsed_seconds: float) -> str:
    clamped = max(0.0, float(elapsed_seconds))
    if clamped < 60:
        return f"{clamped:.1f} сек."
    minutes, seconds = divmod(int(round(clamped)), 60)
    if minutes < 60:
        return f"{minutes} мин. {seconds} сек."
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч. {minutes} мин. {seconds} сек."


async def _raise_if_cancelled(state: DocumentAnalysisState) -> None:
    callback = _get_runtime_context(state).get("is_cancelled")
    if callback is None:
        return
    result = callback()
    if inspect.isawaitable(result):
        result = await result
    if result:
        raise asyncio.CancelledError


async def _update_summary_progress(
    state: DocumentAnalysisState,
    *,
    title: str,
    content: str,
) -> None:
    callback = _get_runtime_context(state).get("update_progress_box")
    if callback is None:
        return
    result = callback(key="documents_summary_progress", title=title, content=content)
    if inspect.isawaitable(result):
        await result


def _get_summary_policy(state: DocumentAnalysisState) -> Dict[str, Any]:
    context = _get_runtime_context(state)
    policy = context.get("summary_policy") or {}
    if not isinstance(policy, dict):
        policy = {}
    weak_pc = bool(policy.get("weak_pc_mode"))
    return {
        "weak_pc_mode": weak_pc,
        "enable_fast_final_merge": resolve_fast_final_merge_enabled(
            policy=policy,
            env_var="DOCUMENT_ANALYSIS_ENABLE_FAST_FINAL_MERGE",
        ),
        "chunk_input_chars": int(policy.get("chunk_input_chars") or (2200 if weak_pc else MAX_TEXT_FOR_LLM)),
        "group_input_chars": int(policy.get("group_input_chars") or (1800 if weak_pc else MAX_TEXT_FOR_LLM)),
        "final_input_chars": int(policy.get("final_input_chars") or (2200 if weak_pc else MAX_TEXT_FOR_LLM)),
        "group_size": max(2, int(policy.get("group_size") or (2 if weak_pc else 4))),
        "final_max_tokens": max(256, int(policy.get("final_max_tokens") or DOCUMENT_ANALYSIS_REDUCE_MAX_TOKENS)),
    }


def _group_items_for_budget(items: List[str], *, max_chars: int, max_items: int) -> List[List[str]]:
    groups: List[List[str]] = []
    current: List[str] = []
    current_len = 0
    for item in items:
        normalized = str(item or "").strip()
        if not normalized:
            continue
        item_len = len(normalized) + (2 if current else 0)
        if current and (current_len + item_len > max_chars or len(current) >= max_items):
            groups.append(current)
            current = []
            current_len = 0
        current.append(normalized[:max_chars])
        current_len += len(current[-1]) + (2 if len(current) > 1 else 0)
    if current:
        groups.append(current)
    return groups or [items[:max_items]]


def _force_progressive_grouping(items: List[str], *, max_items: int) -> List[List[str]]:
    """Guarantee that collapse reduces item count even when char caps force singleton groups."""
    normalized = [str(item or "").strip() for item in items if str(item or "").strip()]
    if len(normalized) <= 1:
        return [normalized] if normalized else [[]]
    stride = 2 if max_items >= 2 else 1
    return [normalized[index:index + stride] for index in range(0, len(normalized), stride)]


def _estimate_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // 4)


def _resolve_summary_reserve_tokens(budget_tokens: int) -> int:
    if budget_tokens <= 1:
        return 0
    ratio_based = int(max(0.0, SUMMARY_FINAL_RESERVE_RATIO) * budget_tokens)
    reserve = max(int(SUMMARY_FINAL_RESERVE_TOKENS), ratio_based)
    return min(budget_tokens - 1, reserve)


def _build_reduce_admission_snapshot(
    *,
    joined_payload: str,
    final_prompt: str,
    group_prompt: str,
    budget_tokens: int,
) -> Dict[str, Any]:
    joined_payload_tokens_est = _estimate_tokens(joined_payload)
    final_prompt_tokens_est = _estimate_tokens(final_prompt)
    group_prompt_tokens_est = _estimate_tokens(group_prompt)
    reserve_tokens = _resolve_summary_reserve_tokens(budget_tokens)
    margin_tokens = max(0, budget_tokens - final_prompt_tokens_est)
    return {
        "joined_payload_chars": len(str(joined_payload or "")),
        "joined_payload_tokens_est": joined_payload_tokens_est,
        "final_prompt_chars": len(str(final_prompt or "")),
        "final_prompt_tokens_est": final_prompt_tokens_est,
        "group_prompt_chars": len(str(group_prompt or "")),
        "group_prompt_tokens_est": group_prompt_tokens_est,
        "budget_tokens": int(budget_tokens),
        "reserve_tokens": int(reserve_tokens),
        "margin_tokens": int(margin_tokens),
        "fits_with_margin": final_prompt_tokens_est <= max(0, budget_tokens - reserve_tokens),
    }


def _resolve_reduce_strategy_with_snapshot(
    *,
    reduce_items_count: int,
    low_vram: bool,
    degraded_active: bool,
    fast_final_merge_enabled: bool,
    admission_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        strategy_meta = resolve_reduce_strategy(
            reduce_items_count=reduce_items_count,
            low_vram=low_vram,
            degraded_active=degraded_active,
            fast_final_merge_enabled=fast_final_merge_enabled,
            admission_snapshot=admission_snapshot,
        )
    except TypeError:
        strategy_meta = resolve_reduce_strategy(
            reduce_items_count=reduce_items_count,
            final_stage_safe=bool(admission_snapshot.get("fits_with_margin")),
            low_vram=low_vram,
            degraded_active=degraded_active,
            fast_final_merge_enabled=fast_final_merge_enabled,
        )
    normalized = dict(strategy_meta or {})
    normalized.setdefault("admission_snapshot", dict(admission_snapshot))
    normalized.setdefault("fast_path_eligible", bool(admission_snapshot.get("fits_with_margin")))
    return normalized


def _is_summary_shadow_mode_enabled() -> bool:
    return str(os.getenv("SUMMARY_REDUCE_STRATEGY_SHADOW_MODE", "0")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _estimate_reduce_merge_levels(reduce_items_count: int, group_size: int) -> int:
    if reduce_items_count <= 1:
        return 0
    current = max(1, int(reduce_items_count))
    group_size = max(2, int(group_size or 2))
    levels = 0
    while current > group_size:
        current = (current + group_size - 1) // group_size
        levels += 1
    return levels


def _build_summary_shadow_trace(
    *,
    reduce_items_count: int,
    group_size: int,
    executed_strategy: str,
    recommended_strategy: str,
    reason: str,
    admission_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    shadow_baseline_strategy = "single_item" if reduce_items_count <= 1 else "hierarchical_merge"
    shadow_baseline_levels = _estimate_reduce_merge_levels(reduce_items_count, group_size)
    executed_levels = 0 if executed_strategy == "fast_final_merge" else shadow_baseline_levels
    would_skip_levels = max(0, shadow_baseline_levels - executed_levels)
    estimated_token_saving = would_skip_levels * int(admission_snapshot.get("group_prompt_tokens_est", 0) or 0)
    return {
        "executed_strategy": executed_strategy,
        "recommended_strategy": recommended_strategy,
        "shadow_baseline_strategy": shadow_baseline_strategy,
        "shadow_baseline_levels": shadow_baseline_levels,
        "executed_levels": executed_levels,
        "would_skip_levels": would_skip_levels,
        "estimated_token_saving": estimated_token_saving,
        "reason": reason,
        "reduce_items_count": reduce_items_count,
        "group_size": int(group_size),
    }


def _init_summary_metadata() -> Dict[str, Any]:
    return {
        "degraded": False,
        "completed_stages": [],
        "final_synthesis_status": "not_needed",
        "stage_admission": {},
        "degraded_reason": None,
        "degraded_stage": None,
        "reduce_strategy": None,
        "reduce_reason": None,
        "reduce_levels_used": 0,
        "reduce_groups_total": 0,
        "fast_path_eligible": False,
        "final_admission_estimated_tokens": 0,
        "final_prompt_tokens_est": 0,
        "group_prompt_tokens_est": 0,
        "final_admission_budget_tokens": 0,
        "final_admission_reserve_tokens": 0,
        "final_admission_margin_tokens": 0,
        "reduce_decisions": [],
        "early_exit_after_level": None,
    }


def _mark_stage_completed(metadata: Dict[str, Any], stage: str) -> None:
    stages = metadata.setdefault("completed_stages", [])
    if stage not in stages:
        stages.append(stage)


def _mark_degraded(
    metadata: Dict[str, Any],
    *,
    stage: str,
    reason: str,
) -> None:
    metadata["degraded"] = True
    metadata["degraded_stage"] = stage
    metadata["degraded_reason"] = reason


def _record_stage_admission(
    metadata: Dict[str, Any],
    *,
    stage: str,
    admission: Dict[str, Any],
) -> None:
    stage_admission = metadata.setdefault("stage_admission", {})
    stage_admission[stage] = dict(admission)


def _resolve_stage_admission(
    *,
    stage: str,
    payload: str,
    input_char_cap: int,
    output_token_cap: int,
) -> Dict[str, Any]:
    payload_chars = len(str(payload or ""))
    payload_tokens = _estimate_tokens(payload)
    token_budget_limit = max(256, _estimate_tokens("x" * input_char_cap) + int(output_token_cap))
    reserve_tokens = _resolve_summary_reserve_tokens(token_budget_limit)
    requires_bounded = payload_tokens > max(0, token_budget_limit - reserve_tokens)
    return {
        "stage": stage,
        "admission": "requires_bounded" if requires_bounded else "ok",
        "reason": "token_budget" if requires_bounded else "ok",
        "payload_chars": payload_chars,
        "payload_tokens": payload_tokens,
        "input_char_cap": int(input_char_cap),
        "output_token_cap": int(output_token_cap),
        "budget_tokens": int(token_budget_limit),
        "reserve_tokens": int(reserve_tokens),
        "margin_tokens": max(0, token_budget_limit - payload_tokens),
        "fits_with_margin": not requires_bounded,
    }


def _build_group_merge_prompt(payload: str) -> str:
    return f"""<|im_start|>system
Ты аналитик документов. Объедини фрагменты анализа в единую сводку без потери важных фактов.<|im_end|>
<|im_start|>user
Объедини следующие фрагменты анализа документа в единую структурированную сводку. Убери дублирование, сохрани важные детали и различия.

{payload}<|im_end|>
<|im_start|>assistant
"""


def _build_final_synthesis_prompt(type_prompt: str, payload: str) -> str:
    return f"""<|im_start|>system
Ты аналитик документов. Сформируй итоговую структурированную сводку по уже собранным промежуточным summary.<|im_end|>
<|im_start|>user
{type_prompt}
Используй только входные summary. Убери повторы, сохрани ключевые факты и ограничения.

ПРОМЕЖУТОЧНЫЕ СВОДКИ:
{payload}<|im_end|>
<|im_start|>assistant
"""


def _build_partial_summary(items: List[str], *, max_chars: int) -> str:
    fragments: List[str] = []
    current_len = 0
    for idx, item in enumerate(items, start=1):
        normalized = str(item or "").strip()
        if not normalized:
            continue
        block = f"### Блок {idx}\n{normalized}"
        extra = len(block) + (2 if fragments else 0)
        if fragments and current_len + extra > max_chars:
            break
        fragments.append(block)
        current_len += extra
    if not fragments:
        return "Частичная сводка недоступна."
    return (
        "Финальная сводка не была построена полностью. Ниже сохранены промежуточные результаты.\n\n"
        + "\n\n".join(fragments)
    ).strip()


async def _infer_stage_with_policy_retry(
    *,
    stage: str,
    prompt_factory: Any,
    payload: str,
    input_char_cap: int,
    output_token_cap: int,
) -> Dict[str, Any]:
    admission = _resolve_stage_admission(
        stage=stage,
        payload=payload,
        input_char_cap=input_char_cap,
        output_token_cap=output_token_cap,
    )
    prompt_payload = str(payload or "")
    try:
        response, model_execution = await _infer_document_analysis_with_failover(
            stage=stage,
            prompt=prompt_factory(prompt_payload),
            payload={
                "temperature": DOCUMENT_ANALYSIS_SUMMARIZE_TEMPERATURE,
                "max_tokens": output_token_cap,
            },
        )
        return {
            "text": _extract_llm_content(response).strip(),
            "admission": admission,
            "retry_used": False,
            "model_execution": model_execution,
        }
    except Exception as first_exc:
        compact_char_cap = max(256, input_char_cap // 2)
        compact_token_cap = max(256, int(output_token_cap * 0.75))
        compact_payload = truncate_text(payload, compact_char_cap)
        compact_admission = _resolve_stage_admission(
            stage=stage,
            payload=compact_payload,
            input_char_cap=compact_char_cap,
            output_token_cap=compact_token_cap,
        )
        try:
            response, model_execution = await _infer_document_analysis_with_failover(
                stage=stage,
                prompt=prompt_factory(compact_payload),
                payload={
                    "temperature": DOCUMENT_ANALYSIS_SUMMARIZE_TEMPERATURE,
                    "max_tokens": compact_token_cap,
                },
            )
            return {
                "text": _extract_llm_content(response).strip(),
                "admission": compact_admission,
                "retry_used": True,
                "model_execution": model_execution,
            }
        except Exception as retry_exc:
            raise RuntimeError(f"{stage} failed after policy-changing retry: {retry_exc}") from first_exc


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
        logger.warning("Document analysis load_document fallback: %s", e, exc_info=True)
        inc_metric_counter(
            "agent_nav_fallback_events_total",
            labels={"component": "document_analysis", "fallback": "load_document_failed", "source": "workflow"},
        )
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
            logger.warning("Document analysis load_pages fallback: %s", e, exc_info=True)
            inc_metric_counter(
                "agent_nav_fallback_events_total",
                labels={"component": "document_analysis", "fallback": "load_pages_failed", "source": "workflow"},
            )
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
        logger.warning("Document analysis extract_tables degraded path", exc_info=True)
        inc_metric_counter(
            "agent_nav_fallback_events_total",
            labels={"component": "document_analysis", "fallback": "extract_tables_failed", "source": "workflow"},
        )
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
    model_execution_events: List[Dict[str, Any]] = []

    print(f"[DocAnalysis] Extracting positions from: {os.path.basename(path)}")

    # Pass 1: Структурные таблицы
    items_table = []
    try:
        items_table = await _extract_tables_from_doc(path)
        print(f"  [DocAnalysis] Tables: {len(items_table)} items")
    except Exception as e:
        logger.warning("Document analysis table extraction fallback: %s", e, exc_info=True)
        inc_metric_counter(
            "agent_nav_fallback_events_total",
            labels={"component": "document_analysis", "fallback": "table_extract_failed", "source": "workflow"},
        )
        errors.append(f"Table extraction failed: {e}")

    # Pass 2: LLM текстовые позиции (Условный запуск)
    items_text = []
    if len(items_table) >= 2:
        print(f"  [DocAnalysis] Skipping LLM text extraction: {len(items_table)} items already found in structured tables.")
    else:
        try:
            async def _on_extract_chunk_progress(current: int, total: int) -> None:
                await _update_summary_progress(
                    state,
                    title="Извлечение позиций",
                    content=f"Фрагмент {current}/{total}",
                )

            already_names = [it["name"] for it in items_table]
            items_text = await _extract_items_llm(
                path,
                already_names,
                progress_callback=_on_extract_chunk_progress,
                model_execution_events=model_execution_events,
            )
            print(f"  [DocAnalysis] LLM text: {len(items_text)} items")
            await _update_summary_progress(
                state,
                title="Извлечение позиций",
                content="Извлечение завершено",
            )
        except Exception as e:
            logger.warning("Document analysis llm extraction fallback: %s", e, exc_info=True)
            inc_metric_counter(
                "agent_nav_fallback_events_total",
                labels={"component": "document_analysis", "fallback": "llm_extract_failed", "source": "workflow"},
            )
            errors.append(f"LLM extraction failed: {e}")

    items = _dedup_items(items_table + items_text)
    
    # TD-LLM-Polisher: Очищаем сырые характеристики через LLM
    await _polish_items_specs_llm(items, model_execution_events=model_execution_events)
    
    print(f"  [DocAnalysis] Total after dedup: {len(items)}")

    result = {"items": items, "errors": errors}
    if model_execution_events:
        result["model_execution"] = model_execution_events
    return result


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
    summary_metadata = _init_summary_metadata()

    if not full_text.strip():
        return {"summary": "Текст документа пуст.", "errors": errors, "summary_metadata": summary_metadata}

    type_prompt = _SUMMARY_PROMPTS.get(doc_type, _SUMMARY_PROMPTS["other"])
    summary_policy = _get_summary_policy(state)
    shadow_mode_enabled = _is_summary_shadow_mode_enabled()

    print(f"[DocAnalysis] Summarizing (type={doc_type})")

    # Map-reduce: разбиваем на чанки, суммаризируем каждый, объединяем
    chunks = await _chunk_text(full_text)

    print(f"  [DocAnalysis] {len(chunks)} chunk(s) for summarization")

    chunk_summaries = []
    model_execution_events: List[Dict[str, Any]] = []
    shadow_decisions: List[Dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        await _raise_if_cancelled(state)
        chunk_admission = _resolve_stage_admission(
            stage="chunk_summary",
            payload=chunk,
            input_char_cap=summary_policy["chunk_input_chars"],
            output_token_cap=DOCUMENT_ANALYSIS_SUMMARIZE_MAX_TOKENS,
        )
        _record_stage_admission(summary_metadata, stage=f"chunk_summary_{idx + 1}", admission=chunk_admission)
        if chunk_admission["admission"] != "ok":
            _mark_degraded(summary_metadata, stage="chunk_summary", reason="token_budget")
        prompt = _build_summary_chunk_prompt(
            type_prompt,
            truncate_text(chunk, summary_policy["chunk_input_chars"]),
            idx + 1,
            len(chunks),
        )
        print(f"    [DocAnalysis] Processing chunk {idx+1}/{len(chunks)}...")
        try:
            await _update_summary_progress(
                state,
                title="Суммаризация документа",
                content=f"Фрагмент {idx+1}/{len(chunks)}",
            )
            response, model_execution = await _infer_document_analysis_with_failover(
                stage="chunk_summary",
                prompt=prompt,
                payload={
                    "temperature": DOCUMENT_ANALYSIS_SUMMARIZE_TEMPERATURE,
                    "max_tokens": DOCUMENT_ANALYSIS_SUMMARIZE_MAX_TOKENS,
                },
            )
            model_execution_events.append(model_execution)
            content = _extract_llm_content(response)
            if content.strip():
                chunk_summaries.append(content.strip())
                _mark_stage_completed(summary_metadata, "chunk_summary")

            await asyncio.sleep(DOCUMENT_ANALYSIS_SUMMARIZE_SLEEP_S)
        except Exception as e:
            print(f"    [DocAnalysis] Chunk {idx+1} failed: {e}")
            logger.warning("Document analysis summarize chunk fallback idx=%s: %s", idx + 1, e, exc_info=True)
            inc_metric_counter(
                "agent_nav_fallback_events_total",
                labels={"component": "document_analysis", "fallback": "summarize_chunk_failed", "source": "workflow"},
            )
            errors.append(f"Summarize chunk {idx+1} failed: {e}")

    if not chunk_summaries:
        summary_metadata["final_synthesis_status"] = "failed"
        return {"summary": "Не удалось выполнить суммаризацию.", "errors": errors, "summary_metadata": summary_metadata}

    if len(chunk_summaries) == 1:
        summary = chunk_summaries[0]
        summary_metadata["final_synthesis_status"] = "not_needed"
        summary_metadata["reduce_strategy"] = "single_item"
        summary_metadata["reduce_reason"] = "not_needed"
        if shadow_mode_enabled:
            single_trace = _build_summary_shadow_trace(
                reduce_items_count=1,
                group_size=summary_policy["group_size"],
                executed_strategy="single_item",
                recommended_strategy="single_item",
                reason="not_needed",
                admission_snapshot={
                    "group_prompt_tokens_est": 0,
                },
            )
            summary_metadata["executed_strategy"] = "single_item"
            summary_metadata["recommended_strategy"] = "single_item"
            summary_metadata["shadow_baseline_strategy"] = single_trace["shadow_baseline_strategy"]
            summary_metadata["would_skip_levels"] = int(single_trace["would_skip_levels"])
            summary_metadata["estimated_token_saving"] = int(single_trace["estimated_token_saving"])
            summary_metadata["reduce_decisions"] = [single_trace]
        else:
            summary_metadata["reduce_decisions"] = [
                {
                    "items_count": 1,
                    "chars": len(summary),
                    "tokens_est": _estimate_tokens(summary),
                    "strategy_selected": "single_item",
                    "reason": "not_needed",
                    "early_exit_after_level": 0,
                }
            ]
        summary_metadata["early_exit_after_level"] = 0
    else:
        reduce_items = list(chunk_summaries)
        merge_level = 0
        current_admission_snapshot: Dict[str, Any] = {}
        while len(reduce_items) > 1:
            await _raise_if_cancelled(state)
            final_payload = "\n\n---\n\n".join(reduce_items)
            final_admission = _resolve_stage_admission(
                stage="final_synthesis",
                payload=final_payload,
                input_char_cap=summary_policy["final_input_chars"],
                output_token_cap=summary_policy["final_max_tokens"],
            )
            final_prompt = _build_final_synthesis_prompt(type_prompt, final_payload)
            grouped_preview = _group_items_for_budget(
                reduce_items,
                max_chars=summary_policy["group_input_chars"],
                max_items=summary_policy["group_size"],
            )
            if len(grouped_preview) >= len(reduce_items) and len(reduce_items) > summary_policy["group_size"]:
                logger.info(
                    "document_analysis regrouping to guarantee progress items=%s groups=%s group_size=%s",
                    len(reduce_items),
                    len(grouped_preview),
                    summary_policy["group_size"],
                )
                grouped_preview = _force_progressive_grouping(
                    reduce_items,
                    max_items=summary_policy["group_size"],
                )
            group_prompt = _build_group_merge_prompt("\n\n---\n\n".join(grouped_preview[0]))
            admission_snapshot = _build_reduce_admission_snapshot(
                joined_payload=final_payload,
                final_prompt=final_prompt,
                group_prompt=group_prompt,
                budget_tokens=int(final_admission.get("budget_tokens") or summary_policy["final_max_tokens"]),
            )
            strategy_meta = _resolve_reduce_strategy_with_snapshot(
                reduce_items_count=len(reduce_items),
                low_vram=summary_policy["weak_pc_mode"],
                degraded_active=bool(summary_metadata.get("degraded")),
                fast_final_merge_enabled=bool(summary_policy.get("enable_fast_final_merge")),
                admission_snapshot=admission_snapshot,
            )
            current_admission_snapshot = dict(strategy_meta.get("admission_snapshot") or admission_snapshot)
            summary_metadata["reduce_decisions"] = copy.deepcopy(strategy_meta.get("reduce_decisions") or [])
            summary_metadata["early_exit_after_level"] = strategy_meta.get("early_exit_after_level")
            shadow_trace = None
            if shadow_mode_enabled:
                shadow_trace = _build_summary_shadow_trace(
                    reduce_items_count=len(reduce_items),
                    group_size=summary_policy["group_size"],
                    executed_strategy=str(strategy_meta["strategy"]),
                    recommended_strategy=str(strategy_meta["strategy"]),
                    reason=str(strategy_meta["reason"]),
                    admission_snapshot=current_admission_snapshot,
                )
                shadow_decisions.append(dict(shadow_trace))
                if shadow_trace["shadow_baseline_strategy"] != shadow_trace["executed_strategy"]:
                    inc_metric_counter(
                        "agent_nav_summary_strategy_shadow_diff_total",
                        labels={
                            "component": "document_analysis",
                            "executed_strategy": shadow_trace["executed_strategy"],
                            "shadow_baseline_strategy": shadow_trace["shadow_baseline_strategy"],
                        },
                    )
            if not summary_metadata.get("reduce_strategy"):
                summary_metadata["reduce_strategy"] = strategy_meta["strategy"]
                summary_metadata["reduce_reason"] = strategy_meta["reason"]
            elif (
                summary_metadata.get("reduce_reason") == "budget_overflow"
                and strategy_meta["reason"] == "degraded_mode"
            ):
                pass
            else:
                summary_metadata["reduce_strategy"] = strategy_meta["strategy"]
                summary_metadata["reduce_reason"] = strategy_meta["reason"]
            summary_metadata["fast_path_eligible"] = bool(strategy_meta.get("fast_path_eligible"))
            if shadow_trace is not None:
                summary_metadata["executed_strategy"] = shadow_trace["executed_strategy"]
                summary_metadata["recommended_strategy"] = shadow_trace["recommended_strategy"]
                summary_metadata["shadow_baseline_strategy"] = shadow_trace["shadow_baseline_strategy"]
                summary_metadata["would_skip_levels"] = int(shadow_trace["would_skip_levels"])
                summary_metadata["estimated_token_saving"] = int(shadow_trace["estimated_token_saving"])
            summary_metadata["final_admission_estimated_tokens"] = int(
                current_admission_snapshot.get("joined_payload_tokens_est", 0) or 0
            )
            summary_metadata["final_prompt_tokens_est"] = int(
                current_admission_snapshot.get("final_prompt_tokens_est", 0) or 0
            )
            summary_metadata["group_prompt_tokens_est"] = int(
                current_admission_snapshot.get("group_prompt_tokens_est", 0) or 0
            )
            summary_metadata["final_admission_budget_tokens"] = int(
                current_admission_snapshot.get("budget_tokens", 0) or 0
            )
            summary_metadata["final_admission_reserve_tokens"] = int(
                current_admission_snapshot.get("reserve_tokens", 0) or 0
            )
            summary_metadata["final_admission_margin_tokens"] = int(
                current_admission_snapshot.get("margin_tokens", 0) or 0
            )
            logger.info(
                "document_analysis reduce strategy=%s reason=%s items=%s level=%s weak_pc=%s degraded=%s",
                strategy_meta["strategy"],
                strategy_meta["reason"],
                len(reduce_items),
                merge_level,
                summary_policy["weak_pc_mode"],
                summary_metadata.get("degraded"),
            )
            if shadow_trace is not None:
                logger.info(
                    "document_analysis shadow trace executed=%s recommended=%s baseline=%s skip_levels=%s saving=%s items=%s",
                    shadow_trace["executed_strategy"],
                    shadow_trace["recommended_strategy"],
                    shadow_trace["shadow_baseline_strategy"],
                    shadow_trace["would_skip_levels"],
                    shadow_trace["estimated_token_saving"],
                    len(reduce_items),
                )
            inc_metric_counter(
                "agent_nav_summary_strategy_total",
                labels={
                    "component": "document_analysis",
                    "strategy": str(strategy_meta["strategy"]),
                    "reason": str(strategy_meta["reason"]),
                },
            )
            if strategy_meta["strategy"] == "fast_final_merge":
                _record_stage_admission(summary_metadata, stage="final_synthesis", admission=final_admission)
                break
            if len(reduce_items) <= summary_policy["group_size"]:
                _record_stage_admission(summary_metadata, stage="final_synthesis", admission=final_admission)
                break

            merge_level += 1
            grouped = grouped_preview
            summary_metadata["reduce_levels_used"] = merge_level
            summary_metadata["reduce_groups_total"] += len(grouped)
            next_reduce_items = []
            try:
                for group_idx, group in enumerate(grouped, start=1):
                    await _raise_if_cancelled(state)
                    await _update_summary_progress(
                        state,
                        title="Суммаризация документа",
                        content=f"Объединение фрагментов L{merge_level}: {group_idx}/{len(grouped)}",
                    )
                    combined = "\n\n---\n\n".join(group)
                    admission = _resolve_stage_admission(
                        stage="group_merge",
                        payload=combined,
                        input_char_cap=summary_policy["group_input_chars"],
                        output_token_cap=summary_policy["final_max_tokens"],
                    )
                    _record_stage_admission(
                        summary_metadata,
                        stage=f"group_merge_L{merge_level}_{group_idx}",
                        admission=admission,
                    )
                    if admission["admission"] != "ok":
                        _mark_degraded(summary_metadata, stage="group_merge", reason="token_budget")
                    stage_result = await _infer_stage_with_policy_retry(
                        stage="group_merge",
                        prompt_factory=_build_group_merge_prompt,
                        payload=combined,
                        input_char_cap=summary_policy["group_input_chars"],
                        output_token_cap=summary_policy["final_max_tokens"],
                    )
                    if stage_result.get("model_execution"):
                        model_execution_events.append(stage_result["model_execution"])
                    if stage_result["retry_used"]:
                        _mark_degraded(summary_metadata, stage="group_merge", reason="policy_retry")
                    merged_text = stage_result["text"] or truncate_text(combined, summary_policy["group_input_chars"])
                    next_reduce_items.append(merged_text.strip())
                    _mark_stage_completed(summary_metadata, "group_merge")
            except Exception as e:
                logger.warning("Document analysis reduce summarization fallback: %s", e, exc_info=True)
                inc_metric_counter(
                    "agent_nav_fallback_events_total",
                    labels={"component": "document_analysis", "fallback": "reduce_summarization_failed", "source": "workflow"},
                )
                errors.append(f"Reduce summarization failed: {e}")
                _mark_degraded(summary_metadata, stage="group_merge", reason="retry_exhausted")
                reduce_items = next_reduce_items or reduce_items
                break
            reduce_items = next_reduce_items

        final_payload = "\n\n---\n\n".join(reduce_items)
        final_admission = _resolve_stage_admission(
            stage="final_synthesis",
            payload=final_payload,
            input_char_cap=summary_policy["final_input_chars"],
            output_token_cap=summary_policy["final_max_tokens"],
        )
        _record_stage_admission(summary_metadata, stage="final_synthesis", admission=final_admission)
        if final_admission["admission"] != "ok":
            _mark_degraded(summary_metadata, stage="final_synthesis", reason="token_budget")
        if len(reduce_items) == 1 and final_admission["admission"] == "ok":
            summary = truncate_text(reduce_items[0], summary_policy["final_input_chars"]).strip()
            summary_metadata["final_synthesis_status"] = "completed"
            _mark_stage_completed(summary_metadata, "final_synthesis")
        elif final_admission["admission"] != "ok" and len(reduce_items) > summary_policy["group_size"]:
            summary = _build_partial_summary(reduce_items, max_chars=summary_policy["final_input_chars"])
            summary_metadata["final_synthesis_status"] = "skipped"
        else:
            try:
                await _raise_if_cancelled(state)
                await _update_summary_progress(
                    state,
                    title="Суммаризация документа",
                    content="Финальная сборка сводки",
                )
                stage_result = await _infer_stage_with_policy_retry(
                    stage="final_synthesis",
                    prompt_factory=lambda payload: _build_final_synthesis_prompt(type_prompt, payload),
                    payload=final_payload,
                    input_char_cap=summary_policy["final_input_chars"],
                    output_token_cap=summary_policy["final_max_tokens"],
                )
                if stage_result.get("model_execution"):
                    model_execution_events.append(stage_result["model_execution"])
                if stage_result["retry_used"]:
                    _mark_degraded(summary_metadata, stage="final_synthesis", reason="policy_retry")
                summary = stage_result["text"] or _build_partial_summary(
                    reduce_items,
                    max_chars=summary_policy["final_input_chars"],
                )
                summary_metadata["final_synthesis_status"] = "completed"
                _mark_stage_completed(summary_metadata, "final_synthesis")
            except Exception as e:
                logger.warning("Document analysis final synthesis degraded: %s", e, exc_info=True)
                inc_metric_counter(
                    "agent_nav_fallback_events_total",
                    labels={"component": "document_analysis", "fallback": "reduce_summarization_failed", "source": "workflow"},
                )
                errors.append(f"Reduce summarization failed: {e}")
                _mark_degraded(summary_metadata, stage="final_synthesis", reason="retry_exhausted")
                summary_metadata["final_synthesis_status"] = "failed"
                summary = _build_partial_summary(reduce_items, max_chars=summary_policy["final_input_chars"])
        if not summary_metadata.get("reduce_strategy"):
            summary_metadata["reduce_strategy"] = "hierarchical_merge"
            summary_metadata["reduce_reason"] = (
                "hardware_policy" if summary_policy["weak_pc_mode"] else "budget_overflow"
            )
        if shadow_mode_enabled:
            summary_metadata["reduce_decisions"] = shadow_decisions

    await _update_summary_progress(
        state,
        title="Суммаризация документа",
        content=f"Готово: обработано {len(chunk_summaries)}/{len(chunks)} фрагментов.",
    )

    result = {"summary": summary, "errors": errors, "summary_metadata": summary_metadata}
    if model_execution_events:
        result["model_execution"] = model_execution_events
    return result


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
    summary_metadata = state.get("summary_metadata") or {}
    errors = state.get("errors", [])
    runtime_context = _get_runtime_context(state)

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
    report += f"**Формат:** {format_info}"
    started_at_monotonic = runtime_context.get("started_at_monotonic")
    if started_at_monotonic is not None:
        try:
            elapsed_seconds = time.monotonic() - float(started_at_monotonic)
        except (TypeError, ValueError):
            elapsed_seconds = None
        if elapsed_seconds is not None:
            report += f"  **Время выполнения:** {_format_elapsed_seconds(elapsed_seconds)}"
    report += "\n\n"

    # Ошибки
    if errors:
        report += "## Предупреждения\n\n"
        for e in errors:
            report += f"- {e}\n"
        report += "\n"
    if summary_metadata.get("degraded"):
        report += "## Режим выполнения\n\n"
        report += "- Итог собран в bounded/degraded режиме из-за ограничений ресурсов.\n"
        report += f"- completed_stages: {', '.join(summary_metadata.get('completed_stages') or []) or 'нет'}\n"
        report += f"- final_synthesis_status: {summary_metadata.get('final_synthesis_status', 'unknown')}\n"
        report += "\n"

    # Извлечённые позиции
    should_render_items_section = bool(items) or doc_type in {"tz", "smeta", "kp"}
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
    elif should_render_items_section:
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
        return {"final_report": final_report_text, "summary_metadata": summary_metadata}
    except Exception as e:
        report += f"\n---\n**Ошибка сохранения отчета:** {e}"
        return {"final_report": report, "summary_metadata": summary_metadata}


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
