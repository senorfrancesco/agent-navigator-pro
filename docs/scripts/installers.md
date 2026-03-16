# Installer Framework Plan

Этот документ фиксирует безопасный additive plan для installer path. Он не заменяет текущий runtime contour `launcher.sh + runtime_preflight.py` и не переписывает legacy heavy installer `scripts/setup_ubuntu.sh`.

## Current State

- Текущий heavy Linux install path: `bash scripts/setup_ubuntu.sh`
- Текущий guided entrypoint: `./scripts/launcher.sh --install --platform <platform>`
- Текущий install coordinator: `./scripts/install/install.sh`
- `install.sh` уже существует и маршрутизирует install path:
  - `target=native` -> select platform wrapper (`ubuntu`, `ubuntu-server`, `wsl`, `windows`)
  - Linux wrappers -> delegate to `setup_ubuntu.sh`
  - `target=container` -> guidance only, без host install
- Остальные installer-step scripts добавлены как scaffold-only contracts, без destructive install logic

## Platform Matrix

| Platform | Entry point | Current behavior | Official references |
| --- | --- | --- | --- |
| Windows host | `powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1 -Mode Guide` | Проверяет WSL / `winget`; в safe mode печатает guided steps и направляет в WSL-based path | Microsoft WSL install docs, Docker Desktop docs |
| WSL Ubuntu | `./scripts/install/install.sh --platform=wsl` | Делегирует в heavy Ubuntu install flow внутри WSL | Microsoft WSL docs, Ubuntu on WSL docs |
| Ubuntu Desktop | `./scripts/install/install.sh --platform=ubuntu` | Делегирует в `scripts/setup_ubuntu.sh` | Docker Engine on Ubuntu, Python venv, llama.cpp |
| Ubuntu Server | `./scripts/install/install.sh --platform=ubuntu-server` | Делегирует в `scripts/setup_ubuntu.sh` | Ubuntu Server docs, Docker Engine on Ubuntu, NVIDIA CUDA Linux |

## Target Layout

```text
scripts/
  install/
    install.sh
    detect_os.sh
    install_dependencies.sh
    install_cuda.sh
    build_llamacpp.sh
    verify_system.sh
  models/
    install_models.sh
  utils/
    system_check.sh
```

## Design Rules

- Installer path остаётся additive слоем поверх текущего launcher/bootstrap setup.
- `scripts/setup_ubuntu.sh` остаётся существующим heavy Linux bootstrap path до отдельного controlled rewrite.
- Новые scaffold scripts не выполняют реальную установку по умолчанию и намеренно завершаются сообщением `scaffold only`.
- `install.sh` стал unified coordinator shim, а platform-specific wrappers задают поддерживаемые install entrypoints.
- Источником правды для runtime orchestration остаётся `launcher.sh`, а не installer path.

## Responsibilities

### `scripts/install/install.sh`

