# Offline Bundle v1.0 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Собрать новый канонический offline deployment slice в `deploy/offline_bundle/` для ветки `release/v1.0`, который можно перенести на сервер без интернета, загрузить Docker-образы, восстановить состояние системы и запустить стек с понятной host-preflight проверкой.

**Architecture:** Вместо доработки текущего hybrid runtime создаётся отдельный `docker-first` bundle рядом с существующим кодом. Bundle содержит собственные Dockerfile, compose-файл, export/import/deploy-скрипты, manifest версии, wheelhouse, сохранённые state/data и фиксированный набор контейнерных образов. Host prerequisites (`docker`, `docker compose`, `nvidia-smi`, NVIDIA Container Toolkit) не устанавливаются оффлайн автоматически: bundle только проверяет их наличие и останавливает деплой с явным отчётом, если хост не подготовлен.

**Tech Stack:** Docker Compose, Docker image tar archives, Python 3.11, FastAPI, Chainlit, SQLite, shell scripts, existing backend/orchestrator services.

---

## Scope Guard

- В рамках этой задачи код и новые артефакты изменяются только внутри `deploy/offline_bundle/`.
- Правки вне `deploy/offline_bundle/` запрещены по умолчанию.
- Исключение допускается только при крайней необходимости, когда без этого bundle не может быть собран, проверен или запущен.
- Перед любой такой правкой нужно отдельно предупредить пользователя и объяснить, почему её нельзя локализовать внутри `deploy/offline_bundle/`.
- Документационные ссылки на новый bundle можно готовить внутри `deploy/offline_bundle/docs/`; корневые `README.md`, `AGENTS.md`, `TASKS.md` и существующий runtime-код не менять в рамках исполнения плана без отдельного подтверждения.

---

### Task 1: Зафиксировать release-контракт `v1.0`

**Files:**
- Create: `deploy/offline_bundle/docs/RELEASE_CONTRACT.md`
- Create: `deploy/offline_bundle/manifest.template.json`
- Test: `rg -n "release/v1.0|bundle_version|offline_bundle" deploy/offline_bundle`

**Step 1: Обновить каноническую ветку**

Внутри `deploy/offline_bundle/` зафиксировать, что bundle относится к release-линии `release/v1.0`, без изменения корневых operator docs в этой задаче.

**Step 2: Зафиксировать новый deployment slice**

В документах bundle зафиксировать, что `deploy/offline_bundle/` является новым release/runtime slice для offline/server deployment, а старый compose/runtime рассматривается как legacy baseline и источник runtime-контракта.

**Step 3: Проверка**

Run: `rg -n "release/v1.0|offline_bundle|legacy baseline" deploy/offline_bundle`
Expected: release contract описан локально внутри bundle.

### Task 2: Собрать инвентаризацию обязательных runtime-артефактов

**Files:**
- Create: `deploy/offline_bundle/manifest.template.json`
- Create: `deploy/offline_bundle/docs/RUNTIME_CONTENTS.md`
- Test: `backend/orchestrator/state_store.py`

**Step 1: Выделить state/data surface**

Описать как обязательные для bundle:
- `backend/.env`
- `backend/.env.runtime`
- `backend/.env.hardware.override` при наличии
- `backend/open_webui_uploads/`
- `backend/.data/chainlit.db`
- `backend/.data/orchestrator_state.db`
- `backend/.data/orchestrator_kb.db`
- `backend/.data/ums_dynamic_models.json`
- `backend/models/` или внешний models-root, если используется canonical absolute path

**Step 2: Выделить runtime code/config surface**

Описать как обязательные:
- backend source code
- `backend/config/models.yaml`
- `Dockerfile.*.offline`
- `compose.offline.yaml`
- deploy scripts
- monitoring assets, если входят в `v1.0`

**Step 3: Проверка**

Run: `pytest backend/tests/test_state_store.py backend/tests/test_knowledge_base_store.py backend/tests/test_chainlit_persistence_schema.py -q`
Expected: persistence surface подтверждён тестами и соответствует инвентаризации.

### Task 3: Спроектировать каталог `deploy/offline_bundle/`

