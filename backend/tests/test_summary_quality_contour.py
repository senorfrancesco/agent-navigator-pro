import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.evals import summary_quality_contour as contour


def test_build_canonical_cases_returns_four_synthetic_documents():
    cases = contour.build_canonical_cases()

    assert [case.case_id for case in cases] == [
        "short-structured",
        "long-narrative",
        "mixed-headings",
        "collapse-sensitive",
    ]
    assert all(case.document_text.strip() for case in cases)


def test_adaptive_contour_is_not_worse_than_baseline_for_all_canonical_cases():
    cases = contour.build_canonical_cases()

    results = [contour.evaluate_case(case) for case in cases]

    assert all(result.passed for result in results)
    assert all(result.adaptive.model_calls <= result.baseline.model_calls for result in results)
    assert all(result.adaptive.completeness >= result.baseline.completeness for result in results)
    assert all(result.adaptive.coverage >= result.baseline.coverage for result in results)


def test_quality_contour_detects_missing_section_regression():
    case = contour.build_canonical_cases()[2]
    baseline = contour.simulate_path(case, strategy="baseline")
    degraded_adaptive = contour.PathResult(
        strategy="adaptive",
        summary_text="## Введение\nСводка неполная.",
        sections_present=("Введение",),
        entities_present=("ТЗ",),
        coverage=0.25,
        completeness=0.25,
        model_calls=1,
        latency_surrogate=0.1,
    )

    result = contour.compare_results(case=case, baseline=baseline, adaptive=degraded_adaptive)

    assert result.passed is False
    assert "completeness_regression" in result.failures
    assert "coverage_regression" in result.failures

