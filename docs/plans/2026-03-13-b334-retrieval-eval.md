# B3.34 Retrieval Eval Implementation Plan

**Goal:** Ввести канонический retrieval eval harness для сравнения `LaBSE` и `Qwen3-Embedding-0.6B` на document/legal retrieval до любых решений о переносе dense retrieval с `LaBSE` на `Qwen3`.

**Architecture:** Не переписывать runtime. Оценка должна идти отдельным eval harness слоем поверх существующего `HybridRetriever`, c synthetic/curated dataset и детерминированными метриками. `B3.34` не внедряет `knowledge_base_rag`; он только измеряет retrieval quality на сценариях, которые уже нужны продукту: `session_only`, `knowledge_base_only`, `mixed`, `unanswerable`, `duplicate-heavy`.

**Tech Stack:** Python, pytest, YAML dataset, `sentence-transformers`, existing `HybridRetriever`.

**Scope**
- dataset: curated YAML cases для `session_only`, `knowledge_base_only`, `mixed`, `unanswerable`, `duplicate-heavy`
- CLI harness: `backend/evals/retrieval_embedder_eval.py`
- metrics:
  - `recall_at_k`
  - `mrr`
  - `ndcg_at_k`
  - `evidence_hit_rate`
  - `source_origin_accuracy`
  - deterministic `answer_faithfulness`
- tests for loader, metrics, mixed-source scoring, unanswerable behavior

**Out of Scope**
- переключение production retrieval embedder
- реальный `knowledge_base_rag` runtime contour
- LLM-based answer grading as required dependency
- reranker rollout

## Tasks

### Task 1: Dataset и contract

**Files**
- Add: `backend/evals/data/retrieval_eval_dataset.yaml`
- Add: `backend/tests/test_retrieval_embedder_eval.py`

**Steps**
1. Ввести dataset schema:
   - `id`
   - `scenario`
   - `query`
   - `answerable`
   - `reference_answer`
   - `expected_chunk_ids`
   - `expected_source_origins`
   - `chunks[]` with:
     - `chunk_id`
     - `document_id`
     - `source_origin`
     - `text`
2. Добавить минимально полезный набор cases для всех 5 сценариев.
3. Написать unit tests на parsing/validation.

### Task 2: Retrieval eval harness

**Files**
- Add: `backend/evals/retrieval_embedder_eval.py`

**Steps**
1. Реализовать loader dataclasses и validation.
2. Реализовать evaluation loop поверх `HybridRetriever`.
3. Добавить deterministic metrics:
   - `recall_at_k`
   - `mrr`
   - `ndcg_at_k`
   - `evidence_hit_rate`
   - `source_origin_accuracy`
   - `answer_faithfulness`
4. Поддержать `--mode dense|hybrid`, `--top-k`, `--model`, `--device`, `--json-output`.
5. Оставить `BM25` неизменным и сравнивать только dense embedder component.

### Task 3: Tests и verification

**Files**
- Add: `backend/tests/test_retrieval_embedder_eval.py`

**Steps**
1. Проверить dataset loading/validation.
2. Проверить metric math на deterministic stub embeddings.
3. Проверить `mixed` cases и `source_origin_accuracy`.
4. Проверить `unanswerable` cases.
5. Прогнать:
   - `pytest backend/tests/test_retrieval_embedder_eval.py -q`
   - `cd backend && pytest tests/ -q -m "not integration"`
   - `python -m py_compile backend/evals/retrieval_embedder_eval.py backend/tests/test_retrieval_embedder_eval.py`
   - `git diff --check`

### Task 4: Backlog/docs sync

**Files**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`
- Modify: `docs/plans/2026-03-12-execution-review-corrective-plan.md`

**Steps**
1. Если `B3.34` закрыт, отметить это в `TASKS.md`.
2. Зафиксировать, что решение о retrieval embedder теперь может приниматься только по этому harness.
3. Если останется workaround или known gap, записать его как follow-up.