**Files:**
- Create: `deploy/offline_bundle/README.md`
- Create: `deploy/offline_bundle/env.bundle.example`
- Create: `deploy/offline_bundle/.gitignore`
- Test: `find deploy/offline_bundle -maxdepth 2 -type f | sort`

**Step 1: Создать базовую структуру**

Создать:
- `deploy/offline_bundle/compose.offline.yaml`
- `deploy/offline_bundle/Dockerfile.backend.offline`
- `deploy/offline_bundle/Dockerfile.chainlit.offline`
- `deploy/offline_bundle/scripts/`
- `deploy/offline_bundle/images/`
- `deploy/offline_bundle/wheelhouse/`
- `deploy/offline_bundle/state/`
- `deploy/offline_bundle/docs/`

**Step 2: Ввести versioned manifest**

`manifest.template.json` должен содержать:
- `bundle_version: "v1.0"`
- `release_branch: "release/v1.0"`
- список image tar archives
- список state/data paths
- checksum fields
- build timestamp

**Step 3: Проверка**

Run: `find deploy/offline_bundle -maxdepth 3 | sort`
Expected: структура каталога существует и читается как самостоятельный deployment slice.

### Task 4: Собрать новый backend Docker image для offline runtime

**Files:**
- Create: `deploy/offline_bundle/Dockerfile.backend.offline`
- Create: `deploy/offline_bundle/requirements.backend.lock.txt`
- Test: `backend/requirements.txt`

**Step 1: Отвязать от старого Dockerfile**

Новый Dockerfile не должен зависеть от `Dockerfile.backend` как канонического production path. Он должен:
- использовать зафиксированный base image tag;
- ставить только нужные системные пакеты;
- ставить Python deps из заранее подготовленного wheelhouse или lock-файла;
- копировать backend code в predictable path.

**Step 2: Учесть persistence paths**

В image нужно предусмотреть каталоги для:
- `/app/backend/.data`
- `/app/backend/open_webui_uploads`
- `/app/backend/models`

Но сами данные должны приходить bind mounts/volumes, а не baked-in.

**Step 3: Проверка**

Run: `docker build -f deploy/offline_bundle/Dockerfile.backend.offline -t agent-nav-backend-offline:test .`
Expected: backend offline image собирается отдельно от legacy Dockerfile.

### Task 5: Собрать новый Chainlit Docker image для offline runtime

**Files:**
- Create: `deploy/offline_bundle/Dockerfile.chainlit.offline`
- Create: `deploy/offline_bundle/requirements.chainlit.lock.txt`
- Test: `requirements.chainlit.txt`

**Step 1: Изолировать UI runtime**

Новый Chainlit image должен:
- использовать зафиксированный base image tag;
- ставить зависимости оффлайн через wheelhouse или lock requirements;
- не использовать `host.docker.internal`;
- говорить только с docker-network service names.

**Step 2: Подготовить state path**

UI image должен ожидать persistent mount для chainlit SQLite data, а не anonymous compose volume без export contract.

**Step 3: Проверка**

Run: `docker build -f deploy/offline_bundle/Dockerfile.chainlit.offline -t agent-nav-chainlit-offline:test .`
Expected: UI image собирается и не зависит от старого compose path.

### Task 6: Собрать новый `compose.offline.yaml`

**Files:**
- Create: `deploy/offline_bundle/compose.offline.yaml`
- Test: `deploy/offline_bundle/compose.offline.yaml`

**Step 1: Описать канонический сервисный граф**

Добавить сервисы:
- `agent-api`
- `document-server`
- `legal-server`
- `ums`
- `chainlit`
- optional `vllm`
- optional `prometheus`
- optional `grafana`

Все backend/UI сервисы общаются через внутреннюю docker network.

**Step 2: Убрать hybrid host routing**

Не использовать:
- `host.docker.internal`
- host-native conda runtime
- implicit host paths вне bundle root

**Step 3: Описать persistence mounts**

Явно смонтировать:
- `./state/backend-data:/app/backend/.data`
- `./state/uploads:/app/backend/open_webui_uploads`
- `./models:/app/backend/models`
- `./state/chainlit-data:/app/orchestrator/.data` или согласованный UI path

**Step 4: Проверка**

