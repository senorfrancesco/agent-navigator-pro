"""
Workflow: Equipment Analysis (ТЗ vs Смета / Смета vs Смета)

Конвейер с conditional edges:
  extract → [route] → match → evaluate → report
                  ↘ (items пусто) → report (ошибка)

Двухпроходная экстракция: структурные таблицы (без LLM) + LLM текстовые позиции.
Семантический матчинг через Legal Server (LaBSE + Hungarian).
Batch LLM evaluation по BATCH_SIZE=5.
"""

import json
import os
import re
import time
import httpx
from typing import TypedDict, List, Dict, Any, Annotated
import operator
from langgraph.graph import StateGraph, END

# Утилиты
try:
    from orchestrator.utils import parse_json_garbage
except ImportError:
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
    from utils import parse_json_garbage

try:
    from services.model_manager.ums_client import ums_client
except ImportError:
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
    from services.model_manager.ums_client import ums_client

# URLs серверов
MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
MCP_LEGAL_SERVER_URL = os.getenv("MCP_LEGAL_SERVER_URL", "http://localhost:8002")
UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")

BATCH_SIZE = 5  # Позиций в одном LLM-вызове
MAX_TEXT_FOR_LLM = 6000  # Лимит символов текста для LLM-экстракции (~2000 токенов)
CHUNK_MAX_TOKENS = 2000    # токенов на чанк (~6000 символов) — безопасно для 16K ctx
CHUNK_OVERLAP = 150        # overlap токенов между чанками

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
    errors: Annotated[List[str], operator.add]  # Накопление через reducer
    session_id: str


# === Helpers ===

# Ключевые слова заголовков таблиц оборудования
_HEADER_KEYWORDS = {
    "name": ["наименование", "название", "товар", "оборудование", "позиция", "продукция", "модель"],
    "specs": ["характеристик", "описание", "параметр", "спецификац", "тех.требован", "требовани"],
    "quantity": ["кол-во", "количество", "кол.", "шт", "объем", "объём"],
    "price": ["цена", "стоимость", "сумма", "руб", "₽", "price"],
    "unit": ["ед.изм", "единица", "ед.", "измерен"],
}


def _detect_header_columns(header_row: List[str]) -> Dict[str, int]:
    """Детектирует маппинг колонок по ключевым словам в заголовке."""
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


def _is_tz_structure(header_row: List) -> bool:
    """Детектирует ТЗ-структуру (merged-cells) по заголовку таблицы."""
    if not header_row:
        return False
    # Нормализуем: убираем лишние пробелы внутри слов (OCR-артефакты merged-cells)
    raw = " ".join(str(c) for c in header_row if c)
    import re as _re
    header_text = _re.sub(r'\s+', ' ', raw).lower()
    # Явные признаки ТЗ с param/value структурой
    if "требуемый" in header_text or "требуемое" in header_text:
        return True
    if "параметр" in header_text and "значение" in header_text:
        return True
    # Признак "технические характеристики" без ценовых колонок
    if "технические характеристики" in header_text and "цен" not in header_text and "стоимост" not in header_text:
        return True
    return False


def _detect_tz_columns(header_row: List) -> Dict[str, int]:
    """Строит col_map для ТЗ-таблицы с колонками num/name/qty/param/value/unit."""
    mapping = {}
    for col_idx, cell in enumerate(header_row):
        if cell is None:
            continue
        cell_lower = str(cell).lower().strip()
        if not cell_lower:
            continue
        for field, keywords in _TZ_COL_KEYWORDS.items():
            if field not in mapping and any(kw in cell_lower for kw in keywords):
                mapping[field] = col_idx
                break
    return mapping


