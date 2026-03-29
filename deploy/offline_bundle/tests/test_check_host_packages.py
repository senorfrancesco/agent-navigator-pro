from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BUNDLE_ROOT / "scripts" / "check_host_packages.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_host_packages_module", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_driver_package_match_covers_xserver_variant() -> None:
    module = load_module()

    assert module.is_driver_package("xserver-xorg-video-nvidia-570-server") is True


def test_auto_manual_driver_skips_exact_driver_package_audit(tmp_path, monkeypatch, capsys) -> None:
    module = load_module()
    lock_path = tmp_path / "versions.lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "requested_packages": ["xserver-xorg-video-nvidia-570-server"],
                "packages": [
                    {
                        "name": "xserver-xorg-video-nvidia-570-server",
                        "version": "570.211.01-0ubuntu0.24.04.2",
                        "filename": "pool/xserver-xorg-video-nvidia-570-server.deb",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "read_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"})
    monkeypatch.setattr(module, "detect_working_nvidia_driver", lambda: True)
    monkeypatch.setattr(module, "MANUAL_DRIVER_MARKER", tmp_path / "missing-marker")
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_host_packages.py", "--lock-file", str(lock_path)],
    )

    assert module.main() == 0
    captured = capsys.readouterr()
    assert "auto-manual-driver:working-nvidia-driver-detected" in captured.out

