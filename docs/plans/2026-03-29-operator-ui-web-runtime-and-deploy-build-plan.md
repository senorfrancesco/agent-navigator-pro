# Operator UI: Web Runtime, Stitch Shell, Deploy/Build

## Summary

Развивать operator UI как **отдельный web-first сервис**, а не как desktop-first приложение.

Базовая целевая схема:

- `operator-ui` frontend: отдельный web app на базе текущего prototype
- `operator-runner` backend/control API: host-native слой рядом с `agent_api`, который вызывает канонические scripts и стримит логи
- `Stitch` suite: единый shell с общей навигацией, включая отдельную top-level вкладку `Deploy / Build`

Причина выбора:

- ключевые build/deploy действия уже завязаны на host-level операции (`docker`, `apt`, `systemctl`, unpack tar, install host packages)
- desktop app не убирает эту сложность, а добавляет packaging/signing/update lifecycle
- web UI + host-native runner лучше ложится на текущий `FastAPI` и script-based runtime проекта

Опорные источники:

- Electron distribution lifecycle: https://www.electronjs.org/docs/latest/tutorial/distribution-overview
- Tauri distribute / packaging lifecycle: https://v2.tauri.app/distribute/
- Docker multi-container guidance: https://docs.docker.com/get-started/docker-concepts/running-containers/multi-container-applications/
- 12-factor admin processes: https://12factor.net/admin-processes

## Key Changes

### 1. Stitch shell

Довести runtime-control suite до одного shell-pattern:

- общий left nav для экранов:
  - `Overview`
  - `Launch`
  - `Config`
  - `Services`
  - `Deploy / Build`
  - `Dev Actions`
- единый topbar/header pattern
- одинаковые status pills, source blocks, code-path labels, log panels
- screen `Deploy / Build Control` (`9104511724d04a739cd23d2c5969116c`) встроить как часть общей навигации, а не как отдельный изолированный screen

`Dev Actions` не должен содержать release/build/deploy UX.

### 2. Delivery model

Канонический delivery model:

- **separate web app + host-native runner API**

Не делать как основной путь:

- Electron-first desktop app
- Tauri-first desktop app
- container-only admin UI, который сам пытается делать host `apt` / `systemctl` / runtime install

Допустимое будущее:

- later optional Tauri wrapper поверх того же web UI без изменения UX и API

### 3. Backend control API

Добавить новый operator control-plane API рядом с `backend/orchestrator/agent_api.py`.

Минимальные capability groups:

- `GET /operator/health`
- `GET /operator/runtime/summary`
- `GET /operator/runtime/paths`
- `GET /operator/config/{path}`
- `POST /operator/config/{path}/apply`
- `GET /operator/services/{path}`
- `GET /operator/logs/{surface}`
- `POST /operator/actions/run`
- `POST /operator/deploy/build`
- `POST /operator/deploy/import`
- `POST /operator/deploy/host-install`
- `POST /operator/deploy/run`
- `GET /operator/jobs/{id}`

Execution model:

- только allowlisted commands
- каждый action mapped на canonical script path
- no arbitrary shell execution
- long-running jobs публикуют:
  - `job_id`
  - `stage`
  - `status`
  - `stdout/stderr`
  - `start/end timestamps`
  - `exit_code`

### 4. Real UI over prototype

Использовать `prototype/operator-ui` как IA/visual reference и переводить его в реальный app scaffold.

Вкладки:

- `Overview`
- `Launch`
- `Config`
- `Services`
- `Deploy / Build`
- `Dev Actions`

`Deploy / Build` остаётся dual-mode surface:

- `Build Bundle`
- `Import / Deploy`

#### Build Bundle mode

UI должен отражать canonical flow:

- `build_wheelhouse.sh`
- `export_images.sh`
- `export_models.sh`
- `export_state.sh`
- `build_host_apt_bundle.sh`
- `generate_manifest.py`
- `validate_bundle.py --mode deploy`
- pack `deploy/offline_bundle` в единый `tar.gz`

#### Import / Deploy mode

UI должен отражать inverse flow:

- archive select / intake
- unpack tar.gz
- `validate_bundle.py --mode deploy`
- `check_host.sh`
- `install_host_apt_bundle.sh`
- `load_images.sh`
- `restore_state.sh`
- `deploy.sh`
- optional `run_offline_bundle.sh`
- `verify_runtime.sh`

UI behavior:

- action starts job
- UI polls or streams job status
- lower logs panel updates live
- stage states: `ready | running | partial | failed | blocked | completed`
- artifact cards show file path, size, timestamp
- blocked actions stay visible and disabled with reasons

### 5. Safety and boundaries

- `agent_api` остаётся orchestration/runtime API
- новый operator runner не дублирует LangGraph routing
- scripts остаются source of truth execution layer
- browser never executes shell commands
- privileged steps явно маркируются как host/root-required
- command mapping задаётся statically server-side
- log output должен скрывать очевидные секреты, где это необходимо

## Test Plan

### Stitch / navigation

- `Deploy / Build` встроен в общий shell и навигацию
- все operator screens читаются как части одного приложения
- `Dev Actions` не дублирует deploy/build surface

### Backend control API

- health/summary endpoints отвечают без side effects
- allowlisted actions мапятся на правильные scripts
- long-running jobs корректно публикуют stage/status/logs/exit code
- failed/blocked jobs возвращают operator-readable errors

### UI flows

- все 6 вкладок работают через реальные API adapters
- `Build Bundle` mode показывает реальные stage transitions
- `Import / Deploy` mode показывает archive intake, unpack, validate, host install, deploy flow
- logs panel обновляется без перезагрузки вкладки
- disabled actions и partial states отображаются честно

### Acceptance

- оператор видит runtime state и deploy/build state в одном UI
- оператор может запустить bundle build flow и получить logs/job output
- оператор может принять `tar.gz`, развернуть bundle и пройти deploy flow
- UI работает как отдельный web service и не зависит от Chainlit shell

## Assumptions

- canonical delivery model: **separate web app + host-native runner**
- desktop packaging сейчас вне scope
- основной portable artifact: один `tar.gz` поверх `deploy/offline_bundle`
- `deploy.sh` считается canonical deploy action
- `run_offline_bundle.sh` считается convenience wrapper, а не primary deploy path
- реализация должна максимально переиспользовать существующие scripts, а не переносить их логику во frontend
