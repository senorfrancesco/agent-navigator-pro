from __future__ import annotations

import subprocess
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def test_export_models_help() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "export_models.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "backend/models" in result.stdout
    assert "--models-root" in result.stdout
    assert "--env-file" in result.stdout
    assert "staging directory" in result.stdout


def test_build_host_apt_bundle_help_mentions_driver_and_check_modes() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "build_host_apt_bundle.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "nvidia-driver-550-server" in result.stdout
    assert "590.48.01" in result.stdout
    assert "1.19.0-1" in result.stdout
    assert "--distro" in result.stdout
    assert "--check-only" in result.stdout
    assert "--dry-run" in result.stdout


def test_install_host_apt_bundle_help_mentions_local_bundle_install() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "install_host_apt_bundle.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Ubuntu 22.04/24.04" in result.stdout
    assert "--distro" in result.stdout
    assert "--manual-driver" in result.stdout
    assert "versions.lock.json" in result.stdout
    assert "--check-only" in result.stdout


def test_check_host_packages_help_mentions_exact_versions() -> None:
    result = subprocess.run(
        ["python3", str(BUNDLE_ROOT / "scripts" / "check_host_packages.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "exact версии" in result.stdout
    assert "--distro" in result.stdout
    assert "--manual-driver" in result.stdout
    assert "--lock-file" in result.stdout


def test_verify_host_runtime_help_mentions_docker_and_nvidia() -> None:
    result = subprocess.run(
        ["python3", str(BUNDLE_ROOT / "scripts" / "verify_host_runtime.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Docker/NVIDIA runtime" in result.stdout
    assert "--json" in result.stdout


def test_build_bundle_help() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "build_bundle.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "offline bundle" in result.stdout


def test_export_images_help_mentions_backend_app_and_ums() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "export_images.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "backend-app/ums/chainlit" in result.stdout


def test_ums_dockerfile_mentions_llama_cpp_source_contract() -> None:
    content = (BUNDLE_ROOT / "Dockerfile.ums.offline").read_text(encoding="utf-8")

    assert "LLAMA_CPP_REPO" in content
    assert "LLAMA_CPP_REF" in content
    assert "github.com/ggml-org/llama.cpp.git" in content
    assert "git clone" in content
    assert "llama-server" in content
    assert "12.8.1-devel-ubuntu24.04" in content
    assert "12.8.1-runtime-ubuntu24.04" in content
    assert "download.pytorch.org/whl/cu128" in content
    assert 'torch==${TORCH_VERSION}' in content
    assert 'CMAKE_CUDA_ARCHITECTURES="75;86"' in content
    assert "После локального GPU-smoke" in content


def test_chainlit_dockerfile_copies_runtime_dependencies() -> None:
    content = (BUNDLE_ROOT / "Dockerfile.chainlit.offline").read_text(encoding="utf-8")

    assert "ARG BACKEND_BASE_IMAGE=agent-nav-backend-app-offline:v1.0" in content
    assert "FROM ${BACKEND_BASE_IMAGE}" in content
    assert "WORKDIR /app/backend/orchestrator" in content
    assert "python -m pip install" in content


def test_ums_requirements_keep_llama_cpp_python_parity_dependency() -> None:
    content = (BUNDLE_ROOT / "requirements.ums.lock.txt").read_text(encoding="utf-8")

    assert "llama-cpp-python[server]==0.3.16" in content
    assert "активный heavy runtime" in content
    assert "starlette-context==0.3.6" in content


def test_export_state_help_mentions_legacy_files() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "export_state.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "backend/orchestrator/.files" in result.stdout


def test_restore_state_script_validates_manifest_contract() -> None:
    content = (BUNDLE_ROOT / "scripts" / "restore_state.sh").read_text(encoding="utf-8")

    assert "missing-required:" in content
    assert "checksum-mismatch:" in content
    assert "legacy-orchestrator-files" in content


def test_validate_bundle_script_mentions_self_contained_contract() -> None:
    result = subprocess.run(
        ["python3", str(BUNDLE_ROOT / "scripts" / "validate_bundle.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "self-contained runtime contract" in result.stdout
    assert "--mode" in result.stdout
    assert "--models-dir" in result.stdout
    assert "scripts/install_host_apt_bundle.sh" in (BUNDLE_ROOT / "scripts" / "validate_bundle.py").read_text(encoding="utf-8")


def test_preflight_runtime_help_mentions_writeability_and_summary() -> None:
    result = subprocess.run(
        ["python3", str(BUNDLE_ROOT / "scripts" / "preflight_runtime.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "writable state paths" in result.stdout
    assert "--report-only" in result.stdout
