# B3.21 Classifier Quality Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Довести intent classifier до production-safe качества после benchmark: embedder по умолчанию остаётся `Qwen3-Embedding-0.6B`, но runtime начинает честно поддерживать `abstain/unsure`, richer classifier result contract и тестируемую low-confidence policy.

**Architecture:** Не переписывать orchestration. Улучшения ограничены classifier/runtime surface: `classifier.py` резолвит abstain policy, `chainlit_app.py` передаёт расширенный classifier result, `orchestration_runtime.py` трактует `__unsure__` как low-confidence signal и безопасно падает в heuristics/choose-route. Eval harness уже содержит cost-weighted/FP/unsure метрики, поэтому его нужно только синхронизировать с runtime contract, а не переписывать.

**Tech Stack:** Python, numpy, pytest, existing eval runner.

**Status:** Implemented on 2026-03-13. Runtime now supports threshold-based `abstain/unsure` for `embedder`, `hybrid`, and `llm -> embedder fallback`; orchestration treats `__unsure__` as low-confidence signal and falls back to heuristics/choose-route safely.

---

## Tasks

### Task 1: Зафиксировать B3.21 scope в каноне

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/2026-03-12-intent-classifier-benchmark-report.md`
- Modify: `docs/plans/2026-03-13-b321-classifier-quality.md`

**Steps:**
1. Зафиксировать, что `B3.21` в этой фазе закрывает:
   - runtime abstain/unsure;
   - thresholded embedder/hybrid selection;
   - richer classifier result fields;
   - per-intent FP / unsure / cost-weighted metrics уже доступны в eval harness.
2. Явно вынести out-of-scope:
   - новый retrieval eval;
   - смену legal/doc embedder;
   - classifier model hot-swap UI.

### Task 2: Написать падающие tests на classifier contract

**Files:**
- Modify: `backend/tests/test_intent_classifier.py`
- Modify: `backend/tests/test_orchestration_runtime.py`

**Steps:**
1. Добавить tests на classifier result:
   - low-confidence embedder result -> `intent="__unsure__"`, `abstained=True`
   - hybrid low-confidence path -> `__unsure__`
   - rich fields:
     - `predicted_intent`
     - `abstain_reason`
     - `thresholds`
     - `source`
2. Добавить tests на orchestration runtime:
   - `classifier_result.intent="__unsure__"` не ломает routing;
   - при двух документах и `__unsure__` остаётся safe ambiguity/choose-route path;
   - при отсутствии документов `__unsure__` не провоцирует document workflow без heuristics.

### Task 3: Реализовать classifier abstain policy

**Files:**
- Modify: `backend/orchestrator/rag/classifier.py`
- Modify: `backend/orchestrator/chainlit_app.py`

**Steps:**
1. Добавить canonical `UNSURE_INTENT = "__unsure__"`.
2. Реализовать helper для threshold-based abstain policy:
   - embedder confidence threshold;
   - embedder margin threshold;
   - optional llm confidence threshold for hybrid already exists.
3. Возвращать расширенный classifier result:
   - `intent`
   - `predicted_intent`
   - `confidence`
   - `margin`
   - `needs_rag`
   - `abstained`
   - `abstain_reason`
   - `thresholds`
   - `source`
4. В `chainlit_app.py` читать env thresholds и передавать их в `select_classifier_result(...)`.

### Task 4: Обновить orchestration runtime для `__unsure__`

**Files:**
- Modify: `backend/orchestrator/orchestration_runtime.py`

**Steps:**
1. Treat `__unsure__` as low-confidence classifier signal.
2. `detect_intent(...)` не должен blindly trust classifier result with `intent="__unsure__"`.
3. Поведение:
   - two docs + `__unsure__` -> ambiguity/choose-route
   - no docs + `__unsure__` -> heuristics/general fallback
   - session docs + `__unsure__` -> safe document_question/general path by existing heuristics, not hard fake confidence

### Task 5: Verification и documentation sync

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-13-b321-classifier-quality.md`

**Steps:**
1. Запустить:
   - `pytest backend/tests/test_intent_classifier.py backend/tests/test_orchestration_runtime.py -q`
   - `pytest backend/tests/test_intent_classifier.py backend/tests/test_orchestration_runtime.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q`
   - `cd backend && pytest tests/ -q -m "not integration"`
2. Проверить:
   - `python -m py_compile backend/orchestrator/rag/classifier.py backend/orchestrator/orchestration_runtime.py backend/orchestrator/chainlit_app.py`
   - `git diff --check`
3. Обновить `TASKS.md` и benchmark note так, чтобы runtime improvements были отражены явно.
