from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import os
import re
import time
import threading
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from orchestrator.knowledge_base_retrieval import retrieve_merged_chunks
from orchestrator.orchestration_runtime import decide_orchestration, is_social_query
from orchestrator.doc_question_heuristics import has_direct_grounded_evidence_v1
from orchestrator.rag.classifier import (
    EmbeddingIntentClassifier,
    LLMIntentClassifier,
    select_classifier_result,
)
from orchestrator.state_store import get_orchestration_state_store
from orchestrator.ui_control_plane import (
    get_prompt_profile_system_message,
    normalize_inference_device_mode,
    resolve_effective_settings,
)
from orchestrator.workflows.equipment import detect_equipment_mode
from services.observability import inc_metric_counter
from services.model_manager.model_selection import resolve_model_selection
from services.model_manager.ums_client import UMSBusyError, create_ums_embed_fn, ums_client


AsyncStrFn = Callable[..., Awaitable[str]]
AsyncAnyFn = Callable[..., Awaitable[Any]]
SyncAnyFn = Callable[..., Any]

logger = logging.getLogger("execution_runtime")
INTENT_CLASSIFIER_MODE = os.getenv("INTENT_CLASSIFIER_MODE", "embedder").strip().lower()
INTENT_CLASSIFIER_LLM_CONFIDENCE_THRESHOLD = float(
    os.getenv("INTENT_CLASSIFIER_LLM_CONFIDENCE_THRESHOLD", "0.75")
)
INTENT_CLASSIFIER_EMBEDDER_CONFIDENCE_THRESHOLD = float(
    os.getenv("INTENT_CLASSIFIER_EMBEDDER_CONFIDENCE_THRESHOLD", "0.60")
)
INTENT_CLASSIFIER_EMBEDDER_MARGIN_THRESHOLD = float(
    os.getenv("INTENT_CLASSIFIER_EMBEDDER_MARGIN_THRESHOLD", "0.10")
)
_INTENT_CLASSIFIER_CACHE: Dict[str, Any] = {}
_INTENT_CLASSIFIER_LLM_CACHE: Dict[str, LLMIntentClassifier] = {}
_DOCUMENTS_SUMMARY_CACHE: Dict[str, Dict[str, Any]] = {}
_INTENT_CLASSIFIER_LOCK = threading.Lock()
GENERAL_CHAT_LANGUAGE_GUARD_TEMPERATURE = float(
    os.getenv("GENERAL_CHAT_LANGUAGE_GUARD_TEMPERATURE", "0.2")
)
GENERAL_CHAT_LANGUAGE_GUARD_TOP_P = float(
    os.getenv("GENERAL_CHAT_LANGUAGE_GUARD_TOP_P", "0.6")
)
SUMMARY_REDUCE_GROUP_SIZE = 4
SUMMARY_DEGRADED_REDUCE_GROUP_SIZE = 2
SUMMARY_STAGE_MAX_INPUT_CHARS = {
    "chunk": 3200,
    "merge": 2600,
    "global": 3200,
}
SUMMARY_STAGE_DEGRADED_INPUT_CHARS = {
    "chunk": 2200,
    "merge": 1800,
    "global": 2200,
}
SUMMARY_STAGE_MAX_TOKENS = {
    "chunk": {"default": 384, "low_vram": 256},
    "merge": {"default": 448, "low_vram": 320},
    "global": {"default": 512, "low_vram": 384},
}
DOC_ANALYSIS_CHUNK_INPUT_CHARS = int(os.getenv("DOCUMENT_ANALYSIS_CHUNK_INPUT_CHARS", "3200"))
DOC_ANALYSIS_WEAK_PC_CHUNK_INPUT_CHARS = int(os.getenv("DOCUMENT_ANALYSIS_WEAK_PC_CHUNK_INPUT_CHARS", "2200"))
DOC_ANALYSIS_GROUP_INPUT_CHARS = int(os.getenv("DOCUMENT_ANALYSIS_GROUP_INPUT_CHARS", "2600"))
DOC_ANALYSIS_WEAK_PC_GROUP_INPUT_CHARS = int(os.getenv("DOCUMENT_ANALYSIS_WEAK_PC_GROUP_INPUT_CHARS", "1800"))
DOC_ANALYSIS_FINAL_INPUT_CHARS = int(os.getenv("DOCUMENT_ANALYSIS_FINAL_INPUT_CHARS", "3200"))
DOC_ANALYSIS_WEAK_PC_FINAL_INPUT_CHARS = int(os.getenv("DOCUMENT_ANALYSIS_WEAK_PC_FINAL_INPUT_CHARS", "2200"))
DOC_ANALYSIS_GROUP_SIZE = int(os.getenv("DOCUMENT_ANALYSIS_GROUP_SIZE", "4"))
DOC_ANALYSIS_WEAK_PC_GROUP_SIZE = int(os.getenv("DOCUMENT_ANALYSIS_WEAK_PC_GROUP_SIZE", "2"))
DOC_ANALYSIS_FINAL_MAX_TOKENS = int(os.getenv("DOCUMENT_ANALYSIS_FINAL_MAX_TOKENS", "1600"))
DOC_ANALYSIS_WEAK_PC_FINAL_MAX_TOKENS = int(os.getenv("DOCUMENT_ANALYSIS_WEAK_PC_FINAL_MAX_TOKENS", "1024"))
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")
_TRANSLATION_MARKERS = (
    "переведи",
    "translate",
    "translation",
    "на англий",
    "на русском",
    "на китай",
    "по-английски",
    "по-русски",
    "翻译",
)
_CODE_HEAVY_MARKERS = ("```", "def ", "class ", "function ", "=>", "{", "}", "</", "/>")
_RUSSIAN_NUMBER_WORDS = {
    "перв": "1",
    "втор": "2",
    "трет": "3",
    "четвер": "4",
    "пят": "5",
    "шест": "6",
    "седь": "7",
    "вось": "8",
    "девят": "9",
    "десят": "10",
}


def _is_low_vram_device_mode(effective_settings: Optional[Dict[str, Any]]) -> bool:
    return str((effective_settings or {}).get("device_mode") or "").strip().lower() == "low-vram"


def _resolve_summary_device_mode(effective_settings: Optional[Dict[str, Any]]) -> str:
    return normalize_inference_device_mode((effective_settings or {}).get("device_mode"))


