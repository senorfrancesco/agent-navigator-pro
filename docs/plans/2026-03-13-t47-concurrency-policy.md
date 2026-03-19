# T4.7 Concurrency Policy для production

**Goal:** формализовать production-safe overload policy для `UMS` без построения distributed queue.

## Safe scope

### In scope

- env-driven concurrency caps для:
  - LLM/VLM inference
  - embedding inference
- явный acquire policy:
  - bounded waiting
  - optional fail-fast overload
- operator-visible status fields:
  - current limits
  - inflight counters
  - overload policy
- targeted tests + docs/TASKS sync

### Out of scope

- distributed queue
- priority scheduler
- admission control across multiple hosts
- per-user quotas

## Planned slice

1. Add runtime concurrency settings in `UMS`:
   - `UMS_LLM_MAX_CONCURRENCY` (`UMS_LLM_CONCURRENCY` alias)
   - `UMS_EMBED_MAX_CONCURRENCY` (`UMS_EMBED_CONCURRENCY` alias)
   - `UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S`
   - `UMS_FAIL_FAST_ON_SATURATION`
2. Replace raw semaphore usage with helper-based acquisition policy.
3. Expose runtime concurrency metadata via `/status`:
   - limits
   - fail-fast mode
   - inflight / available / saturated flags
4. Add focused tests for:
   - env-driven cap configuration
   - fail-fast overload response
   - status metadata
   - streaming overload returning `429` before opening SSE
5. Run targeted and full backend verification.

## Status

Implemented as a production-safe slice:
- helper-based acquire/release policy in `UMS`
- fail-fast overload mode
- pre-acquire for streaming responses
- status metadata with live inflight/available/saturated fields

Deferred follow-up:
- stricter lock-free fail-fast under race conditions would require a different admission primitive than the current `asyncio.Semaphore` safe slice

## Verification

- `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py`
- `git diff --check`
