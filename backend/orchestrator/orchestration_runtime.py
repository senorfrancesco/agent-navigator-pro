"""
Pure orchestration decision layer shared by Agent API and Chainlit.

This module owns route/policy decisions and returns a transport-friendly
contract that UI layers can render without re-implementing business routing.
"""

from __future__ import annotations

import copy
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from orchestrator.workflows.equipment import detect_equipment_mode
from orchestrator.rag.classifier import UNSURE_INTENT

INTENT_LOW_MARGIN_THRESHOLD = 0.12
INTENT_LOW_CONFIDENCE_THRESHOLD = 0.55
ROUTE_CHOICE_TIMEOUT_S = 90
RuntimeMode = Literal["auto", "chat_only", "specialized_tasks"]
_VALID_RUNTIME_MODES = {"auto", "chat_only", "specialized_tasks"}

_COMPARE_QUERY_KEYWORDS = [
    "сравни", "сравнение", "различия", "отличия", "изменения",
    "что изменилось", "что поменялось", "покажи разницу",
]
_EQUIPMENT_QUERY_KEYWORDS = [
    "тз", "техническое задание", "коммерческое предложение", "кп", "смета",
    "оборудование", "подходит", "что подходит", "что нам подходит",
    "соответствует", "соответствие", "подходит ли",
]
_DOC_QUESTION_KEYWORDS = [
    "что", "какой", "какая", "какие", "сколько", "найди", "покажи", "указано",
    "написано", "содержится", "есть ли",
]
_SUMMARY_QUERY_KEYWORDS = [
    "о чем документ",
    "о чём документ",
    "о чем документы",
    "о чём документы",
    "о чем эти документы",
    "о чём эти документы",
    "суть документа",
    "суть документов",
    "кратко по документ",
    "суммариз",
    "сводк",
    "проанализируй эти документы",
    "проанализируй документы",
    "анализируй эти документы",
    "анализируй документы",
    "анализ документов",
]
_SOCIAL_ONLY_RE = re.compile(
    r"^(?:"
    r"спасибо(?:\s+большое)?|благодарю|спс|"
    r"ок(?:ей)?|понятно|ясно|понял(?:а)?|хорошо|"
    r"привет|здравствуй(?:те)?|добрый день|добрый вечер|доброе утро|"
    r"hi|hello|thanks|thank you"
    r")$",
    re.IGNORECASE,
)

_ACTIVE_MODE_BY_ROUTE = {
    "compare_documents": "compare",
    "equipment_analysis": "equipment",
    "document_analysis": "doc_analysis",
    "document_question": "doc_qa",
    "documents_summary": "doc_summary",
    "general_chat": "chat",
    "greeting": "chat",
}
_EXECUTOR_BY_ROUTE = {
    "compare_documents": "compare_documents",
    "equipment_analysis": "equipment_analysis",
    "document_analysis": "document_analysis",
    "document_question": "document_question",
    "documents_summary": "documents_summary",
    "general_chat": "chat",
    "greeting": "chat",
}
logger = logging.getLogger(__name__)


def _has_any_keyword(query_lower: str, keywords: List[str]) -> bool:
    return any(kw in query_lower for kw in keywords)


def normalize_runtime_mode(runtime_mode: Optional[str]) -> RuntimeMode:
    if runtime_mode in _VALID_RUNTIME_MODES:
        return runtime_mode
    return "auto"


def is_social_query(query: str) -> bool:
    normalized = re.sub(r"[\s\.,!?;:()\"'«»…-]+", " ", (query or "").strip().lower()).strip()
    if not normalized:
        return False
    return bool(_SOCIAL_ONLY_RE.match(normalized))


def is_low_confidence(classifier_result: Optional[Dict[str, Any]]) -> bool:
    if not classifier_result:
        return True
    if classifier_result.get("abstained") or classifier_result.get("intent") == UNSURE_INTENT:
        return True
    return (
        classifier_result.get("confidence", 0.0) < INTENT_LOW_CONFIDENCE_THRESHOLD
        or classifier_result.get("margin", 0.0) < INTENT_LOW_MARGIN_THRESHOLD
    )


