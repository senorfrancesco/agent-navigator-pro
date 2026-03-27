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


def is_manual_driver_excluded_root(name: str) -> bool:
    return name.startswith("nvidia-driver-") or name.startswith("linux-headers-")


def parse_dependency_names(raw_value: str) -> list[str]:
    names: list[str] = []
    for chunk in raw_value.split(","):
        term = chunk.strip()
        if not term:
            continue
        for alternative in term.split("|"):
            candidate = alternative.strip()
            if not candidate:
                continue
            candidate = candidate.split("(", 1)[0].strip()
            candidate = candidate.split(":", 1)[0].strip()
            if candidate:
                names.append(candidate)
    return names


def read_deb_dependency_map(lock_path: Path, packages: list[dict[str, str]]) -> dict[str, list[str]]:
    dependency_map: dict[str, list[str]] = {}
    bundle_packages = {package["name"] for package in packages}
    host_root = lock_path.parent

    for package in packages:
        deb_path = host_root / package["filename"]
        if not deb_path.exists():
            dependency_map[package["name"]] = []
            continue
        result = subprocess.run(
            ["dpkg-deb", "-f", str(deb_path), "Depends", "Pre-Depends"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            dependency_map[package["name"]] = []
            continue

        dependency_names: list[str] = []
        for line in result.stdout.splitlines():
            if ":" not in line:
                continue
            _, raw_value = line.split(":", 1)
            alternatives = parse_dependency_names(raw_value.strip())
            selected = next((name for name in alternatives if name in bundle_packages), None)
            if selected:
                dependency_names.append(selected)
        dependency_map[package["name"]] = dependency_names

    return dependency_map


def relevant_packages(payload: dict[str, object], lock_path: Path, manual_driver: bool) -> list[dict[str, str]]:
    packages = list(payload.get("packages", []))
    if not manual_driver:
        return packages

    requested = [
        name
        for name in payload.get("requested_packages", [])
        if isinstance(name, str) and not is_manual_driver_excluded_root(name)
    ]
    package_map = {package["name"]: package for package in packages}
    dependency_map = read_deb_dependency_map(lock_path, packages)
    selected: dict[str, dict[str, str]] = {}
    queue = [name for name in requested if name in package_map]

    while queue:
        name = queue.pop()
        if name in selected:
            continue
        package = package_map.get(name)
        if package is None:
            continue
        selected[name] = package
        for dependency_name in dependency_map.get(name, []):
            if dependency_name not in selected:
                queue.append(dependency_name)

    return [package for package in packages if package["name"] in selected]


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

    for package in relevant_packages(payload, lock_path, manual_driver):
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
