# Chainlit Auth Secret Bootstrap Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Автоматически создавать и ротировать `CHAINLIT_AUTH_SECRET` в `backend/.env` через install/bootstrap-скрипты, чтобы Chainlit SQL/JWT path не зависел от ручного ввода секрета.

**Architecture:** Источник правды остаётся только `backend/.env`. `scripts/bootstrap_env.sh` получает helper-логику для создания `backend/.env` из шаблона и ротации `CHAINLIT_AUTH_SECRET`, если он отсутствует или оставлен дефолтным. Runtime-код Chainlit/SQL не меняется; документация и тесты синхронизируются с bootstrap-first поведением.

**Tech Stack:** Bash, pytest, repo docs.

---

### Task 1: Зафиксировать ожидаемое bootstrap-поведение тестами

**Files:**
- Modify: `backend/tests/test_runtime_launcher.py`

**Step 1: Write the failing test**

Добавить тесты на:
- создание `backend/.env` из `backend/.env.example` с заменой `CHAINLIT_AUTH_SECRET`;
- ротацию дефолтного `CHAINLIT_AUTH_SECRET` в существующем `.env` при `bootstrap_env.sh --check`.

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_runtime_launcher.py -q -k "bootstrap_check_creates_env_with_generated_chainlit_auth_secret or bootstrap_check_rotates_default_chainlit_auth_secret"`

Expected: FAIL, потому что текущий bootstrap не генерирует и не ротирует secret.

**Step 3: Write minimal implementation**

Реализовать env/secret helper в `scripts/bootstrap_env.sh`.

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_runtime_launcher.py -q -k "bootstrap_check_creates_env_with_generated_chainlit_auth_secret or bootstrap_check_rotates_default_chainlit_auth_secret"`

Expected: PASS.

### Task 2: Реализовать bootstrap/install secret management

**Files:**
- Modify: `scripts/bootstrap_env.sh`
- Modify: `scripts/setup_ubuntu.sh`

**Step 1: Write the failing test**

Опорой остаются тесты из Task 1; при необходимости добавить проверку install guidance.

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_runtime_launcher.py -q -k bootstrap_check`

Expected: FAIL/partial FAIL до фикса.

**Step 3: Write minimal implementation**

Сделать helpers для:
- копирования `backend/.env.example` в `backend/.env`, если файла нет;
- генерации криптостойкого значения `CHAINLIT_AUTH_SECRET`;
- безопасной замены/добавления переменной в `backend/.env`;
- вызова ensure-логики из bootstrap path;
- обновления install guidance в `setup_ubuntu.sh`.

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_runtime_launcher.py backend/tests/test_install_scripts.py -q`

Expected: PASS.

### Task 3: Синхронизировать документацию и backlog

**Files:**
- Modify: `README.md`
- Modify: `docs/scripts/README.md`
- Modify: `docs/scripts/installers.md`
- Modify: `TASKS.md`

**Step 1: Update docs**

Описать, что bootstrap/install:
- создаёт `backend/.env`, если файла нет;
- генерирует `CHAINLIT_AUTH_SECRET`;
- при дефолтном/пустом значении ротирует его автоматически.

**Step 2: Verify docs references**

Run: `rg -n "CHAINLIT_AUTH_SECRET|backend/.env" README.md docs/scripts/README.md docs/scripts/installers.md TASKS.md`

Expected: все упоминания согласованы с новым поведением.
