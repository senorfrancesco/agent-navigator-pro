from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Literal, Optional, TypedDict


DOC_QA_MIN_CHUNKS_SIMPLE = int(os.getenv("DOC_QA_MIN_CHUNKS_SIMPLE", "1"))
DOC_QA_MIN_CHUNKS_MULTIHOP = int(os.getenv("DOC_QA_MIN_CHUNKS_MULTIHOP", "2"))
DOC_QA_MIN_RAW_SCORE_SIMPLE = float(os.getenv("DOC_QA_MIN_RAW_SCORE_SIMPLE", "0.01"))
DOC_QA_MIN_ZSCORE_CORRECTIVE = float(os.getenv("DOC_QA_MIN_ZSCORE_CORRECTIVE", "-0.5"))

_MULTIHOP_MARKERS = ["сравни", "сопостав", "что подходит", "какие отличия", " и ", " vs ", " между "]
_CROSS_DOC_MARKERS = ["сравни", "сопостав", "отличия", "разница", "между", " vs "]


class SourceRef(TypedDict, total=False):
    source_id: int
    document_id: str
    display_name: str
    chunk_id: int
    collection_id: Optional[str]
    source_origin: Optional[str]
    section: Optional[str]
    char_span: Dict[str, Optional[int]]
    page: Optional[int]
    quote: str
    raw_score: float
    normalized_score: float
    grade: Optional[str]
    z_score: Optional[float]


class DocQuestionResponse(TypedDict):
    answer_text: str
    sources: List[SourceRef]
    source_scope_summary: str
    answer_mode: Literal["grounded_answer", "insufficient_evidence"]
    fallback_type: Literal["none", "citation_validation_failed", "insufficient_evidence"]
    fallback_reason: Optional[str]
    confidence: float
    confidence_label: Literal["high", "medium", "low"]
    confidence_method: Literal["heuristic_v1"]
    confidence_version: Literal["1"]


class CoverageSummary(TypedDict):
    total_source_count: int
    total_document_count: int
    cited_source_count: int
    cited_document_count: int
    coverage_ratio: float
    is_multihop: bool
    requires_cross_document_support: bool
    has_cross_document_support: bool


def extract_citation_ids(answer_text: str) -> List[int]:
    return [int(m.group(1)) for m in re.finditer(r"\[(\d+)\]", answer_text or "")]


def citations_are_valid(answer_text: str, source_count: int) -> bool:
    cited = extract_citation_ids(answer_text)
    if not cited:
        return False
    return all(1 <= cid <= source_count for cid in cited)


def is_multihop_query(query: str) -> bool:
    query_lower = f" {(query or '').lower()} "
    return any(marker in query_lower for marker in _MULTIHOP_MARKERS)


def _requires_cross_document_support(query: str) -> bool:
    query_lower = f" {(query or '').lower()} "
    return any(marker in query_lower for marker in _CROSS_DOC_MARKERS)


def compute_coverage_v1(
    sources: List[SourceRef],
    cited_ids: List[int],
    query: str,
) -> CoverageSummary:
    cited = [source for source in sources if source.get("source_id") in cited_ids]
    cited_doc_ids = {
        str(source.get("document_id") or source.get("display_name") or source.get("source_id"))
        for source in cited
    }
    all_doc_ids = {
        str(source.get("document_id") or source.get("display_name") or source.get("source_id"))
        for source in sources
    }
    denom = max(1, min(len(sources), 3))
    coverage_ratio = min(1.0, len(cited) / denom)
    requires_cross_document_support = _requires_cross_document_support(query) and len(all_doc_ids) >= 2
    return {
        "total_source_count": len(sources),
        "total_document_count": len(all_doc_ids),
        "cited_source_count": len(cited),
        "cited_document_count": len(cited_doc_ids),
        "coverage_ratio": coverage_ratio,
        "is_multihop": is_multihop_query(query),
        "requires_cross_document_support": requires_cross_document_support,
        "has_cross_document_support": len(cited_doc_ids) >= 2,
    }


