from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Dict, List, Sequence, Tuple


@dataclass(frozen=True)
class CanonicalSummaryCase:
    case_id: str
    title: str
    document_text: str
    expected_sections: Tuple[str, ...]
    expected_terms: Tuple[str, ...]
    safe_final_merge: bool = True
    chunk_chars: int = 1200
    final_budget_tokens: int = 1200
    reserve_tokens: int = 128


@dataclass(frozen=True)
class PathResult:
    strategy: str
    summary_text: str
    sections_present: Tuple[str, ...]
    entities_present: Tuple[str, ...]
    coverage: float
    completeness: float
    model_calls: int
    latency_surrogate: float


@dataclass(frozen=True)
class ComparisonResult:
    case_id: str
    baseline: PathResult
    adaptive: PathResult
    passed: bool
    failures: Tuple[str, ...]


def _estimate_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // 4)


def _split_into_chunks(text: str, *, max_chars: int) -> List[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    paragraphs = [part.strip() for part in normalized.split("\n\n") if part.strip()]
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


def _normalize_label(value: str) -> str:
    return " ".join(str(value or "").strip().split()).strip()


def _extract_signals(case: CanonicalSummaryCase) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    text = case.document_text.lower()
    sections = []
    for section in case.expected_sections:
        normalized = _normalize_label(section)
        if normalized and normalized.lower() in text:
            sections.append(normalized)
    terms = []
    for term in case.expected_terms:
        normalized = _normalize_label(term)
        if normalized and normalized.lower() in text:
            terms.append(normalized)
    return tuple(dict.fromkeys(sections)), tuple(dict.fromkeys(terms))


def _build_summary_text(case: CanonicalSummaryCase, *, strategy: str) -> Tuple[str, Tuple[str, ...], Tuple[str, ...]]:
    sections_present, entities_present = _extract_signals(case)
    lines = [f"# {case.title}", ""]
    if strategy == "baseline":
        lines.append("## Консервативная сводка")
    else:
        lines.append("## Адаптивная сводка")
    lines.append("")
    if sections_present:
        lines.append("### Разделы")
        lines.extend(f"- {section}" for section in sections_present)
        lines.append("")
    if entities_present:
        lines.append("### Ключевые термины")
        lines.extend(f"- {term}" for term in entities_present)
        lines.append("")
    summary_text = "\n".join(lines).strip()
    return summary_text, sections_present, entities_present


def _compute_metrics(case: CanonicalSummaryCase, summary_text: str, sections_present: Sequence[str], entities_present: Sequence[str]) -> Tuple[float, float]:
    section_hits = len([item for item in case.expected_sections if _normalize_label(item) in sections_present])
    term_hits = len([item for item in case.expected_terms if _normalize_label(item) in entities_present])
    completeness = section_hits / max(1, len(case.expected_sections))
    coverage = term_hits / max(1, len(case.expected_terms))
    if not case.expected_terms:
        coverage = 1.0
    return round(coverage, 4), round(completeness, 4)


def _simulate_model_calls(case: CanonicalSummaryCase, *, strategy: str) -> int:
    chunks = _split_into_chunks(case.document_text, max_chars=case.chunk_chars)
    chunk_calls = max(1, len(chunks))
    if len(chunks) <= 1:
        return chunk_calls + 1
    if strategy == "adaptive" and case.safe_final_merge:
        return chunk_calls + 1
    levels = 0
    items = len(chunks)
    while items > 1:
        items = ceil(items / 2)
        levels += 1
    return chunk_calls + levels + 1


def _simulate_latency(model_calls: int, document_text: str) -> float:
    token_estimate = _estimate_tokens(document_text)
    return round(model_calls * 0.35 + token_estimate / 9000.0, 4)


def build_canonical_cases() -> List[CanonicalSummaryCase]:
    return [
        CanonicalSummaryCase(
            case_id="short-structured",
            title="Краткая структурированная сводка",
            document_text=(
                "# Техническое задание\n\n"
                "## Сроки\n- Поставка в течение 10 дней.\n\n"
                "## Гарантия\n- 12 месяцев.\n\n"
                "## Оплата\n- 50/50 после поставки."
            ),
            expected_sections=("Техническое задание", "Сроки", "Гарантия", "Оплата"),
            expected_terms=("10 дней", "12 месяцев", "50/50"),
            safe_final_merge=True,
            chunk_chars=900,
            final_budget_tokens=700,
        ),
        CanonicalSummaryCase(
            case_id="long-narrative",
            title="Длинная повествовательная сводка",
            document_text=(
                "В документе описан большой проект с несколькими этапами.\n\n"
                "Этап 1: подготовка требований, согласование сроков и ответственных.\n\n"
                "Этап 2: реализация, контроль качества, тестирование и приёмка.\n\n"
                "Этап 3: сопровождение, обучение команды, закрытие рисков и отчётность.\n\n"
                "Ключевые риски включают задержки поставки, ограничение бюджета и зависимость от внешних подрядчиков."
            ),
            expected_sections=("Этап 1", "Этап 2", "Этап 3", "Ключевые риски"),
            expected_terms=("сроков", "контроль качества", "задержки поставки", "бюджета"),
            safe_final_merge=True,
            chunk_chars=220,
            final_budget_tokens=950,
        ),
        CanonicalSummaryCase(
            case_id="mixed-headings",
            title="Смешанная структура с заголовками",
            document_text=(
                "# Обзор\n"
                "Проект состоит из нескольких частей и требует аккуратной структуры.\n\n"
                "## Архитектура\n"
                "Система разделена на контрольный слой, данные и отчёты.\n\n"
                "## Сроки и ограничения\n"
                "Срок реализации ограничен 30 днями, а изменения после согласования считаются scope creep.\n\n"
                "## Итог\n"
                "Документ фиксирует требования, ограничения и ожидаемый результат."
            ),
            expected_sections=("Обзор", "Архитектура", "Сроки и ограничения", "Итог"),
            expected_terms=("30 днями", "scope creep", "требования", "ограничения"),
            safe_final_merge=True,
            chunk_chars=260,
            final_budget_tokens=850,
        ),
        CanonicalSummaryCase(
            case_id="collapse-sensitive",
            title="Чувствительный к collapse документ",
            document_text=(
                "Пункт 1. Общие требования: система должна быть надёжной, воспроизводимой и наблюдаемой.\n\n"
                "Пункт 2. Функциональные требования: сохранять структуру, перечислять все ключевые ограничения и не терять детали.\n\n"
                "Пункт 3. Нефункциональные требования: производительность, стабильность и прозрачные метрики.\n\n"
                "Пункт 4. Контроль качества: обязательна проверка полноты и отсутствие потерь секций."
            ),
            expected_sections=("Пункт 1", "Пункт 2", "Пункт 3", "Пункт 4"),
            expected_terms=("надёжной", "сохранять структуру", "прозрачные метрики", "полноты"),
            safe_final_merge=False,
            chunk_chars=180,
            final_budget_tokens=420,
        ),
    ]


def simulate_path(case: CanonicalSummaryCase, *, strategy: str) -> PathResult:
    if strategy not in {"baseline", "adaptive"}:
        raise ValueError(f"Unsupported strategy: {strategy}")

    summary_text, sections_present, entities_present = _build_summary_text(case, strategy=strategy)
    coverage, completeness = _compute_metrics(case, summary_text, sections_present, entities_present)
    model_calls = _simulate_model_calls(case, strategy=strategy)
    latency_surrogate = _simulate_latency(model_calls, case.document_text)

    return PathResult(
        strategy=strategy,
        summary_text=summary_text,
        sections_present=sections_present,
        entities_present=entities_present,
        coverage=coverage,
        completeness=completeness,
        model_calls=model_calls,
        latency_surrogate=latency_surrogate,
    )


def compare_results(*, case: CanonicalSummaryCase, baseline: PathResult, adaptive: PathResult) -> ComparisonResult:
    failures: List[str] = []
    if adaptive.completeness < baseline.completeness:
        failures.append("completeness_regression")
    if adaptive.coverage < baseline.coverage:
        failures.append("coverage_regression")
    if adaptive.model_calls > baseline.model_calls:
        failures.append("model_call_regression")
    if adaptive.latency_surrogate > baseline.latency_surrogate:
        failures.append("latency_regression")
    return ComparisonResult(
        case_id=case.case_id,
        baseline=baseline,
        adaptive=adaptive,
        passed=not failures,
        failures=tuple(failures),
    )


def evaluate_case(case: CanonicalSummaryCase) -> ComparisonResult:
    baseline = simulate_path(case, strategy="baseline")
    adaptive = simulate_path(case, strategy="adaptive")
    return compare_results(case=case, baseline=baseline, adaptive=adaptive)

