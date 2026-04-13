"""
Workflow: Equipment Analysis (ТЗ vs Смета / Смета vs Смета)

Конвейер с conditional edges:
  extract → [route] → match → evaluate → report
                  ↘ (items пусто) → report (ошибка)

Двухпроходная экстракция: структурные таблицы (без LLM) + LLM текстовые позиции.
Семантический матчинг через Legal Server (multilingual embedding + Hungarian).
Batch LLM evaluation по BATCH_SIZE=5.
"""

import html
import json
import logging
import os
import re
import time
import httpx
import asyncio
from typing import TypedDict, List, Dict, Any, Annotated, Optional, Callable, Awaitable
import operator
from langgraph.graph import StateGraph, END

# Абсолютные импорты пакета (TD-5 Fix)
from services.model_manager.model_selection import resolve_model_selection
from services.model_manager.ums_client import ums_client
from services.observability import inc_metric_counter
from orchestrator.structured_output import extract_model_text, parse_strict_json
from orchestrator.utils import parse_json_garbage
from orchestrator.shared.http_client import get_shared_client
from orchestrator.equipment_parsing import (
    extract_items_docx_fallback as _extract_items_docx_fallback,
    load_docx_elements as _load_docx_elements,
    parse_generic_list_like_elements,
    parse_offer_like_elements,
    parse_specification_like_elements,
    score_document_role,
)
from orchestrator.telemetry_runtime import record_current_duration

# URLs серверов
MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
MCP_LEGAL_SERVER_URL = os.getenv("MCP_LEGAL_SERVER_URL", "http://localhost:8002")
UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")

BATCH_SIZE = 5              # Позиций в одном LLM eval-вызове
BATCH_POLISH = 3            # Позиций в одном LLM polisher-вызове
POLISH_MAX_BATCH_CHARS = 3000  # Бюджет XML payload для одного polisher-батча
MATCH_SIMILARITY_THRESHOLD = 0.45  # Min cosine similarity для equipment matching
MAX_TEXT_FOR_LLM = 6000     # Лимит символов текста для LLM-экстракции (~2000 токенов)
CHUNK_MAX_TOKENS = 2000     # токенов на чанк (~6000 символов) — безопасно для 16K ctx
CHUNK_OVERLAP = 150         # overlap токенов между чанками

# Ключевые слова для mode detection
_TZ_KEYWORDS = ["тз", "техническое задание", "требовани", "specification", "техзадани"]
_SMETA_KEYWORDS = ["смета", "прайс", "предложение", "кп", "коммерческое"]


# Ключевые слова для content-based классификации документов
_TZ_TEXT_KEYWORDS = [
    "техническое задание", "предмет закупки", "требования к поставляемому",
    "требуемый параметр", "технические характеристики", "тз на закупку",
    "требуемое значение", "техзадание",
]
_KP_TEXT_KEYWORDS = [
    "коммерческое предложение", "цена, руб", "стоимость", "прайс",
    "предложение действительно", "итого:", "ндс", "стоимость с ндс",
    "коммерческое", "quotation",
]

logger = logging.getLogger("equipment_workflow")
_LEGAL_COMPARE_SELECTION = resolve_model_selection("llm.legal_compare")


def _is_model_failover_blocked(exc: Exception) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return True
    if isinstance(exc, UMSBusyError):
        return True
    message = str(exc).lower()
    return "429" in message or "busy" in message or "cancel" in message