def get_last_session_docs(session_docs: Dict[str, Any], count: int = 2) -> List[Dict[str, Any]]:
    items = list(session_docs.items())[-count:]
    return [{"name": name, **doc_info} for name, doc_info in items]


def is_docs_summary_query(query: str) -> bool:
    query_lower = (query or "").lower()
    return any(kw in query_lower for kw in _SUMMARY_QUERY_KEYWORDS)


def detect_intent(
    query: str,
    *,
    file_count: int = 0,
    has_session_docs: bool = False,
    active_docs_count: int = 0,
    classifier_result: Optional[Dict[str, Any]] = None,
) -> str:
    query_lower = (query or "").lower()

    if classifier_result and not (
        classifier_result.get("abstained") or classifier_result.get("intent") == UNSURE_INTENT
    ):
        intent = classifier_result["intent"]
        needs_rag = classifier_result["needs_rag"]

        if intent == "document_analysis":
            has_file = file_count >= 1 or (has_session_docs and active_docs_count >= 1)
            if not has_file:
                intent = "general_chat"

        if has_session_docs and needs_rag and intent not in (
            "compare_documents",
            "equipment_analysis",
            "document_analysis",
        ):
            intent = "document_question"
        return intent

    if "сравни" in query_lower or "различия" in query_lower:
        return "compare_documents"
    if "смет" in query_lower or "тз" in query_lower:
        return "equipment_analysis"

    single_file = file_count == 1 or (has_session_docs and active_docs_count == 1)
    if single_file and ("анализ" in query_lower or "документ" in query_lower):
        return "document_analysis"

    if has_session_docs:
        if "привет" in query_lower or "здравствуй" in query_lower:
            return "greeting"
        return "document_question"

    if "привет" in query_lower or "здравствуй" in query_lower:
        return "greeting"

    return "general_chat"


def build_route_choice_prompt(recommended_route: str, mode: str) -> str:
    if mode == "tz_vs_smeta":
        base = "Похоже, у вас ТЗ и коммерческое предложение."
    else:
        base = "Я вижу два загруженных документа."

    if recommended_route == "equipment_analysis":
        return (
            f"{base} Запрос неоднозначный. Что вы хотите сделать?\n\n"
            "1. Проверить, что из КП подходит под ТЗ\n"
            "2. Просто сравнить документы\n"
            "3. Задать вопрос по содержимому\n"
            "4. Сделать общую сводку по документам"
        )
    if recommended_route == "compare_documents":
        return (
            f"{base} Запрос неоднозначный. Что вы хотите сделать?\n\n"
            "1. Сравнить документы\n"
            "2. Проверить соответствие ТЗ и КП\n"
            "3. Задать вопрос по содержимому\n"
            "4. Сделать общую сводку по документам"
        )
    return (
        f"{base} Запрос неоднозначный. Что вы хотите сделать?\n\n"
        "1. Задать вопрос по документам\n"
        "2. Сравнить документы\n"
        "3. Проверить соответствие ТЗ и КП\n"
        "4. Сделать общую сводку по документам"
    )


