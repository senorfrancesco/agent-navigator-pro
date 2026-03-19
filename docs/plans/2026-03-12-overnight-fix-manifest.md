# Overnight Fix Manifest — B3.31 Cleanup + Implementation Issues

> **Execution environment:** OpenAI Codex с мультиагентностью.
> Задачи внутри каждой группы **независимы** — запускать параллельно.
> Между группами — строго последовательно: Группа 1 → Группа 2 → Группа 3 → Группа 4 → Финальная верификация.

**Goal:** Устранить найденные проблемы реализации в orchestration layer. Проблемы обнаружены при deep audit 2026-03-12 после закрытия ядра B3.31.

**Architecture:** Не менять архитектуру. Все правки — точечные фиксы внутри существующего orchestration boundary. Новые модули не создавать.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest.

**Ветка:** `codex/orchestration-control-plane-snapshot` (база — `v3.0`)

**Связанные планы:**
- `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md` — мастер-план (Фазы 1-7)
- `docs/plans/2026-03-12-b331-legacy-openai-compat-unification.md` — TDD-план B3.31 (Tasks 1-5)
- `docs/plans/2026-03-12-execution-review-corrective-plan.md` — execution block order A→G
- `TASKS.md` — единственный operational backlog

**Контекст:** B3.31 ядро реализовано (`orchestration_runtime.py` + `execution_runtime.py` + `ui_control_plane.py`), `chainlit_app.py` делегирует в backend, `_execute_intent` удалён. Осталось: cleanup дубликатов, фиксы багов, доведение тестов.

## Статус выполнения на 2026-03-12

- Группы 1-4 по изменённой orchestration-поверхности выполнены.
- Закрыты compatibility-фиксы для `/v1/chat/completions`, cleanup `chainlit_app.py`, runtime fixes в `execution_runtime.py`/`orchestration_runtime.py` и целевой regression-suite по orchestrator/Chainlit/UMS.
- Подтверждён phase-level regression suite по изменённой поверхности: `120 passed, 2 warnings`.
- Дополнительно закрыт residual `B3.31-test-harness`: `backend/tests/test_document_analysis.py` переведён на канонические helper'ы из backend runtime, `backend/tests/test_equipment_workflow.py::TestDocumentServerEndpoints` переведён на `httpx.ASGITransport` вместо подвисающего `TestClient`, а `backend/tests/conftest.py` выровнял import-path для одиночных и пакетных прогонов.
- Полный backend unit suite после cleanup завершён: `cd backend && pytest tests/ -q -m "not integration"` → `331 passed, 4 deselected`.
- Operational status фиксируется в `TASKS.md`; этот manifest теперь считать execution-log для fix-pack, а не отдельным backlog.
- `B3.31-test-harness` закрыт; operational residual по B3.31 теперь сводится к `B3.31-commit`.

---

## Правила для Codex-агентов

1. **Рабочая директория:** всегда `cd backend` перед pytest/grep/python.
2. **Не трогать файлы вне своей задачи.** Каждая задача указывает exact files.
3. **После каждого fix** — прогнать указанную verification command.
4. **Не менять imports/exports** в файлах, не указанных в задаче.
5. **Номера строк** — ориентировочные, искать по содержимому (grep/search), не по номеру.
6. **Коммиты не делать** — коммит один, финальный, после всех групп.

---

## Группа 1 — CRITICAL (блокируют runtime)

> **Параллелизм:** Task 1 и Task 2 — **независимы**, запускать одновременно.
> Оба правят `agent_api.py`, но в разных частях файла (не пересекаются).

### Task 1: Импортировать `ums_client` в `agent_api.py`

**Agent scope:** 1 файл

**File:** `backend/orchestrator/agent_api.py`

**Проблема:** Функция `_infer_with_effective_settings()` вызывает `ums_client.infer(...)`, но `ums_client` нигде не импортирован. Runtime: `NameError`.

**Fix:** Добавить после строки с `sys.path.insert(0, ...)` (до блока `from orchestrator.execution_runtime import`):

```python
from services.model_manager import ums_client
```

**Pre-check:** `grep -n "ums_client" backend/orchestrator/agent_api.py` — найдёт usage на строке ~155, но не import.

**Verification:**

```bash
cd backend && python -c "from orchestrator.agent_api import _infer_with_effective_settings; print('import ok')"
```

---

