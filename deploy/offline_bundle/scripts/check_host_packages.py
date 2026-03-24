from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BUNDLE_ROOT = SCRIPT_DIR.parent
DEFAULT_DISTRO = "ubuntu-24.04"
SUPPORTED_DISTROS = {"ubuntu-22.04", "ubuntu-24.04"}
LOCK_PATH = BUNDLE_ROOT / "host_packages" / DEFAULT_DISTRO / "versions.lock.json"
MANUAL_DRIVER_MARKER = BUNDLE_ROOT / "state" / "host-install" / "manual-driver"


def read_os_release() -> dict[str, str]:
    payload: dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.exists():
        return payload
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key] = value.strip().strip('"')
    return payload


def detect_distro_from_os_release(payload: dict[str, str]) -> str | None:
    if payload.get("ID") != "ubuntu":
        return None
    version = payload.get("VERSION_ID")
    if version == "22.04":
        return "ubuntu-22.04"
    if version == "24.04":
        return "ubuntu-24.04"
    return None


def package_status(name: str, version: str) -> str:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Version}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return "missing"
    installed = result.stdout.strip()
    if installed == version:
        return "ok"
    return f"version-mismatch:{installed}"


def is_driver_package(name: str) -> bool:
    return name.startswith("nvidia-driver-")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Проверяет exact версии host packages без runtime-аудита Docker/NVIDIA."
    )
    parser.add_argument("--lock-file", default=str(LOCK_PATH), help="Path to host package versions.lock.json")
    parser.add_argument(
        "--distro",
        default="auto",
        help="auto, ubuntu-22.04 или ubuntu-24.04. Используется для выбора host package matrix.",
    )
    parser.add_argument(
        "--manual-driver",
        action="store_true",
        help="Не требовать пакетный nvidia-driver-*, если драйвер установлен вручную.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON report.")
    args = parser.parse_args()

    os_release = read_os_release()
    selected_distro = args.distro
    if selected_distro == "auto":
        detected = detect_distro_from_os_release(os_release)
        selected_distro = detected or DEFAULT_DISTRO
    if selected_distro not in SUPPORTED_DISTROS:
        print(f"unsupported-distro:{selected_distro}")
        return 1

    lock_path = Path(args.lock_file).resolve()
    if args.lock_file == str(LOCK_PATH):
        lock_path = (BUNDLE_ROOT / "host_packages" / selected_distro / "versions.lock.json").resolve()
    if not lock_path.exists():
        print(f"missing-lock-file:{lock_path}")
        return 1

    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    manual_driver = args.manual_driver or MANUAL_DRIVER_MARKER.exists()
    report: dict[str, object] = {
        "os": {"id": os_release.get("ID", ""), "version_id": os_release.get("VERSION_ID", "")},
        "selected_distro": selected_distro,
        "manual_driver": manual_driver,
        "packages": [],
    }

    detected = detect_distro_from_os_release(os_release)
    if detected is None:
        issues.append("unsupported-os:expected-ubuntu-22.04-or-ubuntu-24.04")
    elif detected != selected_distro:
        issues.append(f"distro-mismatch:selected-{selected_distro}-detected-{detected}")

    for package in payload.get("packages", []):
        name = package["name"]
        version = package["version"]
        if manual_driver and is_driver_package(name):
            status = "skipped-manual-driver"
            report["packages"].append({"name": name, "version": version, "status": status})
            continue
        status = package_status(name, version)
        report["packages"].append({"name": name, "version": version, "status": status})
        if status != "ok":
            issues.append(f"package:{name}:{status}")

    if args.json:
        print(json.dumps({"ok": not issues, "issues": issues, "report": report}, ensure_ascii=False, indent=2))
    else:
        for package in report["packages"]:
            print(f"{package['status']}:{package['name']}={package['version']}")

    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
