from __future__ import annotations

import subprocess
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def test_check_host_help() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "check_host.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "docker compose" in result.stdout
    assert "python3" in result.stdout


def test_deploy_help() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "deploy.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "skip-image-load" in result.stdout
    assert "validate_bundle.py" in (BUNDLE_ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "preflight_runtime.py" in (BUNDLE_ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "--env-file \"$ENV_FILE\"" in (BUNDLE_ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
