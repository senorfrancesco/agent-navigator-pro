# T4.14 Unified Runtime Launcher Implementation Plan

Status: implemented on 2026-03-13.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Собрать единый runtime entrypoint для установки, preflight-адаптации под железо, применения runtime profile и запуска системы в `native` или `container` режиме без появления второго источника истины рядом с `UMS`.

**Architecture:** `T4.13` уже сделал `UMS` source of truth для runtime budget metadata, поэтому `T4.14` не должен дублировать profile logic внутри shell-скриптов. Правильный разрез: Python preflight строит runtime plan и `.env.runtime`, launcher только вызывает preflight, применяет env и запускает `native|container`, а текущие `run_all.sh` / `run_native.sh` становятся thin compatibility wrappers.

**Tech Stack:** Bash, Python 3.11, tmux, Docker Compose, FastAPI/UMS, existing repo scripts

**Outcome:** Goal achieved for the current architecture slice.
- Added `scripts/runtime_preflight.py` with `detect/plan/apply/report`.
- Added canonical `scripts/launcher.sh` entrypoint for `native|container`.
- `run_all.sh`, `run_native.sh`, `run_container.sh` now delegate to launcher and only keep target-specific startup logic.
- Added `docs/runtime_profiles.md` and README guidance for the unified runtime flow.
- Full backend non-integration suite passed after integration.

**Pragmatic compromise kept intentionally:**
- launcher install/bootstrap remains conservative and non-destructive by default; `setup_ubuntu.sh` stays as the heavier host bootstrap path;
- repo-maintenance cleanup of old PR inventory is still a separate follow-up, not part of runtime correctness.

---

### Task 1: Зафиксировать runtime preflight contract и `.env.runtime` format

**Files:**
- Create: `scripts/runtime_preflight.py`
- Create: `docs/runtime_profiles.md`
- Test: `backend/tests/test_runtime_preflight.py`

**Step 1: Write the failing tests**

Покрыть:
- `detect -> plan -> report` pipeline возвращает стабильный JSON payload
- profile selection: `default`, `adaptive`, `manual`
- `.env.runtime` рендерится детерминированно
- `manual` уважает explicit overrides, а `adaptive` использует hardware/tier-derived defaults
- preflight не пишет `.env.runtime`, если вызван в `--report-only`

**Step 2: Run tests to verify they fail**

Run:
- `pytest backend/tests/test_runtime_preflight.py -q`

**Step 3: Write minimal implementation**

Добавить `scripts/runtime_preflight.py` с командами:
- `detect`
- `plan`
- `apply`
- `report`

Выходной JSON contract должен содержать минимум:
- `runtime_profile`
- `device_mode`
- `effective_context_tokens`
- `retrieved_context_tokens_budget`
- `generation_tokens_reserve`
- `rag_mode`
- `embedding_backend`
- `llm_ctx_size`
- `source` (`manual`, `adaptive`, `default`)

`.env.runtime` должен содержать только applied runtime variables, например:
- `UMS_RUNTIME_PROFILE`
- `UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS`
- `UMS_RETRIEVED_CONTEXT_RATIO`
- `UMS_GENERATION_TOKENS_RESERVE`
- `DEVICE_MODE`
- `RAG_MODE_OVERRIDE` только если действительно нужен override

Правила:
- preflight не должен дублировать internal UMS hardware logic 1:1; он only plans runtime env
- `.env.runtime` является applied output, а не second decision engine
- `manual` profile допускает CLI/env overrides
- `adaptive` использует safe defaults и hardware tier hints

**Step 4: Run tests to verify they pass**

Run:
- `pytest backend/tests/test_runtime_preflight.py -q`

### Task 2: Собрать единый launcher entrypoint

**Files:**
- Create: `scripts/launcher.sh`
- Modify: `scripts/run_all.sh`
- Modify: `scripts/run_native.sh`
- Modify: `scripts/run_container.sh`
- Test: `backend/tests/test_runtime_launcher.py`

**Step 1: Write the failing tests**

Покрыть:
- `launcher.sh --target native` вызывает preflight и затем native path
- `launcher.sh --target container` вызывает preflight и затем container path
- `--profile manual|adaptive|default`
- `--install`
- `--report-only`
- thin wrapper `run_all.sh` и `run_native.sh` делегируют в `launcher.sh`, а не держат собственную orchestration-логику

**Step 2: Run tests to verify they fail**

Run:
- `pytest backend/tests/test_runtime_launcher.py -q`

**Step 3: Write minimal implementation**

Добавить `scripts/launcher.sh` как canonical entrypoint:
- `--target native|container`
- `--profile default|adaptive|manual`
- `--install`
- `--report-only`
- `--no-attach`
- optional `--write-env-runtime`

Внутренний порядок:
1. bootstrap/install при запросе
2. `runtime_preflight.py plan|apply`
3. source `backend/.env`, затем `backend/.env.runtime` при наличии
4. launch target
5. health/report summary

Текущие скрипты:
- `run_all.sh` -> compatibility alias к `launcher.sh --target container`
- `run_native.sh` -> compatibility alias к `launcher.sh --target native`
- `run_container.sh` -> фактический target-specific runner, но без собственной profile logic

Важно:
- launcher не должен сам решать `effective_context_tokens`
- launcher only orchestrates detect/apply/start/report

**Step 4: Run tests to verify they pass**

