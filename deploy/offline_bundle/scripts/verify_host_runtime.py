from __future__ import annotations

import argparse
import json
import subprocess
import sys


def safe_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def command_ok(cmd: list[str]) -> bool:
    result = safe_run(cmd)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Проверяет готовность Docker/NVIDIA runtime на host после установки и настройки пакетов."
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON report.")
    args = parser.parse_args()

    runtime_checks = {
        "docker": command_ok(["docker", "--version"]),
        "docker_compose": command_ok(["docker", "compose", "version"]),
        "nvidia_smi": command_ok(["nvidia-smi", "-L"]),
        "docker_info_access": False,
        "docker_info_nvidia": False,
    }

    docker_info = safe_run(["docker", "info"])
    if docker_info.returncode == 0:
        runtime_checks["docker_info_access"] = True
        if "nvidia" in docker_info.stdout.lower():
            runtime_checks["docker_info_nvidia"] = True

    issues = [f"runtime-check-failed:{key}" for key, ok in runtime_checks.items() if not ok]

    if args.json:
        print(json.dumps({"ok": not issues, "issues": issues, "runtime_checks": runtime_checks}, ensure_ascii=False, indent=2))
    else:
        for key, ok in runtime_checks.items():
            print(f"{'ok' if ok else 'failed'}:{key}")

    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