def build_route_choice_state(
    query: str,
    recommended_route: str,
    new_files: List[Dict[str, Any]],
    mode: str,
    *,
    trace_id: Optional[str] = None,
    active_doc_ids: Optional[List[str]] = None,
    reason: str = "choose_route",
) -> Dict[str, Any]:
    if recommended_route == "equipment_analysis":
        choices = {
            "1": "equipment_analysis",
            "2": "compare_documents",
            "3": "document_question",
            "4": "documents_summary",
        }
    elif recommended_route == "compare_documents":
        choices = {
            "1": "compare_documents",
            "2": "equipment_analysis",
            "3": "document_question",
            "4": "documents_summary",
        }
    else:
        choices = {
            "1": "document_question",
            "2": "compare_documents",
            "3": "equipment_analysis",
            "4": "documents_summary",
        }

    options = [
        {"id": key, "label": key, "route": route}
        for key, route in choices.items()
    ]
    options.append({"id": "cancel", "label": "Отмена", "route": "cancel"})

    prompt = build_route_choice_prompt(recommended_route, mode)
    return {
        "type": "choose_route",
        "title": prompt,
        "reason": reason,
        "route_choice_id": str(uuid.uuid4())[:8],
        "origin_trace_id": trace_id,
        "query": query,
        "new_files": copy.deepcopy(new_files),
        "choices": choices,
        "options": options,
        "recommended_route": recommended_route,
        "mode": mode,
        "active_doc_ids": list(active_doc_ids or []),
        "expires_at": time.time() + ROUTE_CHOICE_TIMEOUT_S,
    }


def resolve_pending_action_selection(query: str, pending_action: Optional[Dict[str, Any]]) -> Optional[str]:
    if not pending_action:
        return None
    if pending_action.get("expires_at", 0) < time.time():
        return None
    choice = query.strip().lower()
    if choice in ("отмена", "cancel"):
        return "cancel"
    return pending_action.get("choices", {}).get(choice)


def _build_session_state_patch(
    *,
    trace_id: str,
    route: Optional[str] = None,
    executor: Optional[str] = None,
    active_mode: Optional[str] = None,
    pending_action: Optional[Dict[str, Any]] = None,
    preserve_active_mode: bool = False,
) -> Dict[str, Any]:
    patch: Dict[str, Any] = {
        "last_trace_id": trace_id,
        "pending_action": copy.deepcopy(pending_action),
    }
    if route is not None:
        patch["last_route"] = route
    if executor is not None:
        patch["last_executor"] = executor
    if active_mode is not None:
        patch["active_mode"] = active_mode
    if preserve_active_mode:
        patch["preserve_active_mode"] = True
    return patch


