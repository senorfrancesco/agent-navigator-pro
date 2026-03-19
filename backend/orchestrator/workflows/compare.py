"""
Workflow: Compare Documents
Логика сравнения двух юридических документов с использованием LangGraph и микросервисов.
"""

import json
import logging
import os
import re
import asyncio
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

# Настройки URL серверов (через переменные окружения)
MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
MCP_LEGAL_SERVER_URL = os.getenv("MCP_LEGAL_SERVER_URL", "http://localhost:8002")
UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")

BATCH_SIZE = 5  # Кол-во различий в одном LLM-вызове (batch analysis)
COMPARE_TRUNCATE_CHARS = int(os.getenv("COMPARE_TRUNCATE_CHARS", "2000"))
COMPARE_SECTION_MAX_CHARS = int(os.getenv("COMPARE_SECTION_MAX_CHARS", "2000"))
COMPARE_MIN_CHUNK_CHARS = int(os.getenv("COMPARE_MIN_CHUNK_CHARS", "40"))
COMPARE_ANALYSIS_MAX_TOKENS = int(os.getenv("COMPARE_ANALYSIS_MAX_TOKENS", "600"))
COMPARE_ANALYSIS_TEMPERATURE = float(os.getenv("COMPARE_ANALYSIS_TEMPERATURE", "0.1"))
logger = logging.getLogger("compare_workflow")
_LEGAL_COMPARE_SELECTION = resolve_model_selection("llm.legal_compare")


async def _infer_compare_llm(prompt: str, payload: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
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
            "fallback_stage": "compare_analysis",
            "fallback_used": False,
            "status": "completed",
            "source": "workflow",
        }
    return response, model_execution

# === State Definition ===

class CompareState(TypedDict):
    input_1: str  # Путь к старому файлу
    input_2: str  # Путь к новому файлу
    name_1: str   # Оригинальное имя старого файла
    name_2: str   # Оригинальное имя нового файла
    text_1: str
    text_2: str
    document_role_1: str
    document_role_2: str
    base_document_title_1: str
    base_document_title_2: str
    pair_relation_type: str
    compare_mode_selected: str
    semantic_fallback_triggered: bool
    chunks_old: List[str]
    chunks_new: List[str]
    matches: List[Dict[str, Any]]
    analysis_results: List[Any]
    final_report: str
    errors: List[str]
    model_execution: Annotated[List[Dict[str, Any]], operator.add]
    session_id: str  # Привязка workflow к сессии (для дедупликации)

# === Nodes ===

