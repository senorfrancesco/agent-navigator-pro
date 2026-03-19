#!/usr/bin/env python3
"""
Agent Navigator Pro — Benchmark comparison tool.

Сравнивает два JSON-отчёта, собранных через scripts/benchmark.py,
и печатает operator-friendly сводку по latency/status/runtime metadata.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


RUNTIME_DIFF_KEYS = [
    "backend_mode",
    "runtime_profile",
    "effective_context_tokens",
    "retrieved_context_tokens_budget",
    "generation_tokens_reserve",
    "active_heavy_model",
    "running",
    "placements",
]


def _load_report(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ValueError(f"Benchmark report not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid benchmark JSON: {path}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Benchmark report must be an object: {path}")
    results = payload.get("results")
    summary = payload.get("summary")
    if not isinstance(results, list) or not isinstance(summary, dict):
        raise ValueError(f"Benchmark report missing results list or summary object: {path}")
    return payload


def _index_scenarios(results: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        scenario = item.get("scenario")
        if isinstance(scenario, str) and scenario:
            indexed[scenario] = item
    return indexed


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _safe_elapsed(result: Dict[str, Any]) -> Optional[float]:
    elapsed = result.get("elapsed_sec")
    if elapsed is None:
        return None
    try:
        return float(elapsed)
    except (TypeError, ValueError):
        return None


def _compare_status(baseline_status: str, candidate_status: str) -> str:
    if baseline_status == candidate_status == "ok":
        return "same_ok"
    if baseline_status == candidate_status:
        return "same_non_ok"
    if baseline_status == "ok" and candidate_status != "ok":
        return "regressed_status"
    if baseline_status != "ok" and candidate_status == "ok":
        return "improved_status"
    return "changed_non_ok"


def _compare_runtime_diff(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    baseline_status = baseline.get("runtime_metadata") or baseline.get("ums_status") or {}
    candidate_status = candidate.get("runtime_metadata") or candidate.get("ums_status") or {}
    runtime_diff = {
        "baseline": {},
        "candidate": {},
        "changed_keys": [],
    }
    for key in RUNTIME_DIFF_KEYS:
        base_value = baseline_status.get(key)
        cand_value = candidate_status.get(key)
        if base_value is not None:
            runtime_diff["baseline"][key] = base_value
        if cand_value is not None:
            runtime_diff["candidate"][key] = cand_value
        if base_value != cand_value:
            runtime_diff["changed_keys"].append(key)
    return runtime_diff


def compare_reports(
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    baseline_label: str = "baseline",
    candidate_label: str = "candidate",
    baseline_path: Optional[str] = None,
    candidate_path: Optional[str] = None,
    scenarios: Optional[Iterable[str]] = None,
    scenarios_filter: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    baseline_results = _index_scenarios(baseline.get("results", []))
    candidate_results = _index_scenarios(candidate.get("results", []))
    selected = set(scenarios or scenarios_filter or [])
    scenario_names = sorted(set(baseline_results) | set(candidate_results))
    if selected:
        scenario_names = [name for name in scenario_names if name in selected]

    scenarios: List[Dict[str, Any]] = []
    faster_count = 0
    slower_count = 0
    unchanged_count = 0
    improved_status_count = 0
    regressed_status_count = 0
    added_scenarios = 0
    removed_scenarios = 0

    for scenario in scenario_names:
        baseline_result = baseline_results.get(scenario)
        candidate_result = candidate_results.get(scenario)

        if baseline_result is None:
            added_scenarios += 1
            scenarios.append(
                {
                    "scenario": scenario,
                    "change_type": "added",
                    "status_change": "added",
                    "baseline_status": None,
                    "candidate_status": candidate_result.get("status"),
                    "baseline_elapsed_sec": None,
                    "candidate_elapsed_sec": _safe_elapsed(candidate_result),
                    "delta_sec": None,
                    "delta_pct": None,
                    "speedup": None,
                }
            )
            continue

        if candidate_result is None:
            removed_scenarios += 1
            scenarios.append(
                {
                    "scenario": scenario,
                    "change_type": "removed",
                    "status_change": "removed",
                    "baseline_status": baseline_result.get("status"),
                    "candidate_status": None,
                    "baseline_elapsed_sec": _safe_elapsed(baseline_result),
                    "candidate_elapsed_sec": None,
                    "delta_sec": None,
                    "delta_pct": None,
                    "speedup": None,
                }
            )
            continue

        baseline_status = str(baseline_result.get("status") or "unknown")
        candidate_status = str(candidate_result.get("status") or "unknown")
        status_change = _compare_status(baseline_status, candidate_status)
        baseline_elapsed = _safe_elapsed(baseline_result)
        candidate_elapsed = _safe_elapsed(candidate_result)
        delta_sec: Optional[float] = None
        delta_pct: Optional[float] = None
        speedup: Optional[float] = None

        if baseline_elapsed is not None and candidate_elapsed is not None:
            delta_sec = candidate_elapsed - baseline_elapsed
            if baseline_elapsed > 0:
                delta_pct = (delta_sec / baseline_elapsed) * 100.0
            if candidate_elapsed > 0:
                speedup = baseline_elapsed / candidate_elapsed
            if math.isclose(delta_sec, 0.0, abs_tol=1e-9):
                unchanged_count += 1
            elif delta_sec < 0:
                faster_count += 1
            else:
                slower_count += 1

        if status_change == "improved_status":
            improved_status_count += 1
        elif status_change == "regressed_status":
            regressed_status_count += 1

        scenarios.append(
            {
                "scenario": scenario,
                "change_type": "shared",
                "status_change": status_change,
                "baseline_status": baseline_status,
                "candidate_status": candidate_status,
                "baseline_elapsed_sec": _round(baseline_elapsed),
                "candidate_elapsed_sec": _round(candidate_elapsed),
                "delta_sec": _round(delta_sec),
                "delta_pct": _round(delta_pct, 2),
                "speedup": _round(speedup, 4),
            }
        )

    baseline_total_time = float((baseline.get("summary") or {}).get("total_time_sec") or 0.0)
    candidate_total_time = float((candidate.get("summary") or {}).get("total_time_sec") or 0.0)
    overall_speedup = None
    if candidate_total_time > 0:
        overall_speedup = baseline_total_time / candidate_total_time

    return {
        "baseline_label": baseline_label,
        "candidate_label": candidate_label,
        "baseline_path": baseline_path,
        "candidate_path": candidate_path,
        "summary": {
            "shared_scenarios": sum(1 for row in scenarios if row["change_type"] == "shared"),
            "added_scenarios": added_scenarios,
            "removed_scenarios": removed_scenarios,
            "improved_status_count": improved_status_count,
            "regressed_status_count": regressed_status_count,
            "faster_count": faster_count,
            "slower_count": slower_count,
            "unchanged_count": unchanged_count,
            "baseline_total_time_sec": _round(baseline_total_time, 2),
            "candidate_total_time_sec": _round(candidate_total_time, 2),
            "overall_speedup": _round(overall_speedup, 4),
        },
        "runtime_diff": _compare_runtime_diff(baseline, candidate),
        "scenarios": scenarios,
    }


def _status_marker(status_change: str) -> str:
    if status_change == "improved_status":
        return "+"
    if status_change == "regressed_status":
        return "-"
    if status_change in {"added", "removed"}:
        return "*"
    return "="


def _format_float(value: Optional[float], digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}{suffix}"


def render_console_report(comparison: Dict[str, Any]) -> str:
    lines = []
    summary = comparison["summary"]
    lines.append("=" * 72)
    lines.append(
        f"Benchmark Compare: {comparison['baseline_label']} -> {comparison['candidate_label']}"
    )
    lines.append("=" * 72)
    lines.append(
        "Shared: {shared} | Added: {added} | Removed: {removed} | Improved: {improved} | Regressed: {regressed}".format(
            shared=summary["shared_scenarios"],
            added=summary["added_scenarios"],
            removed=summary["removed_scenarios"],
            improved=summary["improved_status_count"],
            regressed=summary["regressed_status_count"],
        )
    )
    lines.append(
        "Total time: {base}s -> {cand}s | Overall speedup: {speedup}".format(
            base=_format_float(summary["baseline_total_time_sec"]),
            cand=_format_float(summary["candidate_total_time_sec"]),
            speedup=_format_float(summary["overall_speedup"], 2, "x"),
        )
    )
    runtime_changed = comparison["runtime_diff"]["changed_keys"]
    if runtime_changed:
        lines.append(f"Runtime changes: {', '.join(runtime_changed)}")
    lines.append("-" * 72)
    lines.append(
        f"{'Scenario':<20} {'Status':<18} {'Baseline':>10} {'Candidate':>10} {'Speedup':>9}"
    )
    lines.append("-" * 72)
    for row in comparison["scenarios"]:
        if row["change_type"] != "shared":
            lines.append(
                f"{row['scenario']:<20} {_status_marker(row['status_change']) + ' ' + row['change_type']:<18} "
                f"{_format_float(row['baseline_elapsed_sec']):>10} {_format_float(row['candidate_elapsed_sec']):>10} {'-':>9}"
            )
            continue
        lines.append(
            f"{row['scenario']:<20} {_status_marker(row['status_change']) + ' ' + row['status_change']:<18} "
            f"{_format_float(row['baseline_elapsed_sec']):>10} {_format_float(row['candidate_elapsed_sec']):>10} "
            f"{_format_float(row['speedup'], 2, 'x'):>9}"
        )
    lines.append("=" * 72)
    return "\n".join(lines)


def _parse_scenarios(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    values = [token.strip() for token in raw.split(",") if token.strip()]
    return values or None


def _resolve_labels(
    baseline_path: Path,
    candidate_path: Path,
    baseline_label: Optional[str],
    candidate_label: Optional[str],
) -> Tuple[str, str]:
    return (
        baseline_label or baseline_path.stem,
        candidate_label or candidate_path.stem,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Сравнить два JSON-отчёта benchmark Agent Navigator Pro.",
        epilog="""
