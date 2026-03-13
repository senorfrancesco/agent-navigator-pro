# T4.6 Port Pool и Scheduler в UMS

**Goal:** заменить ad hoc monotonic port allocation в `UMS` на backend-owned port reservation/release policy и минимальный scheduler слой без превращения фазы в full orchestration platform rewrite.

## Context

После `T4.4` и `T4.5` `UMS` уже умеет:

- управлять lifecycle моделей через control API;
- регистрировать dynamic models;
- автоприсваивать порты для dynamic/discovered моделей.

Но текущий механизм всё ещё слишком примитивен:

- нет явного port registry;
- release/reuse портов не формализован;
- filesystem-discovered модели и registered dynamic models используют разные implicit paths;
- нет backend-owned scheduler policy для serializing conflicting starts beyond per-model locks.

## Safe Scope

### In scope

- backend-owned `port_registry`
- explicit reserve/release helpers
- safe reuse освобождённых dynamic ports
- единая allocation policy для:
  - registered dynamic models
  - filesystem-discovered GGUF models
- minimal global scheduler guard для conflicting heavy-model lifecycle transitions
- focused tests + TASKS/docs sync

### Out of scope

- distributed scheduler
- queue/priority system
- GPU reservation scheduler beyond current placement policy
- auth/ACL for ops surface
- Chainlit UI for port control

## Planned Slice

1. Add runtime state:
   - `reserved_ports`
   - `port_owners`
   - `released_dynamic_ports`
2. Normalize helpers:
   - `_collect_reserved_ports()`
   - `_reserve_port(model_id, requested_port=None)`
   - `_release_port(model_id)`
3. Wire release on:
   - `_stop_model`
   - dynamic unregister
4. Reuse released ports for dynamic/discovered models before bumping monotonic counter.
5. Add lightweight scheduler guard for heavy model start/stop path to avoid conflicting transitions across different model ids.

## Test Plan

- reserve explicit dynamic port
- reject already reserved explicit port
- release port on unregister
- reuse released dynamic port
- discovered GGUF gets reserved port and stable owner mapping
- heavy-model conflicting start path remains serialized

## Verification

- `pytest backend/tests/test_unified_model_server_startup.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
- `git diff --check`

## Completed

- added backend-owned port registry in `UMS` runtime state:
  - `reserved_ports`
  - `port_owners`
  - `released_dynamic_ports`
- unified reserve/release policy for:
  - registered dynamic models
  - filesystem-discovered GGUF models
- released dynamic ports are now safely reused before bumping monotonic counter
- discovered GGUF models get stable reserved port/owner mapping during runtime
- heavy-model conflicting lifecycle transitions are serialized via global reentrant guard, without introducing queue/distributed scheduler complexity

### Verification Evidence

- `pytest backend/tests/test_unified_model_server_startup.py -q` → `32 passed`
- `cd backend && pytest tests/ -q -m "not integration"` → `436 passed, 4 deselected`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
- `git diff --check`