- Делает: выступает unified coordinator shim для install-path; принимает `target`, определяет `platform` и либо ведёт в platform wrapper, либо печатает guidance для container path.
- Не делает: не содержит всю install logic inline и не заменяет Linux heavy installer одним большим shell-файлом.
- Зависимости: `bash`, platform wrappers `scripts/install/install_ubuntu.sh`, `install_ubuntu_server.sh`, `install_wsl.sh`, `install_windows.ps1`; в будущей реализации дополнительно `scripts/install/detect_os.sh`, `scripts/install/install_dependencies.sh`, `scripts/install/verify_system.sh`, опционально `install_cuda.sh`, `build_llamacpp.sh`, `scripts/models/install_models.sh`.
- Успех проверяется через: успешный exit code wrapper path для `native`, корректный dry-run/help output, явный non-destructive guidance output для `container`.
- Official docs: локальный contract этого слоя должен опираться на [os-release spec](https://www.freedesktop.org/software/systemd/man/249/os-release.html) и профильные vendor docs нижележащих шагов.

### `scripts/install/install_windows.ps1`

- Делает: проверяет наличие WSL и `winget`; в safe mode печатает guided next steps для WSL-based path.
- Не делает: не пытается запускать Linux shell installer на Windows напрямую, не поднимает backend/runtime и не делает unattended host install.
- Зависимости: `wsl.exe`, опционально `winget`, Docker Desktop installer path.
- Успех проверяется через: наличие WSL либо корректный actionable next-step report.
- Official docs: Microsoft WSL install docs, Docker Desktop WSL docs.

### `scripts/install/install_wsl.sh`

- Делает: валидирует запуск внутри WSL и делегирует в существующий Ubuntu installer flow.
- Не делает: не ставит WSL на Windows host и не настраивает Docker Desktop integration на стороне Windows.
- Зависимости: WSL environment, `scripts/setup_ubuntu.sh`.
- Успех проверяется через: запуск из WSL и успешный exit code legacy installer flow.
- Official docs: Microsoft WSL install docs, Ubuntu on WSL docs.

### `scripts/install/install_ubuntu.sh`

- Делает: явный wrapper для Ubuntu Desktop install path.
- Не делает: не создаёт отдельную desktop-specific install logic поверх `setup_ubuntu.sh`.
- Зависимости: `scripts/setup_ubuntu.sh`.
- Успех проверяется через: успешный exit code legacy installer flow.
- Official docs: Docker Engine on Ubuntu, Python venv, llama.cpp.

### `scripts/install/install_ubuntu_server.sh`

- Делает: явный wrapper для Ubuntu Server install path.
- Не делает: не создаёт отдельную server-only install logic поверх `setup_ubuntu.sh`.
- Зависимости: `scripts/setup_ubuntu.sh`.
- Успех проверяется через: успешный exit code legacy installer flow.
- Official docs: Ubuntu Server docs, Docker Engine on Ubuntu, NVIDIA CUDA Linux.

### `scripts/install/detect_os.sh`

- Делает: определяет `ID`, `ID_LIKE`, `VERSION_ID`, архитектуру и supported/unsupported install profile.
- Не делает: не ставит пакеты и не делает network calls.
- Зависимости: `bash`, `/etc/os-release` или `/usr/lib/os-release`, `uname`.
- Успех проверяется через: корректно распознанные `os_id`, `version_id`, `arch`; явный unsupported status для неподдерживаемых окружений вместо молчаливого продолжения.
- Official docs: [os-release(5)](https://www.freedesktop.org/software/systemd/man/249/os-release.html).

### `scripts/install/install_dependencies.sh`

- Делает: ставит системные зависимости для supported OS profile, включая базовые build tools, `tmux`, Python toolchain и Docker prerequisites.
- Не делает: не выбирает runtime profile, не пишет model paths и не запускает runtime stack.
- Зависимости: распознанная ОС из `detect_os.sh`, пакетный менеджер ОС, сетевой доступ к vendor repositories, `sudo`.
- Успех проверяется через: наличие команд `tmux`, `git`, `curl`, `python3`, `pip`; для Docker path дополнительно `docker` и `docker compose`.
- Official docs: [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/), [Python `venv`](https://docs.python.org/3/library/venv.html) как базовая reference для isolated Python env; для Miniconda в этом проекте сохраняется текущий vendor URL strategy из `setup_ubuntu.sh`.

### `scripts/install/install_cuda.sh`

- Делает: ставит CUDA Toolkit только когда обнаружена NVIDIA-среда и пользователь явно выбрал GPU path.
- Не делает: не ставит NVIDIA driver "вслепую", не включает CUDA на unsupported OS и не меняет runtime path без проверки.
- Зависимости: `nvidia-smi` или явный user choice, supported OS profile, доступ к NVIDIA repository/packages, `sudo`.
- Успех проверяется через: `nvcc --version` и/или package-level confirmation, корректный toolkit path, совместимость с поддерживаемой ОС.
- Official docs: [NVIDIA CUDA Installation Guide for Linux](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/index.html).

### `scripts/install/build_llamacpp.sh`

- Делает: клонирует или использует pinned source tree `llama.cpp`, собирает `llama-server`/CLI с нужным backend profile, публикует понятный build output path.
- Не делает: не скачивает модели, не пишет project `.env`, не выбирает context/profile policy за runtime preflight.
- Зависимости: `git`, `cmake`, C/C++ toolchain, при GPU build дополнительно CUDA toolkit.
- Успех проверяется через: наличие собранного бинаря `llama-server` или эквивалентного output, успешный `--help`/version smoke test, сохранённый build directory.
- Official docs: [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) и его build documentation; текущая рекомендация выводится из README проекта, где `llama.cpp` предлагает build from source и `llama-server` как OpenAI-compatible API server.

### `scripts/install/verify_system.sh`

- Делает: собирает единый post-install verification report по командам, портам, Docker, Python env, CUDA и runtime prerequisites.
- Не делает: не чинит систему автоматически и не перезапускает весь runtime stack без явной команды.
- Зависимости: установленные зависимости, конфиг из предыдущих шагов, `scripts/utils/system_check.sh`.
- Успех проверяется через: machine-readable summary со статусами `ok/warn/fail`; обязательный fail при отсутствии критичных команд или unsupported OS.
- Official docs: verification опирается на vendor docs конкретных шагов, прежде всего [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/) и [NVIDIA CUDA Installation Guide for Linux](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/index.html).

### `scripts/models/install_models.sh`

- Делает: скачивает или валидирует наличие model artifacts в заранее заданных путях, разделяя LLM/VLM/embedding artifacts.
- Не делает: не выбирает модель "по умолчанию" без явной конфигурации и не перезаписывает существующие большие файлы без подтверждения.
- Зависимости: доступ к model source, свободное место на диске, явные target directories, при Hugging Face path `huggingface_hub`/`hf` CLI или другой утверждённый downloader.
- Успех проверяется через: наличие ожидаемых файлов/директорий, контроль размеров/имен, сформированный отчёт для последующего заполнения `backend/.env`.
- Official docs: [Hugging Face model downloads](https://huggingface.co/docs/hub/en/models-downloading) как reference для `hf download`; конкретные model-card источники должны добавляться отдельно на этапе реальной реализации.

### `scripts/utils/system_check.sh`

- Делает: предоставляет общие helper-проверки для CPU/GPU/диска/памяти/команд, чтобы installer scripts не дублировали одну и ту же shell-логику.
- Не делает: не становится вторым orchestration layer и не хранит project-specific install policy.
- Зависимости: POSIX shell utilities, `uname`, `command -v`, `df`, `free`, опционально `nvidia-smi`.
- Успех проверяется через: reusable helper functions возвращают предсказуемые exit codes и человекочитаемый статус.
- Official docs: локальная helper-утилита, но для OS detection должна следовать [os-release(5)](https://www.freedesktop.org/software/systemd/man/249/os-release.html).

## Verification Contract For This Scaffold Slice

- `bash -n` для всех новых scaffold scripts
- `git diff --check`
- ручная проверка, что новые scripts по умолчанию не выполняют destructive install actions
- ручная проверка согласованности с `README.md`, [docs/scripts/README.md](README.md) и текущим `setup_ubuntu.sh`

## Source Notes

- `detect_os.sh` responsibilities привязаны к `os-release(5)`; это прямой источник формата и полей.
- `install_dependencies.sh` и `verify_system.sh` ориентируются на Docker official install flow для Ubuntu.
- `install_cuda.sh` ориентируется на актуальный Linux installation guide от NVIDIA; из него следует, что нужны supported OS profile и явная post-install verification.
- `build_llamacpp.sh` описан по текущему upstream `ggml-org/llama.cpp`: upstream рекомендует build from source и использует `llama-server` как OpenAI-compatible HTTP server.
- `install_models.sh` привязан к Hugging Face docs, где официально описан `hf download`.
- Windows/WSL path дополнительно опирается на Microsoft WSL docs, Docker Desktop WSL docs и Python-on-Windows docs.
