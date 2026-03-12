import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import benchmark_compare


def _make_report(
    *,
    runtime_profile="adaptive",
    total_time_sec=10.0,
    results=None,
    placements=None,
):
    return {
        "timestamp": "2026-03-13 10:00:00",
        "api_url": "http://localhost:8000",
        "ums_status": {
            "runtime_profile": runtime_profile,
            "effective_context_tokens": 8192,
            "retrieved_context_tokens_budget": 4915,
            "generation_tokens_reserve": 1024,
            "active_heavy_model": "qwen-14b-llm",
            "running": ["qwen-14b-llm"],
            "placements": placements or {},
        },
        "results": results or [],
        "summary": {
            "total_scenarios": len(results or []),
            "ok": sum(1 for item in (results or []) if item["status"] == "ok"),
            "error": sum(1 for item in (results or []) if item["status"] == "error"),
            "skip": 0,
            "timeout": 0,
            "total_time_sec": total_time_sec,
            "repeats": 1,
        },
    }


def test_compare_reports_marks_status_regression_and_speedup():
    baseline = _make_report(
        total_time_sec=12.0,
        results=[
            {"scenario": "chat", "status": "ok", "elapsed_sec": 4.0},
            {"scenario": "compare", "status": "ok", "elapsed_sec": 8.0},
        ],
    )
    candidate = _make_report(
        runtime_profile="manual",
        total_time_sec=9.0,
        results=[
            {"scenario": "chat", "status": "ok", "elapsed_sec": 2.0},
            {"scenario": "compare", "status": "error", "elapsed_sec": 7.0},
        ],
    )

    report = benchmark_compare.compare_reports(
        baseline,
        candidate,
        baseline_label="CPU",
        candidate_label="GPU",
        baseline_path="cpu.json",
        candidate_path="gpu.json",
    )

    assert report["summary"]["shared_scenarios"] == 2
    assert report["summary"]["regressed_status_count"] == 1
    assert report["summary"]["faster_count"] == 2
    assert report["summary"]["overall_speedup"] == 1.3333
    assert report["runtime_diff"]["changed_keys"] == ["runtime_profile"]
    compare_row = next(item for item in report["scenarios"] if item["scenario"] == "compare")
    assert compare_row["status_change"] == "regressed_status"
    assert compare_row["speedup"] == 1.1429


def test_compare_reports_tracks_added_removed_and_filter():
    baseline = _make_report(
        results=[
            {"scenario": "chat", "status": "ok", "elapsed_sec": 4.0},
            {"scenario": "compare", "status": "ok", "elapsed_sec": 8.0},
        ],
    )
    candidate = _make_report(
        results=[
            {"scenario": "chat", "status": "ok", "elapsed_sec": 5.0},
            {"scenario": "equipment", "status": "ok", "elapsed_sec": 7.0},
        ],
    )

    report = benchmark_compare.compare_reports(
        baseline,
        candidate,
        baseline_label="A",
        candidate_label="B",
        baseline_path="a.json",
        candidate_path="b.json",
        scenarios=["chat", "equipment"],
    )

    assert [item["scenario"] for item in report["scenarios"]] == ["chat", "equipment"]
    assert report["summary"]["shared_scenarios"] == 1
    assert report["summary"]["added_scenarios"] == 1
    assert report["summary"]["removed_scenarios"] == 0


def test_render_console_report_contains_runtime_and_summary():
    report = {
        "baseline_label": "CPU",
        "candidate_label": "GPU",
        "baseline_path": "cpu.json",
        "candidate_path": "gpu.json",
        "summary": {
            "shared_scenarios": 1,
            "added_scenarios": 0,
            "removed_scenarios": 0,
            "improved_status_count": 0,
            "regressed_status_count": 0,
            "faster_count": 1,
            "slower_count": 0,
            "unchanged_count": 0,
            "baseline_total_time_sec": 10.0,
            "candidate_total_time_sec": 5.0,
            "overall_speedup": 2.0,
        },
        "runtime_diff": {
            "baseline": {"runtime_profile": "adaptive"},
            "candidate": {"runtime_profile": "manual"},
            "changed_keys": ["runtime_profile"],
        },
        "scenarios": [
            {
                "scenario": "chat",
                "change_type": "shared",
                "status_change": "same_ok",
                "baseline_status": "ok",
                "candidate_status": "ok",
                "baseline_elapsed_sec": 10.0,
                "candidate_elapsed_sec": 5.0,
                "delta_sec": -5.0,
                "delta_pct": -50.0,
                "speedup": 2.0,
            }
        ],
    }

    text = benchmark_compare.render_console_report(report)

    assert "Benchmark Compare: CPU -> GPU" in text
    assert "Runtime changes: runtime_profile" in text
    assert "chat" in text
    assert "2.00x" in text


def test_load_report_fails_on_invalid_shape(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"results": "oops"}))

    with pytest.raises(ValueError, match="summary object"):
        benchmark_compare._load_report(path)


def test_main_writes_json_output(tmp_path, capsys):
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    output_path = tmp_path / "compare.json"
    baseline_path.write_text(
        json.dumps(
            _make_report(results=[{"scenario": "chat", "status": "ok", "elapsed_sec": 5.0}], total_time_sec=5.0)
        )
    )
    candidate_path.write_text(
        json.dumps(
            _make_report(
                runtime_profile="manual",
                results=[{"scenario": "chat", "status": "ok", "elapsed_sec": 2.5}],
                total_time_sec=2.5,
            )
        )
    )

    exit_code = benchmark_compare.main(
        [
            str(baseline_path),
            str(candidate_path),
            "--baseline-label",
            "CPU",
            "--candidate-label",
            "GPU",
            "--json-output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(output_path.read_text())

    assert exit_code == 0
    assert "Benchmark Compare: CPU -> GPU" in captured.out
    assert payload["summary"]["overall_speedup"] == 2.0
    assert payload["runtime_diff"]["changed_keys"] == ["runtime_profile"]