def truncate_text(text: str, max_chars: int = COMPARE_TRUNCATE_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(' ', 1)[0] + "..."

def dc_create_prompt(old, new):
    return f"""<|im_start|>system
Ты эксперт-юрист.<|im_end|>
<|im_start|>user
Сравни тексты.
СТАРЫЙ: {truncate_text(old, COMPARE_TRUNCATE_CHARS)}
НОВЫЙ: {truncate_text(new, COMPARE_TRUNCATE_CHARS)}
Найди юридические изменения (сроки, права, обязанности, штрафы). Игнорируй стиль.
Ответ JSON: {{"is_critical": true/false, "diff": "описание изменения", "impact": "последствия"}}<|im_end|>
<|im_start|>assistant
"""


def _detect_document_role(name: str, text: str) -> str:
    haystack = f"{name}\n{text[:4000]}".lower()
    policy_keywords = [
        "положение",
        "регламент",
        "правила",
        "локальный нормативный",
        "локальный акт",
        "порядок взаимодействия",
    ]
    contract_keywords = [
        "трудовой договор",
        "договор",
        "работодатель",
        "работник",
        "стороны",
        "условия договора",
    ]

    policy_score = sum(1 for keyword in policy_keywords if keyword in haystack)
    contract_score = sum(1 for keyword in contract_keywords if keyword in haystack)

    if policy_score > contract_score and policy_score > 0:
        return "policy"
    if contract_score > policy_score and contract_score > 0:
        return "contract"
    return "other"


def _extract_base_document_title(text: str) -> str:
    head = text[:6000]
    patterns = [
        r"Внести\s+в\s+Закон\s+Республики\s+Беларусь[^«]{0,200}«([^»]{5,200})»",
        r"Об\s+изменении\s+Закона\s+Республики\s+Беларусь\s+«([^»]{5,200})»",
    ]
    for pattern in patterns:
        match = re.search(pattern, head, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _normalize_legal_title(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


def _determine_compare_mode(
    role_1: str,
    role_2: str,
    name_1: str,
    name_2: str,
    text_1: str = "",
    text_2: str = "",
) -> tuple[str, str]:
    base_title_1 = _normalize_legal_title(_extract_base_document_title(text_1))
    base_title_2 = _normalize_legal_title(_extract_base_document_title(text_2))
    if base_title_1 and base_title_1 == base_title_2:
        return "same_base_law_amendments", "semantic_compare"
    if role_1 == "policy" and role_2 == "contract":
        return "policy_vs_contract", "heterogeneous_alignment"
    if role_1 == "contract" and role_2 == "policy":
        return "policy_vs_contract", "heterogeneous_alignment"
    if role_1 == role_2 and role_1 in {"policy", "contract"}:
        normalized_1 = re.sub(r"\W+", " ", name_1.lower()).strip()
        normalized_2 = re.sub(r"\W+", " ", name_2.lower()).strip()
        if normalized_1 and normalized_2 and normalized_1 != normalized_2:
            return "same_genre_legal_compare", "semantic_compare"
        return "same_document_revision", "redline_compare"
    return "unknown", "semantic_compare"


def _resolve_match_threshold(state: CompareState) -> float:
    if state.get("pair_relation_type") == "same_base_law_amendments":
        return 0.64
    if state.get("compare_mode_selected") == "semantic_compare":
        return 0.68
    return 0.72


def _append_structural_result(results: List[Dict[str, Any]], match: Dict[str, Any]) -> None:
    content = match.get("new_text", "") if match.get("type") == "ADDED" else match.get("old_text", "")
    results.append({"type": match["type"], "diff": "Структурное изменение", "content": content})


def _build_compare_batch_items_text(batch: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for idx, item in enumerate(batch, start=1):
        diff_type = item.get("type", "MODIFIED")
        if diff_type == "ADDED":
            parts.append(
                f"\n[{idx}] TYPE=ADDED\n"
                f"ДОБАВЛЕНО: {truncate_text(item.get('new_text', ''), 800)}\n"
            )
        elif diff_type == "DELETED":
            parts.append(
                f"\n[{idx}] TYPE=DELETED\n"
                f"УДАЛЕНО: {truncate_text(item.get('old_text', ''), 800)}\n"
            )
        else:
            parts.append(
                f"\n[{idx}] TYPE=MODIFIED\n"
                f"СТАРЫЙ: {truncate_text(item.get('old_text', ''), 800)}\n"
                f"НОВЫЙ: {truncate_text(item.get('new_text', ''), 800)}\n"
            )
    return "".join(parts)


def _build_heterogeneous_compare_prompt(
    state: CompareState,
    structural: List[Dict[str, Any]],
    llm_candidates: List[Dict[str, Any]],
) -> str:
    structural_lines = []
    for idx, item in enumerate(structural[:8], start=1):
        snippet = item.get("new_text", "") if item.get("type") == "ADDED" else item.get("old_text", "")
        structural_lines.append(
            f"[{idx}] {item.get('type')}: {truncate_text(snippet, 400)}"
        )
    for idx, item in enumerate(llm_candidates[:4], start=len(structural_lines) + 1):
        structural_lines.append(
            f"[{idx}] MODIFIED:\n"
            f"Было: {truncate_text(item.get('old_text', ''), 250)}\n"
            f"Стало: {truncate_text(item.get('new_text', ''), 250)}"
        )

    relation_type = state.get("pair_relation_type", "unknown")
    role_1 = state.get("document_role_1", "other")
    role_2 = state.get("document_role_2", "other")
    base_title_1 = state.get("base_document_title_1", "")
    base_title_2 = state.get("base_document_title_2", "")
    diffs_block = "\n".join(structural_lines) if structural_lines else "Явных структурных различий не выделено."

    return f"""<|im_start|>system
Ты эксперт по юридическому анализу документов. Твоя задача — не делать line-by-line redline, а объяснить юридическое соотношение документов по смыслу.
Если документы вносят изменения в один и тот же базовый акт, выдели совпадающие темы, новые блоки регулирования и нормы, которые могли быть перенесены или переформулированы.
Если документы относятся к разным объектам регулирования и прямого сопоставления нет, скажи это прямо.
Верни JSON объект с полями:
{{
  "relation_summary": "краткий вывод",
  "key_findings": ["..."],
  "coverage_gaps": ["..."],
  "conflicts": ["..."],
  "recommended_actions": ["..."]
}}
<|im_end|>
<|im_start|>user
Сравни юридически связанные документы.
Тип пары: {relation_type}
Документ 1: {state.get('name_1')} (role={role_1})
Документ 2: {state.get('name_2')} (role={role_2})
Базовый акт 1: {base_title_1 or "не определён"}
Базовый акт 2: {base_title_2 or "не определён"}

Краткое содержание документа 1:
{truncate_text(state.get('text_1', ''), 1800)}

Краткое содержание документа 2:
{truncate_text(state.get('text_2', ''), 1800)}

Representative differences / alignments:
{diffs_block}

Сделай вывод по существу:
- как документы соотносятся юридически;
- какие темы покрыты обоими документами;
- что отсутствует в одном документе по отношению к другому;
- есть ли потенциальные конфликты;
- какие действия стоит предпринять.
<|im_end|>
<|im_start|>assistant
"""


async def _run_heterogeneous_compare_analysis(
    state: CompareState,
    structural: List[Dict[str, Any]],
    llm_candidates: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    prompt = _build_heterogeneous_compare_prompt(state, structural, llm_candidates)
    payload = {
        "prompt": prompt,
        "max_tokens": COMPARE_ANALYSIS_MAX_TOKENS,
        "temperature": COMPARE_ANALYSIS_TEMPERATURE,
        "echo": False,
    }
    response, model_execution = await _infer_compare_llm(prompt, payload)

    content = response.get("content", "")
    if not content and "choices" in response:
        content = response["choices"][0].get("text", "")
    elif not content and "result" in response and "choices" in response.get("result", {}):
        content = response["result"]["choices"][0].get("text", "")

    parsed = parse_json_garbage(content)
    if not isinstance(parsed, dict):
        parsed = {}

    result_items: List[Dict[str, Any]] = []
    result_items.append(
        {
            "type": "LEGAL_SUMMARY",
            "relation_summary": parsed.get(
                "relation_summary",
                "Документы связаны по предмету, но требуют дополнительной юридической сверки по покрытию обязательств и процедур.",
            ),
            "key_findings": parsed.get("key_findings", []),
            "coverage_gaps": parsed.get("coverage_gaps", []),
            "conflicts": parsed.get("conflicts", []),
            "recommended_actions": parsed.get("recommended_actions", []),
        }
    )
    for item in parsed.get("coverage_gaps", []) or []:
        result_items.append({"type": "COVERAGE_GAP", "content": item})
    for item in parsed.get("conflicts", []) or []:
        result_items.append({"type": "CONFLICT", "content": item})
    for item in parsed.get("recommended_actions", []) or []:
        result_items.append({"type": "RECOMMENDED_ACTION", "content": item})
    return result_items, model_execution

async def load_documents_node(state: CompareState):
    """Загружает и разбивает документы на чанки через Document Server."""
    print(f"[Workflow] Loading documents: {state['input_1']} and {state['input_2']}")
    
    client = await get_shared_client()
    try:
        # Загружаем первый документ
        resp1 = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": state['input_1']})
        resp1.raise_for_status()
        text1 = resp1.json().get("text", "")
        
        # Загружаем второй документ
        resp2 = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": state['input_2']})
        resp2.raise_for_status()
        text2 = resp2.json().get("text", "")
        
        # Разбиваем на чанки
        def dc_smart_chunk(text: str) -> List[str]:
            chunks = []
            section_pattern = r'\n(?=\d+\.(?:\d+\.)*\s+[А-ЯA])'
            sections = re.split(section_pattern, text)
            for section in sections:
                if len(section) > COMPARE_SECTION_MAX_CHARS:
                    parts = re.split(r'\n\s*\n', section)
                    for p in parts:
                        clean = ' '.join(p.split())
                        if len(clean) > COMPARE_MIN_CHUNK_CHARS: chunks.append(clean)
                else:
                    clean = ' '.join(section.split())
                    if len(clean) > COMPARE_MIN_CHUNK_CHARS: chunks.append(clean)
            return chunks

        chunks_old = dc_smart_chunk(text1)
        chunks_new = dc_smart_chunk(text2)
        role_1 = _detect_document_role(state.get("name_1") or os.path.basename(state["input_1"]), text1)
        role_2 = _detect_document_role(state.get("name_2") or os.path.basename(state["input_2"]), text2)
        base_title_1 = _extract_base_document_title(text1)
        base_title_2 = _extract_base_document_title(text2)
        pair_relation_type, compare_mode_selected = _determine_compare_mode(
            role_1,
            role_2,
            state.get("name_1") or os.path.basename(state["input_1"]),
            state.get("name_2") or os.path.basename(state["input_2"]),
            text1,
            text2,
        )
        print(f"[Workflow] Chunks: old={len(chunks_old)}, new={len(chunks_new)}")
        return {
            "text_1": text1,
            "text_2": text2,
            "document_role_1": role_1,
            "document_role_2": role_2,
            "base_document_title_1": base_title_1,
            "base_document_title_2": base_title_2,
            "pair_relation_type": pair_relation_type,
            "compare_mode_selected": compare_mode_selected,
            "semantic_fallback_triggered": False,
            "chunks_old": chunks_old,
            "chunks_new": chunks_new,
        }
    except Exception as e:
        logger.warning("Compare load_documents fallback: %s", e, exc_info=True)
        inc_metric_counter(
            "agent_nav_fallback_events_total",
            labels={"component": "compare_workflow", "fallback": "load_documents_failed", "source": "workflow"},
        )
        return {"errors": [f"Error loading docs: {str(e)}"]}

async def match_chunks_node(state: CompareState):
    """Сопоставляет чанки через Legal Server (батчевое семантическое сходство)."""
    print(f"[Workflow] Matching batches: {len(state['chunks_old'])} vs {len(state['chunks_new'])}")
    
    if not state['chunks_old'] or not state['chunks_new']:
        return {"matches": []}

    client = await get_shared_client()
    try:
        threshold = _resolve_match_threshold(state)
        # Вызываем новый батчевый эндпоинт
        resp = await client.post(f"{MCP_LEGAL_SERVER_URL}/match_batches", json={
            "list_old": state['chunks_old'],
            "list_new": state['chunks_new'],
            "threshold": threshold,
        })
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "error":
            raise RuntimeError(data.get("error", "Unknown error from legal server"))

        # Фильтруем результаты (оставляем только измененные, удаленные или добавленные)
        all_matches = data.get("matches", [])
        diffs = [m for m in all_matches if m["type"] != "UNCHANGED"]

        print(f"   - Найдено различий: {len(diffs)}")
        return {"matches": diffs}
    except Exception as e:
        print(f"Error in match_batches workflow: {e}")
        logger.warning("Compare match_batches fallback: %s", e, exc_info=True)
        inc_metric_counter(
            "agent_nav_fallback_events_total",
            labels={"component": "compare_workflow", "fallback": "match_batches_failed", "source": "workflow"},
        )
        return {"errors": [f"Error in batch matching: {str(e)}"]}

async def analyze_differences_node(state: CompareState):
    """Анализирует найденные различия через LLM (UMS) — batch по BATCH_SIZE штук за вызов."""
    # Ранний выход если на предыдущем шаге была ошибка
    if state.get('errors'):
        print(f"[Workflow] Skipping analyze due to errors: {state['errors']}")
        return {"analysis_results": []}

    print(f"[Workflow] Analyzing {len(state['matches'])} differences (batch_size={BATCH_SIZE})")

    results = []
    model_execution_events: List[Dict[str, Any]] = []
    critical_kw = ["обязан", "штраф", "срок", "рублей", "не вправе", "запрещено"]

    # Разделяем на структурные (ADDED/DELETED) и требующие LLM анализа (MODIFIED)
    structural = []
    to_analyze = []
    for m in state['matches']:
        if m.get('type') in ['ADDED', 'DELETED']:
            structural.append(m)
        else:
            txt = (m.get('old_text', '') + m.get('new_text', '')).lower()
            score = m.get('similarity_score', m.get('score', 0))
            if score < 0.9 or any(k in txt for k in critical_kw):
                to_analyze.append(m)

    semantic_fallback_triggered = False

    print(f"[Workflow] Structural: {len(structural)}, needs LLM: {len(to_analyze)}")

    compare_mode_selected = state.get("compare_mode_selected", "redline_compare")
    role_1 = state.get("document_role_1", "other")
    role_2 = state.get("document_role_2", "other")
    should_run_semantic_fallback = (
        compare_mode_selected == "heterogeneous_alignment"
        or state.get("pair_relation_type") == "same_base_law_amendments"
        or (
            compare_mode_selected == "semantic_compare"
            and len(structural) >= max(1, len(to_analyze))
        )
        or (role_1 != role_2 and len(structural) > 0 and len(to_analyze) == 0)
    )

    if should_run_semantic_fallback:
        try:
            semantic_results, model_execution = await _run_heterogeneous_compare_analysis(
                state,
                structural,
                to_analyze,
            )
            results.extend(semantic_results)
            semantic_fallback_triggered = True
            if model_execution:
                model_execution_events.append(model_execution)
        except Exception as e:
            logger.warning("Compare semantic fallback failed: %s", e, exc_info=True)
            inc_metric_counter(
                "agent_nav_fallback_events_total",
                labels={"component": "compare_workflow", "fallback": "semantic_compare_error", "source": "workflow"},
            )

    semantic_candidates = structural + to_analyze

    # Batch LLM анализ: по BATCH_SIZE различий в одном промпте
    total_batches = (len(semantic_candidates) + BATCH_SIZE - 1) // BATCH_SIZE if semantic_candidates else 0
    for batch_idx, batch_start in enumerate(range(0, len(semantic_candidates), BATCH_SIZE)):
        batch = semantic_candidates[batch_start:batch_start + BATCH_SIZE]
        print(f"[Workflow] LLM batch {batch_idx+1}/{total_batches} ({len(batch)} diffs)")

        items_text = _build_compare_batch_items_text(batch)

        prompt = f"""<|im_start|>system
Ты эксперт-юрист. Проанализируй {len(batch)} изменений в документе.
Для каждого изменения объясни юридический смысл, даже если это просто добавленный или удалённый фрагмент.
Верни JSON массив из ровно {len(batch)} объектов с полями:
is_critical, diff, impact.<|im_end|>
<|im_start|>user
Для каждого из {len(batch)} изменений определи юридическую суть.
{items_text}
Ответ — JSON массив из ровно {len(batch)} объектов:
[{{"is_critical": true/false, "diff": "суть изменения", "impact": "последствия"}}]<|im_end|>
<|im_start|>assistant
"""
        try:
            payload = {
                "prompt": prompt,
                "max_tokens": COMPARE_ANALYSIS_MAX_TOKENS,
                "temperature": COMPARE_ANALYSIS_TEMPERATURE,
                "echo": False,
            }
            response, model_execution = await _infer_compare_llm(prompt, payload)

            content = response.get("content", "")
            if not content and "choices" in response:
                content = response["choices"][0].get("text", "")
            elif not content and "result" in response and "choices" in response.get("result", {}):
                content = response["result"]["choices"][0].get("text", "")

            parsed = parse_json_garbage(content)
            if not isinstance(parsed, list):
                parsed = [parsed] if isinstance(parsed, dict) else []
            if len(parsed) < len(batch):
                logger.warning(
                    "Compare analyze fallback: structured output count mismatch batch=%s parsed=%s",
                    len(batch),
                    len(parsed),
                )
                inc_metric_counter(
                    "agent_nav_fallback_events_total",
                    labels={"component": "compare_workflow", "fallback": "analyze_parse_partial", "source": "workflow"},
                )

            for idx, m in enumerate(batch):
                item_data = parsed[idx] if idx < len(parsed) else {}
                diff_type = m.get("type", "MODIFIED")
                if item_data and (item_data.get("is_critical") or len(item_data.get("diff", " ")) > 5):
                    payload = {
                        "type": diff_type,
                        "is_critical": item_data.get("is_critical"),
                        "diff": item_data.get("diff"),
                        "impact": item_data.get("impact"),
                        "old_text": m.get('old_text', ''),
                        "new_text": m.get('new_text', ''),
                    }
                    if diff_type in {"ADDED", "DELETED"}:
                        payload["content"] = m.get('new_text', '') if diff_type == "ADDED" else m.get('old_text', '')
                    results.append(payload)
                elif diff_type in {"ADDED", "DELETED"}:
                    _append_structural_result(results, m)

            if model_execution:
                model_execution_events.append(model_execution)

        except Exception as e:
            print(f"[Workflow] Error in batch {batch_idx+1}: {e}")
            logger.warning("Compare analyze fallback in batch %s: %s", batch_idx + 1, e, exc_info=True)
            inc_metric_counter(
                "agent_nav_fallback_events_total",
                labels={"component": "compare_workflow", "fallback": "analyze_batch_error", "source": "workflow"},
            )
            for m in batch:
                diff_type = m.get("type", "MODIFIED")
                if diff_type in {"ADDED", "DELETED"}:
                    _append_structural_result(results, m)
                else:
                    results.append({
                        "type": "MODIFIED",
                        "is_critical": False,
                        "diff": "Ошибка анализа",
                        "impact": "Требуется ручной анализ",
                        "old_text": m.get('old_text', ''),
                        "new_text": m.get('new_text', '')
                    })

    return {
        "analysis_results": results,
        "model_execution": model_execution_events,
        "semantic_fallback_triggered": semantic_fallback_triggered,
    }

async def generate_report_node(state: CompareState):
    """Формирует финальный Markdown отчет и сохраняет его."""
    import time
    import glob as glob_mod

    name_1 = state.get('name_1') or os.path.basename(state['input_1'])
    name_2 = state.get('name_2') or os.path.basename(state['input_2'])

    report = "# Отчет о сравнении документов\n\n"
    report += f"**Дата:** {time.strftime('%Y-%m-%d %H:%M')}\n"
    report += f"**Файлы:**\n- Старая версия: {name_1}\n- Новая версия: {name_2}\n\n"
    report += f"**Найдено изменений:** {len(state['analysis_results'])}\n\n"
    legal_summaries = [r for r in state["analysis_results"] if r.get("type") == "LEGAL_SUMMARY"]
    if legal_summaries:
        summary = legal_summaries[0]
        report += "## Юридический вывод\n"
        report += f"{summary.get('relation_summary', 'Юридический вывод не сформирован.')}\n\n"

        key_findings = summary.get("key_findings", []) or []
        if key_findings:
            report += "## Ключевые смысловые различия\n"
            for item in key_findings:
                report += f"- {item}\n"
            report += "\n"

        coverage_gaps = summary.get("coverage_gaps", []) or []
        if coverage_gaps:
            report += "## Что отсутствует / требует отражения\n"
            for item in coverage_gaps:
                report += f"- {item}\n"
            report += "\n"

        conflicts = summary.get("conflicts", []) or []
        if conflicts:
            report += "## Возможные риски / конфликты\n"
            for item in conflicts:
                report += f"- {item}\n"
            report += "\n"

        recommended_actions = summary.get("recommended_actions", []) or []
        if recommended_actions:
            report += "## Рекомендуемые действия\n"
            for item in recommended_actions:
                report += f"- {item}\n"
            report += "\n"

    report += "## Приложение: различия по пунктам\n\n"
    for r in state['analysis_results']:
        diff_type = r.get('type', 'MODIFIED')

        if diff_type == 'MODIFIED':
            icon = "🔴 КРИТИЧНО" if r.get('is_critical') else "📝 ИЗМЕНЕНО"
            report += f"### {icon}\n"
            report += f"**Суть:** {r.get('diff', 'Изменение текста')}\n"
            if r.get('impact'):
                report += f"**Влияние:** {r.get('impact')}\n"
            report += f"> **Было:** {r.get('old_text', '')[:200]}...\n"
            report += f"> **Стало:** {r.get('new_text', '')[:200]}...\n\n"
        elif diff_type == 'ADDED':
            report += "### ✅ ДОБАВЛЕНО\n"
            if r.get('diff'):
                report += f"**Суть:** {r.get('diff')}\n"
            if r.get('impact'):
                report += f"**Влияние:** {r.get('impact')}\n"
            report += f"> {r.get('content', '')[:200]}...\n\n"
        elif diff_type == 'DELETED':
            report += "### ❌ УДАЛЕНО\n"
            if r.get('diff'):
                report += f"**Суть:** {r.get('diff')}\n"
            if r.get('impact'):
                report += f"**Влияние:** {r.get('impact')}\n"
            report += f"> {r.get('content', '')[:200]}...\n\n"

    # Сохранение в файл (с проверкой дубликатов через общую утилиту)
    try:
        from orchestrator.shared.report_utils import save_report_with_dedup
        final_report_text = save_report_with_dedup(
            report_text=report,
            prefix="Report_Compare",
            input_names=[name_1, name_2],
            current_metric=len(state['analysis_results']),
            metric_marker="Найдено изменений:**"
        )
        return {"final_report": final_report_text}
    except Exception as e:
        report += f"\n---\n**Ошибка сохранения отчета:** {e}"
        return {"final_report": report}

# === Build Graph ===

def create_compare_graph():
    workflow = StateGraph(CompareState)
    
    workflow.add_node("load", load_documents_node)
    workflow.add_node("match", match_chunks_node)
    workflow.add_node("analyze", analyze_differences_node)
    workflow.add_node("report", generate_report_node)
    
    workflow.set_entry_point("load")
    workflow.add_edge("load", "match")
    workflow.add_edge("match", "analyze")
    workflow.add_edge("analyze", "report")
    workflow.add_edge("report", END)
    
    return workflow.compile()
