from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys
import time

from dotenv import set_key


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
TEST_HARNESS_SYSTEM_DIR = PROJECT_ROOT / "tests" / "harness" / "system"


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


def _write_executable(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _wait_for_port(port: int, *, timeout_s: float = 5.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"port-not-listening:{port}")


def _spawn_http_server(port: int) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(port)
    return process


def _build_stop_script_stub_path(tmp_path: pathlib.Path, *, docker_exit_code: int = 1) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "tmux", "#!/bin/sh\nexit 1\n")
    _write_executable(bin_dir / "pgrep", "#!/bin/sh\nexit 1\n")
    _write_executable(bin_dir / "docker", f"#!/bin/sh\nexit {docker_exit_code}\n")
    return f"{bin_dir}:{os.environ.get('PATH', '')}"


def test_launcher_report_only_outputs_runtime_plan(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

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
    assert "Рекомендуемый блок для backend/.env:" in result.stdout
    assert "UMS_RUNTIME_PROFILE=manual" in result.stdout
    assert not runtime_env.exists()


def test_launcher_native_rejects_runtime_env_flags(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--skip-runtime-apply",
        env=env,
    )

    assert result.returncode == 1
    assert "больше не использует .env.runtime для native path" in result.stderr


def test_launcher_help_documents_install_and_platform_flags():
    result = _run_script("launcher.sh", "--help", env=os.environ.copy())

    assert result.returncode == 0
    assert "Compatibility wrapper" in result.stdout
    assert "Python-first operator control plane" in result.stdout
    assert "--install --platform ubuntu" in result.stdout
    assert "--gpu-layers-mode auto|max|manual" in result.stdout
    assert "--ensure-model-download" in result.stdout
    assert "--skip-openwebui" in result.stdout
    assert "--skip-chainlit" in result.stdout


def test_run_native_help_documents_primary_ui_flag():
    result = _run_script("run_native.sh", "--help", env=os.environ.copy())

    assert result.returncode == 0
    assert "--skip-openwebui" in result.stdout
    assert "--skip-chainlit" in result.stdout
    assert "Не запускать контейнер `Open WebUI`" in result.stdout
    assert "--skip-runtime-apply" in result.stdout
    assert "--apply-runtime" in result.stdout
    assert "./scripts/evaluate_runtime.sh recommend" in result.stdout


def test_launcher_test_mode_writes_env_runtime_and_reports_target(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "container",
        "--profile",
        "adaptive",
        "--apply-runtime",
        env=env,
    )

    assert result.returncode == 0
    assert "models:plan" not in result.stdout
    assert "launcher:test-mode target=container profile=adaptive" in result.stdout
    assert runtime_env.exists()
    assert "UMS_RUNTIME_PROFILE=adaptive" in runtime_env.read_text(encoding="utf-8")


def test_launcher_does_not_run_model_download_phase_by_default(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        env=env,
    )

    assert result.returncode == 0
    assert "models:plan" not in result.stdout
    assert "launcher:test-mode target=native" in result.stdout


def test_launcher_forwards_skip_openwebui_to_native_runner(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--skip-openwebui",
        env=env,
    )

    assert result.returncode == 0
    assert "launcher:test-mode target=native" in result.stdout
    assert "skip_openwebui=true" in result.stdout.lower()


def test_launcher_can_explicitly_enable_model_download_phase(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--ensure-model-download",
        env=env,
    )

    assert result.returncode == 0
    assert "models:plan" in result.stdout
    assert "launcher:test-mode target=native" in result.stdout


def test_launcher_forwards_models_root_to_downloader(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    models_root = tmp_path / "models-root"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--ensure-model-download",
        f"--models-root={models_root}",
        "--asset-set=all",
        env=env,
    )

    assert result.returncode == 0
    assert str(models_root / "gguf" / "qwen-14b" / "Qwen2.5-14B-Instruct-Q4_K_M.gguf") in result.stdout
    assert str(models_root / "gguf" / "Qwen3-VL-8B-Q4" / "mmproj-Qwen3-VL-8B-Instruct-F16.gguf") in result.stdout
    assert not runtime_env.exists()


def test_launcher_report_only_uses_backend_env_for_recommendations(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    backend_env = tmp_path / ".env"
    backend_env.write_text(
        "CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\nDEVICE_MODE='gpu'\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--profile",
        "adaptive",
        "--report-only",
        env=env,
    )

    assert result.returncode == 0
    assert "DEVICE_MODE=gpu" in result.stdout


def test_launcher_report_only_cli_gpu_layers_override_beats_backend_env(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    backend_env = tmp_path / ".env"
    backend_env.write_text(
        "CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\nGPU_LAYERS_MODE='manual'\nN_GPU_LAYERS_OVERRIDE='24'\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--gpu-layers-mode=max",
        "--report-only",
        env=env,
    )

    assert result.returncode == 0
    assert "GPU_LAYERS_MODE=max" in result.stdout
    assert "N_GPU_LAYERS_OVERRIDE=-1" in result.stdout


def test_launcher_report_only_cli_component_device_override_beats_backend_env(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    backend_env = tmp_path / ".env"
    backend_env.write_text(
        "CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\nINTENT_EMBEDDER_DEVICE_MODE='cpu'\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--intent-embedder-device-mode=gpu",
        "--report-only",
        env=env,
    )

    assert result.returncode == 0
    assert "INTENT_EMBEDDER_DEVICE_MODE=gpu" in result.stdout


def test_launcher_report_only_loads_special_character_secrets_without_shell_evaluation(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    backend_env = tmp_path / ".env"
    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\nDEVICE_MODE='gpu'\n", encoding="utf-8")
    set_key(backend_env, "CHAINLIT_ADMIN_PASSWORD", "pa$$w'rd $(echo hacked) #bang", quote_mode="always")
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--profile",
        "adaptive",
        "--report-only",
        env=env,
    )

    assert result.returncode == 0
    assert "DEVICE_MODE=gpu" in result.stdout


def test_launcher_native_rejects_review_runtime(tmp_path):
    backend_env = tmp_path / ".env"
    backend_env.write_text(
        "CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\nINTENT_EMBEDDER_DEVICE_MODE='cpu'\nRETRIEVAL_EMBEDDER_DEVICE_MODE='cpu'\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)

    result = _run_script(
        "launcher.sh",
        "--target",
        "native",
        "--profile",
        "adaptive",
        "--review-runtime",
        env=env,
    )

    assert result.returncode == 1
    assert "больше не выполняет runtime review для native path" in result.stderr


def test_run_native_direct_start_uses_backend_env_by_default(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"
    llm_file = tmp_path / "model.gguf"
    vlm_file = tmp_path / "model-vlm.gguf"
    llm_file.write_text("stub", encoding="utf-8")
    vlm_file.write_text("stub", encoding="utf-8")
    intent_dir = tmp_path / "intent"
    retrieval_dir = tmp_path / "retrieval"
    intent_dir.mkdir()
    retrieval_dir.mkdir()
    uploads_dir.mkdir()

    backend_env.write_text(
        "\n".join(
            [
                "CHAINLIT_AUTH_SECRET='ok'",
                "CHAINLIT_ADMIN_PASSWORD='ok'",
                f"MODEL_PATH_LLM='{llm_file}'",
                f"MODEL_PATH_VLM='{vlm_file}'",
                "MMPROJ_PATH=''",
                f"MODEL_PATH_EMBEDDING_INTENT='{intent_dir}'",
                f"MODEL_PATH_EMBEDDING_RETRIEVAL='{retrieval_dir}'",
                f"UPLOADS_DIR='{uploads_dir}'",
            ]
        ),
        encoding="utf-8",
    )
    runtime_env.write_text("MODEL_PATH_LLM='/missing/path.llm'\n", encoding="utf-8")
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_SKIP_CONDA_CHECKS"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_native:test-mode validated" in result.stdout


def test_run_native_prints_startup_config_summary_in_test_mode(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"
    llm_file = tmp_path / "model.gguf"
    vlm_file = tmp_path / "model-vlm.gguf"
    llm_file.write_text("stub", encoding="utf-8")
    vlm_file.write_text("stub", encoding="utf-8")
    intent_dir = tmp_path / "intent"
    retrieval_dir = tmp_path / "retrieval"
    intent_dir.mkdir()
    retrieval_dir.mkdir()
    uploads_dir.mkdir()

    backend_env.write_text(
        "\n".join(
            [
                "CHAINLIT_AUTH_SECRET='ok'",
                "CHAINLIT_ADMIN_PASSWORD='ok'",
                "CONDA_ENV='diploma_llm'",
                "BACKEND_MODE='llama-cpp-python'",
                "UMS_RUNTIME_PROFILE='adaptive'",
                "LLM_DEVICE_MODE='gpu'",
                "VLM_DEVICE_MODE='gpu'",
                "INTENT_EMBEDDER_DEVICE_MODE='cpu'",
                "RETRIEVAL_EMBEDDER_DEVICE_MODE='cpu'",
                "UMS_LLM_GPU_INDICES='0,1'",
                "UMS_EMBEDDING_GPU_INDEX='1'",
                "AGENT_API_PORT='8100'",
                "DOC_PORT='8101'",
                "LEGAL_PORT='8102'",
                "UMS_PORT='8190'",
                "OPENWEBUI_PORT='3101'",
                "QDRANT_PORT='6433'",
                f"MODEL_PATH_LLM='{llm_file}'",
                f"MODEL_PATH_VLM='{vlm_file}'",
                "MMPROJ_PATH=''",
                f"MODEL_PATH_EMBEDDING_INTENT='{intent_dir}'",
                f"MODEL_PATH_EMBEDDING_RETRIEVAL='{retrieval_dir}'",
                f"UPLOADS_DIR='{uploads_dir}'",
            ]
        ),
        encoding="utf-8",
    )
    runtime_env.write_text("MODEL_PATH_LLM='/missing/path.llm'\n", encoding="utf-8")
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_SKIP_CONDA_CHECKS"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--no-attach", env=env)

    assert result.returncode == 0
    assert "runtime: source=backend/.env conda=diploma_llm backend=llama-cpp-python profile=adaptive qdrant=on openwebui=on" in result.stdout
    assert "placement: llm=gpu vlm=gpu intent=cpu retrieval=cpu llm_gpus=0,1 embed_gpu=1" in result.stdout
    assert "ports: api=8100 doc=8101 legal=8102 ums=8190 qdrant=6433 openwebui=3101" in result.stdout
    assert "run_native:test-mode validated" in result.stdout


def test_run_native_direct_start_skip_chainlit_alias_maps_to_openwebui_skip(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"
    llm_file = tmp_path / "model.gguf"
    llm_file.write_text("stub", encoding="utf-8")
    intent_dir = tmp_path / "intent"
    retrieval_dir = tmp_path / "retrieval"
    intent_dir.mkdir()
    retrieval_dir.mkdir()

    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\n", encoding="utf-8")
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
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_SKIP_CONDA_CHECKS"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--apply-runtime", "--skip-chainlit", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_native:test-mode validated" in result.stdout
    assert "skip_openwebui=true" in result.stdout.lower()
    assert "skip_chainlit_compat=true" in result.stdout.lower()


def test_evaluate_runtime_defaults_to_plan(tmp_path):
    backend_env = tmp_path / ".env"
    backend_env.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)

    result = _run_script("evaluate_runtime.sh", env=env)

    assert result.returncode == 0
    assert "Рекомендации для backend/.env" in result.stdout


def test_evaluate_runtime_rejects_apply(tmp_path):
    backend_env = tmp_path / ".env"
    backend_env.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)

    result = _run_script("evaluate_runtime.sh", "apply", env=env)

    assert result.returncode == 1
    assert "не применяет изменения автоматически" in result.stderr


def test_stop_native_uses_override_env_files_and_safe_loader(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    port = 18991
    backend_env.write_text(f"CHAINLIT_AUTH_SECRET='ok'\nAGENT_API_PORT='{port}'\n", encoding="utf-8")
    set_key(backend_env, "CHAINLIT_ADMIN_PASSWORD", "pa$$w'rd $(echo hacked) #bang", quote_mode="always")
    server = _spawn_http_server(port)
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["PATH"] = _build_stop_script_stub_path(tmp_path)

    try:
        result = _run_script("stop_native.sh", env=env)
        assert result.returncode == 0
        assert "command not found" not in result.stderr
        server.wait(timeout=5)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=5)


def test_stop_all_uses_override_env_files_and_safe_loader(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    port = 18992
    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\n", encoding="utf-8")
    set_key(backend_env, "CHAINLIT_ADMIN_PASSWORD", "pa$$w'rd $(echo hacked) #bang", quote_mode="always")
    runtime_env.write_text(f"AGENT_API_PORT='{port}'\n", encoding="utf-8")
    server = _spawn_http_server(port)
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["PATH"] = _build_stop_script_stub_path(tmp_path, docker_exit_code=0)

    try:
        result = _run_script("stop_all.sh", env=env)
        assert result.returncode == 0
        assert "command not found" not in result.stderr
        server.wait(timeout=5)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=5)


def test_run_native_from_launcher_reports_invalid_model_path(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"

    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\n", encoding="utf-8")
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
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_SKIP_CONDA_CHECKS"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--from-launcher", "--apply-runtime", "--no-attach", env=env)

    assert result.returncode == 1
    assert "env-invalid:MODEL_PATH_LLM:missing-file:" in result.stderr


def test_run_native_from_launcher_skips_runtime_env_load_when_requested(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"
    llm_file = tmp_path / "model.gguf"
    vlm_file = tmp_path / "model-vlm.gguf"
    llm_file.write_text("stub", encoding="utf-8")
    vlm_file.write_text("stub", encoding="utf-8")
    intent_dir = tmp_path / "intent"
    retrieval_dir = tmp_path / "retrieval"
    intent_dir.mkdir()
    retrieval_dir.mkdir()
    uploads_dir.mkdir()

    backend_env.write_text(
        "\n".join(
            [
                "CHAINLIT_AUTH_SECRET='ok'",
                "CHAINLIT_ADMIN_PASSWORD='ok'",
                f"MODEL_PATH_LLM='{llm_file}'",
                f"MODEL_PATH_VLM='{vlm_file}'",
                "MMPROJ_PATH=''",
                f"MODEL_PATH_EMBEDDING_INTENT='{intent_dir}'",
                f"MODEL_PATH_EMBEDDING_RETRIEVAL='{retrieval_dir}'",
                f"UPLOADS_DIR='{uploads_dir}'",
            ]
        ),
        encoding="utf-8",
    )
    runtime_env.write_text("MODEL_PATH_LLM='/missing/path.llm'\n", encoding="utf-8")
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_SKIP_CONDA_CHECKS"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--from-launcher", "--skip-runtime-apply", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_native:test-mode validated" in result.stdout
    assert "env-load-failed:runtime overrides" not in result.stderr


def test_run_native_from_launcher_honors_skip_chainlit_alias_in_test_mode(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"
    llm_file = tmp_path / "model.gguf"
    intent_dir = tmp_path / "intent"
    retrieval_dir = tmp_path / "retrieval"
    llm_file.write_text("stub", encoding="utf-8")
    intent_dir.mkdir()
    retrieval_dir.mkdir()

    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\n", encoding="utf-8")
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
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_SKIP_CONDA_CHECKS"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--from-launcher", "--apply-runtime", "--skip-chainlit", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_native:test-mode validated" in result.stdout
    assert "skip_openwebui=true" in result.stdout.lower()
    assert "skip_chainlit_compat=true" in result.stdout.lower()


def test_run_native_from_launcher_repairs_writable_uploads_dir(tmp_path):
    backend_env = tmp_path / ".env"
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
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_SKIP_CONDA_CHECKS"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    try:
        result = _run_script("run_native.sh", "--from-launcher", "--apply-runtime", "--no-attach", env=env)
    finally:
        uploads_dir.chmod(0o700)

    assert result.returncode == 0
    assert "run_native:test-mode validated" in result.stdout


def test_run_native_from_launcher_validates_env_in_test_mode(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"

    llm_file = tmp_path / "model.gguf"
    llm_file.write_text("stub", encoding="utf-8")
    intent_dir = tmp_path / "intent"
    retrieval_dir = tmp_path / "retrieval"
    intent_dir.mkdir()
    retrieval_dir.mkdir()

    backend_env.write_text("CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='ok'\n", encoding="utf-8")
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
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_SKIP_CONDA_CHECKS"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--from-launcher", "--apply-runtime", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_native:test-mode validated" in result.stdout


def test_run_native_from_launcher_accepts_backend_relative_model_paths(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"
    llm_path = tmp_path / "models" / "gguf" / "qwen-14b" / "Qwen2.5-14B-Instruct-Q4_K_M.gguf"
    vlm_path = tmp_path / "models" / "gguf" / "Qwen3-VL-8B-Q4" / "Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
    mmproj_path = tmp_path / "models" / "gguf" / "Qwen3-VL-8B-Q4" / "mmproj-Qwen3-VL-8B-Instruct-F16.gguf"
    intent_dir = tmp_path / "models" / "st" / "Qwen3-Embedding-0.6B"
    retrieval_dir = tmp_path / "models" / "st" / "LaBSE"
    llm_path.parent.mkdir(parents=True, exist_ok=True)
    vlm_path.parent.mkdir(parents=True, exist_ok=True)
    intent_dir.mkdir(parents=True, exist_ok=True)
    retrieval_dir.mkdir(parents=True, exist_ok=True)
    llm_path.write_text("stub", encoding="utf-8")
    vlm_path.write_text("stub", encoding="utf-8")
    mmproj_path.write_text("stub", encoding="utf-8")

    backend_env.write_text(
        "\n".join(
            [
                "CHAINLIT_AUTH_SECRET='ok'",
                "CHAINLIT_ADMIN_PASSWORD='ok'",
                "MODEL_PATH_LLM='./models/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf'",
                "MODEL_PATH_VLM='./models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf'",
                "MMPROJ_PATH='./models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf'",
                "MODEL_PATH_EMBEDDING_INTENT='./models/st/Qwen3-Embedding-0.6B'",
                "MODEL_PATH_EMBEDDING_RETRIEVAL='./models/st/LaBSE'",
            ]
        ),
        encoding="utf-8",
    )
    runtime_env.write_text(f"UPLOADS_DIR='{uploads_dir}'\n", encoding="utf-8")
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_SKIP_CONDA_CHECKS"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_native.sh", "--from-launcher", "--apply-runtime", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_native:test-mode validated" in result.stdout


def test_run_native_prefers_base_when_configured_conda_env_is_missing(tmp_path):
    backend_env = tmp_path / ".env"
    runtime_env = tmp_path / ".env.runtime"
    uploads_dir = tmp_path / "uploads"

    llm_file = tmp_path / "model.gguf"
    llm_file.write_text("stub", encoding="utf-8")
    intent_dir = tmp_path / "intent"
    retrieval_dir = tmp_path / "retrieval"
    intent_dir.mkdir()
    retrieval_dir.mkdir()

    backend_env.write_text(
        "\n".join(
            [
                "CHAINLIT_AUTH_SECRET='ok'",
                "CHAINLIT_ADMIN_PASSWORD='ok'",
                "CONDA_ENV='diploma_llm'",
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
    runtime_env.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(backend_env)
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)
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
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_all.sh", "--no-attach", env=env)

    assert result.returncode == 0
    assert "launcher:test-mode target=container" in result.stdout


def test_run_all_from_launcher_enables_vllm_compose_profile(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["BACKEND_MODE"] = "vllm"

    result = _run_script("run_all.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_all:test-mode backend_mode=vllm" in result.stdout
    assert "compose_profiles=--profile vllm" in result.stdout
    assert "compose_up_mode=--no-build" in result.stdout
    assert "phase1_services=qdrant document-server legal-server ums vllm" in result.stdout
    assert "phase2_services=agent-api open-webui" in result.stdout


def test_run_all_from_launcher_keeps_openwebui_qdrant_for_local_backend(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)
    env["BACKEND_MODE"] = "llama-server"

    result = _run_script("run_all.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 0
    assert "run_all:test-mode backend_mode=llama-server" in result.stdout
    assert "compose_profiles=(none)" in result.stdout
    assert "compose_up_mode=--no-build" in result.stdout
    assert "phase1_services=qdrant document-server legal-server ums" in result.stdout
    assert "phase2_services=agent-api open-webui" in result.stdout


def test_install_mode_uses_installer_coordinator(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("launcher.sh", "--target", "native", "--install", env=env)

    assert result.returncode == 0
    assert "install:test-mode target=native" in result.stdout
    assert "platform=ubuntu" in result.stdout or "platform=wsl" in result.stdout


def test_install_script_supports_dry_run_in_test_mode():
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"

    result = _run_script("install/install.sh", "--target=native", "--platform=ubuntu", "--dry-run", env=env)

    assert result.returncode == 0
    assert "install:test-mode target=native platform=ubuntu dry_run=1" in result.stdout


def test_install_coordinator_reports_native_legacy_delegate(tmp_path):
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"

    result = _run_script("install/install.sh", "--target=native", "--platform=ubuntu", env=env)

    assert result.returncode == 0
    assert "install:test-mode target=native platform=ubuntu" in result.stdout
    assert "coordinator=install_ubuntu.sh" in result.stdout


def test_install_coordinator_accepts_ubuntu_server_platform():
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"

    result = _run_script("install/install.sh", "--target=native", "--platform=ubuntu-server", env=env)

    assert result.returncode == 0
    assert "platform=ubuntu-server" in result.stdout
    assert "coordinator=install_ubuntu_server.sh" in result.stdout


def test_install_coordinator_accepts_wsl_platform():
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"

    result = _run_script("install/install.sh", "--target=native", "--platform=wsl", env=env)

    assert result.returncode == 0
    assert "platform=wsl" in result.stdout
    assert "coordinator=install_wsl.sh" in result.stdout


def test_install_coordinator_auto_detects_wsl():
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["WSL_DISTRO_NAME"] = "Ubuntu"

    result = _run_script("install/install.sh", "--target=native", env=env)

    assert result.returncode == 0
    assert "platform=wsl" in result.stdout
    assert "coordinator=install_wsl.sh" in result.stdout


def test_install_coordinator_windows_platform_reports_powershell_entrypoint():
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"

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
                'CHAINLIT_AUTH_SECRET="llm-tools-platform-secret-key-change-me"',
                'CHAINLIT_ADMIN_PASSWORD="admin"',
                'GF_SECURITY_ADMIN_PASSWORD="change-me-grafana"',
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(env_file)
    env["LLM_TOOLS_PLATFORM_SKIP_COMMAND_CHECKS"] = "1"

    result = _run_script("bootstrap_env.sh", "--check", "--target=native", env=env)

    assert result.returncode == 1
    assert "secret-regenerated:CHAINLIT_AUTH_SECRET" in result.stdout
    assert "insecure-secret:CHAINLIT_ADMIN_PASSWORD" in result.stdout
    assert "insecure-secret:GF_SECURITY_ADMIN_PASSWORD" in result.stdout
    contents = env_file.read_text(encoding="utf-8")
    assert 'CHAINLIT_AUTH_SECRET="llm-tools-platform-secret-key-change-me"' not in contents


def test_bootstrap_check_creates_env_with_generated_chainlit_auth_secret(tmp_path):
    env_file = tmp_path / ".env"
    template_file = tmp_path / ".env.example"
    template_file.write_text(
        "\n".join(
            [
                'CHAINLIT_AUTH_SECRET="llm-tools-platform-secret-key-change-me"',
                'CHAINLIT_ADMIN_PASSWORD="strong-password"',
                'GF_SECURITY_ADMIN_PASSWORD="strong-grafana-password"',
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(env_file)
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_TEMPLATE_FILE"] = str(template_file)
    env["LLM_TOOLS_PLATFORM_SKIP_COMMAND_CHECKS"] = "1"

    result = _run_script("bootstrap_env.sh", "--check", "--target=native", env=env)

    assert result.returncode == 0
    assert "env-created:" in result.stdout
    assert "secret-generated:CHAINLIT_AUTH_SECRET" in result.stdout
    contents = env_file.read_text(encoding="utf-8")
    assert 'CHAINLIT_AUTH_SECRET="llm-tools-platform-secret-key-change-me"' not in contents
    assert 'CHAINLIT_ADMIN_PASSWORD="strong-password"' in contents


def test_bootstrap_check_rotates_default_chainlit_auth_secret_only(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'CHAINLIT_AUTH_SECRET="llm-tools-platform-secret-key-change-me"',
                'CHAINLIT_ADMIN_PASSWORD="strong-password"',
                'GF_SECURITY_ADMIN_PASSWORD="strong-grafana-password"',
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(env_file)
    env["LLM_TOOLS_PLATFORM_SKIP_COMMAND_CHECKS"] = "1"

    result = _run_script("bootstrap_env.sh", "--check", "--target=native", env=env)

    assert result.returncode == 0
    assert "secret-regenerated:CHAINLIT_AUTH_SECRET" in result.stdout
    contents = env_file.read_text(encoding="utf-8")
    assert 'CHAINLIT_AUTH_SECRET="llm-tools-platform-secret-key-change-me"' not in contents
    assert 'CHAINLIT_ADMIN_PASSWORD="strong-password"' in contents
    assert 'GF_SECURITY_ADMIN_PASSWORD="strong-grafana-password"' in contents


def test_bootstrap_check_allows_insecure_defaults_when_explicitly_enabled(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'CHAINLIT_AUTH_SECRET="llm-tools-platform-secret-key-change-me"',
                'CHAINLIT_ADMIN_PASSWORD="admin"',
                'GF_SECURITY_ADMIN_PASSWORD="change-me-grafana"',
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE"] = str(env_file)
    env["LLM_TOOLS_PLATFORM_SKIP_COMMAND_CHECKS"] = "1"
    env["LLM_TOOLS_PLATFORM_ALLOW_INSECURE_DEFAULTS"] = "1"

    result = _run_script("bootstrap_env.sh", "--check", "--target=native", env=env)

    assert result.returncode == 0
    assert "bootstrap:ok target=native" in result.stdout


def test_run_all_never_prints_default_password_hint(tmp_path):
    runtime_env = tmp_path / ".env.runtime"
    env = os.environ.copy()
    env["LLM_TOOLS_PLATFORM_TEST_MODE"] = "1"
    env["LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE"] = str(runtime_env)

    result = _run_script("run_all.sh", "--from-launcher", "--no-attach", env=env)

    assert result.returncode == 0
    assert "admin/admin" not in result.stdout


def test_native_and_start_system_test_export_backend_pythonpath_for_service_servers():
    run_native = (SCRIPTS_DIR / "run_native.sh").read_text(encoding="utf-8")
    start_system_test = (TEST_HARNESS_SYSTEM_DIR / "start_system_test.sh").read_text(encoding="utf-8")

    assert "export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_document_server:app" in run_native
    assert "export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_legal_server:app" in run_native
    assert "export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_document_server:app" in start_system_test
    assert "export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_legal_server:app" in start_system_test


def test_openwebui_eval_docs_use_direct_compose_command():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    guide = (PROJECT_ROOT / "docs" / "guides" / "openwebui-eval-contour.md").read_text(encoding="utf-8")

    assert "docker compose up -d open-webui" in readme
    assert "./scripts/run_openwebui.sh" not in readme
    assert "docker compose up -d open-webui" in guide
    assert "./scripts/run_openwebui.sh" not in guide


def test_start_system_test_contains_executable_tmux_commands():
    start_system_test = (TEST_HARNESS_SYSTEM_DIR / "start_system_test.sh").read_text(encoding="utf-8")

    assert '\ntmux new-session -d -s "$SESSION_NAME" -x 200 -y 50\n' in start_system_test
    assert '\ntmux new-window -t "$SESSION_NAME" -n "agent-api"\n' in start_system_test
    assert '\ntmux new-window -t "$SESSION_NAME" -n "doc-server"\n' in start_system_test
    assert '\ntmux new-window -t "$SESSION_NAME" -n "legal-server"\n' in start_system_test
    assert '\ntmux new-window -t "$SESSION_NAME" -n "ums"\n' in start_system_test


def test_run_all_waits_for_infer_ready_endpoint():
    run_all = (SCRIPTS_DIR / "run_all.sh").read_text(encoding="utf-8")

    assert "/ready/infer" in run_all
    assert "wait_for_infer_ready" in run_all
    assert 'up --no-build --no-deps -d "${PHASE2_SERVICES[@]}"' in run_all


def test_run_native_waits_for_infer_ready_before_starting_ui():
    run_native = (SCRIPTS_DIR / "run_native.sh").read_text(encoding="utf-8")

    assert "wait_for_infer_ready" in run_native
    assert "/ready/infer" in run_native
    assert 'echo -e "${YELLOW}Пропуск запуска Agent API и Open WebUI: UMS infer-ready не подтвержден.${NC}"' in run_native
    assert "docker compose up --no-build --no-deps -d open-webui" in run_native


def test_stop_scripts_kill_detached_runtime_process_patterns():
    stop_native = (SCRIPTS_DIR / "stop_native.sh").read_text(encoding="utf-8")
    stop_all = (SCRIPTS_DIR / "stop_all.sh").read_text(encoding="utf-8")

    native_expected_patterns = [
        'kill_matching_processes "UMS" "services/model_manager/unified_model_server.py"',
        'kill_matching_processes "llama-server runtime" "llama-server"',
        'kill_matching_processes "embedding runtime" "services/model_manager/st_server.py"',
        'kill_matching_processes "Document Server" "uvicorn mcp_document_server:app --host 0.0.0.0 --port $DOC_PORT"',
        'kill_matching_processes "Legal Server" "uvicorn mcp_legal_server:app --host 0.0.0.0 --port $LEGAL_PORT"',
        'kill_matching_processes "Agent API" "python agent_api.py"',
    ]

    for pattern in native_expected_patterns:
        assert pattern in stop_native
        assert pattern in stop_all

    assert 'docker compose stop open-webui qdrant' in stop_native
    assert 'kill_matching_processes "Chainlit" "chainlit run chainlit_app.py --host 0.0.0.0 --port $CHAINLIT_PORT"' not in stop_native
    assert 'kill_matching_processes "Chainlit" "chainlit run chainlit_app.py --host 0.0.0.0 --port $CHAINLIT_PORT"' in stop_all