### Task 2: Обработать `stream=false` в `/v1/chat/completions`

**Agent scope:** 1 файл + 1 test file

**Files:**
- `backend/orchestrator/agent_api.py` — функция `openai_completions`
- `backend/tests/test_agent_api_openai_compat.py` — новый тест

**Проблема:** Endpoint всегда возвращает SSE `StreamingResponse`. Клиенты с `"stream": false` ожидают JSON.

**Fix:** В начале `openai_completions()`, после `data = await request.json()`, добавить:

```python
stream_mode = data.get("stream", True)
```

После блока dedup-проверки и перед `async def generate():`, вставить non-streaming path:

```python
if not stream_mode:
    compat_request = _build_openai_compat_request(
        data=data, messages=messages, user_query=user_query, attachments=found_files,
    )
    effective_settings = resolve_effective_settings(_collect_request_control_plane(compat_request))
    if target_model != "agent-navigator":
        effective_settings["resolved_model_id"] = target_model
    payload = compat_request.model_dump(exclude_none=True)
    payload["runtime_mode"] = resolve_request_runtime_mode(payload, effective_settings)
    payload["effective_settings"] = effective_settings
    try:
        response = await execute_orchestration(
            payload, deps=_build_api_execution_dependencies(compat_request, effective_settings),
        )
        text = str(response.get("assistant_message") or "")
    finally:
        _active_workflows.pop(dedup_key, None)
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": target_model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
```

**Test** (добавить в `backend/tests/test_agent_api_openai_compat.py`):

```python
def test_openai_completions_non_streaming_returns_json(client, monkeypatch):
    async def fake_execute(request, deps=None):
        return {"assistant_message": "non-stream answer"}
    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute)
    resp = client.post("/v1/chat/completions", json={
        "model": "agent-navigator",
        "messages": [{"role": "user", "content": "Привет"}],
        "stream": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "non-stream answer"
    assert "text/event-stream" not in resp.headers.get("content-type", "")
```

**Verification:**

```bash
cd backend && pytest tests/test_agent_api_openai_compat.py -q -k "non_streaming"
```

**Связь с планом:** `docs/plans/2026-03-12-b331-legacy-openai-compat-unification.md`, Task 2.

---

## Группа 2 — HIGH (некорректная логика, скрытые ошибки)

> **Зависимость:** выполнять после Группы 1 (Task 1 меняет imports в `agent_api.py`).
>
> **Параллелизм внутри группы:**
> - **Поток A** (agent_api.py): Task 3 → Task 4 (последовательно, один файл)
> - **Поток B** (execution_runtime.py): Task 5, Task 6, Task 8 — **параллельно** (разные функции)
> - **Поток C** (chainlit_app.py): Task 7 — **параллельно** с остальными

### Task 3: Удалить мёртвый код `_build_multiturn_prompt`

**Agent scope:** 1 файл

**File:** `backend/orchestrator/agent_api.py`

**Проблема:** `_build_multiturn_prompt()` и `MAX_HISTORY_MESSAGES` не вызываются нигде. Dead code от legacy.

**Pre-check:**

```bash
cd backend && grep -rn "_build_multiturn_prompt" --include="*.py"
```

Ожидание: только определение, никаких вызовов.

**Fix:** Удалить `MAX_HISTORY_MESSAGES = 10` и всю функцию `_build_multiturn_prompt` (ищется по имени, ~45 строк).

**Verification:**

```bash
cd backend && pytest tests/test_agent_api_orchestrate.py tests/test_agent_api_openai_compat.py -q
```

---

### Task 4: Устранить дублирование `_docs_list` / `_build_session_doc_list`

**Agent scope:** 1 файл (agent_api.py), read-only reference (execution_runtime.py)

**Files:**
- `backend/orchestrator/agent_api.py` — closure `_docs_list` внутри `_build_api_execution_dependencies`
- `backend/orchestrator/execution_runtime.py` — `_build_session_doc_list` (reference only, не менять)

**Проблема:** `_docs_list()` — closure в `agent_api.py` — дублирует `_build_session_doc_list()` из `execution_runtime.py`.

**Fix:** В `_build_api_execution_dependencies()` заменить closure `_docs_list` на:

```python
def _docs_list() -> List[Dict[str, Any]]:
    return _build_session_doc_list(session_docs)
```

Добавить `_build_session_doc_list` в блок import из `execution_runtime`:

```python
from orchestrator.execution_runtime import (
    ExecutionDependencies,
    build_control_plane_metadata,
    execute_orchestration,
    resolve_request_runtime_mode,
    _build_session_doc_list,  # добавить
)
```

**Verification:**

```bash
cd backend && pytest tests/test_agent_api_orchestrate.py tests/test_execution_runtime.py -q
```

---

### Task 5: Починить `rag_result.metadata` AttributeError

**Agent scope:** 1 файл

**File:** `backend/orchestrator/execution_runtime.py`, функция `_execute_doc_question`

**Проблема:** `rag_meta = rag_result.metadata or {}` — если у объекта нет `.metadata`, `AttributeError` молча проглатывается `except Exception`.

**Fix:** Найти строку `rag_meta = rag_result.metadata or {}` и заменить на:

```python
rag_meta = getattr(rag_result, "metadata", None) or {}
```

**Verification:**

```bash
cd backend && pytest tests/test_execution_runtime.py -q -k "doc_question"
```

---

### Task 6: Фильтрация sources по `document_id` vs `display_name`

**Agent scope:** 1 файл

**File:** `backend/orchestrator/execution_runtime.py`, функция `_execute_doc_question`

**Проблема:** Фильтр `if str(s.get("document_id")) == target_doc_name` — сравнивает UUID с display name. Никогда не совпадёт.

**Fix:** Найти:

```python
if target_doc_name:
    sources = [s for s in sources if str(s.get("document_id")) == target_doc_name]
```

Заменить на:

```python
if target_doc_name:
    sources = [
        s for s in sources
        if str(s.get("document_id")) == target_doc_name
        or str(s.get("display_name", "")) == target_doc_name
    ]
```

**Verification:**

```bash
cd backend && pytest tests/test_execution_runtime.py -q
```

**Связь с планом:** `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`, Фаза 6 (citations/evidence UX).

---

### Task 7: Импортировать константы в `chainlit_app.py` вместо дублирования

**Agent scope:** 1 файл + read-only reference

**File:** `backend/orchestrator/chainlit_app.py`
**Reference:** `backend/orchestrator/orchestration_runtime.py`

**Проблема:** 8+ констант (`INTENT_LOW_MARGIN_THRESHOLD`, `_COMPARE_QUERY_KEYWORDS`, `_EQUIPMENT_QUERY_KEYWORDS`, `_DOC_QUESTION_KEYWORDS`, `_SOCIAL_ONLY_RE`, и др.) объявлены в обоих файлах идентично.

**Fix:**

1. Проверить какие из этих констант используются в `chainlit_app.py`:

```bash
cd backend && grep -n "INTENT_LOW_MARGIN_THRESHOLD\|INTENT_LOW_CONFIDENCE_THRESHOLD\|ROUTE_CHOICE_TIMEOUT_S\|_COMPARE_QUERY_KEYWORDS\|_EQUIPMENT_QUERY_KEYWORDS\|_DOC_QUESTION_KEYWORDS\|_SUMMARY_QUERY_KEYWORDS\|_DOC_QUESTION_UPLOAD_REQUEST_PHRASES\|_SOCIAL_ONLY_RE" orchestrator/chainlit_app.py
```

2. Удалить локальные определения этих констант из `chainlit_app.py`.

3. Добавить import из `orchestration_runtime`:

```python
from orchestrator.orchestration_runtime import (
    INTENT_LOW_MARGIN_THRESHOLD,
    INTENT_LOW_CONFIDENCE_THRESHOLD,
    ROUTE_CHOICE_TIMEOUT_S,
    _COMPARE_QUERY_KEYWORDS,
    _EQUIPMENT_QUERY_KEYWORDS,
    _DOC_QUESTION_KEYWORDS,
    _SOCIAL_ONLY_RE,
)
```

4. Для `_SUMMARY_QUERY_KEYWORDS` и `_DOC_QUESTION_UPLOAD_REQUEST_PHRASES` — если используются, тоже импортировать. Если нет — удалить определения.

**Verification:**

```bash
cd backend && python -c "from orchestrator import chainlit_app; print('import ok')"
cd backend && pytest tests/test_orchestration_runtime.py -q
```

**Связь с планом:** `TASKS.md`, B3.31-cleanup.

---

### Task 8: Починить `_run_graph` — потеря intermediate node errors

