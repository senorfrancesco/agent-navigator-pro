from __future__ import annotations

import os
import pathlib
import subprocess
import sys


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPAT_SCRIPT = PROJECT_ROOT / "backend" / "orchestrator" / "operator_shell_compat.py"
DEPLOY_WRAPPER = PROJECT_ROOT / "deploy" / "offline_bundle" / "scripts" / "deploy.sh"
RUN_WRAPPER = PROJECT_ROOT / "deploy" / "offline_bundle" / "scripts" / "run_offline_bundle.sh"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, str(COMPAT_SCRIPT), *args],
        cwd=str(PROJECT_ROOT),
        env=merged_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_operator_shell_compat_help_mentions_compatibility_wrapper():
    result = _run("--help")

    assert result.returncode == 0
    assert "Compatibility wrappers" in result.stdout
    assert "Python operator control plane" in result.stdout


def test_operator_shell_compat_offline_deploy_test_mode_reports_flags(tmp_path):
    bundle_root = tmp_path / "deploy" / "offline_bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)

    result = _run(
        "offline-deploy",
        "--bundle-root",
        str(bundle_root),
        "--skip-host-check",
        env={"AGENT_NAVIGATOR_TEST_MODE": "1"},
    )

    assert result.returncode == 0
    assert "operator-shell-compat:test-mode" in result.stdout
    assert "entrypoint=offline-deploy" in result.stdout
    assert "skip_host_check=1" in result.stdout
    assert "skip_image_load=1" in result.stdout


def test_operator_shell_compat_offline_deploy_can_explicitly_enable_image_load(tmp_path):
    bundle_root = tmp_path / "deploy" / "offline_bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)

    result = _run(
        "offline-deploy",
        "--bundle-root",
        str(bundle_root),
        "--ensure-image-load",
        env={"AGENT_NAVIGATOR_TEST_MODE": "1"},
    )

    assert result.returncode == 0
    assert "entrypoint=offline-deploy" in result.stdout
    assert "skip_image_load=0" in result.stdout


def test_operator_shell_compat_offline_run_test_mode_reports_flags(tmp_path):
    bundle_root = tmp_path / "deploy" / "offline_bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)

    result = _run(
        "offline-run",
        "--bundle-root",
        str(bundle_root),
        "--with-monitoring",
        "--no-tmux",
        env={"AGENT_NAVIGATOR_TEST_MODE": "1"},
    )

    assert result.returncode == 0
    assert "entrypoint=offline-run" in result.stdout
    assert "with_monitoring=1" in result.stdout
    assert "start_tmux=0" in result.stdout
    assert "skip_image_load=1" in result.stdout


def test_operator_shell_compat_offline_run_can_explicitly_enable_image_load(tmp_path):
    bundle_root = tmp_path / "deploy" / "offline_bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)

    result = _run(
        "offline-run",
        "--bundle-root",
        str(bundle_root),
        "--ensure-image-load",
        env={"AGENT_NAVIGATOR_TEST_MODE": "1"},
    )

    assert result.returncode == 0
    assert "entrypoint=offline-run" in result.stdout
    assert "skip_image_load=0" in result.stdout


def test_deploy_shell_wrapper_delegates_to_python_compat_in_test_mode():
    result = subprocess.run(
        ["bash", str(DEPLOY_WRAPPER), "--skip-host-check"],
        cwd=str(PROJECT_ROOT),
        env={**os.environ.copy(), "AGENT_NAVIGATOR_TEST_MODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "operator-shell-compat:test-mode" in result.stdout
    assert "entrypoint=offline-deploy" in result.stdout


def test_run_offline_shell_wrapper_delegates_to_python_compat_in_test_mode():
    result = subprocess.run(
        ["bash", str(RUN_WRAPPER), "--with-monitoring", "--no-tmux"],
        cwd=str(PROJECT_ROOT),
        env={**os.environ.copy(), "AGENT_NAVIGATOR_TEST_MODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "operator-shell-compat:test-mode" in result.stdout
    assert "entrypoint=offline-run" in result.stdout
