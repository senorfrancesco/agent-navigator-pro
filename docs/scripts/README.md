# Script Runtime Map

Этот документ фиксирует каноническую карту runtime-скриптов проекта и разделяет user-facing entrypoints, compatibility aliases, legacy path и internal helpers.

## Canonical Paths

- Основной entrypoint для разработки: `./scripts/launcher.sh --target native --profile adaptive`
- Основной entrypoint для container/compose path: `./scripts/launcher.sh --target container --profile default`
- Основной entrypoint для guided install path: `./scripts/launcher.sh --install --platform <platform>`
- Основной entrypoint для Linux/WSL installer dispatch: `./scripts/install/install.sh --platform auto`
- Основной entrypoint для Windows host bootstrap: `powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1 -Mode Guide`
- Основной stop-path для native runtime: `./scripts/stop_native.sh`
- Основной stop-path для compose/container runtime: `./scripts/stop_all.sh`
- `Open WebUI` не является основным UI; его запуск через `scripts/run_openwebui.sh` считается legacy path.

## Platform Install Quick Start

| Platform | Canonical command | Notes |
| --- | --- | --- |
| Windows host | `powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1 -Mode Guide` | Guided/check wrapper only. Реальный install path продолжается в WSL. |
| WSL (Ubuntu) | `./scripts/launcher.sh --install --platform wsl` | Проверяет WSL context и делегирует в Ubuntu installer path. |
| Ubuntu Desktop | `./scripts/launcher.sh --install --platform ubuntu` | Переиспользует `scripts/setup_ubuntu.sh` через unified installer coordinator. |
| Ubuntu Server | `./scripts/launcher.sh --install --platform ubuntu-server` | Тот же heavy install path, но с отдельным server-oriented wrapper. |

## Model Download Quick Start

```bash
./scripts/models/install_models.sh --dry-run
./scripts/models/install_models.sh --ensure-present
./scripts/models/install_models.sh --ensure-present --asset-set=all
./scripts/models/install_models.sh --ensure-present --models-root=/mnt/d/agent-models
```

Если модели нужно хранить вне `C:` в `WSL`, используйте `/mnt/d/...` и пропишите абсолютные пути в `backend/.env.native`:

```bash
MODEL_PATH_LLM="/mnt/d/agent-models/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf"
MODEL_PATH_VLM="/mnt/d/agent-models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
MMPROJ_PATH="/mnt/d/agent-models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"
MODEL_PATH_EMBEDDING_INTENT="/mnt/d/agent-models/st/Qwen3-Embedding-0.6B"
MODEL_PATH_EMBEDDING_RETRIEVAL="/mnt/d/agent-models/st/LaBSE"
```

`launcher.sh` по умолчанию вызывает model downloader перед стартом runtime. Для полного набора с VLM:

```bash
./scripts/launcher.sh --target native --asset-set all --models-root /mnt/d/agent-models
```

`--models-root` применяет derived `MODEL_PATH_*` к текущему runtime-запуску. Для постоянной конфигурации зафиксируйте эти же absolute paths в `backend/.env.native`.

Чтобы временно отключить этот шаг:

```bash
./scripts/launcher.sh --target native --skip-model-download
```

## Inventory

