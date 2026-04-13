import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _run_bash(script: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=REPO_ROOT,
        env=merged_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_launcher_install_passes_platform_to_bootstrap():
    result = _run_bash(
        SCRIPTS_DIR / "launcher.sh",
        "--install",
        "--platform=ubuntu-server",
        env={"LLM_TOOLS_PLATFORM_TEST_MODE": "1"},
    )

    assert result.returncode == 0
    assert "install:test-mode" in result.stdout
    assert "platform=ubuntu-server" in result.stdout
    assert "coordinator=install_ubuntu_server.sh" in result.stdout


def test_bootstrap_install_test_mode_includes_platform():
    result = _run_bash(
        SCRIPTS_DIR / "bootstrap_env.sh",
        "--install",
        "--target=native",
        "--platform=wsl",
        env={"LLM_TOOLS_PLATFORM_TEST_MODE": "1"},
    )

    assert result.returncode == 0
    assert "install:test-mode" in result.stdout
    assert "platform=wsl" in result.stdout
    assert "coordinator=install_wsl.sh" in result.stdout


def test_install_coordinator_resolves_wrapper_in_test_mode():
    result = _run_bash(
        SCRIPTS_DIR / "install" / "install.sh",
        "--target=native",
        "--platform=ubuntu",
        env={"LLM_TOOLS_PLATFORM_TEST_MODE": "1"},
    )

    assert result.returncode == 0
    assert "install:test-mode" in result.stdout
    assert "platform=ubuntu" in result.stdout
    assert "coordinator=install_ubuntu.sh" in result.stdout


def test_install_coordinator_rejects_unsupported_platform():
    result = _run_bash(
        SCRIPTS_DIR / "install" / "install.sh",
        "--target=native",
        "--platform=solaris",
    )

    assert result.returncode != 0
    assert "Неподдерживаемая installer platform" in result.stderr
