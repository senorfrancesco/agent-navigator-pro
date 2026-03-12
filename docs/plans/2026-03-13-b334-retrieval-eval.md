# B3.34 Retrieval Eval Implementation Plan

**Goal:** Ввести канонический retrieval eval harness для сравнения `LaBSE` и `Qwen3-Embedding-0.6B` на document/legal retrieval до любых решений о переносе dense retrieval с `LaBSE` на `Qwen3`.

**Status:** Implemented on 2026-03-13.

Что реально закрыто:
- `backend/evals/data/retrieval_eval_dataset.yaml`
- `backend/evals/retrieval_embedder_eval.py`
- `backend/tests/test_retrieval_embedder_eval.py`
- strict fail-fast dataset validation
- deterministic metrics and JSON report contract
- real CPU smoke on local `LaBSE` and `Qwen3-Embedding-0.6B`
- docs/backlog sync with conservative verdict

**Architecture:** Не переписывать runtime. Оценка идёт отдельным eval harness слоем поверх существующего `HybridRetriever`, c curated YAML dataset и детерминированными метриками. `B3.34` не внедряет `knowledge_base_rag`; он только измеряет retrieval quality на сценариях, которые уже нужны продукту: `session_only`, `knowledge_base_only`, `mixed`, `unanswerable`, `duplicate-heavy`.

**Tech Stack:** Python, pytest, YAML dataset, `sentence-transformers`, existing `HybridRetriever`.

Current verdict: curated dataset shows parity, so retrieval embedder is **not** unified yet; `LaBSE` remains the dense retrieval/legal baseline until dataset expansion.

## Definition of Done

Фаза считается закрытой, если выполнены все условия:
1. dataset schema валидируется кодом и тестами;
2. eval harness запускается из CLI и пишет JSON report;
3. метрики детерминированы на stub-embeddings и покрыты unit tests;
4. есть подтверждённый smoke-run как минимум на одном реальном embedder model path;
5. `TASKS.md` и канонические phase-docs синхронизированы;
6. decision "можно ли переносить dense retrieval на Qwen3" остаётся явно заблокированным до результатов этого harness.

## Scope

- curated dataset для:
  - `session_only`
  - `knowledge_base_only`
  - `mixed`
  - `unanswerable`
  - `duplicate-heavy`
- CLI harness: `backend/evals/retrieval_embedder_eval.py`
- metrics:
  - `recall_at_k`
  - `mrr`
  - `ndcg_at_k`
  - `evidence_hit_rate`
  - `source_origin_accuracy`
  - deterministic `answer_faithfulness`
  - `unanswerable_rejection_rate`
- tests for:
  - dataset loading/validation
  - metric math
  - mixed-source scoring
  - unanswerable behavior
  - CLI/report shape if feasible without heavy model downloads

## Out of Scope

- переключение production retrieval embedder
- реальный `knowledge_base_rag` runtime contour
- LLM-based answer grading as required dependency
- reranker rollout
- UI/report visualization поверх eval output

## Operational Notes

1. CLI smoke должен использовать локальный `model_path`; использование HF model id без локального кеша в offline-среде не воспроизводимо.
2. `answer_faithfulness` здесь — deterministic lexical proxy, а не LLM judge.
3. `bm25` mode оставлен в CLI как baseline/debug mode, но decision `LaBSE vs Qwen3` принимается только по dense/hybrid runs.
4. Current dataset остаётся curated/synthetic и должен расширяться отдельным follow-up `B3.34a`.

## Execution Summary

### Step 1: Harden dataset contract

**Files**
- Modify: `backend/evals/data/retrieval_eval_dataset.yaml`
- Modify: `backend/evals/retrieval_embedder_eval.py`
- Modify: `backend/tests/test_retrieval_embedder_eval.py`

**Work**
1. Явно зафиксировать dataset schema:
   - `id`
   - `scenario`
   - `query`
   - `answerable`
   - `reference_answer`
   - `expected_chunk_ids`
   - `expected_source_origins`
   - `chunks[]`
2. Решить validation policy:
   - invalid dataset entry = `ValueError`, а не silent skip
   - invalid `scenario` / `source_origin` = hard fail
3. Добавить tests на invalid dataset shape и helpful error messages.

