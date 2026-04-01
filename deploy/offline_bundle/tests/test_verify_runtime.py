from __future__ import annotations

import subprocess
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def test_verify_runtime_help() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "verify_runtime.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "post-start verification" in result.stdout
    assert "timeout" in result.stdout