def _extract_numeric_specs(spec_rows: List[Dict]) -> str:
    """
    Формирует компактный текст из строк-характеристик ТЗ.
    Берёт строки с числовыми требованиями и специфическими значениями.
    Пример: "ОЗУ: не менее 32 ГБ. БП: не менее 2 шт. CPU: не менее 2 шт."
    """
    numeric_pattern = re.compile(
        r'(не\s+менее|не\s+более|от|до)\s*[\d.,]+', re.IGNORECASE
    )
    specific_value_pattern = re.compile(
        r'\b(DDR\d|RDIMM|UDIMM|DIMM|SAS|SATA|NVMe|PCIe|RAID|TPM|ECC|LFF|SFF|'
        r'Rack|Tower|Windows|Linux|UEFI|USB|HDMI|VGA|DP|RJ.?45)\b',
        re.IGNORECASE
    )

    parts = []
    for row in spec_rows:
        param = str(row.get("param", "")).replace('\n', ' ').strip()
        value = str(row.get("value", "")).replace('\n', ' ').strip()
        unit  = str(row.get("unit",  "")).replace('\n', ' ').strip()

        if not param:
            continue

        value_lower = value.lower()
        if value_lower in ('', 'none', 'соответствие', '-'):
            if specific_value_pattern.search(param):
                parts.append(param)
            continue

        if numeric_pattern.search(value) or re.search(r'\d', value):
            full = f"{param}: {value}"
            if unit and unit != value:
                full += f" {unit}"
            parts.append(full)
        elif specific_value_pattern.search(value):
            parts.append(f"{param}: {value}")

    return ". ".join(parts[:10])


def _parse_tz_table_rows(
    rows: List[List],
    col_map: Dict[str, int],
) -> List[Dict[str, Any]]:
    """
    Group-by парсер для ТЗ-таблиц с merged-cells.

    Строка-позиция:      col[num] — целое число (1, 2, 3...)
    Строка-характеристика: col[num] и col[name] — None/пусты
    """
    items = []
    current_item: Dict[str, Any] | None = None
    current_specs: List[Dict] = []

    def _cell(row, key):
        idx = col_map.get(key)
        if idx is None or idx >= len(row):
            return None
        val = row[idx]
        return str(val).replace('\n', ' ').strip() if val is not None else None

    def _flush():
        nonlocal current_item, current_specs
        if current_item is not None:
            current_item["specs"] = _extract_numeric_specs(current_specs)
            items.append(current_item)
        current_item = None
        current_specs = []

    for row in rows:
        num_val  = _cell(row, "num")
        name_val = _cell(row, "name")

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
                "unit": _cell(row, "unit") or "",
                "source": "table",
                "page": None,
            }
            current_specs = []

            # Первая строка позиции может уже содержать характеристику
            param = _cell(row, "param")
            value = _cell(row, "value")
            unit  = _cell(row, "unit")
            if param:
                current_specs.append({"param": param, "value": value or "", "unit": unit or ""})

        # Строка-характеристика: num и name — пусты
        elif (not num_val or num_val in ('None', '')) and \
             (not name_val or name_val in ('None', '')):
            if current_item is None:
                continue
            param = _cell(row, "param")
            value = _cell(row, "value")
            unit  = _cell(row, "unit")
            if param:
                current_specs.append({"param": param, "value": value or "", "unit": unit or ""})

        # Строка с именем, но без номера — продолжение предыдущей позиции
        elif not num_val and name_val and current_item is not None:
            current_item["name"] += " " + name_val

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
    if header_row and _is_tz_structure(header_row):
        tz_col_map = _detect_tz_columns(header_row)
        if "name" in tz_col_map and ("param" in tz_col_map or "value" in tz_col_map):
            tz_items = _parse_tz_table_rows(table_data[start_row:], tz_col_map)
            return tz_items, col_map

    items = []
    for row in table_data[start_row:]:
        if len(row) <= col_map.get("name", 0):
            continue
        name = _safe_cell(row, col_map, "name")
        if not name or len(name) < 3:
            continue
        # Пропускаем строки-итоги
        if any(kw in name.lower() for kw in ["итого", "всего", "total", "сумма"]):
            continue

        item = {
            "name": name,
            "specs": _safe_cell(row, col_map, "specs"),
            "quantity": _safe_cell(row, col_map, "quantity"),
            "price": _safe_cell(row, col_map, "price"),
            "unit": _safe_cell(row, col_map, "unit"),
            "source": "table",
            "page": page_info,
        }
        items.append(item)

    return items, col_map