def _build_model_execution_event(
    *,
    selection: Any,
    used_model_id: str,
    fallback_used: bool,
    fallback_reason: Optional[str],
    attempt_count: int,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    payload = dict(selection.to_dict() if hasattr(selection, "to_dict") else selection or {})
    payload.update(
        {
            "primary_model_id": payload.get("resolved_model_id") or payload.get("primary_model_id"),
            "fallback_model_id": payload.get("fallback_model_id") if payload.get("fallback_available") else None,
            "used_model_id": used_model_id,
            "fallback_used": bool(fallback_used),
            "fallback_reason": fallback_reason,
            "fallback_stage": stage,
            "attempt_count": max(1, int(attempt_count)),
            "status": "fallback_completed" if fallback_used else "completed",
        }
    )
    return payload


async def _infer_equipment_llm_with_failover(
    *,
    stage: str,
    prompt: str,
    payload: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    started = time.monotonic()
    response = await ums_client.async_infer(
        "llm.legal_compare",
        {
            **payload,
            "prompt": prompt,
        },
    )
    record_current_duration(
        name="ums_equipment_infer",
        elapsed_seconds=time.monotonic() - started,
        kind="tool",
        category="llm",
        meta={"stage": stage},
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


def detect_equipment_mode(
    file1_name: str,
    file2_name: str,
    query: str,
    text_1: str = "",
    text_2: str = "",
) -> str:
    """Эвристика: tz_vs_smeta или smeta_vs_smeta.

    Уровень 1: имена файлов
    Уровень 2: content-based (первые 1000 символов текста)
    Уровень 3: запрос
    """
    f1 = file1_name.lower()
    f2 = file2_name.lower()
    q = query.lower()

    # Уровень 1: имена файлов
    if any(kw in f1 for kw in _TZ_KEYWORDS) or any(kw in f2 for kw in _TZ_KEYWORDS):
        return "tz_vs_smeta"
    if any(kw in f1 for kw in _SMETA_KEYWORDS) and any(kw in f2 for kw in _SMETA_KEYWORDS):
        return "smeta_vs_smeta"

    # Уровень 2: content-based классификация текста
    def _classify_text(text: str) -> str:
        t = text[:1000].lower()
        tz_score = sum(1 for kw in _TZ_TEXT_KEYWORDS if kw in t)
        kp_score = sum(1 for kw in _KP_TEXT_KEYWORDS if kw in t)
        if tz_score > kp_score:
            return "tz"
        if kp_score > tz_score:
            return "kp"
        return "unknown"

    if text_1 or text_2:
        type1 = _classify_text(text_1)
        type2 = _classify_text(text_2)
        if type1 == "tz" or type2 == "tz":
            return "tz_vs_smeta"
        if type1 == "kp" and type2 == "kp":
            return "smeta_vs_smeta"

    # Уровень 3: запрос
    if "сравни смет" in q or "сравнение смет" in q:
        return "smeta_vs_smeta"
    return "tz_vs_smeta"


# === State Definition ===

class EquipmentState(TypedDict):
    # Inputs
    input_1: str              # Путь к документу 1 (ТЗ или Смета-old)
    input_2: str              # Путь к документу 2 (Смета)
    name_1: str               # Оригинальное имя файла 1
    name_2: str               # Оригинальное имя файла 2
    mode: str                 # "tz_vs_smeta" | "smeta_vs_smeta"

    # Extraction
    items_1: List[Dict[str, Any]]  # Позиции из документа 1
    items_2: List[Dict[str, Any]]  # Позиции из документа 2

    # Matching
    matches: List[Dict[str, Any]]  # Результаты Legal Server

    # Evaluation
    analysis_results: List[Dict[str, Any]]  # LLM verdicts

    # Output
    final_report: str
    extraction_metadata: Dict[str, Any]
    model_execution: Annotated[List[Dict[str, Any]], operator.add]
    errors: Annotated[List[str], operator.add]  # Накопление через reducer
    runtime_context: Dict[str, Any]
    session_id: str


# === Config & Constants ===

def _load_parsers_config() -> Dict:
    path = os.path.join(os.path.dirname(__file__), "..", "data", "parsers_config.yaml")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                import yaml
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {}

_CONFIG = _load_parsers_config()
_HEADER_KEYWORDS = _CONFIG.get("header_keywords", {})
_GARBAGE_VALUES = _CONFIG.get("garbage_values", [])


def _get_runtime_context(state: EquipmentState) -> Dict[str, Any]:
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


def _detect_header_columns(header_row: List[str]) -> Dict[str, int]:
    """Детектирует маппинг колонок по ключевым словам из конфига."""
    mapping = {}
    for col_idx, cell in enumerate(header_row):
        if cell is None:
            continue
        cell_lower = str(cell).lower().strip()
        if not cell_lower:
            continue
        for field, keywords in _HEADER_KEYWORDS.items():
            if field not in mapping and any(kw in cell_lower for kw in keywords):
                mapping[field] = col_idx
                break
    return mapping


# Ключевые слова для ТЗ-колонок (merged-cell структура)
_TZ_COL_KEYWORDS = {
    "num":   ["№", "номер", "n", "п/п"],
    "name":  ["наименование", "товар", "артикул", "оборудование"],
    "qty":   ["количество", "кол-во", "кол.", "шт"],
    "param": ["требуемый параметр", "параметр", "характеристика", "требуем",
              "технические характе"],   # ← добавлено для этого PDF
    "value": ["требуемое значение", "значение", "требование", "требуемое"],
    "unit":  ["ед. изм", "единица", "ед.изм", "ед."],
}


def _is_tz_structure(header_rows: List[List]) -> bool:
    """Детектирует ТЗ-структуру (merged-cells) по 2-3 строкам заголовка таблицы."""
    if not header_rows:
        return False
    raw_parts = []
    for row in header_rows[:3]:
        raw_parts.extend(str(c) for c in row if c)
    raw = "".join(raw_parts).lower()
    
    # Чтобы считать таблицу ТЗ-структурой, она должна иметь колонки типа "Параметр" и "Значение" (или "Требование" и "Соответствие").
    # Просто слова "Характеристики" недостаточно (это может быть обычная смета).
    has_param_col = any(kw in raw for kw in ["параметр", "требован"])
    has_value_col = any(kw in raw for kw in ["значение", "соответствие", "результат"])
    
    return has_param_col and has_value_col


def _detect_tz_columns(header_rows: List[List]) -> Dict[str, int]:
    """Строит col_map для ТЗ-таблицы, анализируя несколько строк заголовка."""
    mapping = {}
    
    # Объединяем первые 2-3 строки в один "супер-заголовок" для поиска колонок
    combined_cells = []
    num_cols = len(header_rows[0]) if header_rows else 0
    for col_idx in range(num_cols):
        cells = []
        for row in header_rows[:3]:
            if col_idx < len(row) and row[col_idx]:
                cells.append(str(row[col_idx]).lower().replace('\n', ' '))
        combined_cells.append(" ".join(cells))

    # Ищем колонки в объединенном тексте
    for col_idx, cell_text in enumerate(combined_cells):
        for field, keywords in _TZ_COL_KEYWORDS.items():
            if field not in mapping and any(kw in cell_text for kw in keywords):
                mapping[field] = col_idx
                break
    
    # Фолбэк для этого конкретного PDF: если нашли "технические характер" в колокне N, 
    # а следующая N+1 пустая или "значение"
    if "param" not in mapping:
        for idx, text in enumerate(combined_cells):
            if "характе" in text:
                mapping["param"] = idx
                if idx + 1 < len(combined_cells) and "value" not in mapping:
                    mapping["value"] = idx + 1
                break

    return mapping


_GARBAGE_SPEC_VALUES = {'nan', 'none', '-', '.', '..', 'да', 'есть', 'соответствие', 'соответствует'}


def _format_specs_fallback(raw_specs: List[Any]) -> str:
    """Форматирует сырые specs без LLM, фильтруя мусорные значения."""
    parts = []
    for s in raw_specs:
        if not isinstance(s, dict):
            continue
        param = str(s.get('p', '')).strip()
        value = str(s.get('v', '')).strip()
        unit = str(s.get('u', '')).strip()
        if not param or param.lower() in _GARBAGE_SPEC_VALUES:
            continue
        if not value or value.lower() in _GARBAGE_SPEC_VALUES:
            continue
        spec = f"{param}: {value}"
        if unit and unit.lower() not in _GARBAGE_SPEC_VALUES:
            spec += f" {unit}"
        parts.append(spec)
    return ", ".join(parts[:15]) if parts else ""


def _normalize_spec_for_polish(spec: Any) -> Optional[str]:
    """Нормализует одну spec-запись для LLM-polisher, убирая только явный мусор."""
    if not isinstance(spec, dict):
        return None
    param = re.sub(r"\s+", " ", str(spec.get("p", "") or "")).strip()
    value = re.sub(r"\s+", " ", str(spec.get("v", "") or "")).strip()
    unit = re.sub(r"\s+", " ", str(spec.get("u", "") or "")).strip()

    if not param or param.lower() in _GARBAGE_SPEC_VALUES:
        return None

    if not value or value.lower() in _GARBAGE_SPEC_VALUES:
        inline_match = re.match(r"^(.*?\S)\s+[–-]\s+(\S.*)$", param)
        if inline_match:
            param = f"{inline_match.group(1)}: {inline_match.group(2)}"
        return _clean_polished_spec(param)

    normalized = f"{param}: {value}"
    if unit and unit.lower() not in _GARBAGE_SPEC_VALUES:
        normalized += f" {unit}"
    return _clean_polished_spec(normalized)


def _build_polish_source_specs(raw_specs: List[Dict[str, Any]]) -> List[str]:
    """Готовит deduplicated список строк характеристик для XML-входа polisher-а."""
    prepared: List[str] = []
    seen: set[str] = set()

    for spec in raw_specs:
        normalized = _normalize_spec_for_polish(spec)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        prepared.append(normalized)

    return prepared


def _render_polish_source_item_xml(item_id: int, item_name: str, specs: List[str]) -> str:
    """Рендерит один source_item для XML prompt-а."""
    specs_xml = "\n".join(f"      <spec>{html.escape(spec)}</spec>" for spec in specs)
    return (
        f'  <source_item id="{item_id}">\n'
        f"    <name>{html.escape(item_name)}</name>\n"
        f"    <raw_specs>\n{specs_xml}\n    </raw_specs>\n"
        f"  </source_item>"
    )


def _estimate_polish_source_item_size(item: Dict[str, Any], specs: List[str]) -> int:
    """Оценивает размер XML-блока item для budget-based batching."""
    return len(_render_polish_source_item_xml(0, item.get("name", ""), specs))


def _build_polish_batches(items: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Строит батчи для polisher-а по max items и budget размера XML payload."""
    prepared_items: List[Dict[str, Any]] = []
    for item in items:
        source_specs = _build_polish_source_specs(item.get("raw_specs", []))
        prepared_items.append({
            "item": item,
            "source_specs": source_specs,
            "estimated_size": _estimate_polish_source_item_size(item, source_specs),
        })

    batches: List[List[Dict[str, Any]]] = []
    current_batch: List[Dict[str, Any]] = []
    current_chars = len("<input>\n\n</input>")

    for prepared in prepared_items:
        item_chars = prepared["estimated_size"]
        exceeds_item_limit = len(current_batch) >= BATCH_POLISH
        exceeds_char_budget = current_batch and (current_chars + item_chars > POLISH_MAX_BATCH_CHARS)

        if exceeds_item_limit or exceeds_char_budget:
            batches.append(current_batch)
            current_batch = []
            current_chars = len("<input>\n\n</input>")

        current_batch.append(prepared)
        current_chars += item_chars

    if current_batch:
        batches.append(current_batch)

    return batches


def _render_polish_input_xml(batch: List[Dict[str, Any]]) -> str:
    """Рендерит весь XML input для текущего батча c локальными batch ids."""
    rendered_items = [
        _render_polish_source_item_xml(idx, entry["item"].get("name", ""), entry["source_specs"])
        for idx, entry in enumerate(batch)
    ]
    return "<input>\n" + "\n".join(rendered_items) + "\n</input>"


async def _polish_items_specs_llm(
    items: List[Dict[str, Any]],
    model_execution_events: Optional[List[Dict[str, Any]]] = None,
):
    """
    Уровень 3: LLM Polisher.
    Превращает сырые списки характеристик в чистый технический текст.
    """
    to_polish = [it for it in items if it.get("raw_specs")]
    if not to_polish:
        return

    print(f"  [LLM Polisher] Polishing specs for {len(to_polish)} items...")
    batches = _build_polish_batches(to_polish)

    for batch_idx, batch in enumerate(batches):
        prompt_input_xml = _render_polish_input_xml(batch)
        expected_ids = list(range(len(batch)))
        batch_chars = len(prompt_input_xml)

        prompt = f"""<|im_start|>system
Ты технический эксперт. Твоя задача — очистить "сырые" характеристики оборудования.
Убери только явный мусор: пустые значения, nan, none, "-", ".", "..", "да", "есть", "соответствие", "соответствует".
Не удаляй технически значимые параметры.
Верни строго JSON в формате:
{{"schema_version":"b3.11.v1","ok":true,"data":{{"results":[{{"id":0,"text":"строка 1"}},{{"id":1,"text":"строка 2"}}]}},"error":null}}
Для каждого source_item верни ровно один result с тем же id.
Поле text должно быть краткой строкой через запятую.
Не добавляй markdown, code fences, комментарии или пояснения до и после JSON.
<|im_end|>
<|im_start|>user
Данные для очистки:
{prompt_input_xml}
<|im_end|>
<|im_start|>assistant
"""
        try:
            print(
                f"  [DEBUG-POLISH] Batch {batch_idx + 1}/{len(batches)}: "
                f"items={len(batch)}, payload_chars={batch_chars}, ids={expected_ids}"
            )
            resp, model_execution = await _infer_equipment_llm_with_failover(
                stage="polisher",
                prompt=prompt,
                payload={"temperature": 0.1, "max_tokens": 2000},
            )
            if model_execution_events is not None and model_execution:
                model_execution_events.append(model_execution)
            
            content = extract_model_text(resp)
            print(f"  [DEBUG-POLISH] LLM Response (200 chars): {content[:200]}...")
            results = _parse_polish_results_json(content, expected_ids=expected_ids)

            if results:
                for entry_idx, entry in enumerate(batch):
                    item = entry["item"]
                    result = results.get(entry_idx)
                    if result:
                        item["specs"] = result
                        print(f"  [DEBUG-POLISH] Item {entry_idx} specs updated: {item['specs'][:50]}...")
            else:
                print("  [DEBUG-POLISH] Failed to parse XML polish response.")
                logger.warning("DEBUG-POLISH returned invalid structured output; applying fallback", extra={"stage": "polisher"})
                inc_metric_counter(
                    "llm_tools_platform_equipment_fallback_total",
                    labels={"stage": "polisher", "reason": "parse_failed"},
                )
        except Exception as e:
            print(f"  [LLM Polisher] Error batch {batch_idx}: {e}")
            logger.warning("DEBUG-POLISH failed; applying fallback: %s", e, exc_info=True)
            inc_metric_counter(
                "llm_tools_platform_equipment_fallback_total",
                labels={"stage": "polisher", "reason": "llm_error"},
            )
            
        # Гарантированный Fallback для каждого айтема в батче, если specs остались пустыми
        for entry in batch:
            item = entry["item"]
            if not item.get("specs") and item.get("raw_specs"):
                item["specs"] = _format_specs_fallback(item["raw_specs"])
                print(f"  [LLM Polisher] Fallback applied for {item['name'][:30]}...")


def _clean_polished_spec(text: Any) -> str:
    """Нормализует строку характеристик после LLM-парсинга."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r'"\s*:\s*$', "", cleaned)
    cleaned = re.sub(r"\s*:\s*", ": ", cleaned)
    cleaned = cleaned.strip(' "\',;:')
    return cleaned


def _parse_polish_results_json(content: str, expected_ids: List[int]) -> Optional[Dict[int, str]]:
    """Строго парсит structured JSON ответ polisher-а по обязательному id-mapping."""
    payload = parse_strict_json(content, expected_type=dict)
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != "b3.11.v1":
        return None
    if payload.get("ok") is not True:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    items = data.get("results")
    if not isinstance(items, list):
        return None
    results: Dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            return None
        item_id = item.get("id")
        if not isinstance(item_id, int):
            return None
        if item_id in results:
            return None
        text = _clean_polished_spec(item.get("text"))
        if text:
            results[item_id] = text
        else:
            return None

    expected_id_set = set(expected_ids)
    result_id_set = set(results.keys())
    if result_id_set != expected_id_set:
        missing_ids = sorted(expected_id_set - result_id_set)
        extra_ids = sorted(result_id_set - expected_id_set)
        print(f"  [DEBUG-POLISH] Invalid XML ids: missing={missing_ids}, extra={extra_ids}")
        return None

    return results


def _normalize_evaluate_results_payload(parsed: Any) -> List[Dict[str, Any]]:
    """Нормализует LLM-ответ evaluate в плоский список объектов verdict."""
    if isinstance(parsed, dict):
        nested_results = parsed.get("results")
        if isinstance(nested_results, list):
            parsed = nested_results
        else:
            return [parsed]

    if not isinstance(parsed, list):
        return []

    normalized: List[Dict[str, Any]] = []

    def _collect(value: Any) -> None:
        if isinstance(value, dict):
            normalized.append(value)
            return
        if isinstance(value, list):
            for item in value:
                _collect(item)

    _collect(parsed)
    return normalized


def _parse_tz_table_rows(
    rows: List[List],
    col_map: Dict[str, int],
    page_info: Any = None,
) -> List[Dict[str, Any]]:
    """
    Group-by парсер для ТЗ-таблиц. Собирает ВСЕ сырые характеристики.
    """
    items = []
    current_item: Dict[str, Any] | None = None
    current_raw_specs: List[Dict] = []

    def _cell(row, key):
        idx = col_map.get(key)
        if idx is None or idx >= len(row): return None
        val = row[idx]
        return str(val).replace('\n', ' ').strip() if val is not None else None

    def _flush():
        nonlocal current_item, current_raw_specs
        if current_item is not None:
            current_item["raw_specs"] = current_raw_specs
            items.append(current_item)
        current_item = None
        current_raw_specs = []

    def _is_empty(val):
        if val is None: return True
        s = str(val).strip().lower()
        return s in ('', 'none', 'nan', '-', '.', '..')

    for row in rows:
        num_val  = _cell(row, "num")
        name_val = _cell(row, "name")
        param_val = _cell(row, "param")
        value_val = _cell(row, "value")
        unit_val = _cell(row, "unit")

        # Строка-позиция: num_val — целое число
        if num_val and re.match(r'^\d+$', num_val.strip()):
            _flush()
            qty_raw = _cell(row, "qty") or "1"
            qty_match = re.search(r'\d+', qty_raw)
            qty = int(qty_match.group()) if qty_match else 1

            current_item = {
                "name": name_val or f"Позиция {num_val}",
                "specs": "", 
                "quantity": str(qty),
                "price": "",
                "unit": unit_val or "",
                "source": "table",
                "page": page_info,
            }
            if not _is_empty(param_val):
                current_raw_specs.append({"p": param_val, "v": value_val or "", "u": unit_val or ""})

        # Строка-характеристика: num и name — пусты
        elif _is_empty(num_val) and _is_empty(name_val):
            if current_item is not None and not _is_empty(param_val):
                current_raw_specs.append({"p": param_val, "v": value_val or "", "u": unit_val or ""})

        # Продолжение названия
        elif _is_empty(num_val) and not _is_empty(name_val) and current_item is not None:
            if re.match(r"^Позиция\s+\d+$", current_item["name"].strip(), re.IGNORECASE):
                current_item["name"] = name_val
            else:
                current_item["name"] += " " + name_val
            if not _is_empty(param_val):
                current_raw_specs.append({"p": param_val, "v": value_val or "", "u": unit_val or ""})

    _flush()
    return items


def _safe_cell(row: List, col_map: Dict[str, int], field: str) -> str:
    """None-safe извлечение значения ячейки."""
    if field not in col_map or len(row) <= col_map[field]:
        return ""
    val = row[col_map[field]]
    return str(val).replace('\n', ' ').strip() if val is not None else ""


def _parse_table_rows(
    table_data: List[List[str]],
    page_info: Any = None,
    inherited_col_map: Dict[str, int] | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Парсит строки таблицы: находит заголовок, маппит колонки, извлекает позиции.
    Возвращает (items, col_map) — col_map нужен для передачи в продолжения таблицы.

    inherited_col_map — маппинг колонок от предыдущего фрагмента той же таблицы
    (когда таблица разбита по страницам и заголовок только на первой).
    """
    if not table_data:
        return [], inherited_col_map or {}

    # Ищем строку-заголовок (первую строку с >= 2 распознанных ключевых слова)
    header_idx = -1
    col_map = {}
    header_row = []
    for i, row in enumerate(table_data[:5]):  # Проверяем первые 5 строк
        candidate = _detect_header_columns(row)
        if len(candidate) >= 2:
            header_idx = i
            col_map = candidate
            header_row = row
            break

    # Если заголовок не найден — используем inherited_col_map (продолжение таблицы)
    if header_idx < 0 or "name" not in col_map:
        if inherited_col_map and "name" in inherited_col_map:
            col_map = inherited_col_map
            header_idx = -1  # Парсим с row[0]
        else:
            return [], inherited_col_map or {}

    start_row = header_idx + 1 if header_idx >= 0 else 0

    # Проверяем: ТЗ-структура (merged-cell таблица с характеристиками)
    tz_header_rows = table_data[header_idx : header_idx + 3] if header_idx >= 0 else []
    if tz_header_rows and _is_tz_structure(tz_header_rows):
        tz_col_map = _detect_tz_columns(tz_header_rows)
        # Присутствие хотя бы одной колонки для параметров
        if "name" in tz_col_map and any(k in tz_col_map for k in ["param", "value", "specs"]):
            actual_data_start = header_idx + 1
            next_row = table_data[header_idx + 1] if header_idx + 1 < len(table_data) else []
            if next_row and not next_row[0] and any(kw in str(next_row).lower() for kw in ["параметр", "значение", "требован"]):
                actual_data_start = header_idx + 2
            
            tz_items = _parse_tz_table_rows(table_data[actual_data_start:], tz_col_map, page_info=page_info)
            return tz_items, col_map

    # Fallback: Обычный парсер (тоже учим его собирать сырые характеристики)
    items = []
    current_item = None
    for row in table_data[start_row:]:
        if len(row) <= col_map.get("name", 0): continue
        name = _safe_cell(row, col_map, "name")
        
        if name and len(name) >= 3 and not any(kw in name.lower() for kw in ["итого", "всего", "total", "сумма"]):
            current_item = {
                "name": name,
                "specs": _safe_cell(row, col_map, "specs"),
                "quantity": _safe_cell(row, col_map, "quantity"),
                "price": _safe_cell(row, col_map, "price"),
                "unit": _safe_cell(row, col_map, "unit"),
                "source": "table",
                "page": page_info,
                "raw_specs": []
            }
            # Если в этой же строке есть данные в колонках характеристик
            p = _safe_cell(row, col_map, "param") or _safe_cell(row, col_map, "specs")
            v = _safe_cell(row, col_map, "value")
            if p: current_item["raw_specs"].append({"p": p, "v": v, "u": ""})
            items.append(current_item)
        elif not name and current_item:
            # Строка без названия - вероятно, продолжение характеристик (merged cells)
            p = _safe_cell(row, col_map, "param") or _safe_cell(row, col_map, "specs")
            v = _safe_cell(row, col_map, "value")
            if p: current_item["raw_specs"].append({"p": p, "v": v, "u": ""})

    return items, col_map


def _dedup_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Дедупликация позиций. Сохраняет raw_specs при объединении."""
    seen = {}
    for item in items:
        key = item["name"].replace('\n', ' ').lower().strip()[:60]
        if not key: continue
        
        if key in seen:
            existing = seen[key]
            # Если у нового айтема больше raw_specs - берем его (или объединяем)
            new_raw = item.get("raw_specs", [])
            old_raw = existing.get("raw_specs", [])
            if len(new_raw) > len(old_raw):
                existing["raw_specs"] = new_raw
            
            # Предпочитаем table-источник
            if existing["source"] != "table" and item["source"] == "table":
                # Переносим важные поля
                item["raw_specs"] = existing.get("raw_specs", []) or item.get("raw_specs", [])
                seen[key] = item
        else:
            seen[key] = item
    return list(seen.values())


def truncate_text(text: str, max_chars: int = 2000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(' ', 1)[0] + "..."


def _split_by_lines(text: str, max_chars: int) -> List[str]:
    """Fallback: разбивает текст по строкам когда /smart_chunk не справился."""
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_chars and current:
            chunks.append(current)
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


async def _chunk_text(text: str) -> List[str]:
    """Разбивает текст на чанки через /smart_chunk. Short texts — без HTTP."""
    if len(text) <= MAX_TEXT_FOR_LLM:
        return [text]

    client = await get_shared_client()
    try:
        started = time.monotonic()
        resp = await client.post(
            f"{MCP_DOCUMENT_SERVER_URL}/smart_chunk",
            json={"text": text, "max_tokens": CHUNK_MAX_TOKENS, "overlap": CHUNK_OVERLAP},
        )
        resp.raise_for_status()
        record_current_duration(
            name="document_server.smart_chunk",
            elapsed_seconds=time.monotonic() - started,
            kind="tool",
            category="service",
            meta={"text_chars": len(text)},
        )
        data = resp.json()
        chunks = data.get("chunks", [])
        if chunks:
            # Если smart_chunk вернул oversized чанки (текст без \n\n) — дробим локально
            has_oversized = any(len(c) > MAX_TEXT_FOR_LLM for c in chunks)
            if not has_oversized:
                return chunks
            result = []
            for c in chunks:
                if len(c) > MAX_TEXT_FOR_LLM:
                    result.extend(_split_by_lines(c, MAX_TEXT_FOR_LLM))
                else:
                    result.append(c)
            print(f"[Chunk] Re-split oversized chunks: {len(chunks)} → {len(result)}")
            return result
    except Exception as e:
        print(f"[Chunk] /smart_chunk failed, fallback: {e}")
        logger.warning("/smart_chunk failed, using line-based fallback: %s", e, exc_info=True)
        inc_metric_counter(
            "llm_tools_platform_equipment_fallback_total",
            labels={"stage": "chunking", "reason": "smart_chunk_failed"},
        )

    return _split_by_lines(text, MAX_TEXT_FOR_LLM)


async def _extract_from_single_chunk(
    chunk_text: str,
    chunk_idx: int,
    total_chunks: int,
    already_found: List[str],
    model_execution_events: Optional[List[Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Извлекает позиции из одного текстового чанка через LLM."""
    exclude_hint = ""
    if already_found:
        names_list = ", ".join(already_found[:30])
        exclude_hint = f"\nУЖЕ ИЗВЛЕЧЕНО (НЕ ДУБЛИРУЙ): {names_list}"

    chunk_context = ""
    if total_chunks > 1:
        chunk_context = f"\n(Фрагмент {chunk_idx + 1} из {total_chunks})"

    prompt = f"""<|im_start|>system
Ты аналитик закупок. Извлеки позиции оборудования/товаров из текста документа.<|im_end|>
<|im_start|>user
Извлеки список оборудования/товаров/материалов из текста. Для каждой позиции укажи название, характеристики, количество и цену (если есть).{exclude_hint}{chunk_context}

ОТВЕТЬ ТОЛЬКО JSON-МАССИВОМ:
[{{"name": "название", "specs": "характеристики", "quantity": "кол-во", "price": "цена"}}]
Если позиций нет, верни [].

Текст:
{chunk_text}<|im_end|>
<|im_start|>assistant
"""
    response, model_execution = await _infer_equipment_llm_with_failover(
        stage="extract_chunk",
        prompt=prompt,
        payload={"temperature": 0.1, "max_tokens": 2000},
    )
    if model_execution_events is not None and model_execution:
        model_execution_events.append(model_execution)
    content = response.get("content", "")
    if not content and "choices" in response:
        content = response["choices"][0].get("text", "")

    items = []
    parsed = parse_json_garbage(content)
    if isinstance(parsed, list):
        for p in parsed:
            if isinstance(p, dict) and p.get("name"):
                items.append({
                    "name": p["name"],
                    "specs": p.get("specs", ""),
                    "quantity": p.get("quantity", ""),
                    "price": p.get("price", ""),
                    "unit": p.get("unit", ""),
                    "source": "text",
                    "page": None,
                })
    return items, model_execution if model_execution else None


# === Node 1: Extract ===

async def _extract_tables_from_doc(path: str) -> List[Dict[str, Any]]:
    """Извлекает таблицы из документа через Document Server (Pass 1)."""
    ext = os.path.splitext(path)[1].lower()
    items = []
    last_col_map = None

    client = await get_shared_client()
    try:
        started = time.monotonic()
        if ext == ".pdf":
            resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/extract_tables", json={"path": path})
        elif ext == ".docx":
            resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/extract_tables_docx", json={"path": path})
        elif ext in (".xlsx", ".xls"):
            resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/extract_tables_excel", json={"path": path})
        else:
            return []

        resp.raise_for_status()
        record_current_duration(
            name="document_server.extract_tables",
            elapsed_seconds=time.monotonic() - started,
            kind="tool",
            category="service",
            meta={"path": path, "ext": ext},
        )
        data = resp.json()
        if data.get("status") == "error":
            raise RuntimeError(data.get("error", "Unknown error from document server"))

        # TD-Pagination Fix: Склеиваем таблицы-продолжения перед парсингом
        raw_tables = data.get("tables", [])
        if not raw_tables:
            return []

        merged_blocks = [] # List of (data, start_page, col_map)
        current_block = []
        current_start_page = None
        current_col_map = None

        for table_info in raw_tables:
            table_data = table_info.get("data", [])
            page = table_info.get("page")
            if not table_data: continue

            # Проверяем наличие заголовка в этом фрагменте
            header_mapping = {}
            for row in table_data[:3]: # Проверяем только начало фрагмента
                m = _detect_header_columns(row)
                if len(m) >= 2:
                    header_mapping = m
                    break
            
            # Логика склейки:
            # Если нашли новый заголовок ИЛИ это первая таблица ИЛИ кол-во колонок изменилось
            if header_mapping or not current_block or len(table_data[0]) != len(current_block[0]):
                # Сохраняем предыдущий блок если он был
                if current_block:
                    merged_blocks.append((current_block, current_start_page, current_col_map))
                
                # Начинаем новый блок
                current_block = list(table_data)
                current_start_page = page
                current_col_map = header_mapping if header_mapping else current_col_map
            else:
                # Это продолжение предыдущей таблицы (нет заголовка и те же колонки)
                current_block.extend(table_data)

        # Не забываем последний блок
        if current_block:
            merged_blocks.append((current_block, current_start_page, current_col_map))

        # Теперь парсим склеенные блоки
        last_col_map = None
        for block_data, page, block_map in merged_blocks:
            # Передаем накопленный блок в парсер
            parsed, last_col_map = _parse_table_rows(
                block_data, 
                page_info=page, 
                inherited_col_map=block_map or last_col_map
            )
            items.extend(parsed)

    except Exception as e:
        print(f"[Extract] Table extraction failed for {path}: {e}")
        raise  # Пробрасываем для накопления в errors ноды

    return items


async def _extract_items_llm(
    path: str,
    already_found: List[str],
    progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
    model_execution_events: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Извлекает позиции из текста документа через LLM (Pass 2, Map-Reduce)."""
    client = await get_shared_client()
    try:
        started = time.monotonic()
        resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": path})
        resp.raise_for_status()
        record_current_duration(
            name="document_server.load_document",
            elapsed_seconds=time.monotonic() - started,
            kind="tool",
            category="service",
            meta={"path": path},
        )
        text = resp.json().get("text", "")
        if not text.strip():
            return []
    except Exception as e:
        print(f"[Extract] LLM extraction failed for {path}: {e}")
        raise

    chunks = await _chunk_text(text)
    print(f"[LLM Extract] {len(chunks)} chunk(s) for {os.path.basename(path)} ({len(text)} chars)")

    all_items = []
    accumulated_names = list(already_found)

    for idx, chunk in enumerate(chunks):
        try:
            if progress_callback is not None:
                await progress_callback(idx + 1, len(chunks))
            chunk_items, model_execution = await _extract_from_single_chunk(
                chunk_text=chunk,
                chunk_idx=idx,
                total_chunks=len(chunks),
                already_found=accumulated_names,
                model_execution_events=model_execution_events,
            )
            all_items.extend(chunk_items)
            accumulated_names.extend(it["name"] for it in chunk_items)
            print(f"  [Chunk {idx + 1}/{len(chunks)}] Found {len(chunk_items)} items")
        except Exception as e:
            print(f"  [Chunk {idx + 1}/{len(chunks)}] Failed: {e}")

    return all_items


async def load_and_extract_node(state: EquipmentState) -> dict:
    """Node 1: Двухпроходная экстракция позиций из обоих документов."""
    print(f"[Equipment] Extracting items from: {state['input_1']} and {state['input_2']}")
    errors = []
    extraction_metadata: Dict[str, Any] = {}
    model_execution_events: List[Dict[str, Any]] = []

    # === Документ 1 ===
    items_1_table = []
    items_1_fallback = []
    items_1_text = []
    try:
        items_1_table = await _extract_tables_from_doc(state["input_1"])
        print(f"  [Doc1] Tables: {len(items_1_table)} items")
    except Exception as e:
        errors.append(f"Table extraction doc1 failed: {e}")

    # Архитектурное Решение 1: Условная маршрутизация (Short-Circuit)
    # Если парсер таблиц нашел четкое ТЗ (>= 2 позиций), мы пропускаем LLM-поиск по тексту.
    if len(items_1_table) >= 2:
        print(f"  [Doc1] Skipping LLM text extraction: {len(items_1_table)} items already found in structured tables.")
    else:
        try:
            already_names = [it["name"] for it in items_1_table]
            items_1_fallback, metadata_1 = _extract_items_docx_fallback(state["input_1"], already_names)
            extraction_metadata["doc_1"] = metadata_1
            if items_1_fallback:
                print(
                    f"  [Doc1] DOCX fallback: {len(items_1_fallback)} items "
                    f"(strategy={metadata_1['strategy_name']}, role={metadata_1['role']}, conf={metadata_1['confidence']})"
                )
        except Exception as e:
            errors.append(f"DOCX fallback doc1 failed: {e}")

        try:
            already_names = [it["name"] for it in (items_1_table + items_1_fallback)]
            if len(items_1_fallback) >= 2:
                print(f"  [Doc1] Skipping LLM text extraction: {len(items_1_fallback)} items already found in DOCX fallback.")
            else:
                items_1_text = await _extract_items_llm(
                    state["input_1"],
                    already_names,
                    model_execution_events=model_execution_events,
                )
                print(f"  [Doc1] LLM text: {len(items_1_text)} items")
        except Exception as e:
            errors.append(f"LLM extraction doc1 failed: {e}")

    items_1 = _dedup_items(items_1_table + items_1_fallback + items_1_text)

    # === Документ 2 ===
    items_2_table = []
    items_2_fallback = []
    items_2_text = []
    try:
        items_2_table = await _extract_tables_from_doc(state["input_2"])
        print(f"  [Doc2] Tables: {len(items_2_table)} items")
    except Exception as e:
        errors.append(f"Table extraction doc2 failed: {e}")

    if len(items_2_table) >= 2:
        print(f"  [Doc2] Skipping LLM text extraction: {len(items_2_table)} items already found in structured tables.")
    else:
        try:
            already_names = [it["name"] for it in items_2_table]
            items_2_fallback, metadata_2 = _extract_items_docx_fallback(state["input_2"], already_names)
            extraction_metadata["doc_2"] = metadata_2
            if items_2_fallback:
                print(
                    f"  [Doc2] DOCX fallback: {len(items_2_fallback)} items "
                    f"(strategy={metadata_2['strategy_name']}, role={metadata_2['role']}, conf={metadata_2['confidence']})"
                )
        except Exception as e:
            errors.append(f"DOCX fallback doc2 failed: {e}")

        try:
            already_names = [it["name"] for it in (items_2_table + items_2_fallback)]
            if len(items_2_fallback) >= 2:
                print(f"  [Doc2] Skipping LLM text extraction: {len(items_2_fallback)} items already found in DOCX fallback.")
            else:
                items_2_text = await _extract_items_llm(
                    state["input_2"],
                    already_names,
                    model_execution_events=model_execution_events,
                )
                print(f"  [Doc2] LLM text: {len(items_2_text)} items")
        except Exception as e:
            errors.append(f"LLM extraction doc2 failed: {e}")

    items_2 = _dedup_items(items_2_table + items_2_fallback + items_2_text)

    # TD-LLM-Polisher: Очищаем сырые характеристики через LLM
    await _polish_items_specs_llm(items_1, model_execution_events=model_execution_events)
    await _polish_items_specs_llm(items_2, model_execution_events=model_execution_events)

    print(f"  [Equipment] Total: doc1={len(items_1)}, doc2={len(items_2)}")
    return {
        "items_1": items_1,
        "items_2": items_2,
        "extraction_metadata": extraction_metadata,
        **({"model_execution": model_execution_events} if model_execution_events else {}),
        "errors": errors,
    }


# === Routing ===

def _route_after_extract(state: EquipmentState) -> str:
    """Conditional edge: skip matching if both item lists empty."""
    if not state.get("items_1") and not state.get("items_2"):
        return "report"
    return "match"


# === Node 2: Match ===

def _item_to_text(item: Dict[str, Any]) -> str:
    """Конвертирует item в текст для семантического матчинга."""
    name = item.get("name", "").replace('\n', ' ').strip()

    # Извлекаем артикул/модель из "(Аналог X)" и добавляем в текст для embedder matching
    analog_match = re.search(r'\(аналог\s+(.+?)\)', name, re.IGNORECASE)
    if analog_match:
        analog_ref = analog_match.group(1).strip()
        name = f"{name} {analog_ref}"

    # Убираем лишние фразы из ТЗ-имён, мешающие матчингу
    noise_patterns = [
        r'\s*или\s+эквивалент\s*',
        r'\bили\s+аналог\b',
        r'\b4LFF\b', r'\b4SFF\b', r'\b8SFF\b', r'\b2LFF\b',
    ]
    for pattern in noise_patterns:
        name = re.sub(pattern, ' ', name, flags=re.IGNORECASE).strip()

    parts = [name]
    specs = item.get("specs", "").replace('\n', ' ').strip()
    if specs:
        parts.append(specs)
    if item.get("quantity"):
        parts.append(f"кол-во: {item['quantity']}")
    return ". ".join(parts)


async def match_items_node(state: EquipmentState) -> dict:
    """Node 2: Семантический матчинг через Legal Server (embedding + Hungarian)."""
    items_1 = state.get("items_1", [])
    items_2 = state.get("items_2", [])

    if not items_1 or not items_2:
        # Если одна сторона пуста — все позиции другой стороны = GAP
        matches = []
        for it in items_1:
            matches.append({
                "type": "DELETED", "old_text": _item_to_text(it), "new_text": "",
                "item_1": it, "item_2": None,
            })
        for it in items_2:
            matches.append({
                "type": "ADDED", "old_text": "", "new_text": _item_to_text(it),
                "item_1": None, "item_2": it,
            })
        return {"matches": matches}

    print(f"[Equipment] Matching {len(items_1)} vs {len(items_2)} items via Legal Server")

    list_old = [_item_to_text(it) for it in items_1]
    list_new = [_item_to_text(it) for it in items_2]

    client = await get_shared_client()
    try:
        started = time.monotonic()
        resp = await client.post(f"{MCP_LEGAL_SERVER_URL}/match_batches", json={
            "list_old": list_old,
            "list_new": list_new,
            "threshold": MATCH_SIMILARITY_THRESHOLD,
        })
        resp.raise_for_status()
        record_current_duration(
            name="legal_server.match_batches",
            elapsed_seconds=time.monotonic() - started,
            kind="tool",
            category="service",
            meta={"old_items": len(list_old), "new_items": len(list_new)},
        )
        data = resp.json()

        if data.get("status") == "error":
            raise RuntimeError(data.get("error", "Unknown error from legal server"))

        raw_matches = data.get("matches", [])

        # O(1) reverse mapping по тексту (TD-7 Fix)
        items_1_by_text = {txt: items_1[i] for i, txt in enumerate(list_old)}
        items_2_by_text = {txt: items_2[i] for i, txt in enumerate(list_new)}

        enriched = []
        for m in raw_matches:
            old_text = m.get("old_text", "")
            new_text = m.get("new_text", "")

            enriched.append({
                **m,
                "item_1": items_1_by_text.get(old_text),
                "item_2": items_2_by_text.get(new_text),
            })

        print(f"  [Equipment] Matched: {len(enriched)} pairs")
        return {"matches": enriched}

    except Exception as e:
        print(f"[Equipment] Matching failed: {e}")
        return {"matches": [], "errors": [f"Matching failed: {e}"]}


# === Node 3: Evaluate ===

async def evaluate_compliance_node(state: EquipmentState) -> dict:
    """Node 3: Batch LLM evaluation — BATCH_SIZE позиций за вызов."""
    if state.get("errors"):
        errs = state["errors"]
        # Пропускаем если matching провалился
        if any("Matching failed" in e for e in errs):
            print(f"[Equipment] Skipping evaluate due to errors: {errs}")
            return {"analysis_results": []}

    matches = state.get("matches", [])
    if not matches:
        return {"analysis_results": []}

    mode = state.get("mode", "tz_vs_smeta")
    results = []

    # Структурные (ADDED/DELETED) — без LLM
    structural = []
    to_analyze = []
    for m in matches:
        if m.get("type") in ("ADDED", "DELETED"):
            structural.append(m)
        else:
            to_analyze.append(m)

    for m in structural:
        item = m.get("item_2") or m.get("item_1") or {}
        results.append({
            "item_1": m.get("item_1"),
            "item_2": m.get("item_2"),
            "result": "GAP",
            "reason": "Добавлена" if m["type"] == "ADDED" else "Удалена",
            "type": m["type"],
        })

    print(f"[Equipment] Structural: {len(structural)}, needs LLM: {len(to_analyze)}")

    # Batch LLM evaluation
    total_batches = (len(to_analyze) + BATCH_SIZE - 1) // BATCH_SIZE if to_analyze else 0
    model_execution_events: List[Dict[str, Any]] = []

    for batch_idx, batch_start in enumerate(range(0, len(to_analyze), BATCH_SIZE)):
        batch = to_analyze[batch_start:batch_start + BATCH_SIZE]
        print(f"[Equipment] LLM batch {batch_idx + 1}/{total_batches} ({len(batch)} items)")

        items_text = ""
        for idx, m in enumerate(batch):
            item_1 = m.get("item_1") or {}
            item_2 = m.get("item_2") or {}
            i1_desc = f"{item_1.get('name', '?')}. {item_1.get('specs', '')}. Кол-во: {item_1.get('quantity', '?')}, Цена: {item_1.get('price', '?')}"
            i2_desc = f"{item_2.get('name', '?')}. {item_2.get('specs', '')}. Кол-во: {item_2.get('quantity', '?')}, Цена: {item_2.get('price', '?')}"
            items_text += f"\n[{idx + 1}] ПОЗИЦИЯ 1: {truncate_text(i1_desc, 600)}\n    ПОЗИЦИЯ 2: {truncate_text(i2_desc, 600)}\n"

        if mode == "tz_vs_smeta":
            prompt = f"""<|im_start|>system
Ты эксперт по закупкам. Проверь соответствие предложения требованиям ТЗ.<|im_end|>
<|im_start|>user
Для каждой из {len(batch)} пар определи соответствие предложения (ПОЗИЦИЯ 2) требованиям ТЗ (ПОЗИЦИЯ 1).
{items_text}
Ответ — JSON-массив из ровно {len(batch)} объектов:
[{{"result": "PASS|PARTIAL|FAIL|OVER", "reason": "почему"}}]
PASS — полностью соответствует, PARTIAL — частично, FAIL — не соответствует, OVER — превышает требования.<|im_end|>
<|im_start|>assistant
"""
        else:
            prompt = f"""<|im_start|>system
Ты эксперт по закупкам. Сравни позиции двух смет.<|im_end|>
<|im_start|>user
Для каждой из {len(batch)} пар определи различия между сметами.
{items_text}
Ответ — JSON-массив из ровно {len(batch)} объектов:
[{{"result": "SAME|PRICE_CHANGE|SPEC_CHANGE|REPLACED", "reason": "почему"}}]
SAME — без изменений, PRICE_CHANGE — изменилась цена, SPEC_CHANGE — изменились характеристики, REPLACED — замена оборудования.<|im_end|>
<|im_start|>assistant
"""
        try:
            response, model_execution = await _infer_equipment_llm_with_failover(
                stage="evaluate",
                prompt=prompt,
                payload={"max_tokens": 600, "temperature": 0.1, "echo": False},
            )
            if model_execution:
                model_execution_events.append(model_execution)
            content = response.get("content", "")
            if not content and "choices" in response:
                content = response["choices"][0].get("text", "")
            elif not content and "result" in response:
                r = response["result"]
                if isinstance(r, dict) and "choices" in r:
                    content = r["choices"][0].get("text", "")

            parsed = _normalize_evaluate_results_payload(parse_json_garbage(content))

            for idx, m in enumerate(batch):
                item_data = parsed[idx] if idx < len(parsed) and isinstance(parsed[idx], dict) else {}
                results.append({
                    "item_1": m.get("item_1"),
                    "item_2": m.get("item_2"),
                    "result": item_data.get("result", "ERROR") if item_data else "ERROR",
                    "reason": item_data.get("reason", "Не удалось определить") if item_data else "Ошибка анализа",
                    "type": m.get("type", "MODIFIED"),
                    "similarity": m.get("similarity_score", m.get("score")),
                })

        except Exception as e:
            print(f"[Equipment] Error in batch {batch_idx + 1}: {e}")
            logger.warning("Equipment LLM batch failed: %s", e, exc_info=True)
            inc_metric_counter(
                "llm_tools_platform_equipment_fallback_total",
                labels={"stage": "evaluate", "reason": "llm_batch_error"},
            )
            for m in batch:
                results.append({
                    "item_1": m.get("item_1"),
                    "item_2": m.get("item_2"),
                    "result": "ERROR",
                    "reason": f"Ошибка LLM: {e}",
                    "type": m.get("type", "MODIFIED"),
                })

    result = {"analysis_results": results}
    if model_execution_events:
        result["model_execution"] = model_execution_events
    return result


# === Node 4: Report ===

async def generate_equipment_report_node(state: EquipmentState) -> dict:
    """Node 4: Генерация Markdown отчёта с сохранением и дедупликацией."""
    name_1 = state.get("name_1") or os.path.basename(state["input_1"])
    name_2 = state.get("name_2") or os.path.basename(state["input_2"])
    mode = state.get("mode", "tz_vs_smeta")
    runtime_context = _get_runtime_context(state)
    errors = state.get("errors", [])
    items_1 = state.get("items_1", [])
    items_2 = state.get("items_2", [])
    results = state.get("analysis_results", [])

    # Заголовок
    if mode == "tz_vs_smeta":
        title = "Анализ соответствия сметы техническому заданию"
    else:
        title = "Сравнение смет"

    report = f"# {title}\n\n"
    report += f"**Дата:** {time.strftime('%Y-%m-%d %H:%M')}\n"
    started_at_monotonic = runtime_context.get("started_at_monotonic")
    if started_at_monotonic is not None:
        try:
            elapsed_seconds = time.monotonic() - float(started_at_monotonic)
        except (TypeError, ValueError):
            elapsed_seconds = None
        if elapsed_seconds is not None:
            report += f"**Время выполнения:** {_format_elapsed_seconds(elapsed_seconds)}\n"
    report += f"**Документ 1:** {name_1}\n"
    report += f"**Документ 2:** {name_2}\n"
    report += f"**Режим:** {mode}\n\n"

    # Ошибки
    if errors:
        report += "## Предупреждения\n\n"
        for e in errors:
            report += f"- {e}\n"
        report += "\n"

    # Если items пусты — краткий отчёт
    if not items_1 and not items_2:
        report += "## Результат\n\nНе удалось извлечь позиции из документов. "
        report += "Проверьте формат файлов и наличие таблиц/списков оборудования.\n"
        return {"final_report": report}

    # Сводка
    report += f"**Извлечено позиций:** из документа 1: {len(items_1)}, из документа 2: {len(items_2)}\n\n"

    if results:
        def _md_cell(value: Any) -> str:
            text = str(value or "-").strip()
            if not text:
                text = "-"
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            text = text.replace("|", "\\|").replace("\n", "<br>")
            return text

        def _md_block(value: Any) -> str:
            text = str(value or "-").strip()
            if not text:
                text = "-"
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            return text.replace("|", "\\|")

        # Подсчёт статусов
        status_counts = {}
        for r in results:
            s = r.get("result", "ERROR")
            status_counts[s] = status_counts.get(s, 0) + 1

        report += "## Сводка\n\n"
        report += "| Статус | Количество |\n"
        report += "| :--- | :---: |\n"
        for status, count in sorted(status_counts.items()):
            report += f"| {status} | {count} |\n"
        report += "\n"

        _ICONS = {
            "PASS": "✅", "SAME": "✅",
            "PARTIAL": "⚠️", "OVER": "📈",
            "FAIL": "❌", "GAP": "🔴",
            "PRICE_CHANGE": "💰", "SPEC_CHANGE": "🔧", "REPLACED": "🔄",
            "ERROR": "⚙️",
        }

        if mode == "tz_vs_smeta":
            main_results = [r for r in results if r.get("item_1")]
            extra_results = [r for r in results if not r.get("item_1") and r.get("item_2")]

            report += "## Таблица соответствия ТЗ и КП\n\n"
            report += "| # | Позиция ТЗ | Позиция КП | Статус | Примечание |\n"
            report += "| :---: | :--- | :--- | :---: | :--- |\n"

            for idx, r in enumerate(main_results, 1):
                i1 = r.get("item_1") or {}
                i2 = r.get("item_2") or {}
                status = r.get("result", "?")
                icon = _ICONS.get(status, "❓")
                report += (
                    f"| {idx} | {_md_cell(i1.get('name', '-'))} | {_md_cell(i2.get('name', '-'))} | "
                    f"{icon} {status} | {_md_cell(r.get('reason', ''))} |\n"
                )
            report += "\n"

            if extra_results:
                report += "## Дополнительные позиции из КП\n\n"
                report += "| # | Позиция КП | Характеристики | Кол-во | Цена |\n"
                report += "| :---: | :--- | :--- | :---: | :---: |\n"
                for idx, r in enumerate(extra_results, 1):
                    item = r.get("item_2") or {}
                    report += (
                        f"| {idx} | {_md_cell(item.get('name', '-'))} | {_md_cell(item.get('specs', '-'))} | "
                        f"{_md_cell(item.get('quantity', '-'))} | {_md_cell(item.get('price', '-'))} |\n"
                    )
                report += "\n"
        else:
            report += "## Детальный анализ\n\n"
            report += "| # | Позиция 1 | Позиция 2 | Статус | Примечание |\n"
            report += "| :---: | :--- | :--- | :---: | :--- |\n"

            for idx, r in enumerate(results, 1):
                i1 = r.get("item_1") or {}
                i2 = r.get("item_2") or {}
                name1 = _md_cell(i1.get("name", "-"))
                name2 = _md_cell(i2.get("name", "-"))
                status = r.get("result", "?")
                icon = _ICONS.get(status, "❓")
                reason = _md_cell(r.get("reason", ""))
                report += f"| {idx} | {name1} | {name2} | {icon} {status} | {reason} |\n"

        report += "\n"
        report += "## Полные детали по позициям\n\n"

        for idx, r in enumerate(results, 1):
            i1 = r.get("item_1") or {}
            i2 = r.get("item_2") or {}
            status = r.get("result", "?")
            icon = _ICONS.get(status, "❓")
            similarity = r.get("similarity")

            report += f"### {idx}. {icon} {status}\n\n"
            report += f"- **Позиция 1:** {_md_block(i1.get('name', '-'))}\n"
            report += f"- **Позиция 2:** {_md_block(i2.get('name', '-'))}\n"

            specs_1 = i1.get("specs")
            specs_2 = i2.get("specs")
            if specs_1:
                report += f"- **Характеристики 1:** {_md_block(specs_1)}\n"
            if specs_2:
                report += f"- **Характеристики 2:** {_md_block(specs_2)}\n"
            if similarity is not None:
                report += f"- **Similarity:** {similarity}\n"

            report += f"- **Примечание:** {_md_block(r.get('reason', ''))}\n\n"

    # Сохранение в файл через общую утилиту
    try:
        from orchestrator.shared.report_utils import save_report_with_dedup
        final_report_text = save_report_with_dedup(
            report_text=report,
            prefix="Report_Equipment",
            input_names=[name_1, name_2],
            current_metric=len(results),
            metric_marker="Извлечено позиций:** из документа 1:"
        )
        return {"final_report": final_report_text}
    except Exception as e:
        report += f"\n---\n**Ошибка сохранения отчета:** {e}"
        return {"final_report": report}


# === Build Graph ===

def create_equipment_graph():
    workflow = StateGraph(EquipmentState)

    workflow.add_node("extract", load_and_extract_node)
    workflow.add_node("match", match_items_node)
    workflow.add_node("evaluate", evaluate_compliance_node)
    workflow.add_node("report", generate_equipment_report_node)

    workflow.set_entry_point("extract")
    workflow.add_conditional_edges("extract", _route_after_extract, {
        "match": "match",
        "report": "report",
    })
    workflow.add_edge("match", "evaluate")
    workflow.add_edge("evaluate", "report")
    workflow.add_edge("report", END)

    return workflow.compile()
