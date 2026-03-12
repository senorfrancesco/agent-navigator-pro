# T4.16 Benchmark Comparison Tool

**Goal:** добавить канонический инструмент сравнения двух benchmark JSON-отчётов из `scripts/benchmark.py`, чтобы CPU/GPU/Hybrid прогоны сравнивались детерминированно и с operator-friendly выводом, а не вручную по сырым JSON.

## Context

`T4.15` уже дал единый генератор benchmark-отчётов:

- `scripts/benchmark.py`
- JSON output через `--output`
- единый набор сценариев и `env_snapshot`

Следующий шаг — сравнение двух прогонов:

- `CPU vs GPU`
- `GPU vs Hybrid`
- `before vs after`

без ad hoc `jq`, ручных diff и визуального чтения двух JSON файлов.

## Scope

### In scope

- новый canonical script `scripts/benchmark_compare.py`
- сравнение двух JSON-отчётов `BenchmarkReport`
- summary table по:
  - latency delta
  - speedup ratio
  - status changes
  - missing/new scenarios
- агрегированная сводка по:
  - total ok/error/timeout
  - total_time_sec delta
  - changed runtime metadata (`runtime_profile`, loaded models, placements if present)
- machine-readable JSON output для CI / operator workflows
- tests на parsing / diff / CLI behavior
- docs/TASKS sync

### Out of scope

- запуск benchmark внутри compare-tool
- plotting / charts / HTML report
- CI integration
- regressions gating policy в pipeline
- statistical significance framework

## Canonical interface

### CLI

```bash
python scripts/benchmark_compare.py base.json candidate.json
python scripts/benchmark_compare.py results/cpu.json results/gpu.json --json-output results/compare.json
python scripts/benchmark_compare.py a.json b.json --baseline-label CPU --candidate-label GPU
python scripts/benchmark_compare.py a.json b.json --scenarios chat,doc_question
```

### Expected behavior

- fail-fast на битом JSON или incompatible report shape
- deterministic ordering scenarios
- human-readable console summary
- optional JSON artifact through `--json-output`

## Comparison semantics

### Scenario alignment

- primary key: `scenario`
- if scenario exists only in baseline:
  - mark as `removed`
- if scenario exists only in candidate:
  - mark as `added`
- if both exist:
  - compare status, latency, response size, selected details

### Latency metrics

For shared scenarios:

- `baseline_elapsed_sec`
- `candidate_elapsed_sec`
- `delta_sec = candidate - baseline`
- `delta_pct`
- `speedup = baseline / candidate` if candidate > 0

Interpretation:

- `speedup > 1.0` means candidate faster
- `delta_sec < 0` means candidate faster

### Status comparison

Need explicit classification:

- `same_ok`
- `same_error`
- `regressed_status`
- `improved_status`
- `changed_non_ok`

Where:

- `ok -> error/timeout/skip` is regression
- `error/timeout/skip -> ok` is improvement

### Metadata comparison

At report level compare:

- `runtime_profile`
- `effective_context_tokens`
- `retrieved_context_tokens_budget`
- `generation_tokens_reserve`
- `running`
- `active_heavy_model`

If keys absent, compare-tool should degrade gracefully.

## JSON output contract

Top-level shape:

```json
{
  "baseline_label": "CPU",
  "candidate_label": "GPU",
  "baseline_path": "...",
  "candidate_path": "...",
  "summary": {
    "shared_scenarios": 0,
    "added_scenarios": 0,
    "removed_scenarios": 0,
    "improved_status_count": 0,
    "regressed_status_count": 0,
    "faster_count": 0,
    "slower_count": 0,
    "unchanged_count": 0,
    "baseline_total_time_sec": 0.0,
    "candidate_total_time_sec": 0.0,
    "overall_speedup": 0.0
  },
  "runtime_diff": {
    "baseline": {},
    "candidate": {},
    "changed_keys": []
  },
  "scenarios": [
    {
      "scenario": "chat",
      "change_type": "shared",
      "status_change": "same_ok",
      "baseline_status": "ok",
      "candidate_status": "ok",
      "baseline_elapsed_sec": 0.0,
      "candidate_elapsed_sec": 0.0,
      "delta_sec": 0.0,
      "delta_pct": 0.0,
      "speedup": 0.0
    }
  ]
}
```

## File plan

### New

- `scripts/benchmark_compare.py`
- `backend/tests/test_benchmark_compare.py`

### Update

- `TASKS.md`
- `README.md` if CLI section needs one-line cross-link

## Execution order

1. Implement pure comparison helpers in `scripts/benchmark_compare.py`
2. Add CLI wrapper and JSON output
3. Add focused tests on report parsing and comparison semantics
4. Add docs/TASKS sync
5. Run targeted tests
6. Run full backend unit suite

## Verification

- `pytest backend/tests/test_benchmark_compare.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile scripts/benchmark_compare.py backend/tests/test_benchmark_compare.py`
- `git diff --check`

## Definition of Done

- compare-tool exists as canonical script
- compares two benchmark JSONs deterministically
- prints useful console summary
- can save machine-readable JSON result
- covered by focused tests
- reflected in `TASKS.md`

## Completed

- Added canonical script: `scripts/benchmark_compare.py`
- Added focused coverage: `backend/tests/test_benchmark_compare.py`
- Added README usage reference for `benchmark.py -> benchmark_compare.py` workflow
- Synced `TASKS.md`

## Verification

- `pytest backend/tests/test_benchmark_compare.py -q` -> `5 passed`
- `cd backend && pytest tests/ -q -m "not integration"` -> `408 passed, 4 deselected`
- `python -m py_compile scripts/benchmark_compare.py backend/tests/test_benchmark_compare.py`
- `git diff --check`
