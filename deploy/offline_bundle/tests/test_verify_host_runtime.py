from __future__ import annotations

import importlib.util
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BUNDLE_ROOT / "scripts" / "verify_host_runtime.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("offline_verify_host_runtime", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_safe_run_replaces_invalid_utf8() -> None:
    module = _load_module()

    result = module.safe_run(
        [
            "python3",
            "-c",
            "import sys; sys.stdout.buffer.write(b'bad:\\xb3\\n')",
        ]
    )

    assert result.returncode == 0
    assert "bad:" in result.stdout