Run: `docker compose -f deploy/offline_bundle/compose.offline.yaml config`
Expected: compose валиден и не содержит `host.docker.internal`.

### Task 7: Добавить multi-agent runtime как явный offline capability

**Files:**
- Create: `deploy/offline_bundle/docs/MULTI_AGENT_RUNTIME.md`
- Create: `deploy/offline_bundle/env.bundle.example`
- Test: `backend/tests/test_orchestration_runtime.py`

**Step 1: Зафиксировать operational contract**

Определить, что в `v1.0` multi-agent mode означает:
- orchestrated multi-step decomposition внутри локального runtime;
- отсутствие внешних SaaS dependencies;
- ограничение параллелизма по hardware policy;
- shared state/cancellation/timeout contract.

**Step 2: Ввести env/config knobs**

Сначала зафиксировать проектируемые настройки bundle-документацией и env-template внутри `deploy/offline_bundle/`, например:
- `MULTI_AGENT_ENABLED`
- `MULTI_AGENT_MAX_WORKERS`
- `MULTI_AGENT_EXECUTION_MODE=sequential|parallel`
- `MULTI_AGENT_BUDGET_TOKENS` или честный runtime budget аналог

**Step 3: Подготовить offline-safe сценарии**

Включить multi-agent только там, где:
- он реально ускоряет сложный анализ;
- не ломает determinism там, где нужен строгий legal/report path;
- его можно диагностировать и отключить операторски.

**Step 4: Проверка**

Run: `rg -n "MULTI_AGENT_" deploy/offline_bundle`
Expected: multi-agent contract и operator knobs зафиксированы локально внутри bundle.

### Task 8: Реализовать export pipeline на dev-машине

**Files:**
- Create: `deploy/offline_bundle/scripts/build_bundle.sh`
- Create: `deploy/offline_bundle/scripts/export_images.sh`
- Create: `deploy/offline_bundle/scripts/export_state.sh`
- Create: `deploy/offline_bundle/scripts/build_wheelhouse.sh`
- Create: `deploy/offline_bundle/scripts/generate_manifest.py`
- Test: `deploy/offline_bundle/scripts/*.sh`

**Step 1: Export image archives**

Скрипт должен:
- build или tag нужные images;
- `docker save` их в `deploy/offline_bundle/images/*.tar(.gz)`;
- использовать фиксированные имена и версии.

**Step 2: Export wheelhouse**

Собрать wheelhouse для host-side Python tooling, если в bundle остаются Python CLI/utilities.

**Step 3: Export state/data**

Скопировать в `deploy/offline_bundle/state/`:
- SQLite базы
- uploads
- dynamic model registry
- env files

**Step 4: Сгенерировать manifest**

Manifest должен содержать размеры, checksums и обязательные host prerequisites.

**Step 5: Проверка**

Run: `bash -n deploy/offline_bundle/scripts/build_bundle.sh deploy/offline_bundle/scripts/export_images.sh deploy/offline_bundle/scripts/export_state.sh deploy/offline_bundle/scripts/build_wheelhouse.sh`
Expected: export scripts валидны синтаксически.

### Task 9: Реализовать import/deploy pipeline на оффлайн-сервере

**Files:**
- Create: `deploy/offline_bundle/scripts/check_host.sh`
- Create: `deploy/offline_bundle/scripts/load_images.sh`
- Create: `deploy/offline_bundle/scripts/restore_state.sh`
- Create: `deploy/offline_bundle/scripts/deploy.sh`
- Create: `deploy/offline_bundle/docs/DEPLOY_OFFLINE.md`
- Test: `deploy/offline_bundle/scripts/check_host.sh`

**Step 1: Host preflight**

`check_host.sh` должен проверять:
- `docker`
- `docker compose`
- `nvidia-smi` при GPU path
- наличие NVIDIA runtime в `docker info`
- свободное место на диске

Если чего-то нет, скрипт пишет конкретно, что не установлено и что bundle это не исправляет.

**Step 2: Image import**

`load_images.sh` должен грузить все image tar archives из `images/`.

**Step 3: State restore**

`restore_state.sh` должен раскладывать state/data в каталоги, ожидаемые compose.

**Step 4: Deploy**

