# T4.3 LLM Profile Selector Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Довести `model_profile` в Chainlit до product-ready control-plane уровня: пользователь выбирает поддерживаемый профиль модели, backend резолвит его в effective config (`model_id`, generation defaults, context budget hint, device mode hint), а UI показывает итоговую применённую конфигурацию без raw model-id selector.

**Architecture:** Реализация остаётся backend-first. `Chainlit` отправляет только `model_profile`; `ui_control_plane.py` становится source of truth для profile definitions и effective resolution; `chainlit_app.py` только рендерит selector и effective summary. Никаких route/policy решений в UI и никакого raw model-id input.

**Tech Stack:** Chainlit 2.x, Python control-plane resolver, env-backed configuration, pytest.

---

## Status

Implemented on `2026-03-13`.

Что реально вошло:
- canonical user-facing `model_profile` values:
  - `default-chat`
  - `long-context`
  - `legal-compare`
  - `low-vram`
- env-backed mapping `model_profile -> resolved_model_id`
- effective config fields:
  - `device_mode`
  - `context_budget_profile`
  - `profile_generation_defaults`
- Chainlit summary и `Model` tab на canonical profile vocabulary
- compatibility aliases для старых internal values:
  - `coder -> long-context`
  - `agentic -> long-context`
  - `analyst -> legal-compare`

Что сознательно осталось вне фазы:
- per-request runtime hot-switching в `UMS`
- raw model-id selector
- новый orchestration routing

## Scope

Входит в фазу:
- user-facing model profiles:
  - `default-chat`
  - `long-context`
  - `legal-compare`
  - `low-vram`
- env-backed mapping `profile -> model_id`
- effective settings fields для:
  - `resolved_model_id`
  - `device_mode`
  - `context_budget_profile`
  - `profile_generation_defaults`
- обновление Chainlit `Model` tab и effective summary
- compatibility aliases для уже существующих internal values, если они нужны для безболезненной миграции

Не входит:
- dynamic runtime reconfiguration UMS per request
- raw model-id selector
- knowledge-base retrieval implementation
- новый routing/execution logic

## Tasks

### Task 1: Зафиксировать profile contract и compatibility strategy

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`
- Modify: `docs/plans/2026-03-13-t43-llm-profile-selector.md`

**Steps:**
1. Зафиксировать canonical user-facing profiles и effective fields.
2. Решить migration strategy для старых internal profile ids (`coder`, `agentic`, `analyst`) через aliases, а не через мгновенный hard break.

### Task 2: Написать падающие tests на profile resolution

**Files:**
- Modify: `backend/tests/test_ui_control_plane.py`
- Modify: `backend/tests/test_chainlit_runtime_mode.py`

**Steps:**
1. Покрыть `resolve_effective_settings(...)` для:
   - `default-chat`
   - `long-context`
   - `legal-compare`
   - `low-vram`
2. Проверить, что effective config возвращает:
   - `resolved_model_id`
   - `device_mode`
   - `context_budget_profile`
   - profile-specific generation defaults
3. Проверить compatibility aliases:
   - `analyst -> legal-compare`
   - `coder/agentic -> default-chat` или другой выбранный canonical mapping
4. Проверить, что Chainlit `Model` tab показывает canonical profile items, а effective summary выводит device/context hints.

### Task 3: Реализовать backend profile resolver

**Files:**
- Modify: `backend/orchestrator/ui_control_plane.py`
- Modify: `backend/.env.example`
- Modify: `docs/runtime_profiles.md`

**Steps:**
1. Ввести `MODEL_PROFILE_DEFINITIONS` с canonical profiles.
2. Добавить alias-normalization для legacy/internal ids.
3. Вынести env-backed mapping:
   - `CHAINLIT_MODEL_PROFILE_DEFAULT_CHAT_MODEL`
   - `CHAINLIT_MODEL_PROFILE_LONG_CONTEXT_MODEL`
   - `CHAINLIT_MODEL_PROFILE_LEGAL_COMPARE_MODEL`
   - `CHAINLIT_MODEL_PROFILE_LOW_VRAM_MODEL`
4. В effective settings резолвить:
   - `resolved_model_id`
   - `device_mode`
   - `context_budget_profile`
   - `profile_generation_defaults`
5. Обновить assistant-mode presets так, чтобы они ссылались на canonical profiles.

### Task 4: Обновить Chainlit control-plane UX

**Files:**
- Modify: `backend/orchestrator/chainlit_app.py`

**Steps:**
1. Обновить `Model` tab на canonical profile items.
2. Расширить effective summary / context summary:
   - `model_profile`
   - `resolved_model_id`
   - `device_mode`
   - `context_budget_profile`
3. Не добавлять raw model input в UI.

### Task 5: Verification и docs sync

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-13-t43-llm-profile-selector.md`
- Modify: `docs/plans/2026-03-12-execution-review-corrective-plan.md`

**Steps:**
1. Запустить:
   - `pytest backend/tests/test_ui_control_plane.py backend/tests/test_chainlit_runtime_mode.py -q`
   - `pytest backend/tests/test_agent_api_orchestrate.py backend/tests/test_execution_runtime.py backend/tests/test_ui_control_plane.py backend/tests/test_chainlit_runtime_mode.py -q`
   - `cd backend && pytest tests/ -q -m "not integration"`
2. Проверить:
   - `python -m py_compile backend/orchestrator/ui_control_plane.py backend/orchestrator/chainlit_app.py`
   - `git diff --check`
3. Отметить `T4.3` выполненным и, если потребуется, записать follow-up по dynamic runtime switching отдельно.

## Verification

Выполнено:
- `pytest backend/tests/test_ui_control_plane.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py backend/tests/test_execution_runtime.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/orchestrator/ui_control_plane.py backend/orchestrator/chainlit_app.py backend/orchestrator/agent_api.py backend/tests/test_ui_control_plane.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py`
- `git diff --check`