| Script | Classification | Mode | Когда использовать | Риски / side effects | Текущий статус |
| --- | --- | --- | --- | --- | --- |
| `scripts/launcher.sh` | `canonical` | `native`, `container` | Основной запуск runtime с bootstrap/preflight слоем | Пишет `backend/.env.runtime`, запускает bootstrap checks, затем делегирует в target runner | Canonical entrypoint |
| `scripts/run_native.sh` | `compatibility` | `native` | Для обратной совместимости или прямого native запуска | При прямом вызове уходит в `launcher.sh`; при запуске из launcher создаёт tmux-сессию, поднимает host-сервисы, вызывает `stop_native.sh` | Compatibility alias + native runner behind launcher |
| `scripts/run_all.sh` | `compatibility` | `container-compose` | Для обратной совместимости container path | При прямом вызове уходит в `launcher.sh`; при запуске из launcher вызывает `stop_all.sh`, поднимает compose stack и tmux monitoring | Compatibility alias + container runner behind launcher |
| `scripts/run_container.sh` | `compatibility` | `container` | Тонкий alias для старых вызовов container path | Немедленно делегирует в `launcher.sh --target container` | Thin compatibility alias |
| `scripts/bootstrap_env.sh` | `internal` | `check`, `install` | В основном используется через `launcher.sh`; вручную полезен только для диагностики bootstrap слоя | Валидирует команды и secrets; `--install` делегирует в `scripts/install/install.sh` | Internal helper for launcher/bootstrap |
| `scripts/stop_native.sh` | `canonical` | `native stop` | Каноническая остановка native tmux/runtime path | Убивает native tmux session и процессы на service ports; Docker не трогает | Canonical native stop script |
| `scripts/stop_all.sh` | `canonical` | `container stop`, `global cleanup` | Каноническая остановка compose/container path | Делает `docker compose down`, завершает обе tmux session и зачищает runtime-процессы на service ports | Canonical container/global stop script |
| `scripts/run_openwebui.sh` | `legacy` | `open-webui` | Только если нужен старый Open WebUI path | Поднимает legacy docker/webui flow и mixed tmux runtime; не синхронизирован с current Chainlit-first docs | Legacy path, не рекомендован как основной |
| `scripts/install/install.sh` | `canonical-installer` | installer | Guided install path за `launcher.sh --install` | Выбирает platform wrapper (`ubuntu`, `ubuntu-server`, `wsl`, `windows`); heavy Linux install всё ещё сводится к legacy `setup_ubuntu.sh` через wrapper layer | Canonical install coordinator |
| `scripts/install/install_ubuntu.sh`, `install_ubuntu_server.sh`, `install_wsl.sh`, `install_windows.ps1` | `platform-wrapper` | installer-platform | Platform-specific install handoff за unified `install.sh` | Linux wrappers пока делегируют в legacy `setup_ubuntu.sh`; Windows path остаётся guided/manual | Platform-specific wrapper layer |
| `scripts/install/*.sh` (кроме `install.sh`) | `planned` | installer-step | Не использовать как самостоятельный install path | Step scripts intentionally scaffold-only | Scaffold-only contracts |
| `scripts/models/install_models.sh` | `canonical-model-provisioning` | model installer | Проверка и дозагрузка обязательных моделей в канонические `MODEL_PATH_*` | Может скачать большие файлы из Hugging Face; launcher по умолчанию проверяет `core` set | Canonical model provisioning |
| `scripts/utils/system_check.sh` | `planned` | installer-support | Внутренние helper checks для installer path | Пока scaffold-only | Scaffold-only contract |

## Launcher Decision

### Зачем проекту нужен launcher

`launcher.sh` даёт один user-facing вход в runtime path и убирает двусмысленность между native и container сценариями. Он централизует:

- bootstrap checks через `bootstrap_env.sh`
- preflight/profile application через `runtime_preflight.py`
- выбор target `native|container`
- генерацию `backend/.env.runtime` перед запуском runtime

### Чем launcher отличается от `run_native.sh` и `run_all.sh`

- `launcher.sh` решает orchestration-уровень: проверить prerequisites, применить runtime profile и выбрать target.
- `run_native.sh` отвечает только за фактический native запуск после того, как launcher уже определил runtime plan.
- `run_all.sh` отвечает только за фактический compose/container запуск после того, как launcher уже определил runtime plan.

Иными словами, `launcher.sh` это user-facing control plane, а `run_native.sh` и `run_all.sh` это target-specific execution runners, оставленные также как compatibility aliases для старых команд.

### Является ли launcher canonical entrypoint

Да. Для документации и новых сценариев запуска canonical entrypoint'ом считается именно `launcher.sh`.

Канонические команды:

```bash
./scripts/launcher.sh --target native --profile adaptive
./scripts/launcher.sh --target container --profile default
```

### Как ограничена роль остальных launcher/runtime scripts

- `run_native.sh`, `run_all.sh`, `run_container.sh` не должны описываться как отдельные конкурирующие entrypoints.
- Их роль: compatibility aliases и target-specific runners за `launcher.sh`.
- `run_openwebui.sh` не должен фигурировать как main path; это legacy script для старого UI contour.

## Script Notes

### `scripts/launcher.sh`

- Назначение: canonical entrypoint для запуска `native` или `container` path через единый bootstrap/preflight contour.
- Кто использует: разработчики и операторы, которым нужен рекомендованный способ старта.
- Пример:

```bash
./scripts/launcher.sh --target native --profile adaptive
./scripts/launcher.sh --target container --profile default --no-attach
./scripts/launcher.sh --target native --profile adaptive --report-only
./scripts/launcher.sh --install --platform ubuntu
```