def _resolve_summary_stage_overrides(
    stage: str,
    effective_settings: Optional[Dict[str, Any]],
    *,
    degraded: bool = False,
) -> Dict[str, Any]:
    low_vram = _is_low_vram_device_mode(effective_settings)
    max_tokens = SUMMARY_STAGE_MAX_TOKENS.get(stage, SUMMARY_STAGE_MAX_TOKENS["global"])
    selected_max_tokens = max_tokens["low_vram"] if low_vram else max_tokens["default"]
    if degraded:
        selected_max_tokens = max(128, selected_max_tokens // 2)
    return {
        "temperature": 0.2,
        "max_tokens": selected_max_tokens,
    }


def _estimate_prompt_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // 4)


def _resolve_summary_stage_plan(
    *,
    stage: str,
    prompt: str,
    effective_settings: Optional[Dict[str, Any]],
    degraded: bool = False,
) -> Dict[str, Any]:
    prompt_chars = len(str(prompt or ""))
    prompt_tokens = _estimate_prompt_tokens(prompt)
    default_cap = SUMMARY_STAGE_MAX_INPUT_CHARS.get(stage, SUMMARY_STAGE_MAX_INPUT_CHARS["global"])
    degraded_cap = SUMMARY_STAGE_DEGRADED_INPUT_CHARS.get(stage, SUMMARY_STAGE_DEGRADED_INPUT_CHARS["global"])
    low_vram = _is_low_vram_device_mode(effective_settings)
    runtime_admission = (((effective_settings or {}).get("runtime_admission") or {}).get("documents_summary") or {})
    explicit_stage_admission = str(runtime_admission.get(stage) or "").strip().lower()
    requires_degraded = (
        explicit_stage_admission == "requires_degraded"
        or low_vram
        or prompt_chars > int(default_cap * 0.85)
        or prompt_tokens > max(128, default_cap // 4)
    )
    return {
        "stage": stage,
        "policy": "reduced_context" if degraded or requires_degraded else "normal",
        "degraded": bool(degraded or requires_degraded),
        "admission": "requires_degraded" if requires_degraded else "ok",
        "reason": "low_vram" if low_vram else "runtime_admission" if explicit_stage_admission == "requires_degraded" else "token_budget",
        "input_char_cap": degraded_cap if (degraded or requires_degraded) else default_cap,
        "prompt_chars": prompt_chars,
        "prompt_tokens": prompt_tokens,
        "overrides": _resolve_summary_stage_overrides(stage, effective_settings, degraded=(degraded or requires_degraded)),
    }


def _join_summary_items(items: List[str], *, max_chars: int) -> str:
    joined: List[str] = []
    current_len = 0
    for idx, item in enumerate(items, start=1):
        normalized = str(item or "").strip()
        if not normalized:
            continue
        candidate = f"[{idx}] {normalized}"
        extra = len(candidate) + (2 if joined else 0)
        if joined and current_len + extra > max_chars:
            break
        joined.append(candidate)
        current_len += extra
    return "\n\n".join(joined)


def _group_summary_items_for_budget(items: List[str], *, max_chars: int, max_items: int) -> List[List[str]]:
    groups: List[List[str]] = []
    current: List[str] = []
    current_len = 0
    for item in items:
        normalized = str(item or "").strip()
        if not normalized:
            continue
        candidate_len = len(normalized) + (2 if current else 0)
        if current and (current_len + candidate_len > max_chars or len(current) >= max_items):
            groups.append(current)
            current = []
            current_len = 0
        current.append(normalized[:max_chars])
        current_len += len(current[-1]) + (2 if len(current) > 1 else 0)
    if current:
        groups.append(current)
    return groups or [items[:max_items]]


def _build_documents_summary_chunk_cache_key(
    *,
    document_id: str,
    document_name: str,
    chunk: str,
    effective_settings: Optional[Dict[str, Any]],
) -> str:
    model_id = str((effective_settings or {}).get("resolved_model_id") or "")
    profile = str((effective_settings or {}).get("model_profile") or "")
    payload = "\n".join(
        [
            "documents_summary_chunk_v1",
            document_id.strip(),
            document_name.strip(),
            model_id,
            profile,
            chunk.strip(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_documents_summary_cache_ttl_s() -> float:
    raw = os.getenv("DOCUMENTS_SUMMARY_CACHE_TTL_S", "1800")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1800.0


def _get_cached_documents_summary_chunk(cache_key: str) -> Optional[str]:
    entry = _DOCUMENTS_SUMMARY_CACHE.get(cache_key)
    if not isinstance(entry, dict):
        return None
    cached_at = float(entry.get("cached_at") or 0.0)
    if (time.time() - cached_at) > _get_documents_summary_cache_ttl_s():
        _DOCUMENTS_SUMMARY_CACHE.pop(cache_key, None)
        return None
    summary = str(entry.get("summary") or "").strip()
    return summary or None


def _store_cached_documents_summary_chunk(cache_key: str, summary: str) -> None:
    _DOCUMENTS_SUMMARY_CACHE[cache_key] = {
        "summary": str(summary or "").strip(),
        "cached_at": time.time(),
    }


async def _infer_documents_summary_stage(
    *,
    deps: ExecutionDependencies,
    prompt: str,
    stage: str,
    effective_settings: Optional[Dict[str, Any]],
) -> tuple[str, Dict[str, Any]]:
    inc_metric_counter(
        "agent_nav_fallback_events_total",
        labels={
            "component": "documents_summary",
            "fallback": f"documents_summary_{stage}_attempt",
            "source": "workflow",
        },
    )
    stage_plan = _resolve_summary_stage_plan(
        stage=stage,
        prompt=prompt,
        effective_settings=effective_settings,
        degraded=False,
    )
    resolved_device_mode = _resolve_summary_device_mode(effective_settings)
    if stage_plan["admission"] == "requires_degraded":
        text = await deps.infer_assistant_text(
            prompt,
            enforced_overrides=stage_plan["overrides"],
            device_mode=resolved_device_mode,
            allow_sync_retry=False,
            raise_on_error=True,
            summary_stage=stage,
        )
        return text, stage_plan
    try:
        text = await deps.infer_assistant_text(
            prompt,
            enforced_overrides=stage_plan["overrides"],
            device_mode=resolved_device_mode,
            allow_sync_retry=False,
            raise_on_error=True,
            summary_stage=stage,
        )
        return text, stage_plan
    except Exception as exc:
        if isinstance(exc, (StopAsyncIteration, StopIteration)):
            raise
        degraded_plan = _resolve_summary_stage_plan(
            stage=stage,
            prompt=prompt,
            effective_settings=effective_settings,
            degraded=True,
        )
        text = await deps.infer_assistant_text(
            prompt,
            enforced_overrides=degraded_plan["overrides"],
            device_mode=resolved_device_mode,
            allow_sync_retry=False,
            raise_on_error=True,
            summary_stage=stage,
        )
        return text, degraded_plan


@dataclass
class ExecutionDependencies:
    infer_assistant_text: AsyncStrFn
    build_prompt: SyncAnyFn
    get_profile_system_prompt: SyncAnyFn
    has_retrieval_adapter: SyncAnyFn
    get_retrieval_embed_fn: SyncAnyFn
    get_knowledge_base_store: SyncAnyFn
    get_active_doc_ids: SyncAnyFn
    get_all_docs: SyncAnyFn
    get_active_docs: SyncAnyFn
    get_report_docs: SyncAnyFn
    resolve_target_doc_name: SyncAnyFn
    is_report_query: SyncAnyFn
    ensure_rag_index_for_doc_ids: AsyncAnyFn
    get_rag_pipeline: SyncAnyFn
    build_sources_from_rag_result: SyncAnyFn
    reindex_sources: SyncAnyFn
    build_doc_question_deterministic_fallback: SyncAnyFn
    render_doc_question_markdown: SyncAnyFn
    build_doc_question_prompt_with_sources: SyncAnyFn
    citations_are_valid: SyncAnyFn
    needs_doc_question_regen: SyncAnyFn
    extract_citation_ids: SyncAnyFn
    has_sufficient_evidence: SyncAnyFn
    compute_confidence_v1: SyncAnyFn
    strip_model_source_sections: SyncAnyFn
    to_host_path: SyncAnyFn
    active_set_status_line: SyncAnyFn
    attach_and_register_report: AsyncAnyFn
    update_progress_box: AsyncAnyFn
    clear_progress_box: AsyncAnyFn
    is_cancelled: SyncAnyFn
    record_model_execution: Optional[SyncAnyFn] = None
    get_model_execution_events: Optional[SyncAnyFn] = None


def _is_execution_cancelled(deps: ExecutionDependencies) -> bool:
    try:
        return bool(deps.is_cancelled())
    except Exception:
        return False


def _raise_if_execution_cancelled(deps: ExecutionDependencies) -> None:
    if _is_execution_cancelled(deps):
        raise asyncio.CancelledError


def _is_model_failover_blocked(exc: Exception) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return True
    if isinstance(exc, UMSBusyError):
        return True
    message = str(exc).lower()
    return "429" in message or "cancel" in message or "busy" in message


def _build_model_execution_event(
    *,
    role_key: str,
    selection: Any,
    used_model_id: str,
    fallback_used: bool,
    fallback_stage: Optional[str] = None,
    fallback_reason: Optional[str] = None,
    attempt_count: int = 1,
) -> Dict[str, Any]:
    payload = dict(selection.to_dict() if hasattr(selection, "to_dict") else selection or {})
    payload.update(
        {
            "role_key": role_key,
            "primary_model_id": payload.get("resolved_model_id") or payload.get("primary_model_id"),
            "fallback_model_id": payload.get("fallback_model_id") if payload.get("fallback_available") else None,
            "used_model_id": used_model_id,
            "fallback_used": bool(fallback_used),
            "fallback_stage": fallback_stage,
            "fallback_reason": fallback_reason,
            "attempt_count": max(1, int(attempt_count)),
            "status": "fallback_completed" if fallback_used else "completed",
        }
    )
    return payload


def _record_model_execution_event(
    deps: ExecutionDependencies,
    event: Optional[Dict[str, Any]],
) -> None:
    if not event:
        return
    recorder = getattr(deps, "record_model_execution", None)
    if callable(recorder):
        try:
            recorder(copy.deepcopy(event))
        except Exception:
            logger.debug("Model execution recorder failed", exc_info=True)


def _collect_model_execution_events(deps: ExecutionDependencies) -> List[Dict[str, Any]]:
    getter = getattr(deps, "get_model_execution_events", None)
    if callable(getter):
        try:
            events = getter()
            if isinstance(events, list):
                return [copy.deepcopy(event) for event in events if isinstance(event, dict)]
        except Exception:
            logger.debug("Model execution getter failed", exc_info=True)
    return []


def _summarize_model_execution_events(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    clean_events = [copy.deepcopy(event) for event in events if isinstance(event, dict)]
    if not clean_events:
        return None
    fallback_events = [event for event in clean_events if bool(event.get("fallback_used"))]
    return {
        "status": "fallback_completed" if fallback_events else "completed",
        "fallback_used": bool(fallback_events),
        "attempt_count": sum(int(event.get("attempt_count") or 1) for event in clean_events),
        "primary_model_ids": [str(event.get("primary_model_id") or "") for event in clean_events if event.get("primary_model_id")],
        "fallback_model_ids": [str(event.get("fallback_model_id") or "") for event in clean_events if event.get("fallback_model_id")],
        "used_model_ids": [str(event.get("used_model_id") or "") for event in clean_events if event.get("used_model_id")],
        "events": clean_events,
    }


def _extract_model_text(response: Any) -> str:
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


async def _infer_with_model_failover(
    *,
    deps: ExecutionDependencies,
    role_key: str,
    prompt: str,
    payload: Dict[str, Any],
    device_mode: str,
    fallback_stage: Optional[str] = None,
) -> tuple[str, Dict[str, Any]]:
    selection = resolve_model_selection(role_key)
    primary_model_id = str(selection.resolved_model_id)
    fallback_model_id = str(selection.fallback_model_id or "").strip() or None
    if fallback_model_id == primary_model_id:
        fallback_model_id = None

    attempts = [primary_model_id]
    if fallback_model_id:
        attempts.append(fallback_model_id)

    last_error: Optional[Exception] = None
    for attempt_idx, model_id in enumerate(attempts, start=1):
        try:
            response = await ums_client.async_infer(
                model_id,
                {
                    **payload,
                    "prompt": prompt,
                },
                device_mode=device_mode,
            )
            event = _build_model_execution_event(
                role_key=role_key,
                selection=selection,
                used_model_id=model_id,
                fallback_used=attempt_idx > 1,
                fallback_stage=fallback_stage,
                fallback_reason=str(last_error) if attempt_idx > 1 and last_error is not None else None,
                attempt_count=attempt_idx,
            )
            _record_model_execution_event(deps, event)
            return _extract_model_text(response), event
        except Exception as exc:
            if _is_model_failover_blocked(exc):
                raise
            last_error = exc
            if attempt_idx < len(attempts):
                logger.warning(
                    "Model failover retry role=%s stage=%s primary=%s fallback=%s error=%s",
                    role_key,
                    fallback_stage or "default",
                    primary_model_id,
                    fallback_model_id,
                    exc,
                    exc_info=True,
                )
                inc_metric_counter(
                    "agent_nav_fallback_events_total",
                    labels={
                        "component": "model_execution",
                        "fallback": "model_failover_retry",
                        "source": "execution_runtime",
                        "stage": fallback_stage or "default",
                    },
                )
                continue
            raise RuntimeError(
                f"Model execution failed for role {role_key} after fallback: {exc}"
            ) from exc


def _resolve_runtime_budget_metadata(
    effective_settings: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    raw = (effective_settings or {}).get("runtime_budget_metadata") or {}
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


def _is_weak_pc_runtime(effective_settings: Optional[Dict[str, Any]]) -> bool:
    metadata = _resolve_runtime_budget_metadata(effective_settings)
    hardware = metadata.get("hardware") or {}
    gpu_count = int(hardware.get("gpu_count") or 0)
    total_vram_gb = float(hardware.get("total_vram_gb") or 0.0)
    tier = metadata.get("tier")
    if _is_low_vram_device_mode(effective_settings):
        return True
    if gpu_count <= 1 and total_vram_gb and total_vram_gb <= 8.5:
        return True
    return tier in {0, 1, 2}


def _build_document_analysis_summary_policy(
    effective_settings: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    weak_pc = _is_weak_pc_runtime(effective_settings)
    return {
        "weak_pc_mode": weak_pc,
        "chunk_input_chars": DOC_ANALYSIS_WEAK_PC_CHUNK_INPUT_CHARS if weak_pc else DOC_ANALYSIS_CHUNK_INPUT_CHARS,
        "group_input_chars": DOC_ANALYSIS_WEAK_PC_GROUP_INPUT_CHARS if weak_pc else DOC_ANALYSIS_GROUP_INPUT_CHARS,
        "final_input_chars": DOC_ANALYSIS_WEAK_PC_FINAL_INPUT_CHARS if weak_pc else DOC_ANALYSIS_FINAL_INPUT_CHARS,
        "group_size": DOC_ANALYSIS_WEAK_PC_GROUP_SIZE if weak_pc else DOC_ANALYSIS_GROUP_SIZE,
        "final_max_tokens": DOC_ANALYSIS_WEAK_PC_FINAL_MAX_TOKENS if weak_pc else DOC_ANALYSIS_FINAL_MAX_TOKENS,
    }


def _infer_intent_via_llm(prompt: str) -> str:
    payload = {
        "prompt": prompt,
        "temperature": 0.0,
        "top_p": 0.1,
        "max_tokens": 96,
    }
    response = ums_client.infer(_resolve_intent_classifier_llm_model_id(), payload)
    return response.get("choices", [{}])[0].get("text", str(response))


def _get_cached_embedder_classifier(model_id: str) -> Optional[EmbeddingIntentClassifier]:
    classifier = _INTENT_CLASSIFIER_CACHE.get(model_id)
    if classifier is not None:
        return classifier
    with _INTENT_CLASSIFIER_LOCK:
        classifier = _INTENT_CLASSIFIER_CACHE.get(model_id)
        if classifier is not None:
            return classifier
        embed_fn = create_ums_embed_fn(model_id=model_id)
        if not embed_fn:
            return None
        classifier = EmbeddingIntentClassifier(embed_fn=embed_fn)
        classifier.initialize()
        _INTENT_CLASSIFIER_CACHE[model_id] = classifier
        return classifier


def _get_cached_llm_classifier(model_id: str) -> LLMIntentClassifier:
    classifier = _INTENT_CLASSIFIER_LLM_CACHE.get(model_id)
    if classifier is not None:
        return classifier


def _resolve_intent_classifier_embedder_model_id() -> str:
    return resolve_model_selection("classifier.intent.embedder").resolved_model_id


def _resolve_intent_classifier_llm_model_id() -> str:
    return resolve_model_selection("classifier.intent.llm").resolved_model_id
    with _INTENT_CLASSIFIER_LOCK:
        classifier = _INTENT_CLASSIFIER_LLM_CACHE.get(model_id)
        if classifier is not None:
            return classifier
        classifier = LLMIntentClassifier(infer_text_fn=_infer_intent_via_llm)
        _INTENT_CLASSIFIER_LLM_CACHE[model_id] = classifier
        return classifier


async def _resolve_classifier_result_for_request(
    *,
    query: str,
    effective_settings: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not query.strip():
        return None

    embedder_result = None
    if INTENT_CLASSIFIER_MODE != "llm":
        intent_embedder_model_id = str(
            effective_settings.get("resolved_intent_embedder_model_id") or _resolve_intent_classifier_embedder_model_id()
        )
        try:
            classifier = await asyncio.to_thread(_get_cached_embedder_classifier, intent_embedder_model_id)
            if classifier is not None:
                embedder_result = await asyncio.to_thread(classifier.classify, query)
        except Exception as exc:
            logger.warning("Backend embedder classifier failed: %s", exc)

    llm_result = None
    if INTENT_CLASSIFIER_MODE in {"llm", "hybrid"}:
        try:
            llm_classifier = _get_cached_llm_classifier(_resolve_intent_classifier_llm_model_id())
            llm_result = await asyncio.to_thread(llm_classifier.classify, query)
        except Exception as exc:
            logger.warning("Backend LLM classifier failed: %s", exc)

    return select_classifier_result(
        INTENT_CLASSIFIER_MODE,
        embedder_result=embedder_result,
        llm_result=llm_result,
        llm_confidence_threshold=INTENT_CLASSIFIER_LLM_CONFIDENCE_THRESHOLD,
        embedder_confidence_threshold=INTENT_CLASSIFIER_EMBEDDER_CONFIDENCE_THRESHOLD,
        embedder_margin_threshold=INTENT_CLASSIFIER_EMBEDDER_MARGIN_THRESHOLD,
    )


def _collect_raw_control_plane(request: Dict[str, Any]) -> Dict[str, Any]:
    raw: Dict[str, Any] = {}
    for field in (
        "assistant_mode",
        "runtime_mode",
        "rag_scope",
        "knowledge_collection_id",
        "model_profile",
        "prompt_profile",
        "custom_system_prompt",
        "tool_scope",
    ):
        if field in request:
            raw[field] = request.get(field)

    generation_overrides = request.get("generation_overrides")
    if generation_overrides:
        raw["generation_overrides"] = dict(generation_overrides)
    return raw


def resolve_request_runtime_mode(
    request: Dict[str, Any],
    effective_settings: Optional[Dict[str, Any]] = None,
) -> str:
    settings = effective_settings or resolve_effective_settings(_collect_raw_control_plane(request))
    runtime_mode = settings.get("runtime_mode")
    if isinstance(runtime_mode, str) and runtime_mode:
        return runtime_mode
    return str(request.get("runtime_mode") or "auto")


def build_control_plane_metadata(
    request: Dict[str, Any],
    effective_settings: Dict[str, Any],
) -> Dict[str, Any]:
    rag_scope = str(effective_settings.get("rag_scope") or "off")
    knowledge_collection_id = effective_settings.get("knowledge_collection_id")
    session_docs = request.get("session_docs") or {}
    active_doc_ids = request.get("active_doc_ids") or []
    attachments_meta = request.get("attachments_meta") or []
    has_overlay_inputs = bool(session_docs or active_doc_ids or attachments_meta)

    if rag_scope == "knowledge_base_rag":
        source_scope_summary = "knowledge_base+session_overlay" if has_overlay_inputs else "knowledge_base"
    elif rag_scope == "session_rag":
        source_scope_summary = "session"
    else:
        source_scope_summary = "off"

    return {
        "rag_scope": rag_scope,
        "knowledge_collection_id": knowledge_collection_id,
        "source_scope_summary": source_scope_summary,
        "model_profile": str(effective_settings.get("model_profile") or "default-chat"),
    }


def _build_state_ref(
    *,
    request_state_ref: Optional[str],
    thread_id: Optional[str],
    session_id: Optional[str],
    trace_id: str,
) -> str:
    if request_state_ref:
        return str(request_state_ref)
    if thread_id:
        return f"thread:{thread_id}"
    if session_id:
        return f"session:{session_id}"
    return f"trace:{trace_id}"


def _extract_pending_action_id(action_required: Optional[Dict[str, Any]], trace_id: str) -> Optional[str]:
    if not action_required:
        return None
    return (
        action_required.get("pending_action_id")
        or action_required.get("route_choice_id")
        or f"{trace_id}:{action_required.get('type', 'action_required')}"
    )


def _build_ui_effects(
    response: Dict[str, Any],
    *,
    pending_action_id: Optional[str],
) -> Dict[str, Any]:
    patch = copy.deepcopy(response.get("session_state_patch") or {})
    action_required = copy.deepcopy(response.get("action_required"))
    ui_hints = copy.deepcopy(response.get("ui_hints") or {})
    effects: Dict[str, Any] = {
        "clear_pending_action": action_required is None,
        "preserve_active_docs": bool(ui_hints.get("preserve_active_docs")),
        "preserve_active_mode": bool(ui_hints.get("preserve_active_mode") or patch.get("preserve_active_mode")),
    }
    if "active_mode" in patch:
        effects["set_active_mode"] = patch.get("active_mode")
    if action_required is not None:
        effects["set_pending_action"] = action_required
        effects["pending_action_id"] = pending_action_id
    return effects


def _count_script(regex: re.Pattern[str], text: str) -> int:
    return len(regex.findall(text or ""))


def _detect_dominant_script(text: str) -> Optional[str]:
    cyrillic = _count_script(_CYRILLIC_RE, text)
    latin = _count_script(_LATIN_RE, text)
    if cyrillic <= 0 and latin <= 0:
        return None
    return "cyrillic" if cyrillic >= latin else "latin"


def _is_translation_request(query: str) -> bool:
    query_lower = (query or "").lower()
    return any(marker in query_lower for marker in _TRANSLATION_MARKERS)


def _is_code_heavy_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _CODE_HEAVY_MARKERS)


def _should_regenerate_for_language_consistency(query: str, answer: str) -> bool:
    if not query.strip() or not answer.strip():
        return False
    if _is_translation_request(query) or _is_code_heavy_text(query) or _is_code_heavy_text(answer):
        return False
    if _count_script(_CJK_RE, query) > 0:
        return False

    dominant_script = _detect_dominant_script(query)
    if dominant_script == "cyrillic":
        return _count_script(_CJK_RE, answer) >= 2 or (
            _count_script(_LATIN_RE, answer) >= 3 and _count_script(_CYRILLIC_RE, answer) == 0
        )
    if dominant_script == "latin":
        return _count_script(_CJK_RE, answer) >= 2 or (
            _count_script(_CYRILLIC_RE, answer) >= 2 and _count_script(_CYRILLIC_RE, query) == 0
        )
    return False


def _doc_answer_needs_direct_evidence_regen(answer_text: str) -> bool:
    lowered = (answer_text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "недостаточно",
            "не хватает",
            "не могу",
            "нет данных",
            "недостаточно данных",
        )
    )


def _build_direct_evidence_concise_prompt(
    deps: ExecutionDependencies,
    *,
    query: str,
    history: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
) -> str:
    return (
        deps.build_doc_question_prompt_with_sources(query, history, sources)
        + "\n\nЕсли ответ прямо подтверждается одним источником, дай короткий фактический ответ "
        + "в одной строке и используй только нужную ссылку [n]. Не добавляй вводных фраз "
        + "про достаточность данных."
    )


def _extract_clean_sentence_for_script(answer: str, dominant_script: Optional[str]) -> str:
    if dominant_script != "cyrillic":
        return answer
    chunks = re.split(r"(?<=[.!?])\s+", answer.strip())
    for chunk in chunks:
        if _count_script(_CYRILLIC_RE, chunk) > 0 and _count_script(_CJK_RE, chunk) == 0:
            return chunk.strip()
    return answer


def _extract_number_from_quote(quote: str, *, query: str = "") -> Optional[str]:
    quote_lower = quote.lower()
    query_lower = (query or "").lower()

    floor_hint = any(
        marker in query_lower
        for marker in (
            "этаж",
            "этаже",
            "этажа",
            "этажу",
            "floor",
        )
    )
    if floor_hint:
        floor_match = re.search(r"\b(\d+)(?:-й|-йй|-й|)\s+этаж", quote_lower)
        if floor_match:
            return floor_match.group(1)
        for stem, value in _RUSSIAN_NUMBER_WORDS.items():
            if f"{stem}ом этаже" in quote_lower or f"{stem}ый этаж" in quote_lower or f"{stem} этаж" in quote_lower:
                return value

    digit_match = re.search(r"\b(\d+)\b", quote_lower)
    if digit_match:
        return digit_match.group(1)
    for stem, value in _RUSSIAN_NUMBER_WORDS.items():
        if stem in quote_lower:
            return value
    return None


def _build_direct_grounded_answer_from_source(query: str, source: Dict[str, Any]) -> Optional[str]:
    quote = str(source.get("quote") or "").strip()
    if not quote:
        return None
    citation_id = int(source.get("source_id", 1))
    query_lower = (query or "").lower()
    if "только числом" in query_lower:
        extracted_number = _extract_number_from_quote(quote, query=query)
        if extracted_number is not None:
            return f"{extracted_number} [{citation_id}]"
    return f"{quote.rstrip('.')} [{citation_id}]"


def _with_execution_metadata(
    response: Dict[str, Any],
    *,
    request: Dict[str, Any],
    effective_settings: Dict[str, Any],
) -> Dict[str, Any]:
    enriched = copy.deepcopy(response)
    trace_id = str(enriched.get("trace_id") or request.get("trace_id") or "")
    pending_action_id = _extract_pending_action_id(enriched.get("action_required"), trace_id)
    enriched["effective_settings"] = copy.deepcopy(effective_settings)
    enriched.update(build_control_plane_metadata(request, effective_settings))
    enriched["state_ref"] = _build_state_ref(
        request_state_ref=request.get("state_ref"),
        thread_id=request.get("thread_id"),
        session_id=request.get("session_id"),
        trace_id=trace_id,
    )
    if request.get("run_id"):
        enriched["run_id"] = request.get("run_id")
    if request.get("state_version") is not None:
        enriched["state_version"] = request.get("state_version")
    enriched["pending_action_id"] = pending_action_id
    enriched["ui_effects"] = _build_ui_effects(enriched, pending_action_id=pending_action_id)
    return enriched


def _resolve_workflow_type(request: Dict[str, Any]) -> str:
    workflow_type = request.get("workflow_type")
    if workflow_type:
        return str(workflow_type)
    assistant_mode = str(request.get("assistant_mode") or "")
    if assistant_mode:
        return assistant_mode
    if request.get("thread_id") or request.get("ui_state") is not None:
        return "chainlit"
    return "api"


def _merge_resume_state_blob(request: Dict[str, Any], response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = request.get("ui_state")
    snapshot: Dict[str, Any] = copy.deepcopy(base) if isinstance(base, dict) else {}
    snapshot.pop("documents_by_id", None)
    snapshot.pop("documents_by_name", None)
    snapshot.pop("session_docs", None)
    patch = response.get("session_state_patch") or {}

    if "pending_action" in patch:
        snapshot["pending_action"] = copy.deepcopy(patch.get("pending_action"))
    elif "action_required" in response:
        snapshot["pending_action"] = copy.deepcopy(response.get("action_required"))

    if patch.get("preserve_active_mode"):
        pass
    elif "active_mode" in patch:
        snapshot["active_mode"] = patch.get("active_mode")

    for key in ("last_route", "last_executor", "last_trace_id", "runtime_mode"):
        if key in patch:
            snapshot[key] = copy.deepcopy(patch.get(key))

    if response.get("route") is not None:
        snapshot["last_route"] = response.get("route")
    if response.get("executor") is not None:
        snapshot["last_executor"] = response.get("executor")
    if request.get("trace_id") is not None:
        snapshot["last_trace_id"] = request.get("trace_id")

    if request.get("active_doc_ids") is not None and "active_doc_ids" not in snapshot:
        snapshot["active_doc_ids"] = list(request.get("active_doc_ids") or [])
    document_refs = _build_document_refs(
        ui_state=request.get("ui_state"),
        session_docs=request.get("session_docs") or {},
        active_doc_ids=snapshot.get("active_doc_ids") or request.get("active_doc_ids") or [],
    )
    if document_refs:
        snapshot["document_refs"] = document_refs
    if request.get("control_plane_state") is not None and "control_plane_state" not in snapshot:
        snapshot["control_plane_state"] = copy.deepcopy(request.get("control_plane_state"))
    if response.get("effective_settings") is not None:
        snapshot["effective_settings"] = copy.deepcopy(response.get("effective_settings"))

    return snapshot or None


def _build_document_refs(
    *,
    ui_state: Any,
    session_docs: Dict[str, Any],
    active_doc_ids: List[str],
) -> List[Dict[str, Any]]:
    if isinstance(ui_state, dict):
        existing = ui_state.get("document_refs")
        if isinstance(existing, list) and existing:
            return copy.deepcopy(existing)

    refs: List[Dict[str, Any]] = []
    seen = set()
    docs_by_id = {}
    if isinstance(ui_state, dict):
        docs_by_id = ui_state.get("documents_by_id") or {}
    if isinstance(docs_by_id, dict):
        ordered = [docs_by_id[doc_id] for doc_id in active_doc_ids if doc_id in docs_by_id]
        ordered.extend(doc for doc_id, doc in docs_by_id.items() if doc_id not in set(active_doc_ids))
        for doc in ordered:
            document_id = str(doc.get("document_id") or "").strip()
            display_name = str(doc.get("display_name") or "").strip()
            if not document_id or not display_name or document_id in seen:
                continue
            seen.add(document_id)
            refs.append(
                {
                    "document_id": document_id,
                    "display_name": display_name,
                    "version": int(doc.get("version", 1)),
                    "path": doc.get("path"),
                    "uploaded_at": doc.get("uploaded_at"),
                    "source_message_id": doc.get("source_message_id"),
                    "source_origin": doc.get("source_origin"),
                    "collection_id": doc.get("collection_id"),
                }
            )
    for key, doc in (session_docs or {}).items():
        document_id = str(doc.get("document_id") or key or "").strip()
        display_name = str(doc.get("display_name") or key or "").strip()
        if not document_id or not display_name or document_id in seen:
            continue
        seen.add(document_id)
        refs.append(
            {
                "document_id": document_id,
                "display_name": display_name,
                "version": int(doc.get("version", 1)),
                "path": doc.get("path"),
                "source_origin": doc.get("source_origin"),
                "collection_id": doc.get("collection_id"),
            }
        )
    return refs


def _build_checkpoint_blob(request: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "route": response.get("route"),
        "executor": response.get("executor"),
        "trace_id": request.get("trace_id"),
        "runtime_mode": response.get("mode"),
        "assistant_mode": response.get("assistant_mode"),
        "rag_scope": response.get("rag_scope"),
        "knowledge_collection_id": response.get("knowledge_collection_id"),
        "pending_action_id": response.get("pending_action_id"),
        "action_required": copy.deepcopy(response.get("action_required")),
    }


def _derive_run_status(response: Dict[str, Any]) -> str:
    execution_status = str(((response.get("execution_metadata") or {}).get("status")) or "").strip().lower()
    if execution_status in {"cancelled", "busy", "degraded"}:
        return execution_status
    assistant_message = str(response.get("assistant_message") or "")
    if response.get("action_required"):
        return "waiting_action"
    if assistant_message.lower().startswith("ошибка выполнения сценария:"):
        return "failed"
    if assistant_message:
        return "completed"
    return "decision_ready"


async def _run_graph(workflow: Any, initial_state: Dict[str, Any]) -> Dict[str, Any]:
    final_state: Dict[str, Any] = {}
    async for event in workflow.astream(initial_state):
        for _, output in event.items():
            if "errors" in output and "errors" in final_state:
                existing = final_state["errors"]
                new = output["errors"]
                if isinstance(existing, list) and isinstance(new, list):
                    output = dict(output)
                    output["errors"] = existing + new
            final_state.update(output)
    return final_state


def _build_session_doc_list(session_docs: Dict[str, Any]) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for index, (name, info) in enumerate((session_docs or {}).items(), start=1):
        docs.append(
            {
                "document_id": str(info.get("document_id") or name),
                "display_name": name,
                "path": info.get("path"),
                "text": info.get("text", ""),
                "report_generated": bool(info.get("report_generated")),
                "order_index": index,
            }
        )
    return docs


def _select_pair_files(new_files: List[Dict[str, Any]], session_docs: Dict[str, Any]) -> tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    files = list(new_files or [])[:2]
    if len(files) < 2:
        file_names = list((session_docs or {}).keys())
        if len(file_names) < 2:
            return None, "Нужно минимум 2 документа для выполнения этого сценария."
        if len(file_names) > 2:
            labels = "\n".join(f"- {name}" for name in file_names)
            return None, (
                "В активном наборе больше 2 документов. Уточните целевую пару "
                f"или оставьте только нужные файлы.\n{labels}"
            )
        files = [{"name": name, "path": session_docs[name].get("path")} for name in file_names]

    if len(files) < 2 or any(not f.get("path") for f in files):
        return None, "Недостаточно валидных файлов для выполнения этого сценария."
    return files, None


def _select_single_file(new_files: List[Dict[str, Any]], session_docs: Dict[str, Any], active_status_line: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    if new_files:
        return new_files[0], None
    if session_docs:
        if len(session_docs) > 1:
            labels = "\n".join(f"- {name}" for name in session_docs.keys())
            return None, (
                "Для анализа нужен один целевой документ. "
                f"Уточните, какой файл анализировать.\n{active_status_line}\n{labels}"
            )
        only_name = next(iter(session_docs))
        return {"name": only_name, "path": session_docs[only_name].get("path")}, None
    return None, "Нужно загрузить документ для анализа."


async def _execute_compare(
    *,
    new_files: List[Dict[str, Any]],
    session_docs: Dict[str, Any],
    deps: ExecutionDependencies,
) -> Dict[str, Any]:
    from orchestrator.workflows.compare import create_compare_graph

    files, error_message = _select_pair_files(new_files, session_docs)
    if error_message:
        return {"assistant_message": error_message}

    workflow = create_compare_graph()
    final_state = await _run_graph(
        workflow,
        {
            "input_1": deps.to_host_path(files[0]["path"]),
            "input_2": deps.to_host_path(files[1]["path"]),
            "name_1": files[0]["name"],
            "name_2": files[1]["name"],
            "chunks_old": [],
            "chunks_new": [],
            "matches": [],
            "analysis_results": [],
            "final_report": "",
            "errors": [],
        },
    )
    report = final_state.get("final_report", "")
    errors = final_state.get("errors", [])
    summary_metadata = final_state.get("summary_metadata") or {}
    model_execution = final_state.get("model_execution") or _summarize_model_execution_events(
        _collect_model_execution_events(deps)
    )
    if report:
        await deps.attach_and_register_report(report)
        execution_metadata = {
            "status": "degraded" if summary_metadata.get("degraded") else "completed",
            "degraded": bool(summary_metadata.get("degraded")),
            "completed_stages": list(summary_metadata.get("completed_stages") or []),
            "final_synthesis_status": str(summary_metadata.get("final_synthesis_status") or "unknown"),
        }
        if summary_metadata.get("degraded_reason"):
            execution_metadata["reason"] = str(summary_metadata.get("degraded_reason"))
        if summary_metadata.get("degraded_stage"):
            execution_metadata["stage"] = str(summary_metadata.get("degraded_stage"))
        return {
            "assistant_message": report,
            "generated_report": report,
            "execution_metadata": execution_metadata,
            **({"model_execution": model_execution} if model_execution is not None else {}),
        }
    if errors:
        return {"assistant_message": "Ошибки:\n" + "\n".join(f"- {e}" for e in errors)}
    return {"assistant_message": "Не удалось создать отчёт."}


async def _execute_equipment(
    *,
    query: str,
    new_files: List[Dict[str, Any]],
    session_docs: Dict[str, Any],
    deps: ExecutionDependencies,
) -> Dict[str, Any]:
    from orchestrator.workflows.equipment import create_equipment_graph

    files, error_message = _select_pair_files(new_files, session_docs)
    if error_message:
        return {"assistant_message": error_message}

    mode = detect_equipment_mode(
        files[0]["name"],
        files[1]["name"],
        query,
        text_1=(session_docs.get(files[0]["name"], {}) or {}).get("text", "")[:1000],
        text_2=(session_docs.get(files[1]["name"], {}) or {}).get("text", "")[:1000],
    )

    workflow = create_equipment_graph()
    final_state = await _run_graph(
        workflow,
        {
            "input_1": deps.to_host_path(files[0]["path"]),
            "input_2": deps.to_host_path(files[1]["path"]),
            "name_1": files[0]["name"],
            "name_2": files[1]["name"],
            "mode": mode,
            "items_1": [],
            "items_2": [],
            "matches": [],
            "analysis_results": [],
            "final_report": "",
            "errors": [],
            "session_id": "",
        },
    )
    report = final_state.get("final_report", "")
    errors = final_state.get("errors", [])
    summary_metadata = final_state.get("summary_metadata") or {}
    model_execution = final_state.get("model_execution") or _summarize_model_execution_events(
        _collect_model_execution_events(deps)
    )
    if report:
        await deps.attach_and_register_report(report)
        execution_metadata = {
            "status": "degraded" if summary_metadata.get("degraded") else "completed",
            "degraded": bool(summary_metadata.get("degraded")),
            "completed_stages": list(summary_metadata.get("completed_stages") or []),
            "final_synthesis_status": str(summary_metadata.get("final_synthesis_status") or "unknown"),
        }
        if summary_metadata.get("degraded_reason"):
            execution_metadata["reason"] = str(summary_metadata.get("degraded_reason"))
        if summary_metadata.get("degraded_stage"):
            execution_metadata["stage"] = str(summary_metadata.get("degraded_stage"))
        return {
            "assistant_message": report,
            "generated_report": report,
            "execution_metadata": execution_metadata,
            **({"model_execution": model_execution} if model_execution is not None else {}),
        }
    if errors:
        return {"assistant_message": "Ошибки:\n" + "\n".join(f"- {e}" for e in errors)}
    return {"assistant_message": "Не удалось создать отчёт."}


async def _execute_document_analysis(
    *,
    new_files: List[Dict[str, Any]],
    session_docs: Dict[str, Any],
    effective_settings: Optional[Dict[str, Any]],
    deps: ExecutionDependencies,
) -> Dict[str, Any]:
    from orchestrator.workflows.document_analysis import create_analysis_graph

    _raise_if_execution_cancelled(deps)
    file_entry, error_message = _select_single_file(new_files, session_docs, deps.active_set_status_line())
    if error_message:
        return {"assistant_message": error_message}

    workflow = create_analysis_graph()
    final_state = await _run_graph(
        workflow,
        {
            "input_path": deps.to_host_path(file_entry["path"]),
            "doc_name": file_entry["name"],
            "doc_type": "",
            "doc_metadata": {},
            "items": [],
            "full_text": "",
            "summary": "",
            "summary_metadata": {},
            "final_report": "",
            "errors": [],
            "runtime_context": {
                "is_cancelled": deps.is_cancelled,
                "update_progress_box": deps.update_progress_box,
                "summary_policy": _build_document_analysis_summary_policy(effective_settings),
                "started_at_monotonic": time.monotonic(),
            },
        },
    )
    report = final_state.get("final_report", "")
    errors = final_state.get("errors", [])
    summary_metadata = final_state.get("summary_metadata") or {}
    model_execution = final_state.get("model_execution") or _summarize_model_execution_events(
        _collect_model_execution_events(deps)
    )
    if report:
        await deps.attach_and_register_report(report)
        execution_metadata = {
            "status": "degraded" if summary_metadata.get("degraded") else "completed",
            "degraded": bool(summary_metadata.get("degraded")),
            "completed_stages": list(summary_metadata.get("completed_stages") or []),
            "final_synthesis_status": str(summary_metadata.get("final_synthesis_status") or "unknown"),
        }
        if summary_metadata.get("degraded_reason"):
            execution_metadata["reason"] = str(summary_metadata.get("degraded_reason"))
        if summary_metadata.get("degraded_stage"):
            execution_metadata["stage"] = str(summary_metadata.get("degraded_stage"))
        return {
            "assistant_message": report,
            "generated_report": report,
            "execution_metadata": execution_metadata,
            **({"model_execution": model_execution} if model_execution is not None else {}),
        }
    if errors:
        return {"assistant_message": "Ошибки:\n" + "\n".join(f"- {e}" for e in errors)}
    return {"assistant_message": "Не удалось создать отчёт."}


async def _execute_documents_summary(
    *,
    query: str,
    history: List[Dict[str, Any]],
    effective_settings: Optional[Dict[str, Any]] = None,
    deps: ExecutionDependencies,
) -> Dict[str, Any]:
    started_at_monotonic = time.monotonic()

    def _split_summary_chunks(text: str, *, max_chars: int = 3500) -> List[str]:
        normalized = str(text or "").strip()
        if not normalized:
            return []
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
        if not paragraphs:
            paragraphs = [normalized]
        chunks: List[str] = []
        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            if len(paragraph) <= max_chars:
                current = paragraph
                continue
            for start in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[start : start + max_chars].strip())
        if current:
            chunks.append(current)
        return [chunk for chunk in chunks if chunk]

    docs = deps.get_all_docs() or []
    if not docs:
        return {"assistant_message": "Нет загруженных документов для суммаризации."}

    per_doc_chunks: List[Dict[str, Any]] = []
    total_chunks = 0
    for doc in docs:
        doc_name = str(doc.get("display_name", "document"))
        doc_id = str(doc.get("document_id") or doc_name)
        text = str(doc.get("text", "")).strip()
        chunks = _split_summary_chunks(text[:30000]) if text else []
        if not chunks:
            per_doc_chunks.append({"document_id": doc_id, "name": doc_name, "chunks": []})
            continue
        per_doc_chunks.append({"document_id": doc_id, "name": doc_name, "chunks": chunks})
        total_chunks += len(chunks)

    per_doc: List[Dict[str, str]] = []
    processed_chunks = 0
    degraded_events: List[Dict[str, Any]] = []
    for doc_entry in per_doc_chunks:
        _raise_if_execution_cancelled(deps)
        doc_name = str(doc_entry["name"])
        doc_id = str(doc_entry.get("document_id") or doc_name)
        chunks = list(doc_entry.get("chunks") or [])
        if not chunks:
            per_doc.append({"name": doc_name, "summary": "Документ пуст или текст не извлечён."})
            continue
        chunk_summaries: List[str] = []
        for chunk in chunks:
            _raise_if_execution_cancelled(deps)
            processed_chunks += 1
            await deps.update_progress_box(
                key="documents_summary_progress",
                title="Суммаризация фрагментов",
                content=f"{processed_chunks}/{total_chunks}",
            )
            prompt = deps.build_prompt(
                (
                    "Кратко суммаризируй фрагмент документа в 3-5 пунктов: тема, цель, ключевые "
                    "требования/положения, сроки/ограничения (если есть), важные риски/последствия."
                ),
                [],
                (
                    "Ты аналитик документов. Пиши строго по тексту, без домыслов. "
                    f"Документ: {doc_name}\n\nФРАГМЕНТ:\n{chunk}"
                ),
            )
            cache_key = _build_documents_summary_chunk_cache_key(
                document_id=doc_id,
                document_name=doc_name,
                chunk=chunk,
                effective_settings=effective_settings,
            )
            chunk_summary = _get_cached_documents_summary_chunk(cache_key)
            if chunk_summary is None:
                chunk_summary, stage_meta = await _infer_documents_summary_stage(
                    deps=deps,
                    prompt=prompt,
                    stage="chunk",
                    effective_settings=effective_settings,
                )
                if stage_meta.get("degraded"):
                    degraded_events.append(stage_meta)
                _store_cached_documents_summary_chunk(cache_key, chunk_summary.strip())
            else:
                inc_metric_counter(
                    "agent_nav_fallback_events_total",
                    labels={
                        "component": "documents_summary",
                        "fallback": "documents_summary_chunk_cache_hit",
                        "source": "workflow",
                    },
                )
                chunk_summary = chunk_summary.strip()
            chunk_summaries.append(chunk_summary.strip())
        if len(chunk_summaries) == 1:
            per_doc.append({"name": doc_name, "summary": chunk_summaries[0]})
            await deps.update_progress_box(
                key="documents_summary_progress",
                title="Промежуточная сводка",
                content=f"Готов документ: {doc_name}\n\n{chunk_summaries[0]}",
            )
            continue
        reduce_items = chunk_summaries
        try:
            while len(reduce_items) > 1:
                _raise_if_execution_cancelled(deps)
                next_reduce_items: List[str] = []
                reduce_group_size = (
                    SUMMARY_DEGRADED_REDUCE_GROUP_SIZE
                    if degraded_events or _is_low_vram_device_mode(effective_settings)
                    else SUMMARY_REDUCE_GROUP_SIZE
                )
                merge_char_cap = (
                    SUMMARY_STAGE_DEGRADED_INPUT_CHARS["merge"]
                    if degraded_events or _is_low_vram_device_mode(effective_settings)
                    else SUMMARY_STAGE_MAX_INPUT_CHARS["merge"]
                )
                for batch in _group_summary_items_for_budget(
                    reduce_items,
                    max_chars=merge_char_cap,
                    max_items=reduce_group_size,
                ):
                    _raise_if_execution_cancelled(deps)
                    merge_prompt = deps.build_prompt(
                        (
                            "Объедини суммаризации фрагментов одного документа в итоговую краткую сводку из 4-6 пунктов, "
                            "без повторов и без домыслов."
                        ),
                        [],
                        (
                            "Ты аналитик документов. Собери единую сводку строго по промежуточным summary. "
                            f"Документ: {doc_name}\n\nСУММАРИЗАЦИИ ФРАГМЕНТОВ:\n"
                            + _join_summary_items(batch, max_chars=merge_char_cap)
                        ),
                    )
                    merged_summary, stage_meta = await _infer_documents_summary_stage(
                        deps=deps,
                        prompt=merge_prompt,
                        stage="merge",
                        effective_settings=effective_settings,
                    )
                    if stage_meta.get("degraded"):
                        degraded_events.append(stage_meta)
                    next_reduce_items.append(merged_summary.strip())
                reduce_items = next_reduce_items
            per_doc.append({"name": doc_name, "summary": reduce_items[0]})
            await deps.update_progress_box(
                key="documents_summary_progress",
                title="Промежуточная сводка",
                content=f"Готов документ: {doc_name}\n\n{reduce_items[0]}",
            )
        except Exception as exc:
            inc_metric_counter(
                "agent_nav_fallback_events_total",
                labels={
                    "component": "documents_summary",
                    "fallback": "documents_summary_merge_degraded",
                    "source": "workflow",
                },
            )
            logger.warning(
                "documents_summary merge degraded doc=%s chunks=%s device_mode=%s: %s",
                doc_name,
                len(chunk_summaries),
                _resolve_summary_device_mode(effective_settings),
                exc,
                exc_info=True,
            )
            fallback_summary = _join_summary_items(
                chunk_summaries,
                max_chars=SUMMARY_STAGE_DEGRADED_INPUT_CHARS["global"],
            )
            per_doc.append(
                {
                    "name": doc_name,
                    "summary": (
                        "Частичная сводка по документу; этап объединения summary не завершился.\n\n"
                        f"{fallback_summary}"
                    ).strip(),
                }
            )
            await deps.update_progress_box(
                key="documents_summary_progress",
                title="Промежуточная сводка",
                content=f"Готов документ: {doc_name}\n\n{per_doc[-1]['summary']}",
            )

        combined_input = _join_summary_items(
            [f"{item['name']}\n{item['summary']}" for item in per_doc],
            max_chars=(
                SUMMARY_STAGE_DEGRADED_INPUT_CHARS["global"]
                if degraded_events or _is_low_vram_device_mode(effective_settings)
                else SUMMARY_STAGE_MAX_INPUT_CHARS["global"]
            ),
        )
        await deps.update_progress_box(
            key="documents_summary_progress",
            title="Формирование итоговой сводки",
            content="Формирую общую сводку по уже собранным промежуточным результатам.",
        )
        _raise_if_execution_cancelled(deps)
        global_prompt = deps.build_prompt(
            query,
            history,
            (
                "Ты аналитик. На основе сводок по документам сформируй:\n"
                "1) ОБЩАЯ СВОДКА (5-8 предложений)\n"
                "2) КЛЮЧЕВЫЕ РАЗЛИЧИЯ/АКЦЕНТЫ (если документов больше одного) списком\n"
                "Пиши только на основе входных сводок.\n\n"
                f"СВОДКИ:\n{combined_input}"
            ),
        )
        try:
            global_summary, stage_meta = await _infer_documents_summary_stage(
                deps=deps,
                prompt=global_prompt,
                stage="global",
                effective_settings=effective_settings,
            )
            if stage_meta.get("degraded"):
                degraded_events.append(stage_meta)
        except Exception as exc:
            inc_metric_counter(
                "agent_nav_fallback_events_total",
                labels={
                    "component": "documents_summary",
                    "fallback": "documents_summary_global_degraded",
                    "source": "workflow",
                },
            )
            logger.warning(
                "documents_summary global degraded docs=%s device_mode=%s: %s",
                len(per_doc),
                _resolve_summary_device_mode(effective_settings),
                exc,
                exc_info=True,
            )
            global_summary = (
                "Общая сводка недоступна: финальный этап суммаризации не завершился. "
                "Ниже сохранены промежуточные сводки по документам."
            )
    await deps.update_progress_box(
        key="documents_summary_progress",
        title="Суммаризация завершена",
        content=f"Готово: обработано {processed_chunks}/{total_chunks} фрагментов.",
    )
    lines = ["## Сводка по документам", ""]
    execution_metadata = None
    if degraded_events:
        final_event = degraded_events[-1]
        execution_metadata = {
            "degraded": True,
            "reason": final_event.get("reason", "low_vram"),
            "stage": final_event.get("stage", "global"),
            "policy": final_event.get("policy", "reduced_context"),
        }
        lines.append(
            "Примечание: ответ упрощён из-за ограничений ресурсов; применён reduced-context режим."
        )
        lines.append("")
    lines.append("### По каждому документу")
    for item in per_doc:
        lines.extend([f"#### {item['name']}", item["summary"], ""])
    lines.extend(
        [
            "### Общая сводка",
            global_summary.strip(),
            "",
            f"**Время выполнения:** {_format_elapsed_seconds(time.monotonic() - started_at_monotonic)}",
        ]
    )
    result = {"assistant_message": "\n".join(lines).strip()}
    if execution_metadata is not None:
        result["execution_metadata"] = execution_metadata
    model_execution = _summarize_model_execution_events(_collect_model_execution_events(deps))
    if model_execution is not None:
        result["model_execution"] = model_execution
    return result


async def _execute_doc_question(
    *,
    query: str,
    history: List[Dict[str, Any]],
    session_docs: Dict[str, Any],
    effective_settings: Dict[str, Any],
    deps: ExecutionDependencies,
) -> Dict[str, Any]:
    all_docs = deps.get_all_docs() or _build_session_doc_list(session_docs)
    active_docs = deps.get_active_docs() or _build_session_doc_list(session_docs)
    target_doc_name = deps.resolve_target_doc_name(query, all_docs)
    rag_scope = str(effective_settings.get("rag_scope") or "off")
    knowledge_collection_id = effective_settings.get("knowledge_collection_id")

    scope_docs = active_docs
    if target_doc_name:
        scope_docs = [doc for doc in all_docs if str(doc.get("display_name")) == target_doc_name]
    elif deps.is_report_query(query):
        report_docs = deps.get_report_docs() or []
        if report_docs:
            scope_docs = [report_docs[-1]]

    await deps.ensure_rag_index_for_doc_ids([str(doc["document_id"]) for doc in scope_docs])
    rag = deps.get_rag_pipeline()
    rag_result = None
    rag_meta: Dict[str, Any] = {}
    rag_mode = "simple"
    retrieval_available = bool(deps.has_retrieval_adapter())
    kb_sources: Optional[List[Dict[str, Any]]] = None
    source_scope_summary = "session" if rag_scope == "session_rag" else "knowledge_base"

    if rag_scope in {"session_rag", "knowledge_base_rag"}:
        merged = retrieve_merged_chunks(
            query=query,
            rag_scope=rag_scope,
            knowledge_collection_id=knowledge_collection_id,
            session_docs=session_docs,
            active_doc_ids=[str(doc["document_id"]) for doc in scope_docs if doc.get("document_id")],
            embed_fn=deps.get_retrieval_embed_fn(),
            kb_store=deps.get_knowledge_base_store(),
            top_k=max(20, int(getattr(deps.get_rag_pipeline() or object(), "top_k", 5)) * 4) if target_doc_name else 20,
            candidate_budget_per_scope=12,
        )
        kb_sources = merged.get("chunks")
        source_scope_summary = str(merged.get("source_scope_summary") or source_scope_summary)

    if kb_sources:
        sources = []
        for idx, chunk in enumerate(kb_sources[:20], start=1):
            meta = dict(chunk.get("metadata_json") or {})
            sources.append(
                {
                    "source_id": idx,
                    "document_id": str(chunk.get("document_id")),
                    "display_name": str(chunk.get("display_name") or chunk.get("document_id")),
                    "chunk_id": str(chunk.get("chunk_id")),
                    "collection_id": chunk.get("collection_id"),
                    "source_origin": chunk.get("source_origin"),
                    "section": meta.get("section"),
                    "char_span": {
                        "start_char": meta.get("start_char"),
                        "end_char": meta.get("end_char"),
                    },
                    "page": meta.get("page"),
                    "quote": str(chunk.get("text", "")),
                    "raw_score": float(chunk.get("raw_score", 0.0)),
                    "normalized_score": float(chunk.get("normalized_score", 0.0)),
                    "grade": None,
                    "z_score": None,
                }
            )
    else:
        sources = []

    if not sources and rag is not None and getattr(rag, "_indexed", False):
        try:
            retrieve_top_k = max(20, int(getattr(rag, "top_k", 5)) * 4) if target_doc_name else None
            rag_result = await asyncio.to_thread(rag.retrieve, query, retrieve_top_k)
            rag_meta = getattr(rag_result, "metadata", None) or {}
            rag_mode = str(rag_meta.get("mode", "simple"))
        except Exception as exc:
            logger.warning("RAG retrieve failed in doc_question path: %s", exc, exc_info=True)
            inc_metric_counter(
                "agent_nav_fallback_events_total",
                labels={"component": "doc_question", "fallback": "rag_exception", "source": "execution_runtime"},
            )
            rag_result = None

    if rag_result is None and not sources and not retrieval_available:
        payload = deps.build_doc_question_deterministic_fallback(
            query=query,
            sources=[],
            fallback_type="retrieval_unavailable",
            fallback_reason=(
                "Для этого execution path backend retrieval adapter пока не подключён. "
                "Execution выполняется через unified core, но session-document retrieval "
                "для данного adapter ещё не реализован."
            ),
            source_scope_summary=source_scope_summary,
        )
        return {
            "assistant_message": deps.render_doc_question_markdown(payload),
            "sources": payload.get("sources", []),
        }

    if rag_result is None and not sources:
        inc_metric_counter(
            "agent_nav_fallback_events_total",
            labels={"component": "doc_question", "fallback": "no_sources", "source": "execution_runtime"},
        )
        return {
            "assistant_message": (
                "По текущему запросу не удалось получить проверяемые источники из RAG. "
                "Уточните формулировку или вопрос к конкретной позиции."
            )
        }

    if not sources:
        sources = deps.build_sources_from_rag_result(rag_result, rag, max_sources=20)
    if target_doc_name:
        sources = [
            s
            for s in sources
            if str(s.get("document_id")) == target_doc_name
            or str(s.get("display_name", "")) == target_doc_name
        ]
        sources = deps.reindex_sources(sources)

    if not sources:
        fallback_reason = None
        if target_doc_name:
            fallback_reason = (
                f"Для документа `{target_doc_name}` не найдено подтверждённых релевантных фрагментов "
                "в активном наборе."
            )
        payload = deps.build_doc_question_deterministic_fallback(
            query=query,
            sources=[],
            fallback_type="insufficient_evidence",
            fallback_reason=fallback_reason,
            source_scope_summary=source_scope_summary,
        )
        return {
            "assistant_message": deps.render_doc_question_markdown(payload),
            "sources": payload.get("sources", []),
            **({"model_execution": _summarize_model_execution_events(_collect_model_execution_events(deps))} if _collect_model_execution_events(deps) else {}),
        }

    prompt = deps.build_doc_question_prompt_with_sources(query, history, sources)
    response_text = await deps.infer_assistant_text(prompt, enforced_overrides={"temperature": 0.3})
    citations_valid = deps.citations_are_valid(response_text, source_count=len(sources))

    if not citations_valid or deps.needs_doc_question_regen(response_text, has_session_docs=bool(session_docs)):
        strict_prompt = (
            deps.build_doc_question_prompt_with_sources(query, history, sources)
            + "\n\nЖЕСТКОЕ ПРАВИЛО: обязательно используй только валидные ссылки [n] из каталога."
        )
        response_text = await deps.infer_assistant_text(strict_prompt, enforced_overrides={"temperature": 0.2})
        citations_valid = deps.citations_are_valid(response_text, source_count=len(sources))

    if not citations_valid:
        if len(sources) == 1 and has_direct_grounded_evidence_v1(
            sources=sources,
            cited_ids=[int(sources[0].get("source_id", 1))],
            query=query,
            citations_valid=True,
        ):
            concise_prompt = _build_direct_evidence_concise_prompt(
                deps,
                query=query,
                history=history,
                sources=sources,
            )
            concise_response = await deps.infer_assistant_text(
                concise_prompt,
                enforced_overrides={"temperature": 0.2},
            )
            if deps.citations_are_valid(concise_response, source_count=len(sources)):
                response_text = concise_response
                citations_valid = True
            else:
                deterministic_direct_answer = _build_direct_grounded_answer_from_source(query, sources[0])
                if deterministic_direct_answer:
                    response_text = deterministic_direct_answer
                    citations_valid = True

    if not citations_valid:
        payload = deps.build_doc_question_deterministic_fallback(
            query=query,
            sources=sources,
            fallback_type="citation_validation_failed",
            source_scope_summary=source_scope_summary,
        )
        return {
            "assistant_message": deps.render_doc_question_markdown(payload),
            "sources": payload.get("sources", []),
            **({"model_execution": _summarize_model_execution_events(_collect_model_execution_events(deps))} if _collect_model_execution_events(deps) else {}),
        }

    cited_ids = deps.extract_citation_ids(response_text)
    has_evidence = deps.has_sufficient_evidence(
        sources=sources,
        cited_ids=cited_ids,
        mode=rag_mode,
        query=query,
        citations_valid=True,
    )
    direct_grounded_evidence = has_direct_grounded_evidence_v1(
        sources=sources,
        cited_ids=cited_ids,
        query=query,
        citations_valid=True,
    )
    if direct_grounded_evidence and _doc_answer_needs_direct_evidence_regen(response_text):
        concise_prompt = _build_direct_evidence_concise_prompt(
            deps,
            query=query,
            history=history,
            sources=sources,
        )
        concise_response = await deps.infer_assistant_text(
            concise_prompt,
            enforced_overrides={"temperature": 0.2},
        )
        if deps.citations_are_valid(concise_response, source_count=len(sources)):
            response_text = concise_response
            cited_ids = deps.extract_citation_ids(response_text)
        elif len(sources) == 1:
            deterministic_direct_answer = _build_direct_grounded_answer_from_source(query, sources[0])
            if deterministic_direct_answer:
                response_text = deterministic_direct_answer
                cited_ids = deps.extract_citation_ids(response_text)

    if not has_evidence:
        if direct_grounded_evidence:
            confidence, label = deps.compute_confidence_v1(
                sources,
                cited_ids,
                "grounded_answer",
                query=query,
            )
            payload = {
                "answer_text": deps.strip_model_source_sections(response_text),
                "sources": sources,
                "source_scope_summary": source_scope_summary,
                "answer_mode": "grounded_answer",
                "fallback_type": "none",
                "fallback_reason": None,
                "confidence": confidence,
                "confidence_label": label,
                "confidence_method": "heuristic_v1",
                "confidence_version": "1",
            }
        else:
            payload = deps.build_doc_question_deterministic_fallback(
                query=query,
                sources=sources,
                fallback_type="insufficient_evidence",
                source_scope_summary=source_scope_summary,
            )
    else:
        confidence, label = deps.compute_confidence_v1(
            sources,
            cited_ids,
            "grounded_answer",
            query=query,
        )
        payload = {
            "answer_text": deps.strip_model_source_sections(response_text),
            "sources": sources,
            "source_scope_summary": source_scope_summary,
            "answer_mode": "grounded_answer",
            "fallback_type": "none",
            "fallback_reason": None,
            "confidence": confidence,
            "confidence_label": label,
            "confidence_method": "heuristic_v1",
            "confidence_version": "1",
        }
    return {
        "assistant_message": deps.render_doc_question_markdown(payload),
        "sources": payload.get("sources", []),
        **({"model_execution": _summarize_model_execution_events(_collect_model_execution_events(deps))} if _collect_model_execution_events(deps) else {}),
    }


async def _execute_general_chat(
    *,
    query: str,
    history: List[Dict[str, Any]],
    session_docs: Dict[str, Any],
    effective_settings: Dict[str, Any],
    deps: ExecutionDependencies,
) -> Dict[str, Any]:
    custom_system_prompt = (effective_settings.get("custom_system_prompt") or "").strip()
    system_prompt = custom_system_prompt or deps.get_profile_system_prompt() or get_prompt_profile_system_message(
        effective_settings.get("prompt_profile")
    )
    prompt = deps.build_prompt(query, history, system_prompt)
    social_query = is_social_query(query)
    if social_query and not session_docs:
        prompt += (
            "\n\nКонтекст: документов в сессии нет. "
            "Ответь как обычный чат-ассистент, без предложений загрузить документы, "
            "если пользователь сам не просит анализ документов."
        )
    elif social_query and session_docs:
        prompt += (
            "\n\nКонтекст: у пользователя уже есть загруженные документы. "
            "Отвечай как в обычном чате, без шаблонных фраз и без просьбы перезагрузить файлы."
        )
    if _detect_dominant_script(query) == "cyrillic" and not _is_translation_request(query):
        prompt += "\n\nОтвечай только на русском языке, если пользователь явно не просит иначе."
    elif _detect_dominant_script(query) == "latin" and not _is_translation_request(query):
        prompt += "\n\nAnswer in the user's language only unless they explicitly request a translation."
    answer = await deps.infer_assistant_text(prompt)
    if _should_regenerate_for_language_consistency(query, answer):
        guarded_prompt = (
            prompt
            + "\n\nЖЕСТКОЕ ПРАВИЛО: ответь только на языке пользователя, без смешения языков. "
            + "Если пользователь пишет по-русски, ответь только по-русски, без иероглифов, английского "
            + "и вводных пояснений. Если пользователь просит короткий ответ, дай одну короткую фразу."
        )
        answer = await deps.infer_assistant_text(
            guarded_prompt,
            enforced_overrides={
                "temperature": GENERAL_CHAT_LANGUAGE_GUARD_TEMPERATURE,
                "top_p": GENERAL_CHAT_LANGUAGE_GUARD_TOP_P,
                "max_tokens": 96,
            },
        )
        if _should_regenerate_for_language_consistency(query, answer):
            answer = _extract_clean_sentence_for_script(answer, _detect_dominant_script(query))
    model_execution = _summarize_model_execution_events(_collect_model_execution_events(deps))
    response = {"assistant_message": answer}
    if model_execution is not None:
        response["model_execution"] = model_execution
    return response


async def execute_orchestration(
    request: Dict[str, Any],
    *,
    deps: Optional[ExecutionDependencies] = None,
) -> Dict[str, Any]:
    request = copy.deepcopy(request)
    session_docs = request.get("session_docs") or {}
    attachments_meta = request.get("attachments_meta") or []
    history = request.get("history") or []

    effective_settings = copy.deepcopy(request.get("effective_settings") or resolve_effective_settings(_collect_raw_control_plane(request)))
    runtime_mode = resolve_request_runtime_mode(request, effective_settings)
    state_store = get_orchestration_state_store()
    run_record = await state_store.get_or_create_run(
        thread_id=request.get("thread_id"),
        session_id=request.get("session_id"),
        workflow_type=_resolve_workflow_type(request),
        idempotency_key=request.get("idempotency_key"),
    )
    request["run_id"] = run_record.run_id
    request["state_ref"] = run_record.state_ref
    request["state_version"] = run_record.version
    classifier_result = request.get("classifier_result")
    if classifier_result is None and not request.get("forced_route"):
        classifier_result = await _resolve_classifier_result_for_request(
            query=str(request.get("message", "") or ""),
            effective_settings=effective_settings,
        )
    request["classifier_result"] = classifier_result
    runtime_budget_metadata = request.get("runtime_budget_metadata")
    if isinstance(runtime_budget_metadata, dict):
        effective_settings["runtime_budget_metadata"] = copy.deepcopy(runtime_budget_metadata)

    decision = decide_orchestration(
        query=request.get("message", ""),
        trace_id=request.get("trace_id"),
        runtime_mode=runtime_mode,
        rag_scope=str(effective_settings.get("rag_scope") or "off"),
        knowledge_collection_id=effective_settings.get("knowledge_collection_id"),
        file_count=int(request.get("file_count", 0)),
        has_session_docs=bool(request.get("has_session_docs", False)),
        session_docs=session_docs,
        classifier_result=classifier_result,
        new_files=attachments_meta,
        active_doc_ids=request.get("active_doc_ids") or [],
        forced_route=request.get("forced_route"),
    )
    response = _with_execution_metadata(decision, request=request, effective_settings=effective_settings)

    if response.get("action_required"):
        updated = await state_store.save_run(
            run_id=run_record.run_id,
            status=_derive_run_status(response),
            pending_action_id=response.get("pending_action_id"),
            resume_state_blob=_merge_resume_state_blob(request, response),
            checkpoint_blob=_build_checkpoint_blob(request, response),
            expected_version=run_record.version,
        )
        response["state_version"] = updated.version
        return response

    if deps is None:
        updated = await state_store.save_run(
            run_id=run_record.run_id,
            status=_derive_run_status(response),
            pending_action_id=response.get("pending_action_id"),
            resume_state_blob=_merge_resume_state_blob(request, response),
            checkpoint_blob=_build_checkpoint_blob(request, response),
            expected_version=run_record.version,
        )
        response["state_version"] = updated.version
        return response

    executor = response.get("executor") or "chat"
    try:
        if executor == "compare_documents":
            result = await _execute_compare(new_files=attachments_meta, session_docs=session_docs, deps=deps)
        elif executor == "equipment_analysis":
            result = await _execute_equipment(
                query=request.get("message", ""),
                new_files=attachments_meta,
                session_docs=session_docs,
                deps=deps,
            )
        elif executor == "document_analysis":
            result = await _execute_document_analysis(
                new_files=attachments_meta,
                session_docs=session_docs,
                effective_settings=effective_settings,
                deps=deps,
            )
        elif executor == "document_question":
            result = await _execute_doc_question(
                query=request.get("message", ""),
                history=history,
                session_docs=session_docs,
                effective_settings=effective_settings,
                deps=deps,
            )
        elif executor == "documents_summary":
            result = await _execute_documents_summary(
                query=request.get("message", ""),
                history=history,
                effective_settings=effective_settings,
                deps=deps,
            )
        else:
            result = await _execute_general_chat(
                query=request.get("message", ""),
                history=history,
                session_docs=session_docs,
                effective_settings=effective_settings,
                deps=deps,
            )
    except asyncio.CancelledError:
        result = {
            "assistant_message": "Запрос остановлен пользователем.",
            "execution_metadata": {"status": "cancelled"},
        }
    except UMSBusyError:
        result = {
            "assistant_message": "Модель занята предыдущим тяжёлым запросом. Дождитесь освобождения слота или остановите активный запуск.",
            "execution_metadata": {"status": "busy"},
        }
    except Exception as exc:
        result = {"assistant_message": f"Ошибка выполнения сценария: {exc}"}

    if result.get("generated_report"):
        response["ui_effects"]["generated_report"] = result["generated_report"]
    response["assistant_message"] = result.get("assistant_message") or response.get("assistant_message")
    response["sources"] = result.get("sources", response.get("sources", []))
    if result.get("execution_metadata"):
        response["execution_metadata"] = result["execution_metadata"]
    if result.get("model_execution"):
        response["model_execution"] = result["model_execution"]
    elif deps is not None:
        collected_model_execution = _summarize_model_execution_events(_collect_model_execution_events(deps))
        if collected_model_execution is not None:
            response["model_execution"] = collected_model_execution
    status = _derive_run_status(response)
    updated = await state_store.save_run(
        run_id=run_record.run_id,
        status=status,
        pending_action_id=response.get("pending_action_id"),
        resume_state_blob=_merge_resume_state_blob(request, response),
        checkpoint_blob=_build_checkpoint_blob(request, response),
        last_error=response.get("assistant_message") if status == "failed" else None,
        expected_version=run_record.version,
    )
    response["state_version"] = updated.version
    return response