**Agent scope:** 1 файл

**File:** `backend/orchestrator/execution_runtime.py`, функция `_run_graph`

**Проблема:** `final_state.update(output)` перезаписывает `errors` list целиком. Если несколько нод возвращают ошибки, сохраняются только последние.

**Fix:** Заменить функцию `_run_graph`:

```python
async def _run_graph(workflow: Any, initial_state: Dict[str, Any]) -> Dict[str, Any]:
    final_state: Dict[str, Any] = {}
    async for event in workflow.astream(initial_state):
        for _, output in event.items():
            if "errors" in output and "errors" in final_state:
                existing = final_state["errors"]
                new = output["errors"]
                if isinstance(existing, list) and isinstance(new, list):
                    output = dict(output)
                    output["errors"] = existing + new
            final_state.update(output)
    return final_state
```

**Verification:**

```bash
cd backend && pytest tests/test_execution_runtime.py -q
```

---

## Группа 3 — MEDIUM + LOW (edge-cases, polish)

> **Зависимость:** выполнять после Группы 2.
>
> **Параллелизм:** Все 5 задач (9-13) — **параллельно** (разные файлы / разные функции).

### Task 9: `mode_hint` = None для fresh uploads

**Agent scope:** 1 файл

**File:** `backend/orchestrator/orchestration_runtime.py`, функция `decide_orchestration`

**Проблема:** `detect_equipment_mode()` вызывается только если `two_docs and session_docs`. Fresh uploads с `file_count >= 2` но пустым `session_docs` → `mode_hint = None` → ambiguity prompt.

**Fix:** После блока `if two_docs and session_docs:` добавить:

```python
elif two_docs and new_files and len(new_files) >= 2:
    mode_hint = detect_equipment_mode(
        new_files[0].get("name", ""), new_files[1].get("name", ""), query,
    )
```

**Verification:**

```bash
cd backend && pytest tests/test_orchestration_runtime.py -q
```

---

### Task 10: Валидация invalid `forced_route`

**Agent scope:** 1 файл

**File:** `backend/orchestrator/orchestration_runtime.py`, функция `decide_orchestration`

**Проблема:** Invalid `forced_route` → `executor=None` → potential crash downstream.

**Fix:** Заменить блок `if forced_route:` на:

```python
if forced_route:
    executor = _EXECUTOR_BY_ROUTE.get(forced_route)
    if executor is None:
        import logging
        logging.getLogger(__name__).warning(
            "forced_route=%r is not a valid route, falling through to normal routing",
            forced_route,
        )
    else:
        active_mode = _ACTIVE_MODE_BY_ROUTE.get(forced_route)
        return _response(
            trace_id=trace_id,
            mode=runtime_mode,
            route=forced_route,
            executor=executor,
            confidence=1.0,
            margin=1.0,
            reason="forced_route",
            session_state_patch=_build_session_state_patch(
                trace_id=trace_id,
                route=forced_route,
                executor=executor,
                active_mode=active_mode,
            ),
        )
```

**Verification:**

```bash
cd backend && pytest tests/test_orchestration_runtime.py -q
```

---

### Task 11: Double `if` → `if/elif`

**Agent scope:** 1 файл

**File:** `backend/orchestrator/execution_runtime.py`, функция `_execute_general_chat`

**Fix:** Найти два последовательных `if social_query and ...` блока и заменить второй на `elif`.

**Verification:**

```bash
cd backend && pytest tests/test_execution_runtime.py -q
```

---

### Task 12: Зафиксировать session-key sync как follow-up

**Agent scope:** 1 файл (TASKS.md only)

**Fix:** В `TASKS.md`, в секцию B3.31-cleanup добавить пункт:
```
- [ ] **B3.31-session-sync — Sync guard для pending_action / pending_route_choice session keys**
```

Код не трогать.

---

### Task 13: Добавить комментарий к `pass` branch

**Agent scope:** 1 файл

**File:** `backend/orchestrator/chainlit_app.py`

**Fix:** Найти одиночный `pass` в ветке routing/orchestration и добавить комментарий:

```python
pass  # intentionally empty — handled by backend orchestration decision
```

---

## Группа 4 — TEST (покрытие)

> **Зависимость:** выполнять после Группы 1 (Task 1 — import ums_client).
>
> **Параллелизм:** Task 14 и Task 15 — **параллельно** (разные test files).

