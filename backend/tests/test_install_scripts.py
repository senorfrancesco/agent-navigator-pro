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


def test_bootstrap_env_uses_python3_when_python_alias_is_missing(tmp_path):
    env_file = tmp_path / ".env"
    env_template = tmp_path / ".env.example"
    env_template.write_text("", encoding="utf-8")

    result = _run_script(
        ["bash", str(SCRIPTS_DIR / "bootstrap_env.sh"), "--check", "--target=native"],
        env={
            "AGENT_NAVIGATOR_TEST_MODE": "1",
            "AGENT_NAVIGATOR_BACKEND_ENV_FILE": str(env_file),
            "AGENT_NAVIGATOR_BACKEND_ENV_TEMPLATE_FILE": str(env_template),
            "PATH": os.environ["PATH"],
        },
    )

    assert result.returncode == 0
    assert "bootstrap:test-mode mode=check target=native" in result.stdout
    assert env_file.exists()
    assert "CHAINLIT_AUTH_SECRET=" in env_file.read_text(encoding="utf-8")


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


def test_wsl_installer_wrapper_mentions_docker_desktop_integration():
    script = (SCRIPTS_DIR / "install" / "install_wsl.sh").read_text(encoding="utf-8")
    assert "Docker Desktop is running on Windows host" in script
    assert "skip the Docker step or stop" in script


def test_setup_ubuntu_handles_wsl_docker_via_guidance_not_apt_install():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert "Для WSL ожидается Docker Desktop на Windows host" in script
    assert "Пропустить шаг Docker и продолжить остальные шаги установки?" in script
    assert "не устанавливает Docker Engine внутрь дистрибутива автоматически" in script


def test_setup_ubuntu_defines_project_root_and_preserves_user_tmux_config():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert 'PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"' in script
    assert 'cd "$PROJECT_ROOT"' in script
    assert 'cmp -s "$source_path" "$target_path"' in script
    assert 'Перезаписать $label репозиторной версией?' in script
    assert 'tmux.conf уже установлен и совпадает с репозиторным шаблоном' not in script


def test_setup_ubuntu_uses_canonical_runtime_and_model_steps_in_final_guidance():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert "./scripts/bootstrap_env.sh --check --target=native" in script
    assert "CHAINLIT_AUTH_SECRET будет сгенерирован автоматически" in script
    assert "./scripts/models/install_models.sh --ensure-present" in script
    assert "./scripts/launcher.sh --target native" in script
    assert 'echo "   cd backend"' not in script
    assert "tmux attach -t agent-navigator-native" in script
    assert "./scripts/stop_native.sh" in script
    assert "docker logs open-webui" not in script
    assert "CLAUDE_MEMORY.md" not in script
    assert 'echo -e "1. ${BLUE}Скачайте модели или проверьте их наличие:${NC}"' in script
    assert 'echo "   source ~/.bashrc"' in script
    assert 'echo "   source activate_env.sh"' not in script


def test_setup_ubuntu_tracks_docker_relogin_without_stale_reply_variable():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert "DOCKER_RELOGIN_REQUIRED=false" in script
    assert "DOCKER_RELOGIN_REQUIRED=true" in script
    assert 'if [ "$DOCKER_RELOGIN_REQUIRED" = true ]; then' in script


def test_setup_ubuntu_uses_conda_base_instead_of_creating_diploma_env():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert "conda create -n diploma_llm" not in script
    assert "conda env remove -n diploma_llm" not in script
    assert 'conda activate base' in script
    assert "Conda base активирована" in script
    assert "conda tos accept" in script
    assert 'find_existing_conda_sh()' in script
    assert '"$HOME/miniconda3/etc/profile.d/conda.sh"' in script
    assert 'mktemp "${TMPDIR:-/tmp}/agent-nav-miniconda-' in script
    assert 'trap cleanup_miniconda_installer EXIT' in script
    assert 'wget -O miniconda_installer.sh' not in script


def test_setup_ubuntu_targets_cuda_12_8_and_driver_r570_baseline():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert "cuda-toolkit-12-8" in script
    assert "cuda-toolkit-12-4" not in script
    assert "570.124.06" in script
    assert "R570+" in script


def test_setup_ubuntu_mentions_driver_too_old_path_separately_from_missing_driver():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert "версия драйвера NVIDIA слишком старая" in script
    assert "Обновите драйвер NVIDIA до версии не ниже 570.124.06" in script
    assert "NVIDIA драйверы не обнаружены" in script


def test_setup_ubuntu_installs_pytorch_2_10_cu128_on_gpu_path():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert "torch==2.10.0" in script
    assert "torchvision==0.25.0" in script
    assert "torchaudio==2.10.0" in script
    assert "https://download.pytorch.org/whl/cu128" in script
    assert "https://download.pytorch.org/whl/cpu" in script


def test_setup_ubuntu_uses_importlib_metadata_for_package_checks_and_honest_llamacpp_summary():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert "from importlib.metadata import PackageNotFoundError, version" in script
    assert 'print_python_package_version "langgraph" "LangGraph"' in script
    assert "resolve_nvcc_bin" in script
    assert 'echo "llama-cpp backend install mode: ${LLAMA_CPP_INSTALL_MODE}"' in script
    assert 'echo "llama.cpp source build: ${LLAMA_CPP_BUILD_STATUS} (${LLAMA_CPP_BUILD_MESSAGE})"' in script