- Основные флаги: `--target`, `--profile`, `--no-attach`, `--report-only`, `--install`.
- Важные env vars: `UMS_RUNTIME_PROFILE`, `AGENT_NAVIGATOR_RUNTIME_ENV_FILE`, `AGENT_NAVIGATOR_TEST_MODE`.
- Ограничения: не заменяет legacy heavy installer path; для `--install` ведёт в unified installer coordinator `scripts/install/install.sh`, который затем выбирает platform wrapper.

### `scripts/run_native.sh`

- Назначение: native host runner для Chainlit + backend сервисов; user-facing только как back-compat alias.
- Кто использует: старые локальные сценарии запуска и launcher после preflight.
- Пример:

```bash
./scripts/run_native.sh
./scripts/run_native.sh --no-attach
```

- Основные флаги: `--no-attach`; внутренний `--from-launcher` только для launcher.
- Важные env vars: `CONDA_ENV`, `AGENT_NAVIGATOR_RUNTIME_ENV_FILE`, `AGENT_API_PORT`, `DOC_PORT`, `LEGAL_PORT`, `UMS_PORT`, `CHAINLIT_PORT`, `UPLOADS_DIR`, `CHAINLIT_DB_URL`.
- Side effects: создаёт tmux session `agent-navigator-native`, вызывает `stop_native.sh`, поднимает host-side `document_server`, `legal_server`, `UMS`, `agent_api`, `Chainlit`.
- Ограничения: напрямую не должен позиционироваться как canonical путь; runtime profile применяет не он, а launcher/preflight слой.

### `scripts/run_all.sh`

- Назначение: container/compose runner для Chainlit-first stack; user-facing только как back-compat alias.
- Кто использует: старые compose-based сценарии и launcher после preflight.
- Пример:

```bash
./scripts/run_all.sh
./scripts/run_all.sh --no-attach
```

- Основные флаги: `--no-attach`; внутренний `--from-launcher` только для launcher.
- Важные env vars: `BACKEND_MODE`, `VLLM_BASE_URL`, `VLLM_PORT`, `VLLM_MODEL_ID_QWEN_14B_LLM`, `AGENT_NAVIGATOR_RUNTIME_ENV_FILE`, `AGENT_NAVIGATOR_TEST_MODE`.
- Side effects: вызывает `stop_all.sh`, поднимает Docker Compose backend/Chainlit stack, при `BACKEND_MODE=vllm` добавляет профиль `vllm`, создаёт tmux session `agent-navigator`.
- Ограничения: не описывать как независимый canonical orchestration path; canonical выбор между native/container делает `launcher.sh`.

### `scripts/run_container.sh`

- Назначение: минимальный совместимый alias для старых container-вызовов.
- Кто использует: старые команды/документы, где фигурировал отдельный container script.
- Пример:

```bash
./scripts/run_container.sh
./scripts/run_container.sh --no-attach
```

- Основные флаги: любые аргументы просто проксируются в `launcher.sh`.
- Важные env vars: те же, что и у `launcher.sh`.
- Side effects: собственных side effects почти нет, кроме немедленной делегации в launcher.
- Ограничения: сам по себе не содержит orchestration logic.

### `scripts/bootstrap_env.sh`

- Назначение: bootstrap guard для checks/install перед запуском runtime.
- Кто использует: прежде всего `launcher.sh`; вручную только для диагностики.
- Пример:

```bash
./scripts/bootstrap_env.sh --check --target=native
./scripts/bootstrap_env.sh --check --target=container
./scripts/bootstrap_env.sh --install --target=native --platform=auto
```

- Основные флаги: `--check`, `--install`, `--target=native|container`, `--platform=auto|windows|wsl|ubuntu|ubuntu-server`.
- Важные env vars: `AGENT_NAVIGATOR_BACKEND_ENV_FILE`, `AGENT_NAVIGATOR_SKIP_COMMAND_CHECKS`, `AGENT_NAVIGATOR_ALLOW_INSECURE_DEFAULTS`, `AGENT_NAVIGATOR_TEST_MODE`.
- Side effects: в `check` режиме валидирует команды и secrets; в `install` режиме делегирует в `scripts/install/install.sh`.
- Ограничения: не является полным installer framework; это guard/bootstrap wrapper вокруг installer coordinator.

### `scripts/install/install.sh`

- Назначение: единый installer coordinator за `./scripts/launcher.sh --install`.
- Кто использует: guided install path для уже клонированного репозитория на Linux/WSL.
- Пример:

```bash
./scripts/install/install.sh --help
./scripts/install/install.sh --platform auto --dry-run
./scripts/install/install.sh --platform ubuntu
./scripts/install/install.sh --platform ubuntu-server
./scripts/install/install.sh --platform wsl
./scripts/launcher.sh --install --platform ubuntu
```

- Основные флаги: `--target`, `--platform`, `--dry-run`, `--help`.
- Важные env vars: `AGENT_NAVIGATOR_TEST_MODE`, `WSL_DISTRO_NAME`.
- Side effects: выбирает platform wrapper; Linux wrappers по-прежнему сводят heavy install к `scripts/setup_ubuntu.sh`, а Windows path печатает host-side guidance / bootstrap command.
- Ограничения: модульные installer-step scripts пока scaffold-only; `install.sh` остаётся coordinator-wrapper, а `scripts/models/install_models.sh` отвечает только за model provisioning, а не за весь installer engine.

### `scripts/install/install_windows.ps1`

- Назначение: canonical Windows host bootstrap для WSL-based dev path.
- Кто использует: пользователь на Windows, до входа в WSL.
- Пример:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1 -Mode Guide
powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1 -Mode Check
```

- Основные флаги: `-Mode Guide|Check|Help`.
- Side effects: в guide/check mode только печатает следующий шаг и проверяет наличие `wsl`/`winget`/`docker`/`git`.
- Ограничения: не запускает backend/runtime на Windows напрямую; рабочий dev path остаётся WSL/Linux.

### `scripts/stop_native.sh`

- Назначение: каноническая остановка native runtime path.
- Кто использует: любой native-оператор и `run_native.sh` перед clean restart.
- Пример:

```bash
./scripts/stop_native.sh
```

- Основные флаги: нет.
- Важные env vars: читает `backend/.env`, `backend/.env.native`, `backend/.env.runtime` для определения service ports.
- Side effects: убивает tmux session `agent-navigator-native`, завершает связанные runtime-процессы и процессы на service ports.
- Ограничения: Docker intentionally не останавливает.

### `scripts/stop_all.sh`

- Назначение: каноническая остановка compose/container path и общий cleanup runtime-процессов.
- Кто использует: любой container/compose-оператор и `run_all.sh` перед clean restart.
- Пример:

```bash
./scripts/stop_all.sh
```

- Основные флаги: нет.
- Важные env vars: читает `backend/.env`, `backend/.env.native`, `backend/.env.runtime` для определения service ports.
- Side effects: завершает tmux session `agent-navigator` и `agent-navigator-native`, делает `docker compose down`, затем зачищает зависшие процессы.
- Ограничения: это более широкий cleanup, чем `stop_native.sh`; для native-only path лучше использовать именно `stop_native.sh`.

### `scripts/run_openwebui.sh`

- Назначение: legacy script для старого Open WebUI-first contour.
- Кто использует: только при необходимости временно поднять legacy UI path.
- Пример:

```bash
./scripts/run_openwebui.sh
```

- Основные флаги: отсутствуют.
- Важные env vars: `CONDA_ENV`, `AGENT_API_PORT`, `UMS_PORT` и базовые runtime переменные из `backend/.env`.
- Side effects: поднимает mixed tmux + Docker flow, вызывает `stop_all.sh`, использует старую модель `webui` окна и печатает Open WebUI как основной UI.
- Ограничения: не соответствует Chainlit-first документации и не должен использоваться как canonical entrypoint.

## Installer Status

В текущем состоянии репозитория installer path существует частично:

- `scripts/install/install.sh` уже работает как installer coordinator;
- `scripts/install/install_ubuntu.sh`, `install_ubuntu_server.sh`, `install_wsl.sh`, `install_windows.ps1` существуют как platform wrappers;
- `scripts/install/*.sh` step scripts и `scripts/utils/system_check.sh` пока scaffold-only;
- `scripts/models/install_models.sh` уже рабочий coordinator для model provisioning;
- heavy Linux install path всё ещё проходит через `scripts/setup_ubuntu.sh`, а `launcher.sh --install` ведёт в `scripts/install/install.sh -> platform wrapper`.

Поддерживаемые platform-specific paths сейчас такие:

- Windows host -> `powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1 -Mode Guide`
- WSL Ubuntu -> `scripts/install/install.sh --platform=wsl`
- Ubuntu Desktop -> `scripts/install/install.sh --platform=ubuntu`
- Ubuntu Server -> `scripts/install/install.sh --platform=ubuntu-server`

После текущего scaffold-slice responsibilities и safe contracts вынесены в [docs/scripts/installers.md](installers.md).
