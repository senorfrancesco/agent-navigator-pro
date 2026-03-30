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
- Большинство installer-step scripts пока остаются scaffold-only contracts, без destructive install logic; исключения: `scripts/install/build_llamacpp.sh` уже реализован как рабочий build-step, а `scripts/models/install_models.sh` работает как model provisioning layer

## Platform Matrix

| Platform | Entry point | Current behavior | Official references |
| --- | --- | --- | --- |
| Windows host | `powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1 -CheckOnly` | Проверяет WSL / `winget`; в safe mode печатает guided steps и направляет в WSL-based path | Microsoft WSL install docs, Docker Desktop docs |
| WSL Ubuntu | `./scripts/install/install.sh --platform=wsl` | Делегирует в heavy Ubuntu install flow внутри WSL | Microsoft WSL docs, Ubuntu on WSL docs |
| Ubuntu Desktop | `./scripts/install/install.sh --platform=ubuntu` | Делегирует в `scripts/setup_ubuntu.sh` | Docker Engine on Ubuntu, Miniconda/Conda, llama.cpp |
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
- Часть installer-step scripts всё ещё остаётся scaffold-only, но `scripts/install/build_llamacpp.sh` уже является рабочим build step для локального `llama-server`.
- `install.sh` стал unified coordinator shim, а platform-specific wrappers задают поддерживаемые install entrypoints.
- Источником правды для runtime orchestration остаётся `launcher.sh`, а не installer path.

## Responsibilities

### `scripts/install/install.sh`

