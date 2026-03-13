# T4.11 vLLM Migration E2E Benchmark Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Добавить reproducible before/after benchmark для `llama-server` vs `vLLM` migration path, не меняя orchestration core и не подменяя eval ручными впечатлениями.

**Architecture:** Бенчмарк использует уже существующий `scripts/benchmark.py` и `scripts/benchmark_compare.py`, но добавляет канонический runner/profile для двух backend modes (`llama-server` и `vllm`), единый output contract и operator notes. Источником истины остаются реальные runtime paths через `UMS` и текущий orchestration stack.

**Tech Stack:** Python benchmark scripts, UMS, docker compose/profile `vllm`, JSON reports, pytest smoke coverage.

---

### Task 1: Зафиксировать benchmark scenarios и report contract

**Files:**
- Modify: `docs/plans/2026-03-13-t410-vllm-compose-profile.md`
- Create: `docs/plans/2026-03-13-t411-vllm-benchmark.md`
- Modify: `TASKS.md`

**Step 1:** Описать benchmark matrix:
- `llama-server` baseline
- `vllm` candidate
- одинаковые prompts/datasets
- fixed warmup/iteration counts

**Step 2:** Описать success metrics:
- latency (`p50/p95`)
- throughput where available
- request success rate
- no regression in functional responses for canonical scenarios

### Task 2: Добавить benchmark runner surface

**Files:**
- Modify: `scripts/benchmark.py`
- Modify: `scripts/benchmark_compare.py`
- Modify: `scripts/launcher.sh` or add helper only if needed

**Step 1:** Добавить backend-mode metadata в benchmark output.

**Step 2:** Добавить helper/flag, чтобы runner честно фиксировал:
- `backend_mode`
- `runtime_profile`
- `active model profile`
- target URLs

**Step 3:** Не делать orchestration rewrite; использовать существующие endpoints.

### Task 3: Тесты и smoke coverage

**Files:**
- Modify/Create: `backend/tests/test_benchmark_compare.py`
- Possibly create: `backend/tests/test_benchmark_runner.py`

**Step 1:** Добавить unit coverage на report metadata / comparison semantics.

**Step 2:** Убедиться, что compare tool понимает `backend_mode=llama-server|vllm`.

### Task 4: Operator workflow docs

**Files:**
- Modify: `README.md`
- Modify: `docs/deploy-guide.md`
- Modify: `TASKS.md`

**Step 1:** Зафиксировать exact workflow:
1. запустить baseline runtime
2. прогнать benchmark
3. запустить `vllm` runtime/profile
4. прогнать benchmark
5. сравнить отчёты

**Step 2:** Явно отметить, что без живого `vLLM` runtime phase не считается полностью верифицированной.

### Task 5: Verification

**Run:**
- `pytest backend/tests/test_benchmark_compare.py -q`
- `python -m py_compile scripts/benchmark.py scripts/benchmark_compare.py`
- `git diff --check`
- optional live smoke only when operator runtime is available

## Implemented

- `scripts/benchmark.py` публикует `backend_mode` и `runtime_metadata` в JSON report.
- `scripts/benchmark_compare.py` учитывает `backend_mode` как runtime diff key.
- Добавлен unit coverage для benchmark runner/report contract:
  - `backend/tests/test_benchmark_runner.py`
  - `backend/tests/test_benchmark_compare.py`
- Operator workflow для before/after migration задокументирован в `README.md` и `docs/deploy-guide.md`.

## Verification notes

- Harness-level verification закрыта локально.
- Полноценный live benchmark с реальным `vLLM` runtime и production-size моделью остаётся отдельным operator exercise; эта фаза закрывает reproducible tooling, а не empirical rollout result.
