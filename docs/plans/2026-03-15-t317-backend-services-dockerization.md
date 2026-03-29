# T3.17 Backend Services Dockerization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Добавить рабочий container-path для `agent_api`, `document_server`, `legal_server` и `UMS`, сохранив `native` как канонический dev path.

**Architecture:** Вместо полного docker-rewrite вводится один общий backend image и отдельные compose services с разными `command`/`working_dir`. `chainlit` получает container-network endpoints только в compose backend profile, а launcher продолжает различать `native` и `container` targets.

**Tech Stack:** Docker Compose, Python 3.11, FastAPI, uvicorn, existing launcher/runtime-preflight scripts.

---

### Task 1: Зафиксировать текущий compose/runtime baseline

**Files:**
- Modify: `TASKS.md`
- Test: `docker-compose.yaml`

**Step 1: Подтвердить текущий разрыв**

Проверить, что сейчас `docker-compose.yaml` поднимает только `chainlit`, `open-webui`, monitoring и optional `vllm`, а backend services живут только в host/native scripts.

**Step 2: Зафиксировать scope в backlog**

В `TASKS.md` описать, что `T3.17` закрывает только containerization backend services для локального/prod-like compose path и не включает orchestration rewrite или Kubernetes.

**Step 3: Проверка**

Run: `docker compose config`
Expected: текущая конфигурация валидна до изменений.

### Task 2: Добавить общий backend Docker image

**Files:**
- Create: `Dockerfile.backend`
- Modify: `backend/requirements.txt` только если обнаружится missing runtime dependency
- Test: `docker-compose.yaml`

**Step 1: Написать минимальный image**

Сделать `Dockerfile.backend`, который:
- использует Python 3.11 slim;
- ставит системные зависимости, реально нужные `document_server`/`legal_server`/`UMS`;
- копирует `backend/`;
- ставит `requirements.txt`;
- выставляет `PYTHONPATH=/app/backend`.

**Step 2: Не тащить лишнее**

Не включать модели и пользовательские uploads внутрь образа; они должны приходить volume mounts или host paths через compose.

**Step 3: Проверка**

Run: `docker compose build agent-api document-server legal-server ums`
Expected: image backend services собирается без изменения runtime semantics.

### Task 3: Добавить compose services для backend

**Files:**
- Modify: `docker-compose.yaml`
- Test: `docker-compose.yaml`

**Step 1: Добавить services**

Добавить:
- `agent-api`
- `document-server`
- `legal-server`
- `ums`

Все четыре используют `Dockerfile.backend`, но с разными:
- `working_dir`
- `command`
- `ports`
- `healthcheck`

**Step 2: Сеть и env**

Для container-path:
- `chainlit` должен ходить в `ums`, `document-server`, `legal-server`, `agent-api` по service names;
- host-only `host.docker.internal` сохранить для legacy/native mode, не ломая текущий path.

**Step 3: Shared volumes**

Смонтировать:
- `./backend/open_webui_uploads`
- `./backend/models`
- при необходимости `backend/.env`

Не делать отдельный storage redesign в этой фазе.

**Step 4: Проверка**

Run: `docker compose config`
Expected: compose валиден, service graph согласован.

### Task 4: Связать launcher с compose backend profile

**Files:**
- Modify: `scripts/launcher.sh`
- Modify: `scripts/run_all.sh`
- Possibly modify: `scripts/run_container.sh`
- Test: `backend/tests/test_runtime_launcher.py`

**Step 1: Уточнить meaning container target**

`--target container` должен означать запуск compose path с backend services в контейнерах, а не только `chainlit`.

**Step 2: Реализовать compose profile wiring**

`run_all.sh` должен:
- поднимать backend compose services + `chainlit`;
- не стартовать host tmux windows для backend services в container mode;
- сохранять existing native path без изменений.

**Step 3: Проверка**

Run: `pytest backend/tests/test_runtime_launcher.py -q`
Expected: launcher tests проходят с обновлённой semantics.

### Task 5: Настроить healthchecks и smoke path для backend containers

**Files:**
- Modify: `docker-compose.yaml`
- Test: `backend/tests/test_agent_api_orchestrate.py` только если потребуется

**Step 1: Healthchecks**

Добавить:
- `agent-api -> /health` или текущий живой endpoint
- `document-server -> /health`
- `legal-server -> /health`
- `ums -> /health`/`/status` в зависимости от доступного safe endpoint

**Step 2: depends_on**

Использовать `depends_on.condition: service_healthy` там, где это реально помогает, но не переусложнять.

**Step 3: Проверка**

Run: `docker compose up -d agent-api document-server legal-server ums chainlit`
Run: `docker compose ps`
Expected: сервисы healthy или хотя бы stable running с корректными портами.

### Task 6: Обновить документацию и backlog

**Files:**
- Modify: `README.md`
- Modify: `docs/deploy-guide.md`
- Modify: `TASKS.md`

**Step 1: Документация**

Описать:
- difference `native` vs `container`
- как запустить полный backend stack через compose
- какие volumes/env обязательны

**Step 2: Зафиксировать компромиссы**

В `TASKS.md` записать:
- что `T3.17` закрывает;
- что остаётся на `T3.18`, `T3.20`, `T3.21`;
- если models/large GGUF на host mounts остаются обязательными, явно указать это как ограничение текущей фазы.

### Task 7: Финальная verification

**Files:**
- Verify: `docker-compose.yaml`
- Verify: `scripts/launcher.sh`
- Verify: `scripts/run_all.sh`
- Verify: `scripts/run_container.sh`

**Step 1: Syntax / config checks**

Run:
- `bash -n scripts/launcher.sh scripts/run_all.sh scripts/run_container.sh`
- `docker compose config`

Expected: clean.

**Step 2: Targeted tests**

Run:
- `pytest backend/tests/test_runtime_launcher.py -q`

**Step 3: Compose smoke**

Run:
- `docker compose up -d agent-api document-server legal-server ums chainlit`
- `docker compose ps`
- `docker compose logs --tail=100 chainlit agent-api document-server legal-server ums`

Expected:
- container services start;
- `chainlit` points to container backend endpoints in this mode;
- no regression for native scripts.

## Notes

- `native` остаётся каноническим dev path.
- Эта фаза не заменяет `T3.18` model delivery strategy: большие GGUF и embeddings продолжают приходить через host-mounted `backend/models`.
- Эта фаза не закрывает HTTPS/reverse proxy и full docker observability; это остаётся на `T3.20` и `T3.21`.
