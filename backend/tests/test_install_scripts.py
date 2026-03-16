import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _run_script(args, *, env=None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=merged_env,
        check=False,
    )


def test_detect_os_help():
    result = _run_script(["bash", str(SCRIPTS_DIR / "install" / "detect_os.sh"), "--help"])
    assert result.returncode == 0
    assert "Safe platform detector" in result.stdout


def test_detect_os_print_platform_returns_supported_value():
    result = _run_script(["bash", str(SCRIPTS_DIR / "install" / "detect_os.sh"), "--print-platform"])
    assert result.returncode == 0
    assert result.stdout.strip() in {"windows", "wsl", "ubuntu", "ubuntu-server", "unsupported"}


def test_install_dispatcher_reports_platform_in_test_mode():
    result = _run_script(
        ["bash", str(SCRIPTS_DIR / "install" / "install.sh"), "--platform=ubuntu", "--target=native"],
        env={"AGENT_NAVIGATOR_TEST_MODE": "1"},
    )
    assert result.returncode == 0
    assert "platform=ubuntu" in result.stdout
    assert "coordinator=install_ubuntu.sh" in result.stdout


def test_launcher_install_forwards_platform_to_dispatcher():
    result = _run_script(
        ["bash", str(SCRIPTS_DIR / "launcher.sh"), "--install", "--platform=ubuntu-server"],
        env={"AGENT_NAVIGATOR_TEST_MODE": "1"},
    )
    assert result.returncode == 0
    assert "platform=ubuntu-server" in result.stdout
    assert "coordinator=install_ubuntu_server.sh" in result.stdout


def test_windows_installer_contains_wsl_guidance():
    script = (SCRIPTS_DIR / "install" / "install_windows.ps1").read_text(encoding="utf-8")
    assert "windows/wsl/install" in script
    assert "launcher.sh --install" in script