Примеры:
  python scripts/benchmark_compare.py results/cpu.json results/gpu.json
  python scripts/benchmark_compare.py base.json cand.json --scenarios health,chat
  python scripts/benchmark_compare.py base.json cand.json --json-output results/compare.json
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("baseline", help="Базовый отчёт benchmark в формате JSON")
    parser.add_argument("candidate", help="Кандидатный отчёт benchmark в формате JSON")
    parser.add_argument("--baseline-label", default=None, help="Подпись для базового отчёта в консольном выводе")
    parser.add_argument("--candidate-label", default=None, help="Подпись для кандидатного отчёта в консольном выводе")
    parser.add_argument("--scenarios", default=None, help="Фильтр по сценариям через запятую")
    parser.add_argument("--json-output", default=None, help="Путь для сохранения итогового JSON-сравнения")
    args = parser.parse_args(argv)

    baseline_path = Path(args.baseline)
    candidate_path = Path(args.candidate)
    try:
        baseline = _load_report(baseline_path)
        candidate = _load_report(candidate_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    baseline_label, candidate_label = _resolve_labels(
        baseline_path, candidate_path, args.baseline_label, args.candidate_label
    )
    comparison = compare_reports(
        baseline,
        candidate,
        baseline_label=baseline_label,
        candidate_label=candidate_label,
        baseline_path=str(baseline_path),
        candidate_path=str(candidate_path),
        scenarios_filter=_parse_scenarios(args.scenarios),
    )
    print(render_console_report(comparison))
    if args.json_output:
        output_path = Path(args.json_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2, default=str))
        print(f"JSON report saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
