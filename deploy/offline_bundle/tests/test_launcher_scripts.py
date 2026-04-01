from __future__ import annotations

import subprocess
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def test_run_offline_bundle_help() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "run_offline_bundle.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "with-monitoring" in result.stdout
    assert "attach-tmux" in result.stdout


def test_stop_offline_bundle_help() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "stop_offline_bundle.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Останавливает offline docker runtime" in result.stdout