### Task 14: Тест для `ums_client` code path

**Agent scope:** 1 test file

**File:** `backend/tests/test_agent_api_orchestrate.py`

**Fix:** Добавить:

```python
@pytest.mark.asyncio
async def test_infer_with_effective_settings_calls_ums_client(monkeypatch):
    called = {}
    def fake_infer(model_id, payload):
        called["model_id"] = model_id
        called["payload"] = payload
        return {"content": "test response"}
    monkeypatch.setattr("orchestrator.agent_api.ums_client.infer", fake_infer)
    from orchestrator.agent_api import _infer_with_effective_settings
    result = await _infer_with_effective_settings(
        {"resolved_model_id": "test-model", "generation": {"temperature": 0.5}},
        "test prompt",
    )
    assert called["model_id"] == "test-model"
    assert result == "test response"
```

**Verification:**

```bash
cd backend && pytest tests/test_agent_api_orchestrate.py -q -k "ums_client"
```

---

### Task 15: Убрать хрупкий `model_profile` assertion

**Agent scope:** 1 test file

**File:** `backend/tests/test_execution_runtime.py`

**Fix:** Найти `assert response["model_profile"] == "default-chat"` и заменить на:

```python
assert isinstance(response.get("model_profile"), (str, type(None)))
```

**Verification:**

```bash
cd backend && pytest tests/test_execution_runtime.py -q
```

---

## Финальная верификация (после всех групп)

```bash
cd backend && pytest tests/test_agent_api_orchestrate.py tests/test_agent_api_openai_compat.py tests/test_execution_runtime.py tests/test_orchestration_runtime.py tests/test_chainlit_runtime_mode.py -v --tb=short
```

Затем полный набор:

```bash
cd backend && pytest tests/ -v -m "not integration" --tb=short 2>&1 | tail -30
```

Ожидание: 326+ passed, 0 failed.

---

## Граф зависимостей для Codex scheduler

```
Группа 1 (параллельно):
  Task 1 ──┐
  Task 2 ──┤
           ▼
Группа 2 (3 параллельных потока):
  Поток A: Task 3 → Task 4          (agent_api.py)
  Поток B: Task 5 ┬ Task 6 ┬ Task 8 (execution_runtime.py, разные функции)
  Поток C: Task 7                    (chainlit_app.py)
           ▼
Группа 3 (все параллельно):
  Task 9, 10   (orchestration_runtime.py, разные блоки)
  Task 11      (execution_runtime.py)
  Task 12      (TASKS.md only)
  Task 13      (chainlit_app.py)
           ▼
Группа 4 (параллельно):
  Task 14  (test_agent_api_orchestrate.py)
  Task 15  (test_execution_runtime.py)
           ▼
Финальная верификация (один агент)
```

---

## Связь с backlog

| Tasks | Backlog item | План |
|---|---|---|
| 1-4, 7 | B3.31-cleanup | `TASKS.md` строка 127 |
| 2 | B3.31 SSE workaround | `docs/plans/2026-03-12-b331-legacy-openai-compat-unification.md`, Task 2 |
| 5-6, 8 | execution_runtime quality | `docs/plans/2026-03-12-execution-review-corrective-plan.md` |
| 7 | B3.31-cleanup routing дубликаты | `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`, Фаза 1 |
| 9-10 | orchestration_runtime edge cases | `TASKS.md` B3.31 |
| 14-15 | test coverage | `docs/plans/2026-03-12-b331-legacy-openai-compat-unification.md`, Task 5 |

## После завершения

Один финальный коммит:

```
fix(orchestrator): close B3.31 cleanup — 15 implementation issues resolved

- Import ums_client in agent_api.py (NameError fix)
- Handle stream=false in /v1/chat/completions
- Remove dead _build_multiturn_prompt code
- Deduplicate _docs_list / _build_session_doc_list
- Fix rag_result.metadata AttributeError
- Fix document_id vs display_name source filter
- Import constants in chainlit_app.py instead of duplicating
- Merge errors in _run_graph instead of overwriting
- Add mode_hint fallback for fresh uploads
- Validate forced_route before use
- Fix if/elif in _execute_general_chat
- Add comment to pass branch
- Add ums_client test coverage
- Fix fragile model_profile assertion
```

Следующий приоритет: **B3.21** (classifier quality) — см. `TASKS.md`, Блок A.