def test_setup_ubuntu_wires_llamacpp_build_and_prompted_bashrc_management():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert 'bash "$PROJECT_ROOT/scripts/install/build_llamacpp.sh"' in script
    assert "Добавить managed Agent Navigator block в ~/.bashrc" in script
    assert "conda activate base >/dev/null 2>&1 || true" in script
    assert 'agent_nav_prepend_path "$LLAMA_CPP_BUILD_DIR/bin"' in script
    assert "activate_env.sh" not in script


def test_setup_ubuntu_installs_python_packages_only_when_missing_or_mismatched():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert "collect_python_requirements_to_install()" in script
    assert "install_python_requirements_if_needed()" in script
    assert 'install_python_requirements_if_needed "requirements.txt" "llama-cpp-python,torch,torchvision,torchaudio"' in script
    assert 'pip install -r requirements.txt --no-cache-dir' not in script


def test_setup_ubuntu_skips_llamacpp_reinstall_when_package_is_already_present():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert 'python_package_installed "llama-cpp-python"' in script
    assert "llama_cpp_python_has_cuda_support" in script
    assert '--force-reinstall --no-cache-dir' not in script
    assert "llama-cpp-python уже установлен" in script


def test_setup_ubuntu_checks_existing_torch_runtime_before_reinstalling_canonical_baseline():
    script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")
    assert "torch_runtime_matches_target()" in script
    assert 'torch.version.cuda or ""' in script
    assert "Canonical PyTorch GPU baseline" in script
    assert "Canonical PyTorch CPU baseline" in script


def test_build_llamacpp_script_supports_clone_and_cuda_build_flags():
    script = (SCRIPTS_DIR / "install" / "build_llamacpp.sh").read_text(encoding="utf-8")
    assert "git clone --depth 1 --branch" in script
    assert "-DGGML_CUDA=ON" in script
    assert '--target llama-server' in script
    assert "build-llamacpp:ok" in script


def test_build_llamacpp_checks_for_cmake_and_setup_ubuntu_installs_it():
    build_script = (SCRIPTS_DIR / "install" / "build_llamacpp.sh").read_text(encoding="utf-8")
    setup_script = (SCRIPTS_DIR / "setup_ubuntu.sh").read_text(encoding="utf-8")

    assert 'require_command() {' in build_script
    assert 'require_command cmake cmake' in build_script
    assert 'Требуется $command_name для сборки llama.cpp' in build_script
    assert "sudo apt-get install -y \\" in setup_script
    assert "    cmake \\" in setup_script


def test_model_downloader_dry_run_supports_custom_models_root(tmp_path):
    result = _run_script(
        [
            "bash",
            str(SCRIPTS_DIR / "models" / "install_models.sh"),
            "--dry-run",
            f"--models-root={tmp_path}",
            "--asset-set=all",
        ]
    )
    assert result.returncode == 0
    assert "models:plan" in result.stdout
    assert str(tmp_path / "gguf" / "qwen-14b" / "Qwen2.5-14B-Instruct-Q4_K_M.gguf") in result.stdout
    assert str(tmp_path / "st" / "Qwen3-Embedding-0.6B") in result.stdout


def test_model_downloader_uses_python3_when_python_alias_is_missing(tmp_path):
    result = _run_script(
        [
            "bash",
            str(SCRIPTS_DIR / "models" / "install_models.sh"),
            "--dry-run",
            f"--models-root={tmp_path}",
        ],
        env={"PATH": os.environ["PATH"]},
    )

    assert result.returncode == 0
    assert "models:plan" in result.stdout


def test_model_downloader_reads_quoted_env_without_shell_evaluation(tmp_path):
    env_file = tmp_path / ".env"
    models_root = tmp_path / "models"
    env_file.write_text(
        "\n".join(
            [
                "CHAINLIT_AUTH_SECRET='ok'",
                "CHAINLIT_ADMIN_PASSWORD='pa$$w\\'rd $(echo hacked) #bang'",
                f"MODEL_PATH_LLM='{models_root / 'gguf' / 'qwen-14b' / 'Qwen2.5-14B-Instruct-Q4_K_M.gguf'}'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run_script(
        [
            "bash",
            str(SCRIPTS_DIR / "models" / "install_models.sh"),
            "--dry-run",
        ],
        env={
            "AGENT_NAVIGATOR_BACKEND_ENV_FILE": str(env_file),
        },
    )

    assert result.returncode == 0
    assert "command not found" not in result.stderr
    assert str(models_root / "gguf" / "qwen-14b" / "Qwen2.5-14B-Instruct-Q4_K_M.gguf") in result.stdout


def test_model_downloader_help_mentions_models_root_example():
    result = _run_script(["bash", str(SCRIPTS_DIR / "models" / "install_models.sh"), "--help"])
    assert result.returncode == 0
    assert "--models-root=/mnt/d/agent-models" in result.stdout