def has_sufficient_evidence_v1(
    sources: List[SourceRef],
    mode: str,
    query: str,
    citations_valid: bool,
    cited_ids: Optional[List[int]] = None,
) -> bool:
    if not citations_valid:
        return False
    effective_cited_ids = cited_ids if cited_ids is not None else [int(source.get("source_id", 0)) for source in sources]
    coverage = compute_coverage_v1(sources, effective_cited_ids, query)
    min_chunks = DOC_QA_MIN_CHUNKS_MULTIHOP if coverage["is_multihop"] else DOC_QA_MIN_CHUNKS_SIMPLE
    if coverage["total_source_count"] < min_chunks or coverage["cited_source_count"] < min_chunks:
        return False
    if coverage["requires_cross_document_support"] and not coverage["has_cross_document_support"]:
        return False

    cited_sources = [source for source in sources if source.get("source_id") in effective_cited_ids]
    top_source = cited_sources[0] if cited_sources else (sources[0] if sources else None)
    if top_source is None:
        return False

    raw_top = float(top_source.get("raw_score", 0.0))
    z_top = top_source.get("z_score")
    grade_top = (top_source.get("grade") or "").lower()
    mode = (mode or "simple").lower()

    if coverage["coverage_ratio"] <= 0.0:
        return False

    if mode in ("corrective", "agentic", "iterative"):
        if z_top is not None:
            return float(z_top) >= DOC_QA_MIN_ZSCORE_CORRECTIVE
        return grade_top in ("excellent", "good") or raw_top >= DOC_QA_MIN_RAW_SCORE_SIMPLE

    return raw_top >= DOC_QA_MIN_RAW_SCORE_SIMPLE


def _confidence_label(value: float) -> Literal["high", "medium", "low"]:
    if value >= 0.75:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"


def compute_confidence_v1(
    sources: List[SourceRef],
    cited_ids: List[int],
    answer_mode: Literal["grounded_answer", "insufficient_evidence"],
    *,
    query: str = "",
) -> tuple[float, Literal["high", "medium", "low"]]:
    cited_sources = [source for source in sources if source.get("source_id") in cited_ids] if cited_ids else []
    coverage = compute_coverage_v1(sources, cited_ids, query)

    if not cited_sources:
        base = 0.2
    else:
        avg_raw = sum(float(source.get("raw_score", 0.0)) for source in cited_sources) / len(cited_sources)
        avg_norm = sum(float(source.get("normalized_score", 0.0)) for source in cited_sources) / len(cited_sources)
        good_bonus = (
            0.08 if any((source.get("grade") or "").lower() in ("good", "excellent") for source in cited_sources) else 0.0
        )
        coverage_bonus = 0.12 * coverage["coverage_ratio"]
        multihop_bonus = 0.05 if coverage["is_multihop"] and coverage["cited_source_count"] >= 2 else 0.0
        multihop_penalty = -0.12 if coverage["is_multihop"] and coverage["cited_source_count"] < 2 else 0.0
        cross_doc_bonus = 0.05 if coverage["has_cross_document_support"] else 0.0
        cross_doc_penalty = -0.08 if coverage["requires_cross_document_support"] and not coverage["has_cross_document_support"] else 0.0
        base = (
            0.25
            + 0.30 * avg_norm
            + 0.20 * avg_raw
            + coverage_bonus
            + good_bonus
            + multihop_bonus
            + multihop_penalty
            + cross_doc_bonus
            + cross_doc_penalty
        )
        base = max(0.0, min(1.0, base))

    if answer_mode == "insufficient_evidence":
        base = min(base, 0.35)
    return base, _confidence_label(base)


def build_doc_question_deterministic_fallback(
    query: str,
    sources: List[SourceRef],
    fallback_type: Literal["citation_validation_failed", "insufficient_evidence"],
    fallback_reason: Optional[str] = None,
    source_scope_summary: str = "off",
) -> DocQuestionResponse:
    top_sources = sources[:2]
    if top_sources:
        lines = [
            f"По запросу «{query}» в найденных фрагментах есть только следующие подтверждённые данные:",
        ]
        for source in top_sources:
            lines.append(f"- [{source['source_id']}] {source['quote']}")
        lines.append("Данных недостаточно для точного вывода без дополнительных подтверждений.")
        answer_text = "\n".join(lines)
    else:
        answer_text = (
            f"По запросу «{query}» в текущем контексте загруженных документов "
            "недостаточно подтверждённых данных для точного вывода."
        )

    confidence, label = compute_confidence_v1(sources, [], "insufficient_evidence", query=query)
    return {
        "answer_text": answer_text,
        "sources": sources,
        "source_scope_summary": source_scope_summary,
        "answer_mode": "insufficient_evidence",
        "fallback_type": fallback_type,
        "fallback_reason": fallback_reason or "Недостаточно подтверждённых данных или невалидный citation-ответ модели.",
        "confidence": confidence,
        "confidence_label": label,
        "confidence_method": "heuristic_v1",
        "confidence_version": "1",
    }