Run:
- `pytest backend/tests/test_runtime_launcher.py -q`

### Task 3: Вынести install/bootstrap в controlled path

**Files:**
- Create: `scripts/bootstrap_env.sh`
- Modify: `scripts/setup_ubuntu.sh`
- Modify: `README.md`
- Test: `backend/tests/test_runtime_launcher.py`

**Step 1: Write the failing tests**

Покрыть:
- `--install` не запускает destructive actions по умолчанию без явного флага/confirm path
- bootstrap script детерминированно проверяет prerequisites:
  - `tmux`
  - `conda`
  - `docker` / `docker compose`
  - Python deps marker
- launcher корректно сообщает missing prerequisites

**Step 2: Run tests to verify they fail**

Run:
- `pytest backend/tests/test_runtime_launcher.py -q -k install`

**Step 3: Write minimal implementation**

Добавить:
- `scripts/bootstrap_env.sh` как non-destructive checker/bootstrap wrapper
- разделение:
  - check prerequisites
  - suggest install commands
  - optional install hooks

`setup_ubuntu.sh` оставить как heavier host bootstrap, но не как canonical runtime entrypoint.

Правило:
- install/bootstrap flow не должен быть неявочным side effect обычного запуска
- `launcher.sh --install` должен явно говорить, что именно будет сделано

**Step 4: Run tests to verify they pass**

Run:
- `pytest backend/tests/test_runtime_launcher.py -q`

### Task 4: Увязать launcher c текущим UMS/runtime contract и health/report

**Files:**
- Modify: `scripts/run_container.sh`
- Modify: `scripts/stop_all.sh`
- Modify: `scripts/stop_native.sh`
- Modify: `README.md`
- Test: `backend/tests/test_runtime_launcher.py`

**Step 1: Write the failing tests**

Покрыть:
- launch summary показывает:
  - target
  - runtime profile
  - effective context budget
  - retrieved context budget
  - selected device mode
- launcher корректно читает `UMS /status` после старта
- stop scripts совместимы с новым session/target naming

**Step 2: Run tests to verify they fail**

Run:
- `pytest backend/tests/test_runtime_launcher.py -q -k report`

**Step 3: Write minimal implementation**

После запуска target launcher должен:
- опрашивать `UMS /status`
- выводить applied runtime summary
- показывать:
  - `runtime_profile`
  - `effective_context_tokens`
  - `retrieved_context_tokens_budget`
  - `rag_mode`
  - `device_mode`

Если `UMS` недоступен:
- launcher должен честно показать degraded status
- не скрывать failure behind green banner

**Step 4: Run tests to verify they pass**

Run:
- `pytest backend/tests/test_runtime_launcher.py -q`

### Task 5: Закрыть T4.14 verification и docs sync

**Files:**
- Modify: `TASKS.md`
- Modify: `README.md`
- Modify: `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`
- Modify: `docs/plans/2026-03-12-execution-review-corrective-plan.md`
- Modify: `docs/plans/2026-03-13-t414-unified-runtime-launcher.md`

**Step 1: Run phase-level verification**

Run:
- `pytest backend/tests/test_runtime_preflight.py backend/tests/test_runtime_launcher.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `bash -n scripts/launcher.sh scripts/run_all.sh scripts/run_native.sh scripts/run_container.sh scripts/bootstrap_env.sh`
- `python -m py_compile scripts/runtime_preflight.py`
- `git diff --check`

**Step 2: Update backlog/docs**

Зафиксировать:
- `T4.14` closed
- canonical runtime path теперь `launcher.sh + runtime_preflight.py`
- `run_all.sh` / `run_native.sh` / `run_container.sh` стали wrappers/target runners, а не конкурирующие orchestration scripts
- `.env.runtime` — applied output only, not decision source

**Step 3: Commit**

```bash
git add scripts/runtime_preflight.py \
        scripts/launcher.sh \
        scripts/bootstrap_env.sh \
        scripts/run_all.sh \
        scripts/run_native.sh \
        scripts/run_container.sh \
        scripts/setup_ubuntu.sh \
        scripts/stop_all.sh \
        scripts/stop_native.sh \
        backend/tests/test_runtime_preflight.py \
        backend/tests/test_runtime_launcher.py \
        README.md \
        TASKS.md \
        docs/runtime_profiles.md \
        docs/plans/2026-03-11-unified-recovery-and-ui-plan.md \
        docs/plans/2026-03-12-execution-review-corrective-plan.md \
        docs/plans/2026-03-13-t414-unified-runtime-launcher.md
git commit -m "feat(runtime): unify launcher and hardware preflight"
```

---

## Design Notes

### Recommended structure

Canonical runtime flow:

```text
launcher.sh
  -> bootstrap_env.sh (optional)
  -> runtime_preflight.py plan/apply/report
  -> source backend/.env + backend/.env.runtime
  -> target runner (native|container)
  -> health/report summary
```

### Explicit non-goals for T4.14

Не делать в этой фазе:
- UI selector runtime profile
- raw model-id selection in UI
- rewrite `UMS` hardware detection
- full installer/platform manager
- moving dev-loop back to Docker-first

### Why this order

Сначала нужно собрать один authoritative launcher/preflight contour, и только потом развивать UX вокруг него. Иначе `T4.2/T4.3` снова начнут работать поверх нескольких конкурирующих runtime truths.