`deploy.sh` выполняет:
1. `check_host.sh`
2. `load_images.sh`
3. `restore_state.sh`
4. `docker compose -f compose.offline.yaml up -d`
5. health-check probes

**Step 5: Проверка**

Run: `bash deploy/offline_bundle/scripts/check_host.sh --help`
Run: `bash deploy/offline_bundle/scripts/deploy.sh --help`
Expected: import/deploy path документирован и запускается predictably.

### Task 10: Добавить smoke и verification для bundle

**Files:**
- Create: `deploy/offline_bundle/tests/test_manifest.py`
- Create: `deploy/offline_bundle/tests/test_bundle_layout.py`
- Create: `deploy/offline_bundle/tests/test_host_checks.py`
- Modify: `backend/tests/test_full_stack_smoke.py`
- Test: `deploy/offline_bundle/tests/*.py`

**Step 1: Проверить layout**

Тесты должны валидировать наличие обязательных путей bundle и корректный manifest.

**Step 2: Проверить host-check semantics**

Тестами подтвердить, что при отсутствии `docker`/`nvidia-smi`/compose скрипт сообщает это явно, а не делает частичный deploy.

**Step 3: Добавить offline compose smoke hook**

Новый smoke harness должен уметь проверять `compose.offline.yaml`, а не только legacy root compose.

**Step 4: Проверка**

Run: `pytest deploy/offline_bundle/tests -q`
Expected: bundle layout и preflight semantics покрыты тестами.

### Task 11: Обновить операторскую документацию

**Files:**
- Create: `deploy/offline_bundle/docs/DEPLOYMENT_CONTRACT.md`
- Create: `deploy/offline_bundle/docs/MIGRATION_NOTES.md`
- Test: `rg -n "offline_bundle|release/v1.0|host.docker.internal|legacy" deploy/offline_bundle/docs`

**Step 1: Описать новый canonical path**

Документация должна явно говорить:
- `release/v1.0` — каноническая ветка;
- `deploy/offline_bundle/` — канонический offline/server runtime slice;
- старый docker/native path остаётся только как legacy/dev baseline.

**Step 2: Описать multi-agent режим**

В документации bundle зафиксировать:
- когда он включается;
- как ограничивается по ресурсам;
- как отключается оператором.

**Step 3: Проверка**

Run: `rg -n "release/v1.0|offline_bundle|multi-agent" deploy/offline_bundle/docs deploy/offline_bundle`
Expected: новый runtime contract задокументирован последовательно внутри bundle.

### Task 12: Финальная verification

**Files:**
- Verify: `deploy/offline_bundle/`
- Verify: `README.md`
- Verify: `docs/deploy-guide.md`
- Verify: `TASKS.md`

**Step 1: Syntax checks**

Run:
- `bash -n deploy/offline_bundle/scripts/*.sh`
- `docker compose -f deploy/offline_bundle/compose.offline.yaml config`

Expected: clean.

**Step 2: Targeted backend/runtime tests**

Run:
- `pytest backend/tests/test_install_scripts.py backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py -q`
- `pytest backend/tests/test_state_store.py backend/tests/test_knowledge_base_store.py backend/tests/test_chainlit_persistence_schema.py -q`
- `pytest backend/tests/test_orchestration_runtime.py backend/tests/test_execution_runtime.py -q`

Expected: deployment-critical and multi-agent-critical tests pass.

**Step 3: Bundle tests**

Run:
- `pytest deploy/offline_bundle/tests -q`

Expected: bundle manifest/layout/host-check semantics pass.

**Step 4: Smoke run**

Run:
- `bash deploy/offline_bundle/scripts/build_bundle.sh`
- `bash deploy/offline_bundle/scripts/deploy.sh --skip-host-install`

Expected:
- bundle собирается;
- deploy path либо поднимает сервисы, либо честно останавливается на missing host prerequisite с явным отчётом.

## Notes

- Host prerequisites intentionally remain external to the bundle. `v1.0` bundle only verifies and reports missing host setup.
- `deploy/offline_bundle/` is a maintained release artifact, not a temporary export folder.
- Old root-level Docker artifacts are legacy baseline only; they should not define the new production/offline contract.
- Multi-agent support is in scope only as an offline-safe, operator-controlled capability with explicit resource limits.
