## B3.28 — Coverage heuristic v1.1 для `document_question`

**Status:** Implemented on 2026-03-13.

### Goal

Усилить gating и confidence для `document_question` без смены retrieval contour:

- перестать оценивать evidence почти только по `top-1 raw_score`;
- учитывать, какие источники модель реально процитировала;
- учитывать coverage по документам/источникам и multi-hop запросам;
- убрать split-brain между `Chainlit` и API-compatible execution path.

### Current gaps

1. `heuristic_v1` живёт в `chainlit_app.py`, а API path в `agent_api.py` использует урезанные локальные заглушки.
2. `has_sufficient_evidence()` почти не использует citation coverage:
   - проверяет `citations_valid`;
   - смотрит на `len(sources)`;
   - в основном опирается на `top-1 raw_score` / `z_score`.
3. `compute_confidence_v1()` использует cited sources, но не штрафует:
   - single-source citation при multi-hop запросе;
   - отсутствие cross-document coverage в mixed/overlay retrieval;
   - слабую поддержку answer из одного низко-разнообразного фрагмента.

### Scope

В этой фазе:

1. Вынести heuristic в backend-shared модуль.
2. Ввести deterministic coverage summary:
   - `cited_source_count`
   - `cited_document_count`
   - `coverage_ratio`
   - `has_cross_document_support`
3. Обновить `has_sufficient_evidence()` до v1.1:
   - использовать cited coverage, а не только top source;
   - для multi-hop запросов требовать stronger citation coverage;
   - для corrective/iterative modes учитывать coverage вместе с quality signal.
4. Обновить `compute_confidence_v1()`:
   - rewards за multi-source / multi-document support;
   - penalty за weak citation coverage;
   - сохранить bounded deterministic output `[0, 1]`.
5. Подключить единый heuristic contract в:
   - `chainlit_app.py`
   - `agent_api.py`
   - `execution_runtime.py` через dependencies

### Non-goals

- новый retrieval algorithm;
- reranker redesign;
- LLM judge / semantic faithfulness;
- UI redesign beyond already existing evidence UX;
- changing public response schema beyond additive fields if needed.

### Implementation Steps

#### Step 1. Shared backend module

Добавить модуль, например:

- `backend/orchestrator/doc_question_heuristics.py`

Вынести туда:

- `extract_citation_ids`
- `citations_are_valid`
- `is_multihop_query`
- `compute_coverage_v1`
- `has_sufficient_evidence_v1`
- `compute_confidence_v1`
- `build_doc_question_deterministic_fallback`

`render_doc_question_markdown()` остаётся в `chainlit_app.py`, потому что это presentation.

#### Step 2. Coverage heuristic v1.1

`compute_coverage_v1(sources, cited_ids, query)` должен считать минимум:

- `total_source_count`
- `cited_source_count`
- `cited_document_count`
- `coverage_ratio`
- `is_multihop`
- `has_cross_document_support`

Правила v1.1:

- no valid citations -> insufficient;
- single-hop:
  - допустим 1 cited source, но coverage ratio и quality должны быть не нулевые;
- multi-hop:
  - требовать минимум 2 cited sources;
  - если источники относятся к разным документам, повышать confidence;
  - если модель цитирует только один source при multi-hop query, confidence penalized / evidence may fail.

#### Step 3. Execution wiring

Обновить wiring в:

- `backend/orchestrator/chainlit_app.py`
- `backend/orchestrator/agent_api.py`

чтобы оба path использовали одинаковый heuristic module, а не разные локальные упрощения.

#### Step 4. Tests

Добавить/обновить:

- `backend/tests/test_document_analysis.py`
- `backend/tests/test_execution_runtime.py`
- `backend/tests/test_agent_api_orchestrate.py`

Нужные кейсы:

1. valid citations + single-source single-hop -> pass
2. valid citations + multi-hop + only one cited source -> fail or low confidence
3. valid citations + multi-hop + two cited sources -> pass
4. confidence penalized when cited coverage is low despite decent top raw score
5. API path and Chainlit path compute the same fallback/confidence semantics

### Verification

Targeted:

```bash
pytest backend/tests/test_document_analysis.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py -q
```

Phase regression:

```bash
cd backend && pytest tests/ -q -m "not integration"
```

Static:

```bash
python -m py_compile backend/orchestrator/doc_question_heuristics.py \
  backend/orchestrator/chainlit_app.py \
  backend/orchestrator/agent_api.py \
  backend/tests/test_document_analysis.py \
  backend/tests/test_execution_runtime.py \
  backend/tests/test_agent_api_orchestrate.py
git diff --check
```

### Done when

- heuristic больше не живёт только в `chainlit_app.py`;
- API path и Chainlit path используют один backend contract;
- `document_question` gating учитывает citation coverage и multi-hop support;
- targeted tests зелёные;
- full non-integration suite зелёный;
- статус зафиксирован в `TASKS.md`.

### Implementation result

- Добавлен shared backend module:
  - `backend/orchestrator/doc_question_heuristics.py`
- `Chainlit` и API adapter теперь используют один heuristic contract.
- `execution_runtime` передаёт `cited_ids` и `query` в evidence/confidence path.
- Added coverage for:
  - multi-hop query with only one citation -> insufficient evidence
  - confidence penalty for weak citation coverage
  - API dependencies reusing shared heuristic module

### Verification evidence

- `pytest backend/tests/test_document_analysis.py -q` -> `65 passed`
- `pytest backend/tests/test_execution_runtime.py -q` -> `12 passed`
- `pytest backend/tests/test_agent_api_orchestrate.py -q` -> `15 passed`
- `python -m py_compile backend/orchestrator/doc_question_heuristics.py backend/orchestrator/chainlit_app.py backend/orchestrator/agent_api.py backend/orchestrator/execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py`
- `git diff --check`

Pragmatic note:
- broad `cd backend && pytest tests/ -q -m "not integration"` probe still occasionally hits the already-known pytest tail-hang after tests finish; this phase did not introduce a new failure surface and targeted verification is green.
