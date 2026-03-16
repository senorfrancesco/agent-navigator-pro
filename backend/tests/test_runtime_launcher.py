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


def test_launcher_help_documents_install_and_platform_flags():
    result = _run_script("launcher.sh", "--help", env=os.environ.copy())

    assert result.returncode == 0
    assert "Canonical entrypoint" in result.stdout
    assert "--install --platform ubuntu" in result.stdout


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


def test_launcher_sources_native_overrides_before_runtime_preflight(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    native_env = PROJECT_ROOT / "backend" / ".env.native"
    original = native_env.read_text(encoding="utf-8") if native_env.exists() else None
    try:
        native_env.write_text('DEVICE_MODE="gpu"\n', encoding="utf-8")
        env = os.environ.copy()
        env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
        env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

        result = _run_script(
            "launcher.sh",
            "--target",
            "native",
            "--profile",
            "adaptive",
            env=env,
        )
    finally:
        if original is None:
            native_env.unlink(missing_ok=True)
        else:
            native_env.write_text(original, encoding="utf-8")

    assert result.returncode == 0
    assert runtime_env.exists()
    assert "DEVICE_MODE=gpu" in runtime_env.read_text(encoding="utf-8")


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
    assert "compose_profiles=--profile backend --profile vllm" in result.stdout
    assert "compose_services=agent-api document-server legal-server ums chainlit vllm" in result.stdout


def test_run_all_from_launcher_keeps_chainlit_only_for_local_backend(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["BACKEND_MODE"] = "llama-server"

    result = _run_script("run_all.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_all:test-mode backend_mode=llama-server" in result.stdout
    assert "compose_profiles=--profile backend" in result.stdout
    assert "compose_services=agent-api document-server legal-server ums chainlit" in result.stdout


def test_install_mode_uses_installer_coordinator(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("launcher.sh", "--target", "native", "--install", env=env)

    assert result.returncode == 0
    assert "install:test-mode target=native" in result.stdout
    assert "platform=ubuntu" in result.stdout or "platform=wsl" in result.stdout


def test_install_script_supports_dry_run_in_test_mode():
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"

    result = _run_script("install/install.sh", "--target=native", "--platform=ubuntu", "--dry-run", env=env)

    assert result.returncode == 0
    assert "install:test-mode target=native platform=ubuntu dry_run=1" in result.stdout


def test_install_coordinator_reports_native_legacy_delegate(tmp_path):
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"

    result = _run_script("install/install.sh", "--target=native", "--platform=ubuntu", env=env)

    assert result.returncode == 0
    assert "install:test-mode target=native platform=ubuntu" in result.stdout
    assert "coordinator=install_ubuntu.sh" in result.stdout


def test_install_coordinator_accepts_ubuntu_server_platform():
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"

    result = _run_script("install/install.sh", "--target=native", "--platform=ubuntu-server", env=env)

    assert result.returncode == 0
    assert "platform=ubuntu-server" in result.stdout
    assert "coordinator=install_ubuntu_server.sh" in result.stdout


def test_install_coordinator_accepts_wsl_platform():
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"

    result = _run_script("install/install.sh", "--target=native", "--platform=wsl", env=env)

    assert result.returncode == 0
    assert "platform=wsl" in result.stdout
    assert "coordinator=install_wsl.sh" in result.stdout


def test_install_coordinator_auto_detects_wsl():
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["WSL_DISTRO_NAME"] = "Ubuntu"

    result = _run_script("install/install.sh", "--target=native", env=env)

    assert result.returncode == 0
    assert "platform=wsl" in result.stdout
    assert "coordinator=install_wsl.sh" in result.stdout


def test_install_coordinator_windows_platform_reports_powershell_entrypoint():
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"

    result = _run_script("install/install.sh", "--target=native", "--platform=windows", env=env)

    assert result.returncode == 0
    assert "platform=windows" in result.stdout
    assert "coordinator=install_windows.ps1" in result.stdout


def test_install_coordinator_accepts_container_guidance(tmp_path):
    env = os.environ.copy()

    result = _run_script("install/install.sh", "--target=container", env=env)

    assert result.returncode == 0
    assert "Container target does not require host installer" in result.stderr


def test_bootstrap_check_rejects_default_secrets(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'CHAINLIT_AUTH_SECRET="agent-navigator-secret-key-change-me"',
                'CHAINLIT_ADMIN_PASSWORD="admin"',
                'GF_SECURITY_ADMIN_PASSWORD="change-me-grafana"',
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_BACKEND_ENV_FILE"] = str(env_file)
    env["AGENT_NAVIGATOR_SKIP_COMMAND_CHECKS"] = "1"

    result = _run_script("bootstrap_env.sh", "--check", "--target=native", env=env)

    assert result.returncode == 1
    assert "insecure-secret:CHAINLIT_AUTH_SECRET" in result.stdout
    assert "insecure-secret:CHAINLIT_ADMIN_PASSWORD" in result.stdout
    assert "insecure-secret:GF_SECURITY_ADMIN_PASSWORD" in result.stdout


def test_bootstrap_check_allows_insecure_defaults_when_explicitly_enabled(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'CHAINLIT_AUTH_SECRET="agent-navigator-secret-key-change-me"',
                'CHAINLIT_ADMIN_PASSWORD="admin"',
                'GF_SECURITY_ADMIN_PASSWORD="change-me-grafana"',
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_BACKEND_ENV_FILE"] = str(env_file)
    env["AGENT_NAVIGATOR_SKIP_COMMAND_CHECKS"] = "1"
    env["AGENT_NAVIGATOR_ALLOW_INSECURE_DEFAULTS"] = "1"

    result = _run_script("bootstrap_env.sh", "--check", "--target=native", env=env)

    assert result.returncode == 0
    assert "bootstrap:ok target=native" in result.stdout


def test_run_all_never_prints_default_password_hint(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_all.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 0
    assert "admin/admin" not in result.stdout


def test_native_and_legacy_scripts_export_backend_pythonpath_for_service_servers():
    run_native = (SCRIPTS_DIR / "run_native.sh").read_text(encoding="utf-8")
    run_openwebui = (SCRIPTS_DIR / "run_openwebui.sh").read_text(encoding="utf-8")
    start_system_test = (SCRIPTS_DIR / "start_system_test.sh").read_text(encoding="utf-8")

    assert "export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_document_server:app" in run_native
    assert "export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_legal_server:app" in run_native
    assert "export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_document_server:app" in run_openwebui
    assert "export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_legal_server:app" in run_openwebui
    assert "export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_document_server:app" in start_system_test
    assert "export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_legal_server:app" in start_system_test


def test_stop_scripts_kill_detached_runtime_process_patterns():
    stop_native = (SCRIPTS_DIR / "stop_native.sh").read_text(encoding="utf-8")
    stop_all = (SCRIPTS_DIR / "stop_all.sh").read_text(encoding="utf-8")

    expected_patterns = [
        'kill_matching_processes "UMS" "services/model_manager/unified_model_server.py"',
        'kill_matching_processes "llama-server runtime" "llama-server"',
        'kill_matching_processes "embedding runtime" "services/model_manager/st_server.py"',
        'kill_matching_processes "Document Server" "uvicorn mcp_document_server:app --host 0.0.0.0 --port $DOC_PORT"',
        'kill_matching_processes "Legal Server" "uvicorn mcp_legal_server:app --host 0.0.0.0 --port $LEGAL_PORT"',
        'kill_matching_processes "Agent API" "python agent_api.py"',
        'kill_matching_processes "Chainlit" "chainlit run chainlit_app.py --host 0.0.0.0 --port $CHAINLIT_PORT"',
    ]

    for pattern in expected_patterns:
        assert pattern in stop_native
        assert pattern in stop_all
