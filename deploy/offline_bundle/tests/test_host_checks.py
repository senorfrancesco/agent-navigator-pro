from __future__ import annotations

import subprocess
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]
COMPAT_SCRIPT = BUNDLE_ROOT.parents[1] / "backend" / "orchestrator" / "operator_shell_compat.py"


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
    assert "ensure-image-load" in result.stdout
    assert "skip-image-load" in result.stdout
    deploy_wrapper = (BUNDLE_ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    compat_script = COMPAT_SCRIPT.read_text(encoding="utf-8")
    assert "operator_shell_compat.py" in deploy_wrapper
    assert "validate_bundle.py" in compat_script
    assert "preflight_runtime.py" in compat_script
    assert '"--env-file", str(env_file)' in compat_script
