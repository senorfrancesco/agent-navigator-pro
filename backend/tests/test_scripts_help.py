from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(PROJECT_ROOT),
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("script_name", "expected"),
    [
        ("bootstrap_env.sh", "Использование"),
        ("run_native.sh", "Флаги"),
        ("run_all.sh", "Флаги"),
        ("run_container.sh", "Совместимый алиас"),
        ("run_monitoring.sh", "Prometheus"),
        ("restart_all.sh", "Перезапускает"),
        ("run_openwebui.sh", "legacy"),
        ("run_vllm_service.sh", "Переменные окружения"),
        ("setup_ubuntu.sh", "AGENT_NAVIGATOR_ASSUME_YES"),
        ("start_system_test.sh", "Проверочный"),
        ("stop_all.sh", "Останавливает"),
        ("stop_native.sh", "Останавливает"),
    ],
)
def test_shell_scripts_expose_russian_help(script_name: str, expected: str):
    result = _run(["bash", str(SCRIPTS_DIR / script_name), "--help"])

    assert result.returncode == 0
    assert expected in result.stdout


@pytest.mark.parametrize(
    ("script_name", "expected"),
    [
        ("bootstrap_env.sh", "Использование"),
        ("run_native.sh", "Флаги"),
        ("run_all.sh", "Флаги"),
        ("run_container.sh", "Совместимый алиас"),
        ("run_monitoring.sh", "Prometheus"),
        ("restart_all.sh", "Перезапускает"),
        ("run_openwebui.sh", "legacy"),
        ("run_vllm_service.sh", "Переменные окружения"),
        ("setup_ubuntu.sh", "AGENT_NAVIGATOR_ASSUME_YES"),
        ("start_system_test.sh", "Проверочный"),
        ("stop_all.sh", "Останавливает"),
        ("stop_native.sh", "Останавливает"),
    ],
)
def test_shell_scripts_expose_short_h_help(script_name: str, expected: str):
    result = _run(["bash", str(SCRIPTS_DIR / script_name), "-h"])

    assert result.returncode == 0
    assert expected in result.stdout


@pytest.mark.parametrize(
    ("script_path", "expected"),
    [
        (SCRIPTS_DIR / "benchmark.py", "Сценарии"),
        (SCRIPTS_DIR / "benchmark_compare.py", "Базовый отчёт"),
        (SCRIPTS_DIR / "runtime_preflight.py", "Профиль runtime"),
        (SCRIPTS_DIR / "models" / "download_models.py", "корневой каталог"),
    ],
)
def test_python_scripts_expose_russian_help(script_path: pathlib.Path, expected: str):
    result = _run([sys.executable, str(script_path), "--help"])

    assert result.returncode == 0
    assert expected in result.stdout


def test_windows_installer_contains_russian_help_block():
    script = (SCRIPTS_DIR / "install" / "install_windows.ps1").read_text(encoding="utf-8")

    assert ".SYNOPSIS" in script
    assert "Проверяет и при необходимости устанавливает" in script
    assert "[switch]$Help" in script


def test_run_native_reexecs_with_bash_when_started_via_sh():
    result = _run(["sh", str(SCRIPTS_DIR / "run_native.sh"), "--help"])

    assert result.returncode == 0
    assert "run_native.sh" in result.stdout
    assert "Флаги" in result.stdout
    assert "Bad substitution" not in result.stderr
