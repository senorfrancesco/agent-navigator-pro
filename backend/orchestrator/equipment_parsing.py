from __future__ import annotations

import html
import os
import re
from typing import Any, Dict, List, Optional, TypedDict
from zipfile import ZipFile


class DocumentElement(TypedDict):
    type: str
    text: str
    page: Optional[int]
    metadata: Dict[str, Any]


class DocumentRoleDetection(TypedDict):
    role: str
    confidence: float
    feature_scores: Dict[str, float]


class ParserStrategyMetadata(TypedDict):
    strategy_name: str
    role: str
    confidence: float
    matched_blocks: int
    feature_scores: Dict[str, float]


def _normalize_line(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _load_docx_logical_lines(path: str) -> List[str]:
    if os.path.splitext(path)[1].lower() != ".docx":
        return []
    try:
        with ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception:
        return []

    text = (
        xml.replace("</w:tc>", "\t")
        .replace("</w:tr>", "\n")
        .replace("</w:p>", "\n")
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [_normalize_line(line) for line in text.splitlines()]
    return [line for line in lines if line]


def load_docx_elements(path: str) -> List[DocumentElement]:
    elements: List[DocumentElement] = []
    for line in _load_docx_logical_lines(path):
        element_type = "paragraph"
        if re.match(r"^\d+[\.\)]\s*", line):
            element_type = "row_like"
        elif re.fullmatch(r"\d+", line):
            element_type = "list_item"
        elif len(line) < 120 and line == line.upper():
            element_type = "title"
        elif "\t" in line:
            element_type = "row_like"
        elements.append(
            {
                "type": element_type,
                "text": line,
                "page": None,
                "metadata": {},
            }
        )
    return elements


def _safe_ratio(numerator: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))


def _count_offer_row_candidates(texts: List[str]) -> int:
    count = 0
    pattern = re.compile(
        r"^\d+\s*.+\d{1,4}\d{1,3}(?: \d{3})*,\d{2}\d{1,3}(?: \d{3})*,\d{2}$"
    )
    for text in texts:
        if pattern.match(text):
            count += 1
    return count


def score_document_role(elements: List[DocumentElement]) -> DocumentRoleDetection:
    texts = [_normalize_line(element.get("text", "")) for element in elements if _normalize_line(element.get("text", ""))]
    if not texts:
        return {"role": "unknown", "confidence": 0.0, "feature_scores": {"offer": 0.0, "specification": 0.0, "generic_list": 0.0}}

    head = " ".join(texts[:40]).lower()
    total = len(texts)
    offer_row_candidates = _count_offer_row_candidates(texts)
    numbered_rows = sum(1 for text in texts if re.match(r"^\d+[\.\)]\s*", text))
    standalone_numbers = sum(1 for text in texts if re.fullmatch(r"\d+", text))
    money_lines = sum(1 for text in texts if len(re.findall(r"\d[\d\s]*,\d{2}", text)) >= 2)
    specification_headers = sum(
        1
        for marker in (
            "наименование оборудования",
            "технические характеристики",
            "страна происхождения",
            "№ п/п",
        )
        if marker in head
    )
    offer_lexicon = sum(
        1
        for marker in (
            "коммерческое предложение",
            "срок действия предложения",
            "цена за ед",
            "всего с ндс",
        )
        if marker in head
    )
    list_like_lines = sum(1 for text in texts if text.startswith("-") or text.startswith("•"))

    offer_score = (
        0.50 * _safe_ratio(offer_row_candidates, 3)
        + 0.20 * _safe_ratio(money_lines, 3)
        + 0.20 * _safe_ratio(offer_lexicon, 3)
        + 0.10 * _safe_ratio(numbered_rows, max(total, 1))
    )
    specification_score = (
        0.35 * _safe_ratio(specification_headers, 3)
        + 0.30 * _safe_ratio(numbered_rows + standalone_numbers, 6)
        + 0.25 * _safe_ratio(sum(1 for text in texts if len(text) > 40), total)
        + 0.10 * _safe_ratio(sum(1 for text in texts if "не менее" in text.lower()), 4)
    )
    generic_list_score = (
        0.45 * _safe_ratio(list_like_lines + standalone_numbers, 8)
        + 0.30 * _safe_ratio(numbered_rows, 6)
        + 0.25 * _safe_ratio(sum(1 for text in texts if 4 <= len(text.split()) <= 18), total)
    )

    feature_scores = {
        "offer": round(offer_score, 4),
        "specification": round(specification_score, 4),
        "generic_list": round(generic_list_score, 4),
    }
    role_map = {
        "offer": "offer",
        "specification": "specification",
        "generic_list": "generic_list",
    }
    top_key = max(feature_scores, key=feature_scores.get)
    sorted_scores = sorted(feature_scores.values(), reverse=True)
    margin = sorted_scores[0] - (sorted_scores[1] if len(sorted_scores) > 1 else 0.0)
    confidence = min(1.0, feature_scores[top_key] + max(0.0, margin) * 0.5)
    return {
        "role": role_map.get(top_key, "unknown"),
        "confidence": round(confidence, 4),
        "feature_scores": feature_scores,
    }


def _parse_ru_money(value: str) -> Optional[float]:
    normalized_value = str(value or "").replace(" ", "").replace(",", ".")
    try:
        return float(normalized_value)
    except ValueError:
        return None


def _build_item_from_offer_row(line: str) -> Optional[Dict[str, Any]]:
    normalized = _normalize_line(line)
    if not normalized or normalized.lower().startswith("итого"):
        return None

    row_match = re.match(
        r"^(?P<row>\d+)\s*(?P<name_and_qty>.+?)(?P<price>\d{1,3}(?: \d{3})*,\d{2})(?P<total>\d{1,3}(?: \d{3})*,\d{2})$",
        normalized,
    )
    if row_match is None:
        return None

    price_value = row_match.group("price").strip()
    total_value = row_match.group("total").strip()
    name_qty_match = re.match(r"^(?P<name>.+?)(?P<qty>\d{1,4})$", row_match.group("name_and_qty").strip())
    if name_qty_match is None:
        total_amount = _parse_ru_money(total_value)
        repaired_match = None
        for qty_len in range(1, 4):
            qty_candidate = price_value[:qty_len]
            unit_price_candidate = price_value[qty_len:].strip()
            if not re.fullmatch(r"\d{1,3}(?: \d{3})*,\d{2}", unit_price_candidate):
                continue
            unit_amount = _parse_ru_money(unit_price_candidate)
            if total_amount is None or unit_amount is None:
                continue
            if abs((unit_amount * int(qty_candidate)) - total_amount) <= 0.01:
                repaired_match = {
                    "name": row_match.group("name_and_qty").strip(),
                    "qty": qty_candidate,
                    "price": unit_price_candidate,
                }
                break
        if repaired_match is None:
            return None
        raw_name = repaired_match["name"].strip(" -")
        quantity = repaired_match["qty"]
        price_value = repaired_match["price"]
    else:
        raw_name = name_qty_match.group("name").strip(" -")
        quantity = name_qty_match.group("qty")

    specs = ""
    paren_match = re.match(r"^(?P<name>.+?)\s*\((?P<specs>.+)\)$", raw_name)
    if paren_match is not None:
        raw_name = paren_match.group("name").strip(" -")
        specs = paren_match.group("specs").strip()
    if len(raw_name) < 3:
        return None
    return {
        "name": raw_name,
        "specs": specs,
        "quantity": quantity,
        "price": price_value,
        "unit": "шт.",
        "source": "docx_text_fallback",
        "page": None,
    }


def parse_offer_like_elements(elements: List[DocumentElement]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for element in elements:
        item = _build_item_from_offer_row(element.get("text", ""))
        if item is not None:
            items.append(item)
    return items


def parse_specification_like_elements(elements: List[DocumentElement]) -> List[Dict[str, Any]]:
    texts = [_normalize_line(element.get("text", "")) for element in elements if _normalize_line(element.get("text", ""))]
    if not texts:
        return []

    header_idx = -1
    for idx, text in enumerate(texts):
        compact = text.lower().replace(" ", "")
        if compact.startswith("№п/п") or ("наименование оборудования" in text.lower() and idx + 1 < len(texts)):
            header_idx = idx
            break
    if header_idx < 0:
        return []

    header_tail_idx = header_idx
    for idx in range(header_idx, min(len(texts), header_idx + 12)):
        if "страна происхождения" in texts[idx].lower():
            header_tail_idx = idx
            break

    items: List[Dict[str, Any]] = []
    current_item: Optional[Dict[str, Any]] = None
    pending_index = False
    service_prefixes = (
        "цена за ед",
        "итоговая",
        "стоимость",
        "с учетом ндс",
        "страна происхождения",
        "кол-во",
        "наименование оборудования",
        "технические характеристики",
    )

    def _flush_current() -> None:
        nonlocal current_item
        if not current_item:
            return
        spec_lines = current_item.pop("_spec_lines", [])
        current_item["specs"] = "; ".join(spec_lines[:20]).strip()
        if current_item.get("name"):
            items.append(current_item)
        current_item = None

    for idx in range(header_tail_idx + 1, len(texts)):
        line = texts[idx]
        lower_line = line.lower()
        if any(lower_line.startswith(prefix) for prefix in service_prefixes):
            continue
        next_line = texts[idx + 1] if idx + 1 < len(texts) else ""

        if current_item and re.fullmatch(r"\d+", line) and not current_item.get("quantity"):
            if next_line == "" or re.fullmatch(r"\d+[\.\)]?", next_line):
                current_item["quantity"] = line
                continue

        row_with_name = re.match(r"^(?P<row>\d+)[\.\)]\s*(?P<name>.+)$", line)
        if row_with_name:
            _flush_current()
            current_item = {
                "name": row_with_name.group("name").strip(),
                "specs": "",
                "quantity": "",
                "price": "",
                "unit": "",
                "source": "docx_text_fallback",
                "page": None,
                "_spec_lines": [],
            }
            pending_index = False
            continue

        if re.fullmatch(r"\d+[\.\)]?", line):
            _flush_current()
            pending_index = True
            continue

        if pending_index:
            current_item = {
                "name": line.strip(),
                "specs": "",
                "quantity": "",
                "price": "",
                "unit": "",
                "source": "docx_text_fallback",
                "page": None,
                "_spec_lines": [],
            }
            pending_index = False
            continue

        if current_item is not None:
            current_item["_spec_lines"].append(line)

    _flush_current()
    return items


def parse_generic_list_like_elements(elements: List[DocumentElement]) -> List[Dict[str, Any]]:
    texts = [_normalize_line(element.get("text", "")) for element in elements if _normalize_line(element.get("text", ""))]
    items: List[Dict[str, Any]] = []
    for text in texts:
        if len(text) < 8:
            continue
        if text.lower().startswith(("итого", "всего", "срок поставки", "условия оплаты", "гарантия")):
            continue
        if re.match(r"^\d+[\.\)]\s*(.+)$", text):
            name = re.sub(r"^\d+[\.\)]\s*", "", text).strip()
            items.append(
                {
                    "name": name,
                    "specs": "",
                    "quantity": "",
                    "price": "",
                    "unit": "",
                    "source": "docx_text_fallback",
                    "page": None,
                }
            )
    return items


def _extract_items_from_docx_lines_fallback(lines: List[str]) -> List[Dict[str, Any]]:
    elements: List[DocumentElement] = [
        {"type": "paragraph", "text": _normalize_line(line), "page": None, "metadata": {}}
        for line in lines
        if _normalize_line(line)
    ]
    role_detection = score_document_role(elements)
    if role_detection["role"] == "offer":
        return parse_offer_like_elements(elements)
    if role_detection["role"] == "specification":
        return parse_specification_like_elements(elements)
    return parse_generic_list_like_elements(elements)


def extract_items_docx_fallback(path: str, already_found: List[str]) -> tuple[List[Dict[str, Any]], ParserStrategyMetadata]:
    elements = load_docx_elements(path)
    role_detection = score_document_role(elements)
    strategy_order = {
        "offer": ("offer_like", parse_offer_like_elements),
        "specification": ("specification_like", parse_specification_like_elements),
        "generic_list": ("generic_list_like", parse_generic_list_like_elements),
    }
    strategy_name, strategy_fn = strategy_order.get(
        role_detection["role"],
        ("generic_list_like", parse_generic_list_like_elements),
    )
    items = strategy_fn(elements)
    if len(items) < 2 and role_detection["confidence"] < 0.6:
        fallback_items = parse_generic_list_like_elements(elements)
        if len(fallback_items) > len(items):
            items = fallback_items
            strategy_name = "generic_list_like"

    existing = {str(name or "").strip().lower() for name in already_found}
    if existing:
        items = [item for item in items if str(item.get("name", "")).strip().lower() not in existing]

    metadata: ParserStrategyMetadata = {
        "strategy_name": strategy_name,
        "role": role_detection["role"],
        "confidence": role_detection["confidence"],
        "matched_blocks": len(items),
        "feature_scores": role_detection["feature_scores"],
    }
    return items, metadata
