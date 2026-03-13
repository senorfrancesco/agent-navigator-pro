# T4.10 vLLM Production Compose Profile Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Добавить production-ready docker-compose profile для самостоятельного `vLLM` deployment и связать его с уже реализованным `UMS` vLLM adapter без изменения orchestration core.

**Architecture:** `UMS` остаётся control-plane и OpenAI-compatible inference gateway. Новый compose/profile добавляет отдельный `vLLM` service как upstream runtime для `BACKEND_MODE=vllm`, а launcher/docs/env surface описывают только rollout/apply/start/health flow. Embeddings, `gguf-vl`, RAG и backend routing не меняются.

**Tech Stack:** Docker Compose, vLLM OpenAI-compatible server, FastAPI/UMS, Bash launcher scripts, pytest/config smoke checks.

---

### Task 1: Зафиксировать compose/profile surface

**Files:**
- Modify: `docker-compose.yaml`
- Test: `docker compose --profile vllm config`

**Step 1: Write the failing expectation**

Нужен отдельный профиль `vllm` с сервисом, который:
- не ломает существующие `chainlit`, `legacy`, `monitoring`
- не запускается по умолчанию
- получает env через `backend/.env`
- публикует healthcheck/порт для upstream `UMS`

**Step 2: Run config check to verify current state is missing**

Run: `docker compose --profile vllm config`
Expected: profile/service для `vllm` ещё отсутствует или неканоничен.

**Step 3: Implement minimal compose service**

Добавить `vllm` service c profile `vllm`, volume mount под модель, env surface (`VLLM_BASE_URL`, `VLLM_MODEL_ID_QWEN_14B_LLM`, optional API key related settings), `healthcheck`, `extra_hosts`, `restart`.

**Step 4: Run compose config check**

Run: `docker compose --profile vllm config`
Expected: config resolves successfully and includes `vllm` service.

**Step 5: Commit**

```bash
git add docker-compose.yaml
git commit -m "feat(ops): add vllm compose profile"
```

### Task 2: Связать launcher/runtime scripts с новым profile

**Files:**
- Modify: `scripts/launcher.sh`
- Modify: `scripts/run_all.sh`
- Modify: `scripts/run_container.sh`
- Modify: `scripts/bootstrap_env.sh` (только если нужен `--target`/health awareness)
- Test: `bash -n scripts/launcher.sh scripts/run_all.sh scripts/run_container.sh scripts/bootstrap_env.sh`

**Step 1: Write the failing expectation**

Launcher должен уметь запускать container path с `vLLM` profile без второго decision engine и без ломки native-first flow.

**Step 2: Add minimal control surface**

Добавить опцию запуска вроде `--target container --backend vllm` или env-driven detection из `.env.runtime`/`.env`, при которой wrapper поднимает `docker compose --profile vllm up -d vllm` до или вместе с основным stack.

**Step 3: Keep current paths intact**

- `native` path не меняется
- `container` без `vllm` продолжает работать как раньше
- `Chainlit`/`monitoring` profiles не ломаются

**Step 4: Validate shell scripts**

Run: `bash -n scripts/launcher.sh scripts/run_all.sh scripts/run_container.sh scripts/bootstrap_env.sh`
Expected: no syntax errors.

**Step 5: Commit**

```bash
git add scripts/launcher.sh scripts/run_all.sh scripts/run_container.sh scripts/bootstrap_env.sh
git commit -m "feat(scripts): wire vllm compose profile into launcher"
```

### Task 3: Добавить tests/smoke checks для vLLM profile

**Files:**
- Create/Modify: `backend/tests/test_runtime_launcher.py`
- Create/Modify: `backend/tests/test_runtime_preflight.py`
- Optionally create: `backend/tests/test_docker_compose_profiles.py` if existing harness doesn’t fit

**Step 1: Write failing tests**

Покрыть минимум:
- launcher не теряет текущий `native/container` behavior
- vLLM-aware path формирует правильный compose invocation/profile assumptions
- preflight/report path не обещает локальный llama-server, если выбран `BACKEND_MODE=vllm`

**Step 2: Run targeted tests to see failure**

Run: `pytest backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py -q -k "vllm or launcher"`
Expected: tests fail until new logic is implemented.

**Step 3: Implement minimal test-backed behavior**

Патчить только harness-level assertions, без integration dependency на живой Docker daemon.

**Step 4: Re-run targeted tests**

Run: `pytest backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py -q -k "vllm or launcher"`
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py
git commit -m "test(ops): cover vllm launcher profile"
```

### Task 4: Документация и operator rollout notes

**Files:**
- Modify: `README.md`
- Modify: `docs/deploy-guide.md`
- Modify: `docs/runtime_profiles.md`
- Modify: `backend/.env.example`
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-13-t49-vllm-adapter.md`
- Create: `docs/plans/2026-03-13-t410-vllm-compose-profile.md`

**Step 1: Document exact env and rollout flow**

Описать:
- какие env обязательны
- как стартовать `vllm` profile
- как проверить `health` и `/v1/models`
- как откатиться на `BACKEND_MODE=llama-server`
- что embeddings/VL не мигрируют в этот шаг

**Step 2: Sync backlog status**

`TASKS.md` должен честно различать закрытый `T4.9` и новый `T4.10` rollout block.

**Step 3: Validate docs and config hygiene**

Run: `git diff --check`
Expected: no whitespace/path issues.

**Step 4: Commit**

```bash
git add README.md docs/deploy-guide.md docs/runtime_profiles.md backend/.env.example TASKS.md docs/plans/2026-03-13-t49-vllm-adapter.md docs/plans/2026-03-13-t410-vllm-compose-profile.md
git commit -m "docs(ops): document vllm compose rollout"
```

### Task 5: Phase verification

**Files:**
- Verify current changed files only

**Step 1: Run targeted verification**

Run:
- `pytest backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py -q -k "vllm or launcher"`
- `python -m py_compile scripts/runtime_preflight.py`
- `bash -n scripts/launcher.sh scripts/run_all.sh scripts/run_container.sh scripts/bootstrap_env.sh`
- `docker compose --profile vllm config`
- `git diff --check`

Expected: all pass.

**Step 2: Run wider regression slice**

Run:
- `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py -q`

Expected: pass, or if existing harness tail reappears, no early failures and residual documented in `TASKS.md`.

**Step 3: Final status sync**

Mark `T4.10` in `TASKS.md` and relevant plan docs.

## Implemented

- `docker-compose.yaml` получил отдельный `vllm` service под profile `vllm`.
- `scripts/run_vllm_service.sh` собирает `vllm serve` из env-переменных без второго decision engine в launcher.
- `run_all.sh` автоматически добавляет `vllm` service для container path при `BACKEND_MODE=vllm` и умеет ждать `/health` / `/v1/models` upstream runtime.
- `runtime_preflight.py` публикует `backend_mode` в runtime plan/report.
- `README.md`, `docs/deploy-guide.md`, `docs/runtime_profiles.md`, `backend/.env.example` синхронизированы под rollout/rollback flow.

## Verification notes

- Узкий `vLLM/launcher` test slice зелёный.
- `docker compose --profile vllm config` валиден.
- Широкий `UMS + launcher` regression slice показывает прохождение тестов без ранних падений, но может упираться в уже известный pytest tail-hang после основной части suite; это documented harness residual.
