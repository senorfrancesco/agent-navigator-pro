from __future__ import annotations

import os
import pathlib
import subprocess


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def _run_script(script_name: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS_DIR / script_name), *args],
        cwd=str(PROJECT_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_launcher_report_only_outputs_runtime_plan(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--profile",
        "manual",
        "--report-only",
        env=env,
    )

    assert result.returncode == 0
    assert '"runtime_profile": "manual"' in result.stdout
    assert not runtime_env.exists()


def test_launcher_test_mode_writes_env_runtime_and_reports_target(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "container",
        "--profile",
        "adaptive",
        env=env,
    )

    assert result.returncode == 0
    assert "launcher:test-mode target=container profile=adaptive" in result.stdout
    assert runtime_env.exists()
    assert "UMS_RUNTIME_PROFILE=adaptive" in runtime_env.read_text(encoding="utf-8")


def test_run_native_is_wrapper_to_launcher(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--no-attach", env=env)

    assert result.returncode == 0
    assert "launcher:test-mode target=native" in result.stdout


def test_run_all_is_wrapper_to_launcher(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_all.sh", "--no-attach", env=env)

    assert result.returncode == 0
    assert "launcher:test-mode target=container" in result.stdout


def test_run_all_from_launcher_enables_vllm_compose_profile(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["BACKEND_MODE"] = "vllm"

    result = _run_script("run_all.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_all:test-mode backend_mode=vllm" in result.stdout
    assert "compose_profiles=--profile vllm" in result.stdout
    assert "compose_services=chainlit vllm" in result.stdout


def test_run_all_from_launcher_keeps_chainlit_only_for_local_backend(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["BACKEND_MODE"] = "llama-server"

    result = _run_script("run_all.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_all:test-mode backend_mode=llama-server" in result.stdout
    assert "compose_profiles=none" in result.stdout
    assert "compose_services=chainlit" in result.stdout


def test_install_mode_uses_bootstrap_script(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("launcher.sh", "--target", "native", "--install", env=env)

    assert result.returncode == 0
    assert "bootstrap:test-mode mode=install target=native" in result.stdout