def _response(
    *,
    trace_id: str,
    mode: str,
    route: Optional[str],
    executor: Optional[str],
    confidence: float,
    margin: float,
    reason: str,
    action_required: Optional[Dict[str, Any]] = None,
    assistant_message: Optional[str] = None,
    model_profile: str = "default",
    session_state_patch: Optional[Dict[str, Any]] = None,
    ui_hints: Optional[Dict[str, Any]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "mode": mode,
        "route": route,
        "executor": executor,
        "model_profile": model_profile,
        "confidence": confidence,
        "margin": margin,
        "trace_id": trace_id,
        "action_required": action_required,
        "assistant_message": assistant_message,
        "sources": sources or [],
        "session_state_patch": session_state_patch or _build_session_state_patch(trace_id=trace_id),
        "ui_hints": ui_hints or {},
        "reason": reason,
    }


def _upload_required_response(
    *,
    trace_id: str,
    mode: str,
    route: str,
    confidence: float,
    margin: float,
    reason: str,
) -> Dict[str, Any]:
    messages = {
        "document_question": "Чтобы ответить по документу, сначала загрузите документ.",
        "document_analysis": "Чтобы сделать анализ, сначала загрузите документ.",
        "compare_documents": "Чтобы сравнить документы, загрузите как минимум два файла.",
        "equipment_analysis": "Чтобы проверить ТЗ и КП, загрузите соответствующие документы.",
        "documents_summary": "Чтобы сделать сводку, сначала загрузите документы.",
    }
    action = {
        "type": "upload_required",
        "title": messages.get(route, "Сначала загрузите документы."),
        "reason": reason,
        "options": [],
    }
    return _response(
        trace_id=trace_id,
        mode=mode,
        route=route,
        executor=None,
        confidence=confidence,
        margin=margin,
        reason=reason,
        action_required=action,
        assistant_message=action["title"],
    )


def decide_orchestration(
    *,
    query: str,
    trace_id: Optional[str] = None,
    runtime_mode: str = "auto",
    file_count: int = 0,
    has_session_docs: bool = False,
    session_docs: Optional[Dict[str, Any]] = None,
    classifier_result: Optional[Dict[str, Any]] = None,
    new_files: Optional[List[Dict[str, Any]]] = None,
    active_doc_ids: Optional[List[str]] = None,
    forced_route: Optional[str] = None,
) -> Dict[str, Any]:
    session_docs = session_docs or {}
    new_files = new_files or []
    active_doc_ids = list(active_doc_ids or [])
    trace_id = trace_id or str(uuid.uuid4())[:8]
    runtime_mode = normalize_runtime_mode(runtime_mode)

    if forced_route:
        executor = _EXECUTOR_BY_ROUTE.get(forced_route)
        if executor is None:
            logger.warning(
                "forced_route=%r is not a valid route, falling through to normal routing",
                forced_route,
            )
        else:
            active_mode = _ACTIVE_MODE_BY_ROUTE.get(forced_route)
            return _response(
                trace_id=trace_id,
                mode=runtime_mode,
                route=forced_route,
                executor=executor,
                confidence=1.0,
                margin=1.0,
                reason="forced_route",
                session_state_patch=_build_session_state_patch(
                    trace_id=trace_id,
                    route=forced_route,
                    executor=executor,
                    active_mode=active_mode,
                ),
            )

    query_lower = (query or "").lower().strip()
    has_session_docs = has_session_docs or bool(session_docs)
    social_query = is_social_query(query)
    total_docs = max(file_count, len(session_docs), len(active_doc_ids))
    has_any_docs = total_docs > 0
    two_docs = total_docs >= 2
    single_doc = total_docs == 1

    compare_signal = _has_any_keyword(query_lower, _COMPARE_QUERY_KEYWORDS)
    equipment_signal = _has_any_keyword(query_lower, _EQUIPMENT_QUERY_KEYWORDS)
    doc_question_signal = _has_any_keyword(query_lower, _DOC_QUESTION_KEYWORDS)
    summary_signal = is_docs_summary_query(query)

    mode_hint = None
    if two_docs and session_docs:
        docs = get_last_session_docs(session_docs, count=2)
        if len(docs) == 2:
            mode_hint = detect_equipment_mode(
                docs[0]["name"],
                docs[1]["name"],
                query,
                text_1=docs[0].get("text", "")[:1000],
                text_2=docs[1].get("text", "")[:1000],
            )
    elif two_docs and len(new_files) >= 2:
        mode_hint = detect_equipment_mode(
            new_files[0].get("name", ""),
            new_files[1].get("name", ""),
            query,
        )

    if runtime_mode == "chat_only":
        route = "greeting" if social_query else "general_chat"
        executor = _EXECUTOR_BY_ROUTE[route]
        return _response(
            trace_id=trace_id,
            mode=runtime_mode,
            route=route,
            executor=executor,
            confidence=(classifier_result or {}).get("confidence", 1.0 if social_query else 0.0),
            margin=(classifier_result or {}).get("margin", 1.0 if social_query else 0.0),
            reason="chat_only_mode",
            session_state_patch=_build_session_state_patch(
                trace_id=trace_id,
                route=route,
                executor=executor,
                active_mode=_ACTIVE_MODE_BY_ROUTE[route],
                preserve_active_mode=bool(social_query and has_any_docs),
            ),
            ui_hints={"preserve_active_docs": bool(has_any_docs)},
        )

    if has_session_docs and social_query:
        route = "greeting"
        executor = _EXECUTOR_BY_ROUTE[route]
        return _response(
            trace_id=trace_id,
            mode=runtime_mode,
            route=route,
            executor=executor,
            confidence=(classifier_result or {}).get("confidence", 1.0),
            margin=(classifier_result or {}).get("margin", 1.0),
            reason="social_guard",
            session_state_patch=_build_session_state_patch(
                trace_id=trace_id,
                route=route,
                executor=executor,
                preserve_active_mode=True,
            ),
            ui_hints={"preserve_active_docs": True, "preserve_active_mode": True},
        )

    if has_any_docs and summary_signal and not compare_signal and not equipment_signal:
        route = "documents_summary"
        executor = _EXECUTOR_BY_ROUTE[route]
        return _response(
            trace_id=trace_id,
            mode=runtime_mode,
            route=route,
            executor=executor,
            confidence=(classifier_result or {}).get("confidence", 0.0),
            margin=(classifier_result or {}).get("margin", 0.0),
            reason="summary_query",
            session_state_patch=_build_session_state_patch(
                trace_id=trace_id,
                route=route,
                executor=executor,
                active_mode=_ACTIVE_MODE_BY_ROUTE[route],
            ),
        )

    if two_docs and mode_hint == "tz_vs_smeta" and equipment_signal:
        if (
            classifier_result
            and classifier_result.get("intent") == "equipment_analysis"
            and not is_low_confidence(classifier_result)
        ):
            route = "equipment_analysis"
            executor = _EXECUTOR_BY_ROUTE[route]
            return _response(
                trace_id=trace_id,
                mode=runtime_mode,
                route=route,
                executor=executor,
                confidence=classifier_result.get("confidence", 0.0),
                margin=classifier_result.get("margin", 0.0),
                reason="tz_vs_smeta_high_confidence",
                session_state_patch=_build_session_state_patch(
                    trace_id=trace_id,
                    route=route,
                    executor=executor,
                    active_mode=_ACTIVE_MODE_BY_ROUTE[route],
                ),
            )

        pending_action = build_route_choice_state(
            query,
            "equipment_analysis",
            new_files,
            mode_hint or "tz_vs_smeta",
            trace_id=trace_id,
            active_doc_ids=active_doc_ids,
            reason="tz_vs_smeta_ambiguous",
        )
        return _response(
            trace_id=trace_id,
            mode=runtime_mode,
            route="equipment_analysis",
            executor=None,
            confidence=(classifier_result or {}).get("confidence", 0.0),
            margin=(classifier_result or {}).get("margin", 0.0),
            reason="tz_vs_smeta_ambiguous",
            action_required=pending_action,
            assistant_message=pending_action["title"],
            session_state_patch=_build_session_state_patch(
                trace_id=trace_id,
                route="equipment_analysis",
                pending_action=pending_action,
            ),
        )

    inferred_intent = detect_intent(
        query,
        file_count=file_count,
        has_session_docs=has_session_docs,
        active_docs_count=len(active_doc_ids),
        classifier_result=classifier_result,
    )

    if not has_any_docs and inferred_intent in {
        "document_question",
        "document_analysis",
        "compare_documents",
        "equipment_analysis",
        "documents_summary",
    }:
        return _upload_required_response(
            trace_id=trace_id,
            mode=runtime_mode,
            route=inferred_intent,
            confidence=(classifier_result or {}).get("confidence", 0.0),
            margin=(classifier_result or {}).get("margin", 0.0),
            reason="missing_documents",
        )

    if classifier_result:
        intent = inferred_intent
        needs_rag = classifier_result.get("needs_rag", False)
        low_conf = is_low_confidence(classifier_result)

        if intent == "document_analysis":
            if not single_doc:
                if two_docs:
                    display_mode = mode_hint if (mode_hint == "tz_vs_smeta" and equipment_signal) else "unknown"
                    pending_action = build_route_choice_state(
                        query,
                        "document_question",
                        new_files,
                        display_mode,
                        trace_id=trace_id,
                        active_doc_ids=active_doc_ids,
                        reason="document_analysis_multi_doc",
                    )
                    return _response(
                        trace_id=trace_id,
                        mode=runtime_mode,
                        route="document_analysis",
                        executor=None,
                        confidence=classifier_result.get("confidence", 0.0),
                        margin=classifier_result.get("margin", 0.0),
                        reason="document_analysis_multi_doc",
                        action_required=pending_action,
                        assistant_message=pending_action["title"],
                        session_state_patch=_build_session_state_patch(
                            trace_id=trace_id,
                            route="document_analysis",
                            pending_action=pending_action,
                        ),
                    )
                intent = "general_chat"
            elif not (file_count >= 1 or has_session_docs):
                intent = "general_chat"

        if two_docs and low_conf:
            if mode_hint == "tz_vs_smeta" and equipment_signal:
                recommended_route = "equipment_analysis"
            elif compare_signal:
                recommended_route = "compare_documents"
            elif doc_question_signal:
                recommended_route = "document_question"
            elif intent in ("compare_documents", "equipment_analysis", "document_question"):
                recommended_route = intent
            else:
                recommended_route = "compare_documents"
            pending_action = build_route_choice_state(
                query,
                recommended_route,
                new_files,
                mode_hint if (mode_hint == "tz_vs_smeta" and equipment_signal) else "unknown",
                trace_id=trace_id,
                active_doc_ids=active_doc_ids,
                reason="two_docs_low_confidence",
            )
            return _response(
                trace_id=trace_id,
                mode=runtime_mode,
                route=recommended_route,
                executor=None,
                confidence=classifier_result.get("confidence", 0.0),
                margin=classifier_result.get("margin", 0.0),
                reason="two_docs_low_confidence",
                action_required=pending_action,
                assistant_message=pending_action["title"],
                session_state_patch=_build_session_state_patch(
                    trace_id=trace_id,
                    route=recommended_route,
                    pending_action=pending_action,
                ),
            )

        if has_session_docs and needs_rag and intent not in (
            "compare_documents",
            "equipment_analysis",
            "document_analysis",
        ):
            intent = "document_question"

        executor = _EXECUTOR_BY_ROUTE[intent]
        specialized_fallback = runtime_mode == "specialized_tasks" and intent == "general_chat"
        preserve_active_mode = bool(intent == "greeting" and has_any_docs)
        return _response(
            trace_id=trace_id,
            mode=runtime_mode,
            route=intent,
            executor=executor,
            confidence=classifier_result.get("confidence", 0.0),
            margin=classifier_result.get("margin", 0.0),
            reason="specialized_fallback_chat" if specialized_fallback else "semantic_router",
            session_state_patch=_build_session_state_patch(
                trace_id=trace_id,
                route=intent,
                executor=executor,
                active_mode=None if (preserve_active_mode or specialized_fallback) else _ACTIVE_MODE_BY_ROUTE[intent],
                preserve_active_mode=preserve_active_mode or specialized_fallback,
            ),
            ui_hints={
                "preserve_active_docs": bool(
                    has_any_docs and (intent in {"greeting", "document_question"} or specialized_fallback)
                )
            },
        )

    executor = _EXECUTOR_BY_ROUTE[inferred_intent]
    specialized_fallback = runtime_mode == "specialized_tasks" and inferred_intent == "general_chat"
    preserve_active_mode = bool(inferred_intent == "greeting" and has_any_docs)
    return _response(
        trace_id=trace_id,
        mode=runtime_mode,
        route=inferred_intent,
        executor=executor,
        confidence=0.0,
        margin=0.0,
        reason="specialized_fallback_chat" if specialized_fallback else "minimal_fallback",
        session_state_patch=_build_session_state_patch(
            trace_id=trace_id,
            route=inferred_intent,
            executor=executor,
            active_mode=None if (preserve_active_mode or specialized_fallback) else _ACTIVE_MODE_BY_ROUTE[inferred_intent],
            preserve_active_mode=preserve_active_mode or specialized_fallback,
        ),
        ui_hints={
            "preserve_active_docs": bool(
                has_any_docs and (inferred_intent in {"greeting", "document_question"} or specialized_fallback)
            )
        },
    )
