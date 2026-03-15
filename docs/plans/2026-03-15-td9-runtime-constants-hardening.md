# План: TD-9 Runtime Constants Hardening

## Summary

Не делать full-style cleanup по всему репозиторию. Вынести только наиболее рискованные magic numbers из runtime-critical path:

- `backend/services/model_manager/ums_client.py`
- `backend/orchestrator/workflows/compare.py`
- `backend/orchestrator/workflows/document_analysis.py`

## Scope

### In scope

- retries / timeout / pool limits / batching в `ums_client`;
- compare workflow truncation / chunking / batch analysis constants;
- document analysis summarize/extract budgets, где числа влияют на runtime behavior.

### Out of scope

- полный sweep по всем модулям;
- замена status codes и trivial numeric literals;
- redesign prompt profiles / runtime budgets.

## Exit Criteria

- наиболее важные runtime magic numbers переведены в именованные константы;
- где уместно, добавлен env override;
- поведение покрыто существующими targeted tests без регрессий.

## Status

Completed on 2026-03-15 as a narrow runtime-critical slice.

Implemented:

- `backend/services/model_manager/ums_client.py`
  - retries / timeouts / pool limits / embedding batch controls moved to named constants with env overrides
- `backend/orchestrator/workflows/compare.py`
  - truncate / chunking / analyze thresholds moved to named constants with env overrides
- `backend/orchestrator/workflows/document_analysis.py`
  - summarize / reduce temperature, token budgets, and sleep moved to named constants with env overrides
- `backend/orchestrator/knowledge_base_retrieval.py`
  - shortlist merge / dedup / rerank coefficients and candidate budgets moved to named constants with env overrides
- `backend/orchestrator/doc_question_heuristics.py`
  - confidence thresholds, coverage weights, multihop/cross-document bonuses, and insufficient-evidence cap moved to named constants with env overrides

Verification:

- `pytest backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py backend/tests/test_document_analysis.py -q`
- `pytest backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py -q`
- `python -m py_compile backend/orchestrator/doc_question_heuristics.py backend/orchestrator/knowledge_base_retrieval.py backend/orchestrator/workflows/document_analysis.py backend/orchestrator/workflows/compare.py backend/services/model_manager/ums_client.py backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py`

Deferred:

- broad cleanup of remaining magic numbers in `shared/report_utils`, `rag/retriever`, `equipment`, and API glue remains a separate follow-up rather than part of this slice