def _dedup_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Дедупликация позиций по name.lower()[:60], предпочитает source='table'."""
    seen = {}
    for item in items:
        key = item["name"].replace('\n', ' ').lower().strip()[:60]
        if not key:
            continue
        if key in seen:
            # Предпочитаем table-источник
            if seen[key]["source"] != "table" and item["source"] == "table":
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


async def _chunk_text(client: httpx.AsyncClient, text: str) -> List[str]:
    """Разбивает текст на чанки через /smart_chunk. Short texts — без HTTP."""
    if len(text) <= MAX_TEXT_FOR_LLM:
        return [text]

    try:
        resp = await client.post(
            f"{MCP_DOCUMENT_SERVER_URL}/smart_chunk",
            json={"text": text, "max_tokens": CHUNK_MAX_TOKENS, "overlap": CHUNK_OVERLAP},
        )
        resp.raise_for_status()
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

    return _split_by_lines(text, MAX_TEXT_FOR_LLM)


async def _extract_from_single_chunk(
    chunk_text: str,
    chunk_idx: int,
    total_chunks: int,
    already_found: List[str],
) -> List[Dict[str, Any]]:
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
    response = await ums_client.async_infer("qwen-14b-llm", {
        "prompt": prompt, "temperature": 0.1, "max_tokens": 2000
    })
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
    return items


# === Node 1: Extract ===

async def _extract_tables_from_doc(client: httpx.AsyncClient, path: str) -> List[Dict[str, Any]]:
    """Извлекает таблицы из документа через Document Server (Pass 1)."""
    ext = os.path.splitext(path)[1].lower()
    items = []
    last_col_map = None

    try:
        if ext == ".pdf":
            resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/extract_tables", json={"path": path})
        elif ext == ".docx":
            resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/extract_tables_docx", json={"path": path})
        elif ext in (".xlsx", ".xls"):
            resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/extract_tables_excel", json={"path": path})
        else:
            return []

        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "error":
            raise RuntimeError(data.get("error", "Unknown error from document server"))

        for table_info in data.get("tables", []):
            table_data = table_info.get("data", [])
            page = table_info.get("page")
            parsed, last_col_map = _parse_table_rows(table_data, page_info=page, inherited_col_map=last_col_map)
            items.extend(parsed)

    except Exception as e:
        print(f"[Extract] Table extraction failed for {path}: {e}")
        raise  # Пробрасываем для накопления в errors ноды

    return items


async def _extract_items_llm(client: httpx.AsyncClient, path: str, already_found: List[str]) -> List[Dict[str, Any]]:
    """Извлекает позиции из текста через LLM (Pass 2). Map-Reduce: чанки → LLM → merge."""
    try:
        resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": path})
        resp.raise_for_status()
        text = resp.json().get("text", "")
        if not text.strip():
            return []
    except Exception as e:
        print(f"[Extract] LLM extraction failed for {path}: {e}")
        raise

    chunks = await _chunk_text(client, text)
    print(f"[LLM Extract] {len(chunks)} chunk(s) for {os.path.basename(path)} ({len(text)} chars)")

    all_items = []
    accumulated_names = list(already_found)

    for idx, chunk in enumerate(chunks):
        try:
            chunk_items = await _extract_from_single_chunk(
                chunk_text=chunk,
                chunk_idx=idx,
                total_chunks=len(chunks),
                already_found=accumulated_names,
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

    async with httpx.AsyncClient(timeout=180.0) as client:
        # === Документ 1 ===
        items_1_table = []
        items_1_text = []
        try:
            items_1_table = await _extract_tables_from_doc(client, state["input_1"])
            print(f"  [Doc1] Tables: {len(items_1_table)} items")
        except Exception as e:
            errors.append(f"Table extraction doc1 failed: {e}")

        try:
            already_names = [it["name"] for it in items_1_table]
            items_1_text = await _extract_items_llm(client, state["input_1"], already_names)
            print(f"  [Doc1] LLM text: {len(items_1_text)} items")
        except Exception as e:
            errors.append(f"LLM extraction doc1 failed: {e}")

        items_1 = _dedup_items(items_1_table + items_1_text)

        # === Документ 2 ===
        items_2_table = []
        items_2_text = []
        try:
            items_2_table = await _extract_tables_from_doc(client, state["input_2"])
            print(f"  [Doc2] Tables: {len(items_2_table)} items")
        except Exception as e:
            errors.append(f"Table extraction doc2 failed: {e}")

        try:
            already_names = [it["name"] for it in items_2_table]
            items_2_text = await _extract_items_llm(client, state["input_2"], already_names)
            print(f"  [Doc2] LLM text: {len(items_2_text)} items")
        except Exception as e:
            errors.append(f"LLM extraction doc2 failed: {e}")

        items_2 = _dedup_items(items_2_table + items_2_text)

    print(f"  [Equipment] Total: doc1={len(items_1)}, doc2={len(items_2)}")
    return {"items_1": items_1, "items_2": items_2, "errors": errors}


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

    # Извлекаем артикул/модель из "(Аналог X)" и добавляем в текст для LaBSE
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
    """Node 2: Семантический матчинг через Legal Server (LaBSE + Hungarian)."""
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

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            resp = await client.post(f"{MCP_LEGAL_SERVER_URL}/match_batches", json={
                "list_old": list_old,
                "list_new": list_new,
                "threshold": 0.45,  # Снижен с 0.55 для лучшего recall аналогов
            })
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "error":
                raise RuntimeError(data.get("error", "Unknown error from legal server"))

            raw_matches = data.get("matches", [])

            # Обогащаем оригинальными items через обратный маппинг
            enriched = []
            for m in raw_matches:
                old_text = m.get("old_text", "")
                new_text = m.get("new_text", "")

                # Обратный маппинг по тексту
                item_1 = None
                item_2 = None
                for i, txt in enumerate(list_old):
                    if txt == old_text:
                        item_1 = items_1[i]
                        break
                for i, txt in enumerate(list_new):
                    if txt == new_text:
                        item_2 = items_2[i]
                        break

                enriched.append({
                    **m,
                    "item_1": item_1,
                    "item_2": item_2,
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
            response = await ums_client.async_infer("qwen-14b-llm", {
                "prompt": prompt, "max_tokens": 600, "temperature": 0.1, "echo": False,
            })
            content = response.get("content", "")
            if not content and "choices" in response:
                content = response["choices"][0].get("text", "")
            elif not content and "result" in response:
                r = response["result"]
                if isinstance(r, dict) and "choices" in r:
                    content = r["choices"][0].get("text", "")

            parsed = parse_json_garbage(content)
            if not isinstance(parsed, list):
                parsed = [parsed] if isinstance(parsed, dict) else []

            for idx, m in enumerate(batch):
                item_data = parsed[idx] if idx < len(parsed) else {}
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
            for m in batch:
                results.append({
                    "item_1": m.get("item_1"),
                    "item_2": m.get("item_2"),
                    "result": "ERROR",
                    "reason": f"Ошибка LLM: {e}",
                    "type": m.get("type", "MODIFIED"),
                })

    return {"analysis_results": results}


# === Node 4: Report ===

async def generate_equipment_report_node(state: EquipmentState) -> dict:
    """Node 4: Генерация Markdown отчёта с сохранением и дедупликацией."""
    import glob as glob_mod

    name_1 = state.get("name_1") or os.path.basename(state["input_1"])
    name_2 = state.get("name_2") or os.path.basename(state["input_2"])
    mode = state.get("mode", "tz_vs_smeta")
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

        # Детальная таблица
        report += "## Детальный анализ\n\n"
        report += "| # | Позиция 1 | Позиция 2 | Статус | Примечание |\n"
        report += "| :---: | :--- | :--- | :---: | :--- |\n"

        _ICONS = {
            "PASS": "✅", "SAME": "✅",
            "PARTIAL": "⚠️", "OVER": "📈",
            "FAIL": "❌", "GAP": "🔴",
            "PRICE_CHANGE": "💰", "SPEC_CHANGE": "🔧", "REPLACED": "🔄",
            "ERROR": "⚙️",
        }

        for idx, r in enumerate(results, 1):
            i1 = r.get("item_1") or {}
            i2 = r.get("item_2") or {}
            name1 = i1.get("name", "-")[:60]
            name2 = i2.get("name", "-")[:60]
            status = r.get("result", "?")
            icon = _ICONS.get(status, "❓")
            reason = r.get("reason", "")[:80]
            report += f"| {idx} | {name1} | {name2} | {icon} {status} | {reason} |\n"

        report += "\n"

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
