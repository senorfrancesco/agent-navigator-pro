from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_template_has_release_metadata() -> None:
    payload = json.loads((BUNDLE_ROOT / "manifest.template.json").read_text(encoding="utf-8"))

    assert payload["bundle_version"] == "v1.0"
    assert payload["release_branch"] == "release/v1.0"
    assert "images" in payload
    assert "state_paths" in payload
    assert "required_state_paths" in payload
    assert "optional_state_paths" in payload
    assert "required_state_files" in payload
    assert "state/legacy-orchestrator-files" in payload["optional_state_paths"]


def test_generate_manifest_writes_output() -> None:
    output_path = BUNDLE_ROOT / "manifest.json"
    if output_path.exists():
        output_path.unlink()

    subprocess.run(
        [sys.executable, str(BUNDLE_ROOT / "scripts" / "generate_manifest.py")],
        check=True,
        cwd=BUNDLE_ROOT.parents[1],
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["bundle_version"] == "v1.0"
    assert payload["release_branch"] == "release/v1.0"
    assert "build" in payload
    assert "checksums" in payload