Completed:
- invalid dataset entry теперь даёт `ValueError` с `case_id` и полем;
- answerable cases валидируются на `reference_answer` и `expected_chunk_ids`;
- invalid `scenario` / `source_origin` больше не silent-skipped.

### Step 2: Finalize metric semantics

**Files**
- Modify: `backend/evals/retrieval_embedder_eval.py`
- Modify: `backend/tests/test_retrieval_embedder_eval.py`

**Work**
1. Зафиксировать math для:
   - `recall_at_k`
   - `mrr`
   - `ndcg_at_k`
   - `evidence_hit_rate`
   - `source_origin_accuracy`
   - `answer_faithfulness`
   - `unanswerable_rejection_rate`
2. Убедиться, что `mixed` и `duplicate-heavy` действительно проверяют то, что нам важно продуктово:
   - retrieval не теряет нужный origin
   - дубликаты не ломают hit-rate
3. Добавить deterministic tests на каждую группу метрик.

Completed:
- `answer_faithfulness` считается по реально retrieved hit chunks, а не по gold evidence;
- `source_origin_accuracy` считается только по origin-ам реально найденных expected chunks;
- `unanswerable_rejection_rate` и per-scenario summaries закреплены тестами.

### Step 3: Finalize CLI/reporting surface

**Files**
- Modify: `backend/evals/retrieval_embedder_eval.py`
- Optionally modify: `scripts/benchmark.py` only if reuse is justified

**Work**
1. Довести CLI arguments:
   - `--dataset`
   - `--model`
   - `--mode dense|hybrid|bm25`
   - `--top-k`
   - `--device`
   - `--json-output`
2. Зафиксировать report shape и стабильное `stdout` JSON.
3. По возможности добавить smoke test на JSON-output file creation без реальной heavy model загрузки.

Completed:
- JSON report contract закреплён тестом;
- CLI пишет одинаковый JSON в stdout и в `--json-output`;
- real CPU smoke выполнен на локальных путях:
  - `backend/models/st/LaBSE`
  - `backend/models/st/Qwen3-Embedding-0.6B`

### Step 4: Verification and decision sync

**Files**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`
- Modify: `docs/plans/2026-03-12-execution-review-corrective-plan.md`
- Optionally modify: `docs/2026-03-12-intent-classifier-benchmark-report.md` only if cross-linking adds value

**Work**
1. Отметить `B3.34` как closed только после фактического eval harness verification.
2. Зафиксировать, что decision о переносе dense retrieval на `Qwen3` может приниматься только по этому harness.
3. Если останется pragmatic workaround:
   - synthetic dataset limits
   - lexical faithfulness proxy
   - отсутствие full KB runtime contour
   записать это как explicit follow-up в `TASKS.md`.

## Verification Commands

Минимум:
- `pytest backend/tests/test_retrieval_embedder_eval.py -q`
- `python -m py_compile backend/evals/retrieval_embedder_eval.py backend/tests/test_retrieval_embedder_eval.py`
- `git diff --check`

Phase-level:
- `cd backend && pytest tests/ -q -m "not integration"`

Operational smoke:
- `python backend/evals/retrieval_embedder_eval.py --dataset backend/evals/data/retrieval_eval_dataset.yaml --model <model-path> --mode hybrid --top-k 5 --json-output /tmp/retrieval_eval.json`

Completed:
- `TASKS.md` обновлён с conservative verdict и follow-up `B3.34a`;
- unified/corrective phase-docs синхронизированы.

## Real Eval Result

На текущем curated наборе оба прогона дали одинаковые значения:
- `recall_at_k = 1.0`
- `mrr = 1.0`
- `ndcg_at_k = 1.0`
- `evidence_hit_rate = 1.0`
- `source_origin_accuracy = 1.0`
- `answer_faithfulness = 0.9143`
- `unanswerable_rejection_rate = 1.0`

Вывод:
- `Qwen3-Embedding-0.6B` пока не даёт измеримого выигрыша над `LaBSE` на этом наборе;
- dense retrieval baseline не переносим;
- следующий обязательный шаг — dataset expansion (`B3.34a`).

## Expected Deliverables

1. Канонический retrieval eval dataset.
2. Рабочий CLI harness.
3. Unit tests на loader/metrics.
4. JSON report contract.
5. Обновлённый `TASKS.md` с честным статусом и follow-up notes.
