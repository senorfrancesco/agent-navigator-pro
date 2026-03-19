# Compare Collapsible Appendix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `Chainlit Step`-based collapsible appendix for large compare reports so the main chat message stays readable while the full list of differences remains available on demand.

**Architecture:** Keep `backend/orchestrator/workflows/compare.py` producing the same full report contract, then split the final markdown in `backend/orchestrator/chainlit_app.py` into summary and appendix parts. Render summary as the main assistant message and render a collapsed `cl.Step` for the appendix only when the appendix crosses a configured threshold.

**Tech Stack:** Python, Chainlit, pytest, existing compare workflow/report rendering

---

### Task 1: Locate Compare Rendering Hook And Freeze Current Contract

**Files:**
- Modify: `backend/tests/test_chainlit_streaming.py`
- Inspect: `backend/orchestrator/chainlit_app.py`
- Reference: `backend/orchestrator/workflows/compare.py`

**Step 1: Write the failing presentation test**

Add a test that simulates a compare result with:

- `## Юридический вывод`
- `## Ключевые смысловые различия`
- `## Приложение: различия по пунктам`

Assert that the current single-message rendering is no longer acceptable once appendix splitting is introduced.

**Step 2: Run the targeted test to verify red**

Run:

```bash
cd backend && pytest tests/test_chainlit_streaming.py -q -k "compare and appendix"
```

Expected: FAIL because compare output is still rendered as one inline payload.

**Step 3: Identify the exact compare send path**

Trace where `compare_documents` final report becomes a `Chainlit` message:

- executor return shape
- rendering helper
- final `cl.Message` send call

Document the exact hook in code comments only if the flow is genuinely non-obvious.

**Step 4: Re-run the same targeted test**

Run:

```bash
cd backend && pytest tests/test_chainlit_streaming.py -q -k "compare and appendix"
```

Expected: still FAIL, but the target edit point is now known and fixed in scope.

### Task 2: Add Markdown Split Helper

**Files:**
- Modify: `backend/orchestrator/chainlit_app.py`
- Test: `backend/tests/test_chainlit_streaming.py`

**Step 1: Write helper-focused failing tests**

Add tests for a helper that:

- finds the section `## Приложение: различия по пунктам`
- returns `(main_body, appendix_body, appendix_count_or_flag)`
- leaves the report untouched when the section does not exist
- does not duplicate appendix text into the main body

Cover:

- report without appendix;
- report with short appendix;
- report with long appendix;
- malformed/partial appendix header.

**Step 2: Run helper tests to verify red**

Run:

```bash
cd backend && pytest tests/test_chainlit_streaming.py -q -k "appendix split"
```

Expected: FAIL because the helper does not exist yet.

**Step 3: Implement the minimal split helper**

In `backend/orchestrator/chainlit_app.py` add a pure helper that:

- detects canonical appendix heading;
- splits the report safely;
- returns a fallback “no split” result if parsing is ambiguous.

Keep parsing strict and predictable; do not try to infer arbitrary markdown structures.

**Step 4: Re-run helper tests**

Run:

```bash
cd backend && pytest tests/test_chainlit_streaming.py -q -k "appendix split"
```

Expected: PASS.

### Task 3: Add Threshold Policy For Step Rendering

**Files:**
- Modify: `backend/orchestrator/chainlit_app.py`
- Test: `backend/tests/test_chainlit_streaming.py`

**Step 1: Write failing threshold tests**

Add tests covering:

- short appendix stays inline;
- long appendix moves to collapsible rendering path;
- threshold decision is deterministic from explicit constants/config.

Prefer one threshold source of truth, for example:

- appendix char count;
- appendix line count;
- or extracted diff item count.

**Step 2: Run threshold tests to verify red**

Run:

```bash
cd backend && pytest tests/test_chainlit_streaming.py -q -k "appendix threshold"
```

Expected: FAIL because no threshold policy exists yet.

**Step 3: Implement the threshold helper**

Add one small helper/constant set in `backend/orchestrator/chainlit_app.py` that decides:

- `render_inline`
- or `render_in_step`

Do not over-engineer with multi-knob policy at this stage.

