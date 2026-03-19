# B3.48d-e Shadow Mode And Decision Trace Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Finish `B3.48d` and `B3.48e` by adding shadow strategy telemetry, helper invariants, and decision-trace metadata for adaptive reduce policy.

**Architecture:** Keep the current adaptive runtime path unchanged, then layer observability on top of it. Shadow mode must never alter executed behavior; it only emits comparative telemetry. Helper invariants stay centralized in `summary_reduce_policy.py`, while `documents_summary` and `document_analysis` only record and surface decision traces.

**Tech Stack:** Python, pytest, existing observability counters, execution runtime metadata

---

### Task 1: Helper Invariants And Contract

**Files:**
- Modify: `backend/orchestrator/summary_reduce_policy.py`
- Test: `backend/tests/test_summary_reduce_policy.py`

**Step 1: Write failing invariant tests**

Add/extend tests for:
- `degraded_active=True` always blocks `fast_final_merge`
- `low_vram=True` with `allow_fast_final_merge_on_low_vram=False` always forces `hierarchical_merge`
- `partial_only` is not emitted by helper
- `fast_path_eligible=True` can coexist with blocked `strategy != fast_final_merge` only when higher-level policy blocks fast path
- admission snapshot always contains token and margin fields

**Step 2: Run targeted helper tests to verify red**

Run:

```bash
pytest backend/tests/test_summary_reduce_policy.py -q
```

Expected: at least one new failure around missing invariant coverage or missing metadata shape.

**Step 3: Implement helper spec**

Add a docstring/spec above the helper describing invariants:
- helper never emits `partial_only`
- helper is pure and side-effect free
- `degraded_active` and low-VRAM guards dominate admission fitness
- admission snapshot is part of the public internal contract

Keep backward compatibility with the existing shimmed callers.

**Step 4: Re-run helper tests**

Run:

```bash
pytest backend/tests/test_summary_reduce_policy.py -q
```

Expected: all helper tests pass.

### Task 2: Shadow Decision Telemetry

**Files:**
- Modify: `backend/orchestrator/execution_runtime.py`
- Modify: `backend/orchestrator/workflows/document_analysis.py`
- Test: `backend/tests/test_execution_runtime.py`
- Test: `backend/tests/test_document_analysis.py`

**Step 1: Write failing shadow-mode tests**

Cover:
- `SUMMARY_REDUCE_STRATEGY_SHADOW_MODE=1` does not change executed strategy
- metadata/logs include:
  - `executed_strategy`
  - `recommended_strategy`
  - `would_skip_levels`
  - `estimated_token_saving`
- counter `agent_nav_summary_strategy_shadow_diff_total` increments only when the executed strategy differs from shadow baseline

**Step 2: Run targeted tests to verify red**

Run:

```bash
pytest backend/tests/test_execution_runtime.py -q -k "shadow"
pytest backend/tests/test_document_analysis.py -q -k "shadow"
```

Expected: failures because shadow fields/counter do not exist yet.

**Step 3: Implement shadow-mode plumbing**

Rules:
- add env flag `SUMMARY_REDUCE_STRATEGY_SHADOW_MODE`
- current executed path remains unchanged
- shadow mode computes additional comparison against a conservative baseline strategy
- record:
  - `executed_strategy`
  - `recommended_strategy`
  - `shadow_baseline_strategy`
  - `would_skip_levels`
  - `estimated_token_saving`
- increment `agent_nav_summary_strategy_shadow_diff_total` when `shadow_baseline_strategy != executed_strategy`

Do not add branching that changes retries, grouping, or cancellation behavior.

**Step 4: Re-run targeted tests**

Run:

```bash
pytest backend/tests/test_execution_runtime.py -q -k "shadow"
pytest backend/tests/test_document_analysis.py -q -k "shadow"
```

Expected: shadow-mode tests pass.

### Task 3: Decision Trace Metadata

**Files:**
- Modify: `backend/orchestrator/execution_runtime.py`
- Modify: `backend/orchestrator/workflows/document_analysis.py`
- Test: `backend/tests/test_execution_runtime.py`
- Test: `backend/tests/test_document_analysis.py`

**Step 1: Write failing decision-trace tests**

Cover:
- `reduce_decisions[]` exists and records each strategy check
- each trace item includes:
  - `items_count`
  - `chars`
  - `tokens_est`
  - `strategy_selected`
  - `reason`
- `early_exit_after_level` is set when fast path terminates hierarchy early

**Step 2: Run targeted tests to verify red**

Run:

```bash
pytest backend/tests/test_execution_runtime.py -q -k "decision_trace or early_exit"
pytest backend/tests/test_document_analysis.py -q -k "decision_trace or early_exit"
```

Expected: failures because trace fields are missing.

**Step 3: Implement trace recording**

Rules:
- append one trace entry per strategy decision point
- include both diagnostic chars and authoritative token fields
- expose trace through:
  - `execution_metadata` for `documents_summary`
  - `summary_metadata` and report-facing `execution_metadata` for `document_analysis`
- `early_exit_after_level` must be `0` for direct fast path and `N` for exit after `N` hierarchy levels

**Step 4: Re-run targeted tests**

Run:

```bash
pytest backend/tests/test_execution_runtime.py -q -k "decision_trace or early_exit"
pytest backend/tests/test_document_analysis.py -q -k "decision_trace or early_exit"
```

Expected: all new trace tests pass.

### Task 4: Full Verification, TASKS Update, Commit

**Files:**
- Modify: `TASKS.md`

**Step 1: Mark completed backlog items**

Update `TASKS.md`:
- mark `B3.48`, `B3.48a`, `B3.48b`, `B3.48c`, `B3.48d`, `B3.48e` as complete if verification is green
- add a concise completion note for shadow mode and decision trace

**Step 2: Run full verification**

Run:

```bash
pytest backend/tests/test_summary_reduce_policy.py backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_summary_quality_contour.py -q
python -m py_compile backend/orchestrator/summary_reduce_policy.py backend/orchestrator/execution_runtime.py backend/orchestrator/workflows/document_analysis.py backend/evals/summary_quality_contour.py backend/tests/test_summary_reduce_policy.py backend/tests/test_summary_quality_contour.py
```

Expected: all tests pass, `py_compile` exits `0`.

**Step 3: Commit**

Run:

```bash
git add TASKS.md docs/plans/2026-03-18-b348de-shadow-trace-implementation.md backend/orchestrator/summary_reduce_policy.py backend/orchestrator/execution_runtime.py backend/orchestrator/workflows/document_analysis.py backend/evals/summary_quality_contour.py backend/tests/test_summary_reduce_policy.py backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_summary_quality_contour.py
git commit -m "feat(summary): add reduce shadow mode and decision trace"
```

Expected: one logical commit for the completed `B3.48a-e` slice.