- Делает: выступает unified coordinator shim для install-path; принимает `target`, определяет `platform` и либо ведёт в platform wrapper, либо печатает guidance для container path.
- Не делает: не содержит всю install logic inline и не заменяет Linux heavy installer одним большим shell-файлом.
- Зависимости: `bash`, platform wrappers `scripts/install/install_ubuntu.sh`, `install_ubuntu_server.sh`, `install_wsl.sh`, `install_windows.ps1`; в будущей реализации дополнительно `scripts/install/detect_os.sh`, `scripts/install/install_dependencies.sh`, `scripts/install/verify_system.sh`, опционально `install_cuda.sh`, `scripts/models/install_models.sh`.
- Успех проверяется через: успешный exit code wrapper path для `native`, корректный dry-run/help output, явный non-destructive guidance output для `container`.
- Official docs: локальный contract этого слоя должен опираться на [os-release spec](https://www.freedesktop.org/software/systemd/man/249/os-release.html) и профильные vendor docs нижележащих шагов.

### `scripts/install/install_windows.ps1`

- Делает: проверяет наличие WSL и `winget`; в safe mode печатает guided next steps для WSL-based path.
- Не делает: не пытается запускать Linux shell installer на Windows напрямую, не поднимает backend/runtime и не делает unattended host install.
- Зависимости: `wsl.exe`, опционально `winget`, Docker Desktop installer path.
- Успех проверяется через: наличие WSL либо корректный actionable next-step report.
- Official docs: Microsoft WSL install docs, Docker Desktop WSL docs.

### `scripts/install/install_wsl.sh`

- Делает: валидирует запуск внутри WSL, печатает expected Docker Desktop / WSL integration contract и делегирует в существующий Ubuntu installer flow.
- Не делает: не ставит WSL на Windows host и не настраивает Docker Desktop integration на стороне Windows.
- Зависимости: WSL environment, `scripts/setup_ubuntu.sh`.
- Успех проверяется через: запуск из WSL и явный guided flow без silent failure на Docker step.
- Official docs: Microsoft WSL install docs, Ubuntu on WSL docs.

### `scripts/install/install_ubuntu.sh`

- Делает: явный wrapper для Ubuntu Desktop install path.
- Не делает: не создаёт отдельную desktop-specific install logic поверх `setup_ubuntu.sh`.
- Зависимости: `scripts/setup_ubuntu.sh`.
- Успех проверяется через: успешный exit code legacy installer flow.
- Official docs: Docker Engine on Ubuntu, Miniconda/Conda, llama.cpp.

### `scripts/setup_ubuntu.sh`

- Делает: heavy Ubuntu/WSL installer path для зависимостей, Python env и runtime prerequisites.
- Дополнительно:
  - ставит Python-зависимости в `conda base`;
  - предлагает записать managed-block в `~/.bashrc` для `conda`, `CUDA` и локального `llama.cpp` build output;
  - может вызвать `scripts/install/build_llamacpp.sh` как source-build шаг для `llama-server`.
- Для `WSL`: Docker step теперь работает как guided check, а не как silent attempt to install Docker Engine inside distro.
- Если `docker` не виден в `WSL` или daemon недоступен, скрипт:
  - печатает, что ожидается Docker Desktop на Windows host;
  - подсказывает проверить WSL integration;
  - спрашивает, пропустить Docker step или остановиться.
- Для repo-managed конфигов:
  - определяет `PROJECT_ROOT` явно;
  - не перезаписывает существующие пользовательские `tmux` конфиги вслепую;
  - если файл уже совпадает с репозиторным шаблоном, пропускает его;
  - если файл отличается, спрашивает перед overwrite.
- Не делает: не пытается непрозрачно лечить Docker Desktop / WSL integration на стороне Windows.

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

- Делает: ставит `CUDA Toolkit 12.8` только когда обнаружена NVIDIA-среда с достаточным driver baseline и пользователь явно сохраняет GPU path.
- Не делает: не ставит NVIDIA driver "вслепую", не пытается автоматически чинить too-old driver и не включает CUDA на unsupported OS.
- Зависимости: `nvidia-smi`, NVIDIA driver не ниже `570.124.06` для целевого baseline `CUDA 12.8 Update 1`, supported Ubuntu profile (`22.04` или `24.04`), доступ к NVIDIA repository/packages, `sudo`.
- Успех проверяется через: `nvcc --version` с ожидаемым `12.8.x`, корректный toolkit path, совместимость с поддерживаемой ОС.
- Official docs: [NVIDIA CUDA Installation Guide for Linux](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/index.html).

### `scripts/install/build_llamacpp.sh`

- Делает: клонирует или использует pinned source tree `llama.cpp`, собирает `llama-server` с нужным backend profile, публикует понятный build output path.
- Не делает: не скачивает модели, не пишет project `.env`, не выбирает context/profile policy за runtime preflight.
- Зависимости: `git`, `cmake`, C/C++ toolchain, при GPU build дополнительно CUDA toolkit.
- Текущий contract:
  - source path по умолчанию: `deploy/offline_bundle/vendor/llama.cpp`
  - если checkout отсутствует, допускается `git clone --depth 1`
  - build target: `llama-server`
  - CUDA path включается автоматически, если найден `nvcc`
- Успех проверяется через: наличие собранного бинаря `llama-server`, успешный `--help` smoke test, сохранённый build directory.
- Official docs: [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) и его build documentation; текущая рекомендация выводится из README проекта, где `llama.cpp` предлагает build from source и `llama-server` как OpenAI-compatible API server.

### `scripts/install/verify_system.sh`

- Делает: собирает единый post-install verification report по командам, портам, Docker, Python env, NVIDIA driver baseline, CUDA и runtime prerequisites.
- Не делает: не чинит систему автоматически и не перезапускает весь runtime stack без явной команды.
- Зависимости: установленные зависимости, конфиг из предыдущих шагов, `scripts/utils/system_check.sh`.
- Успех проверяется через: machine-readable summary со статусами `ok/warn/fail`; обязательный fail при отсутствии критичных команд или unsupported OS.
- Official docs: verification опирается на vendor docs конкретных шагов, прежде всего [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/) и [NVIDIA CUDA Installation Guide for Linux](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/index.html).

Текущий canonical GPU baseline для guided install path:

- NVIDIA driver: `R570+`, безопасный минимум `570.124.06`
- CUDA Toolkit: `12.8.x`
- PyTorch GPU wheel path: `torch==2.10.0`, `torchvision==0.25.0`, `torchaudio==2.10.0` через `cu128`
- Важно: готовый PyTorch wheel не требует локального CUDA Toolkit, но native CUDA build path для `llama-cpp-python` требует установленный `nvcc`

### `scripts/models/install_models.sh`

- Делает: скачивает или валидирует наличие model artifacts в канонических `MODEL_PATH_*`, разделяя `core` set (`LLM + intent + retrieval`) и `all` (`core + VLM + mmproj`).
- Не делает: не меняет runtime profile; если модели лежат на другом диске, пользователь фиксирует absolute paths в `backend/.env.native`. При этом bootstrap/install path может создать `backend/.env` из шаблона и автоматически записать `CHAINLIT_AUTH_SECRET`, если он отсутствует или остался дефолтным.
- Зависимости: `huggingface_hub`, доступ к model source, свободное место на диске, явные target directories.
- Успех проверяется через: наличие ожидаемых файлов/директорий, контроль путей и печать канонического env block для `MODEL_PATH_LLM`, `MODEL_PATH_VLM`, `MMPROJ_PATH`, `MODEL_PATH_EMBEDDING_INTENT`, `MODEL_PATH_EMBEDDING_RETRIEVAL`.
- Official docs and source ids:
  - [Hugging Face model downloads](https://huggingface.co/docs/hub/en/models-downloading)
  - `Qwen/Qwen2.5-14B-Instruct-GGUF`
  - `Qwen/Qwen3-VL-8B-Instruct-GGUF`
  - `Qwen/Qwen3-Embedding-0.6B`
  - `sentence-transformers/LaBSE`

Примеры:

```bash
./scripts/models/install_models.sh --dry-run
./scripts/models/install_models.sh --ensure-present --models-root=/mnt/d/agent-models
./scripts/models/install_models.sh --ensure-present --asset-set=all --huggingface-cache=/mnt/d/hf-cache
```

Для `WSL` и другого диска модели можно хранить вне `C:` и прописывать absolute paths в `backend/.env.native`, например `/mnt/d/agent-models/...`.

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

## Python Dependencies Note

`backend/requirements.txt` считается каноническим списком Python-зависимостей не только для ML/runtime, но и для SQL/persistence слоя.

Для корректной работы `Chainlit` history/state и backend persistence должны быть установлены как минимум:

- `SQLAlchemy`
- `aiosqlite`
- `asyncpg`
- `Markdown`
- `WeasyPrint`

Для красивого markdown->PDF рендера отчётов на Ubuntu/WSL нужны и системные библиотеки:

- `libcairo2`
- `libpango-1.0-0`
- `libpangocairo-1.0-0`
- `libgdk-pixbuf-2.0-0`
- `shared-mime-info`

Если проект уже разворачивался раньше на частичном наборе пакетов, повторите:

```bash
cd backend && pip install -r requirements.txt
```

Это безопаснее, чем точечно лечить runtime-ошибки после падения `Chainlit` или state store.

## Conda Note

В текущем installer/runtime contract проект больше не требует обязательного отдельного env `diploma_llm`. Safe path:

```bash
source ~/.bashrc
conda activate base
cd backend && pip install -r requirements.txt
```

Если `conda` падает с `ToSNonInteractiveError`, используйте официальные команды принятия условий:

```bash
conda tos accept
```

или точечно:

```bash
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
```

## Runtime placement note

После установки system/runtime path больше не скрывает hardware placement decision. Перед стартом можно посмотреть:

```bash
python scripts/runtime_preflight.py detect
python scripts/runtime_preflight.py plan --profile adaptive
```

И при необходимости переопределить:

```bash
./scripts/launcher.sh --target native --gpu-layers-mode max
./scripts/launcher.sh --target native --gpu-layers-mode manual --gpu-layers 24
./scripts/launcher.sh --target native --device-mode gpu
```

Для постоянных user-owned overrides используйте `backend/.env.hardware.override`, а не `backend/.env.runtime`.

`runtime_preflight plan` теперь показывает placement summary по компонентам, а не только один общий `device_mode`:
- `llm`
- `vlm`
- `intent_embedder`
- `retrieval_embedder`

Для точечного override используйте:
- `LLM_DEVICE_MODE=cpu|gpu|hybrid`
- `VLM_DEVICE_MODE=cpu|gpu|hybrid`
- `INTENT_EMBEDDER_DEVICE_MODE=cpu|gpu|hybrid`
- `RETRIEVAL_EMBEDDER_DEVICE_MODE=cpu|gpu|hybrid`

`DEVICE_MODE` остаётся fallback для heavy runtime path и не означает автоматически, что embeddings тоже пойдут в тот же placement.

Launcher поддерживает оба режима:
- через флаги, например `--llm-device-mode gpu --gpu-layers-mode max`
- через файл, например `--hardware-override-file /path/to/runtime.override.env`

Базовый шаблон лежит в:
- `backend/.env.hardware.override.example`
