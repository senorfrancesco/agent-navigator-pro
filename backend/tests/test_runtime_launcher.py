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


def _run_script_with_input(
    script_name: str,
    *args: str,
    env: dict[str, str],
    user_input: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS_DIR / script_name), *args],
        cwd=str(PROJECT_ROOT),
        env=env,
        text=True,
        input=user_input,
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
    assert "Compatibility wrapper" in result.stdout
    assert "Python-first operator control plane" in result.stdout
    assert "--install --platform ubuntu" in result.stdout
    assert "--hardware-override-file <path>" in result.stdout


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
    assert "models:plan" in result.stdout
    assert "launcher:test-mode target=container profile=adaptive" in result.stdout
    assert runtime_env.exists()
    assert "UMS_RUNTIME_PROFILE=adaptive" in runtime_env.read_text(encoding="utf-8")


def test_launcher_can_skip_model_download_phase(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--skip-model-download",
        env=env,
    )

    assert result.returncode == 0
    assert "models:plan" not in result.stdout
    assert "launcher:test-mode target=native" in result.stdout


def test_launcher_forwards_models_root_to_downloader(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    models_root = tmp_path / "models-root"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        f"--models-root={models_root}",
        "--asset-set=all",
        env=env,
    )

    assert result.returncode == 0
    assert str(models_root / "gguf" / "qwen-14b" / "Qwen3-14B.Q4_K_M.gguf") in result.stdout
    assert str(models_root / "gguf" / "Qwen3-VL-8B-Q4" / "mmproj-Qwen3-VL-8B-Instruct-F16.gguf") in result.stdout
    assert str(models_root / "gguf" / "qwen-14b" / "Qwen3-14B.Q4_K_M.gguf") in runtime_env.read_text(encoding="utf-8")


def test_launcher_sources_native_overrides_before_runtime_preflight(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    backend_env = tmp_path / ".env"
    native_env = tmp_path / ".env.native"
    hardware_env = tmp_path / ".env.hardware.override"
    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\n", encoding="utf-8")
    native_env.write_text('DEVICE_MODE="gpu"\n', encoding="utf-8")
    hardware_env.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_BACKEND_ENV_FILE"] = str(backend_env)
    env["AGENT_NAVIGATOR_BACKEND_NATIVE_ENV_FILE"] = str(native_env)
    env["AGENT_NAVIGATOR_BACKEND_HARDWARE_OVERRIDE_FILE"] = str(hardware_env)
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--profile",
        "adaptive",
        env=env,
    )

    assert result.returncode == 0
    assert runtime_env.exists()
    assert "DEVICE_MODE=gpu" in runtime_env.read_text(encoding="utf-8")


