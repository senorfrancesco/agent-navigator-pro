# B3.34a Retrieval Eval Dataset Expansion Plan

**Goal:** расширить curated retrieval eval dataset на hard negatives, ambiguity-cases и более тяжёлые legal wording примеры, чтобы повторный verdict `LaBSE vs Qwen3-Embedding-0.6B` опирался не только на простые product scenarios.

## Why now

`B3.34` уже дал канонический harness и первый curated verdict. Но текущий набор всё ещё слишком маленький и “чистый”:
- мало ambiguity между близкими legal intents;
- мало hard negatives с пересекающейся лексикой;
- мало тяжёлых формулировок, где legal retrieval может просесть неочевидно.

Без этого нельзя уверенно решать, переносить ли dense retrieval baseline с `LaBSE` на `Qwen3`.

## Scope

### In scope
- расширение `backend/evals/data/retrieval_eval_dataset.yaml`
- обновление stub embed logic и loader/tests под новый dataset size
- повторный real CPU eval на локальных:
  - `backend/models/st/LaBSE`
  - `backend/models/st/Qwen3-Embedding-0.6B`
- синхронизация `TASKS.md` и phase-docs

### Out of scope
- production switch retrieval embedder
- новые retrieval metrics beyond current harness
- LLM judge / human labeling platform

## Dataset additions

Нужно добавить минимум 4 новые cases:

1. **session_only hard negative**
   - близкие по форме сроки (`уведомление` vs `претензия`)
   - цель: проверить, что retrieval не цепляет любой chunk со словом `дней`

2. **knowledge_base_only legal wording**
   - query про `одностороннее расторжение` / `существенное нарушение`
   - distractors с `изменение договора`, `расторжение по соглашению`
   - цель: проверить legal wording sensitivity

3. **mixed ambiguity**
   - вопрос, где нужны два разных origin и один близкий distractor
   - цель: проверить mixed evidence + source_origin accuracy under ambiguity

4. **unanswerable hard negative**
   - query, где chunks содержат близкие термины, но не дают answerable evidence
   - цель: не завысить `unanswerable_rejection_rate` на слишком лёгком наборе

## Implementation steps

### Step 1: Expand curated dataset

**Files**
- `backend/evals/data/retrieval_eval_dataset.yaml`

**Work**
- добавить 4-6 новых cases;
- не вводить новые scenario labels, использовать уже канонические:
  - `session_only`
  - `knowledge_base_only`
  - `mixed`
  - `unanswerable`
  - `duplicate-heavy`

### Step 2: Update test harness expectations

**Files**
- `backend/tests/test_retrieval_embedder_eval.py`

**Work**
- обновить dataset count assertions;
- расширить `_stub_embed_fn`, чтобы новые cases оставались детерминированными;
- сохранить tests на loader, metrics, JSON contract;
- добавить хотя бы один test на hard-negative / ambiguity behavior, если он не покрывается общим dataset run.

### Step 3: Repeat real evals

**Files**
- none required unless result reporting moves into docs/TASKS

**Work**
- прогнать:
  - `LaBSE`
  - `Qwen3-Embedding-0.6B`
- сравнить dense/hybrid verdict на expanded dataset;
- если результат всё ещё паритет/неубедителен, явно зафиксировать `LaBSE stays baseline`.

## Verification

- `pytest backend/tests/test_retrieval_embedder_eval.py -q`
- `python -m py_compile backend/evals/retrieval_embedder_eval.py backend/tests/test_retrieval_embedder_eval.py`
- `python backend/evals/retrieval_embedder_eval.py --dataset backend/evals/data/retrieval_eval_dataset.yaml --model backend/models/st/LaBSE --mode hybrid --top-k 5`
- `python backend/evals/retrieval_embedder_eval.py --dataset backend/evals/data/retrieval_eval_dataset.yaml --model backend/models/st/Qwen3-Embedding-0.6B --mode hybrid --top-k 5`
- `cd backend && pytest tests/ -q -m "not integration"`
- `git diff --check`

## Definition of Done

- dataset заметно расширен beyond initial 5-case curated set;
- expanded dataset остаётся детерминированно зелёным на unit tests;
- real CPU eval rerun выполнен для локальных `LaBSE` и `Qwen3`;
- `TASKS.md` честно отражает updated verdict;
- если verdict остаётся insufficient for migration, это явно зафиксировано, а не подразумевается.

## Status

Completed on 2026-03-13.

### Delivered
- curated dataset expanded from 5 to 9 cases;
- added:
  - `session_only` hard negative on `уведомление` vs `претензия`
  - `knowledge_base_only` legal wording case on unilateral termination / material breach
  - `mixed` ambiguity case on service evidence + liability cap
  - stronger `unanswerable` case with nearby finance vocabulary
- `test_retrieval_embedder_eval.py` updated for expanded dataset size and hard-negative coverage

### Repeated real evals
- `LaBSE` on expanded dataset:
  - `recall_at_k = 1.0`
  - `mrr = 1.0`
  - `ndcg_at_k = 1.0`
  - `evidence_hit_rate = 1.0`
  - `source_origin_accuracy = 1.0`
  - `answer_faithfulness = 0.951`
- `Qwen3-Embedding-0.6B` on expanded dataset:
  - same values

### Verdict
- dataset became stronger, but migration verdict did not change;
- `LaBSE` remains the dense retrieval/legal baseline;
- `Qwen3` still shows parity, not a decisive win.

### Deferred follow-up
- current `unanswerable_rejection_rate` remains an optimistic proxy in this harness and should be tightened before using it as a release gate
