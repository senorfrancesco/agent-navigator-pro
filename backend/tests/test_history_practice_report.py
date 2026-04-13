from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "docs" / "reports" / "practice-history" / "history_practice_report.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(PROJECT_ROOT),
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_history_practice_report_exposes_russian_help():
    assert SCRIPT_PATH.is_file()
    assert not (PROJECT_ROOT / "scripts" / "history_practice_report.py").exists()

    result = _run([sys.executable, str(SCRIPT_PATH), "--help"])

    assert result.returncode == 0
    assert "История Git" in result.stdout
    assert "--output-dir" in result.stdout


def test_history_practice_report_generates_expected_artifacts(tmp_path):
    output_dir = tmp_path / "practice-history"

    result = _run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--repo-root",
            str(PROJECT_ROOT),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert result.returncode == 0, result.stderr

    report_json = output_dir / "data" / "practice_history_report.json"
    timeline_csv = output_dir / "data" / "practice_history_timeline.csv"
    metrics_tex = output_dir / "generated" / "practice_history_metrics.tex"
    branches_tex = output_dir / "generated" / "practice_history_branches.tex"
    timeline_tex = output_dir / "generated" / "practice_history_timeline.tex"

    assert report_json.is_file()
    assert timeline_csv.is_file()
    assert metrics_tex.is_file()
    assert branches_tex.is_file()
    assert timeline_tex.is_file()

    payload = json.loads(report_json.read_text(encoding="utf-8"))

    assert payload["summary"]["total_commits"] >= 200
    assert payload["summary"]["working_start_date"] == "2026-01-05"
    assert payload["summary"]["head_branch"]
    assert "main_phases" in payload
    assert any(branch["name"] == "dev" for branch in payload["branches"])
    assert any(day["date"] == "2026-03-18" for day in payload["days"])

    timeline_content = timeline_tex.read_text(encoding="utf-8")
    assert "2026-03-18" in timeline_content
    assert "Open WebUI" in branches_tex.read_text(encoding="utf-8")