**Step 4: Re-run threshold tests**

Run:

```bash
cd backend && pytest tests/test_chainlit_streaming.py -q -k "appendix threshold"
```

Expected: PASS.

### Task 4: Render Long Appendix In Collapsed Chainlit Step

**Files:**
- Modify: `backend/orchestrator/chainlit_app.py`
- Test: `backend/tests/test_chainlit_streaming.py`

**Step 1: Write the failing rendering test**

Add a test that asserts for a long compare appendix:

- the main `cl.Message` contains the summary but not the appendix section;
- a `cl.Step` is created with a title like `Приложение: различия по пунктам (N)`;
- `autoCollapse` is set to collapsed-by-default behavior;
- the full appendix content is written into the step.

**Step 2: Run the rendering test to verify red**

Run:

```bash
cd backend && pytest tests/test_chainlit_streaming.py -q -k "collapsed compare appendix"
```

Expected: FAIL because compare path still emits one inline message.

**Step 3: Implement rendering integration**

Update the compare final rendering path so that:

- summary/body goes to the main message;
- long appendix goes to a separate `cl.Step`;
- short appendix stays inline;
- existing file/report artifact behavior remains untouched.

Use the already established `Chainlit` conventions in this repo for `Step` creation.

**Step 4: Re-run the rendering test**

Run:

```bash
cd backend && pytest tests/test_chainlit_streaming.py -q -k "collapsed compare appendix"
```

Expected: PASS.

### Task 5: Regression Coverage For Compare Workflow Compatibility

**Files:**
- Modify: `backend/tests/test_compare_workflow.py`
- Test: `backend/tests/test_chainlit_streaming.py`

**Step 1: Write compatibility regression checks**

Cover:

- compare workflow still produces the same `final_report` structure;
- the split/render logic is presentation-only;
- reports without appendix or with tiny appendix still render correctly;
- heterogeneous compare (`Юридический вывод` + appendix) remains supported.

**Step 2: Run targeted regression tests to verify impact**

Run:

```bash
cd backend && pytest tests/test_compare_workflow.py tests/test_chainlit_streaming.py -q
```

Expected: at least one failure until compatibility gaps are closed.

**Step 3: Fix any contract regressions**

Adjust only the presentation layer unless a real report-contract mismatch is found.

Do not rewrite compare workflow output unless tests show the current markdown shape is insufficient for reliable split.

**Step 4: Re-run targeted regression tests**

Run:

```bash
cd backend && pytest tests/test_compare_workflow.py tests/test_chainlit_streaming.py -q
```

Expected: PASS.

### Task 6: Verify, Update Backlog, And Commit

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-19-compare-collapsible-appendix-design.md`
- Modify: `docs/plans/2026-03-19-compare-collapsible-appendix-plan.md`

**Step 1: Add backlog note**

Record the compare UX slice in `TASKS.md`:

- either as a new H13 follow-up item;
- or as a specific subtask under compare UX/reporting improvements.

Keep wording concrete: main compare summary visible, appendix hidden in collapsed `Chainlit Step`.

**Step 2: Run verification commands**

Run:

```bash
cd backend && pytest tests/test_chainlit_streaming.py tests/test_compare_workflow.py -q
python -m py_compile backend/orchestrator/chainlit_app.py backend/orchestrator/workflows/compare.py
```

Expected: PASS.

**Step 3: Review git diff**

Run:

```bash
git diff -- README.md TASKS.md docs/plans/2026-03-19-compare-collapsible-appendix-design.md docs/plans/2026-03-19-compare-collapsible-appendix-plan.md backend/orchestrator/chainlit_app.py backend/tests/test_chainlit_streaming.py backend/tests/test_compare_workflow.py
```

Expected: only the intended compare UX slice is present.

**Step 4: Commit**

```bash
git add TASKS.md docs/plans/2026-03-19-compare-collapsible-appendix-design.md docs/plans/2026-03-19-compare-collapsible-appendix-plan.md backend/orchestrator/chainlit_app.py backend/tests/test_chainlit_streaming.py backend/tests/test_compare_workflow.py
git commit -m "feat(compare): collapse large appendix in chainlit"
```