def test_launcher_sources_hardware_override_file_before_runtime_preflight(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    hardware_env = tmp_path / ".env.hardware.override"
    hardware_env.write_text(
        'GPU_LAYERS_MODE="manual"\nN_GPU_LAYERS_OVERRIDE="24"\nINTENT_EMBEDDER_DEVICE_MODE="gpu"\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["AGENT_NAVIGATOR_BACKEND_HARDWARE_OVERRIDE_FILE"] = str(hardware_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--profile",
        "adaptive",
        env=env,
    )

    assert result.returncode == 0
    contents = runtime_env.read_text(encoding="utf-8")
    assert "GPU_LAYERS_MODE=manual" in contents
    assert "N_GPU_LAYERS_OVERRIDE=24" in contents
    assert "UMS_SELECTED_GPU_LAYERS=24" in contents
    assert "INTENT_EMBEDDER_DEVICE_MODE=gpu" in contents


def test_launcher_cli_gpu_layers_override_beats_hardware_override_file(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    hardware_env = tmp_path / ".env.hardware.override"
    hardware_env.write_text('GPU_LAYERS_MODE="manual"\nN_GPU_LAYERS_OVERRIDE="24"\n', encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["AGENT_NAVIGATOR_BACKEND_HARDWARE_OVERRIDE_FILE"] = str(hardware_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--gpu-layers-mode=max",
        env=env,
    )

    assert result.returncode == 0
    contents = runtime_env.read_text(encoding="utf-8")
    assert "GPU_LAYERS_MODE=max" in contents
    assert "N_GPU_LAYERS_OVERRIDE=-1" in contents
    assert "UMS_SELECTED_GPU_LAYERS=-1" in contents


def test_launcher_cli_component_device_override_beats_hardware_override_file(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    hardware_env = tmp_path / ".env.hardware.override"
    hardware_env.write_text('INTENT_EMBEDDER_DEVICE_MODE="cpu"\n', encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["AGENT_NAVIGATOR_BACKEND_HARDWARE_OVERRIDE_FILE"] = str(hardware_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--intent-embedder-device-mode=gpu",
        env=env,
    )

    assert result.returncode == 0
    contents = runtime_env.read_text(encoding="utf-8")
    assert "INTENT_EMBEDDER_DEVICE_MODE=gpu" in contents


def test_launcher_can_use_custom_hardware_override_file_flag(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    hardware_env = tmp_path / "custom.hardware.override.env"
    hardware_env.write_text('LLM_DEVICE_MODE="cpu"\n', encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--hardware-override-file",
        str(hardware_env),
        env=env,
    )

    assert result.returncode == 0
    contents = runtime_env.read_text(encoding="utf-8")
    assert "LLM_DEVICE_MODE=cpu" in contents


def test_launcher_review_runtime_applies_current_run_override_without_persisting(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    hardware_env = tmp_path / ".env.hardware.override"
    hardware_env.write_text('INTENT_EMBEDDER_DEVICE_MODE="cpu"\nRETRIEVAL_EMBEDDER_DEVICE_MODE="cpu"\n', encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["AGENT_NAVIGATOR_BACKEND_HARDWARE_OVERRIDE_FILE"] = str(hardware_env)

    result = _run_script_with_input(
        "launcher.sh",
        "--target",
        "native",
        "--profile",
        "adaptive",
        "--review-runtime",
        env=env,
        user_input="c\n\n\ngpu\ngpu\nn\n",
    )

    assert result.returncode == 0
    runtime_contents = runtime_env.read_text(encoding="utf-8")
    assert "INTENT_EMBEDDER_DEVICE_MODE=gpu" in runtime_contents
    assert "RETRIEVAL_EMBEDDER_DEVICE_MODE=gpu" in runtime_contents
    hardware_contents = hardware_env.read_text(encoding="utf-8")
    assert 'INTENT_EMBEDDER_DEVICE_MODE="cpu"' in hardware_contents
    assert 'RETRIEVAL_EMBEDDER_DEVICE_MODE="cpu"' in hardware_contents
    assert "Persistent save (.env.hardware.override) is a separate step." in result.stdout


def test_launcher_review_runtime_can_persist_current_run_override(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    hardware_env = tmp_path / ".env.hardware.override"
    hardware_env.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["AGENT_NAVIGATOR_BACKEND_HARDWARE_OVERRIDE_FILE"] = str(hardware_env)

    result = _run_script_with_input(
        "launcher.sh",
        "--target",
        "native",
        "--profile",
        "adaptive",
        "--review-runtime",
        env=env,
        user_input="c\n\n\ncpu\ncpu\ny\n",
    )

    assert result.returncode == 0
    hardware_contents = hardware_env.read_text(encoding="utf-8")
    assert 'INTENT_EMBEDDER_DEVICE_MODE="cpu"' in hardware_contents
    assert 'RETRIEVAL_EMBEDDER_DEVICE_MODE="cpu"' in hardware_contents
    assert 'UMS_RUNTIME_PROFILE="adaptive"' in hardware_contents


def test_run_native_is_wrapper_to_launcher(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--no-attach", env=env)

    assert result.returncode == 0
    assert "launcher:test-mode target=native" in result.stdout


def test_run_native_from_launcher_reports_invalid_model_path(tmp_path):
    backend_env = tmp_path / ".env"
    native_env = tmp_path / ".env.native"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"

    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\n", encoding="utf-8")
    native_env.write_text("", encoding="utf-8")
    runtime_env.write_text(
        "\n".join(
            [
                f"UPLOADS_DIR='{uploads_dir}'",
                f"MODEL_PATH_LLM='{tmp_path / 'missing.gguf'}'",
                "MODEL_PATH_VLM=''",
                "MMPROJ_PATH=''",
                "MODEL_PATH_EMBEDDING_INTENT=''",
                "MODEL_PATH_EMBEDDING_RETRIEVAL=''",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_SKIP_CONDA_CHECKS"] = "1"
    env["AGENT_NAVIGATOR_BACKEND_ENV_FILE"] = str(backend_env)
    env["AGENT_NAVIGATOR_BACKEND_NATIVE_ENV_FILE"] = str(native_env)
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 1
    assert "env-invalid:MODEL_PATH_LLM:missing-file:" in result.stderr


def test_run_native_from_launcher_repairs_writable_uploads_dir(tmp_path):
    backend_env = tmp_path / ".env"
    native_env = tmp_path / ".env.native"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    uploads_dir.chmod(0o500)

    llm_file = tmp_path / "model.gguf"
    llm_file.write_text("stub", encoding="utf-8")
    intent_dir = tmp_path / "intent"
    retrieval_dir = tmp_path / "retrieval"
    intent_dir.mkdir()
    retrieval_dir.mkdir()

    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\n", encoding="utf-8")
    native_env.write_text("", encoding="utf-8")
    runtime_env.write_text(
        "\n".join(
            [
                f"UPLOADS_DIR='{uploads_dir}'",
                f"MODEL_PATH_LLM='{llm_file}'",
                "MODEL_PATH_VLM=''",
                "MMPROJ_PATH=''",
                f"MODEL_PATH_EMBEDDING_INTENT='{intent_dir}'",
                f"MODEL_PATH_EMBEDDING_RETRIEVAL='{retrieval_dir}'",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_SKIP_CONDA_CHECKS"] = "1"
    env["AGENT_NAVIGATOR_BACKEND_ENV_FILE"] = str(backend_env)
    env["AGENT_NAVIGATOR_BACKEND_NATIVE_ENV_FILE"] = str(native_env)
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    try:
        result = _run_script("run_native.sh", "--from-launcher", "--no-attach", env=env)
    finally:
        uploads_dir.chmod(0o700)

    assert result.returncode == 0
    assert "run_native:test-mode validated" in result.stdout


def test_run_native_from_launcher_validates_env_in_test_mode(tmp_path):
    backend_env = tmp_path / ".env"
    native_env = tmp_path / ".env.native"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"

    llm_file = tmp_path / "model.gguf"
    llm_file.write_text("stub", encoding="utf-8")
    intent_dir = tmp_path / "intent"
    retrieval_dir = tmp_path / "retrieval"
    intent_dir.mkdir()
    retrieval_dir.mkdir()

    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\n", encoding="utf-8")
    native_env.write_text("", encoding="utf-8")
    runtime_env.write_text(
        "\n".join(
            [
                f"UPLOADS_DIR='{uploads_dir}'",
                f"MODEL_PATH_LLM='{llm_file}'",
                "MODEL_PATH_VLM=''",
                "MMPROJ_PATH=''",
                f"MODEL_PATH_EMBEDDING_INTENT='{intent_dir}'",
                f"MODEL_PATH_EMBEDDING_RETRIEVAL='{retrieval_dir}'",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_SKIP_CONDA_CHECKS"] = "1"
    env["AGENT_NAVIGATOR_BACKEND_ENV_FILE"] = str(backend_env)
    env["AGENT_NAVIGATOR_BACKEND_NATIVE_ENV_FILE"] = str(native_env)
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_native:test-mode validated" in result.stdout


def test_run_native_from_launcher_accepts_backend_relative_model_paths(tmp_path):
    backend_env = tmp_path / ".env"
    native_env = tmp_path / ".env.native"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"

    backend_env.write_text(
        "\n".join(
            [
                "CHAINLIT_AUTH_SECRET='ok'",
                "CHAINLIT_ADMIN_PASSWORD='ok'",
                "MODEL_PATH_LLM='./models/gguf/qwen-14b/Qwen3-14B.Q4_K_M.gguf'",
                "MODEL_PATH_VLM='./models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf'",
                "MMPROJ_PATH='./models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf'",
                "MODEL_PATH_EMBEDDING_INTENT='./models/st/Qwen3-Embedding-0.6B'",
                "MODEL_PATH_EMBEDDING_RETRIEVAL='./models/st/LaBSE'",
            ]
        ),
        encoding="utf-8",
    )
    native_env.write_text("", encoding="utf-8")
    runtime_env.write_text(f"UPLOADS_DIR='{uploads_dir}'\n", encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_SKIP_CONDA_CHECKS"] = "1"
    env["AGENT_NAVIGATOR_BACKEND_ENV_FILE"] = str(backend_env)
    env["AGENT_NAVIGATOR_BACKEND_NATIVE_ENV_FILE"] = str(native_env)
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_native:test-mode validated" in result.stdout


def test_run_native_prefers_base_when_configured_conda_env_is_missing(tmp_path):
    backend_env = tmp_path / ".env"
    native_env = tmp_path / ".env.native"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"

    llm_file = tmp_path / "model.gguf"
    llm_file.write_text("stub", encoding="utf-8")
    intent_dir = tmp_path / "intent"
    retrieval_dir = tmp_path / "retrieval"
    intent_dir.mkdir()
    retrieval_dir.mkdir()

    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\n", encoding="utf-8")
    native_env.write_text("CONDA_ENV='diploma_llm'\n", encoding="utf-8")
    runtime_env.write_text(
        "\n".join(
            [
                f"UPLOADS_DIR='{uploads_dir}'",
                f"MODEL_PATH_LLM='{llm_file}'",
                "MODEL_PATH_VLM=''",
                "MMPROJ_PATH=''",
                f"MODEL_PATH_EMBEDDING_INTENT='{intent_dir}'",
                f"MODEL_PATH_EMBEDDING_RETRIEVAL='{retrieval_dir}'",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_TEST_MODE"] = "1"
    env["AGENT_NAVIGATOR_BACKEND_ENV_FILE"] = str(backend_env)
    env["AGENT_NAVIGATOR_BACKEND_NATIVE_ENV_FILE"] = str(native_env)
    env["AGENT_NAVIGATOR_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["PATH"] = ""
    fake_conda_bin = tmp_path / "bin"
    fake_conda_bin.mkdir()
    conda_script = fake_conda_bin / "conda"
    conda_script.write_text(
        "#!/bin/bash\n"
        "if [ \"$1\" = \"env\" ] && [ \"$2\" = \"list\" ]; then\n"
        "  echo '# conda environments:'\n"
        "  echo 'base                  *  /tmp/miniconda3'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"shell.bash\" ] && [ \"$2\" = \"hook\" ]; then\n"
        "  echo 'conda(){ return 0; }'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"activate\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"info\" ] && [ \"$2\" = \"--base\" ]; then\n"
        "  echo '/tmp/miniconda3'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    conda_script.chmod(0o755)
    env["PATH"] = f"{fake_conda_bin}:{os.environ.get('PATH', '')}"

    result = _run_script("run_native.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 0
    assert "использую base" in result.stdout.lower()


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
    assert "phase1_services=document-server legal-server ums vllm" in result.stdout
    assert "phase2_services=agent-api chainlit" in result.stdout


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
    assert "phase1_services=document-server legal-server ums" in result.stdout
    assert "phase2_services=agent-api chainlit" in result.stdout


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
    assert "secret-regenerated:CHAINLIT_AUTH_SECRET" in result.stdout
    assert "insecure-secret:CHAINLIT_ADMIN_PASSWORD" in result.stdout
    assert "insecure-secret:GF_SECURITY_ADMIN_PASSWORD" in result.stdout
    contents = env_file.read_text(encoding="utf-8")
    assert 'CHAINLIT_AUTH_SECRET="agent-navigator-secret-key-change-me"' not in contents


def test_bootstrap_check_creates_env_with_generated_chainlit_auth_secret(tmp_path):
    env_file = tmp_path / ".env"
    template_file = tmp_path / ".env.example"
    template_file.write_text(
        "\n".join(
            [
                'CHAINLIT_AUTH_SECRET="agent-navigator-secret-key-change-me"',
                'CHAINLIT_ADMIN_PASSWORD="strong-password"',
                'GF_SECURITY_ADMIN_PASSWORD="strong-grafana-password"',
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_BACKEND_ENV_FILE"] = str(env_file)
    env["AGENT_NAVIGATOR_BACKEND_ENV_TEMPLATE_FILE"] = str(template_file)
    env["AGENT_NAVIGATOR_SKIP_COMMAND_CHECKS"] = "1"

    result = _run_script("bootstrap_env.sh", "--check", "--target=native", env=env)

    assert result.returncode == 0
    assert "env-created:" in result.stdout
    assert "secret-generated:CHAINLIT_AUTH_SECRET" in result.stdout
    contents = env_file.read_text(encoding="utf-8")
    assert 'CHAINLIT_AUTH_SECRET="agent-navigator-secret-key-change-me"' not in contents
    assert 'CHAINLIT_ADMIN_PASSWORD="strong-password"' in contents


def test_bootstrap_check_rotates_default_chainlit_auth_secret_only(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'CHAINLIT_AUTH_SECRET="agent-navigator-secret-key-change-me"',
                'CHAINLIT_ADMIN_PASSWORD="strong-password"',
                'GF_SECURITY_ADMIN_PASSWORD="strong-grafana-password"',
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_NAVIGATOR_BACKEND_ENV_FILE"] = str(env_file)
    env["AGENT_NAVIGATOR_SKIP_COMMAND_CHECKS"] = "1"

    result = _run_script("bootstrap_env.sh", "--check", "--target=native", env=env)

    assert result.returncode == 0
    assert "secret-regenerated:CHAINLIT_AUTH_SECRET" in result.stdout
    contents = env_file.read_text(encoding="utf-8")
    assert 'CHAINLIT_AUTH_SECRET="agent-navigator-secret-key-change-me"' not in contents
    assert 'CHAINLIT_ADMIN_PASSWORD="strong-password"' in contents
    assert 'GF_SECURITY_ADMIN_PASSWORD="strong-grafana-password"' in contents


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


def test_start_system_test_contains_executable_tmux_commands():
    start_system_test = (SCRIPTS_DIR / "start_system_test.sh").read_text(encoding="utf-8")

    assert '\ntmux new-session -d -s "$SESSION_NAME" -x 200 -y 50\n' in start_system_test
    assert '\ntmux new-window -t "$SESSION_NAME" -n "agent-api"\n' in start_system_test
    assert '\ntmux new-window -t "$SESSION_NAME" -n "doc-server"\n' in start_system_test
    assert '\ntmux new-window -t "$SESSION_NAME" -n "legal-server"\n' in start_system_test
    assert '\ntmux new-window -t "$SESSION_NAME" -n "ums"\n' in start_system_test


def test_run_all_waits_for_infer_ready_endpoint():
    run_all = (SCRIPTS_DIR / "run_all.sh").read_text(encoding="utf-8")

    assert "/ready/infer" in run_all
    assert "wait_for_infer_ready" in run_all


def test_run_native_waits_for_infer_ready_before_starting_ui():
    run_native = (SCRIPTS_DIR / "run_native.sh").read_text(encoding="utf-8")

    assert "wait_for_infer_ready" in run_native
    assert "/ready/infer" in run_native
    assert 'echo -e "${YELLOW}Пропуск запуска Agent API и Chainlit: UMS infer-ready не подтвержден.${NC}"' in run_native


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
