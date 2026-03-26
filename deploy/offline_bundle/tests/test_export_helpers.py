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
    assert "manifest.json" in result.stdout


def test_export_images_help_mentions_backend_app_and_ums() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "export_images.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "backend-app/ums/chainlit" in result.stdout
    assert "vendor/llama.cpp" in result.stdout
    assert "wheelhouse" in result.stdout
    assert "--ums-build-mode <offline|connected>" in result.stdout


def test_ums_dockerfile_uses_local_llama_cpp_and_wheelhouse() -> None:
    content = (BUNDLE_ROOT / "Dockerfile.ums.offline").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim-bookworm AS python-runtime" in content
    assert "COPY deploy/offline_bundle/vendor/llama.cpp /opt/llama.cpp" in content
    assert "COPY deploy/offline_bundle/wheelhouse /opt/wheelhouse" in content
    assert "COPY --from=python-runtime /usr/local /usr/local" in content
    assert "python3.11 -m venv /opt/venv" in content
    assert "--no-index" in content
    assert "--find-links /opt/wheelhouse" in content
    assert "llama-server" in content
    assert "12.8.1-devel-ubuntu24.04" in content
    assert "12.8.1-runtime-ubuntu24.04" in content
    assert "download.pytorch.org/whl/cu128" not in content
    assert "git clone" not in content
    assert 'torch==${TORCH_VERSION}' in content
    assert 'CMAKE_CUDA_ARCHITECTURES="75;86"' in content
    assert "После локального GPU-smoke" in content
    assert "/app/backend/uploads" in content


def test_connected_ums_dockerfile_keeps_legacy_networked_build_path() -> None:
    content = (BUNDLE_ROOT / "Dockerfile.ums.connected").read_text(encoding="utf-8")

    assert "LLAMA_CPP_REPO" in content
    assert "LLAMA_CPP_REF=master" in content
    assert "git clone" in content
    assert "download.pytorch.org/whl/cu128" in content
    assert "COPY deploy/offline_bundle/vendor/llama.cpp /opt/llama.cpp" not in content
    assert "COPY deploy/offline_bundle/wheelhouse /opt/wheelhouse" not in content
    assert "/app/backend/uploads" in content


def test_chainlit_dockerfile_copies_runtime_dependencies() -> None:
    content = (BUNDLE_ROOT / "Dockerfile.chainlit.offline").read_text(encoding="utf-8")

    assert "ARG BACKEND_BASE_IMAGE=agent-nav-backend-app-offline:v1.0" in content
    assert "FROM ${BACKEND_BASE_IMAGE}" in content
    assert "WORKDIR /app/backend/orchestrator" in content
    assert "python -m pip install" in content
    assert "/app/backend/uploads" in content


def test_chainlit_lock_and_compose_keep_tab_api_and_shared_upload_path() -> None:
    lock_content = (BUNDLE_ROOT / "requirements.chainlit.lock.txt").read_text(encoding="utf-8")
    compose_content = (BUNDLE_ROOT / "compose.offline.yaml").read_text(encoding="utf-8")

    assert "chainlit==2.9.6" in lock_content
    assert "UPLOADS_DIR: /app/backend/uploads" in compose_content
    assert "HOST_UPLOADS_DIR: /app/backend/uploads" in compose_content
    assert "./state/uploads:/app/backend/uploads" in compose_content


def test_ums_requirements_do_not_depend_on_llama_cpp_python() -> None:
    content = (BUNDLE_ROOT / "requirements.ums.lock.txt").read_text(encoding="utf-8")

    assert "llama-cpp-python[server]" not in content
    assert "активный heavy runtime" in content
    assert "starlette-context==0.4.0" in content


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


def test_build_wheelhouse_help_mentions_ums_and_torch_contract() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_ROOT / "scripts" / "build_wheelhouse.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "requirements.ums.lock.txt" in result.stdout
    assert "torch" in result.stdout
    assert "cu128" in result.stdout
    assert "--skip-download" in result.stdout


def test_build_wheelhouse_script_downloads_lockfiles_separately() -> None:
    content = (BUNDLE_ROOT / "scripts" / "build_wheelhouse.sh").read_text(encoding="utf-8")

    assert '-r "$BACKEND_REQ" \\' not in content
    assert '-r "$CHAINLIT_REQ" \\' not in content
    assert '  -r "$BACKEND_REQ"\n\n"$PYTHON_BIN" -m pip download' in content
    assert '  -r "$CHAINLIT_REQ"\n\n"$PYTHON_BIN" -m pip download' in content
    assert '  -r "$UMS_REQ"' in content


def test_build_bundle_script_requires_real_build_artifacts() -> None:
    content = (BUNDLE_ROOT / "scripts" / "build_bundle.sh").read_text(encoding="utf-8")

    assert 'export_images.sh" --skip-build' not in content
    assert 'build_wheelhouse.sh" --skip-download' not in content
    assert 'bash "$SCRIPT_DIR/build_wheelhouse.sh"' in content
    assert 'bash "$SCRIPT_DIR/export_images.sh"' in content


def test_export_images_script_supports_connected_ums_build_mode() -> None:
    content = (BUNDLE_ROOT / "scripts" / "export_images.sh").read_text(encoding="utf-8")

    assert 'UMS_BUILD_MODE="${UMS_BUILD_MODE:-offline}"' in content
    assert '--ums-build-mode' in content
    assert 'Dockerfile.ums.connected' in content
    assert 'if [[ "$UMS_BUILD_MODE" == "offline" ]]; then' in content


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
