# B3.31 Legacy OpenAI Compatibility Unification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Закрыть остаток `B3.31`: свести legacy `/v1/chat/completions` к compatibility layer над unified backend execution core, выровнять backend response contract и убрать оставшийся split-brain между Chainlit path и OpenAI-compatible path.

**Architecture:** Сохраняем публичный legacy endpoint, но убираем из него отдельный execution-мир: больше никакого собственного `sessions` state, `run_workflow_stream`, отдельного routing и session-RAG. Все execution-решения и исполнение проходят через тот же backend core, что и `Chainlit`: `resolve_effective_settings(...) -> decide_orchestration(...) / execute_orchestration(...)`. При этом `B3.31a` сознательно не трогаем: durable backend-owned orchestration store остаётся отдельной фазой.

**Tech Stack:** FastAPI, Pydantic, backend orchestrator runtime, Chainlit adapter, pytest, FastAPI TestClient / async unit tests.

---

### Task 1: Выровнять effective runtime mode и top-level execution contract

**Files:**
- Modify: `backend/orchestrator/agent_api.py`
- Modify: `backend/orchestrator/execution_runtime.py`
- Modify: `backend/orchestrator/ui_control_plane.py`
- Test: `backend/tests/test_agent_api_orchestrate.py`
- Test: `backend/tests/test_execution_runtime.py`

**Step 1: Write the failing tests**

Добавить тесты на два критичных расхождения:

```python
def test_orchestrate_uses_effective_runtime_mode_from_assistant_mode():
    request = OrchestrationRequest(
        message="Сравни документы",
        assistant_mode="specific_tasks",
        file_count=2,
        has_session_docs=True,
        session_docs={"a.pdf": {"text": "a"}, "b.pdf": {"text": "b"}},
        attachments_meta=[
            {"name": "a.pdf", "path": "/tmp/a.pdf"},
            {"name": "b.pdf", "path": "/tmp/b.pdf"},
        ],
        classifier_result={
            "intent": "compare_documents",
            "confidence": 0.93,
            "margin": 0.6,
            "needs_rag": False,
        },
    )

    response = asyncio.run(orchestrate(request))

    assert response["mode"] == "specialized_tasks"
```

```python
def test_execute_orchestration_returns_top_level_control_plane_fields():
    request = OrchestrationRequest(
        message="Что в базе знаний по штрафам?",
        assistant_mode="rag_qa",
        rag_scope="knowledge_base_rag",
        knowledge_collection_id="legal",
    )

    response = asyncio.run(execute_orchestration_api(request))

    assert response["rag_scope"] == "knowledge_base_rag"
    assert response["knowledge_collection_id"] == "legal"
    assert "source_scope_summary" in response
```

**Step 2: Run tests to verify they fail**

Run:

```bash
cd backend && pytest tests/test_agent_api_orchestrate.py tests/test_execution_runtime.py -q -k "effective_runtime_mode or top_level_control_plane"
```

Expected:
- `mode` останется `auto`;
- `rag_scope`/`knowledge_collection_id`/`source_scope_summary` не будут выставлены top-level.

**Step 3: Write minimal implementation**

Сделать две вещи:

1. В `agent_api.py` резолвить `effective_settings` до вызова decision/execution и использовать:

```python
effective_settings = resolve_effective_settings(_collect_request_control_plane(request))
runtime_mode = effective_settings["runtime_mode"]
rag_scope = effective_settings["rag_scope"]
knowledge_collection_id = effective_settings["knowledge_collection_id"]
```

2. В `execution_runtime.py` централизованно обогащать response:

```python
def _top_level_control_plane_fields(
    *,
    request: Dict[str, Any],
    effective_settings: Dict[str, Any],
) -> Dict[str, Any]:
    rag_scope = effective_settings.get("rag_scope", "off")
    session_doc_count = len(request.get("session_docs") or {})
    active_doc_ids = request.get("active_doc_ids") or []
    if rag_scope == "knowledge_base_rag":
        source_scope_summary = (
            "knowledge_base+session_overlay" if session_doc_count or active_doc_ids else "knowledge_base"
        )
    elif rag_scope == "session_rag":
        source_scope_summary = "session"
    else:
        source_scope_summary = "off"
    return {
        "rag_scope": rag_scope,
        "knowledge_collection_id": effective_settings.get("knowledge_collection_id"),
        "source_scope_summary": source_scope_summary,
        "model_profile": effective_settings.get("model_profile"),
    }
```

И подмешивать это в response и для `/orchestrate`, и для `/execute_orchestration`.

**Step 4: Run tests to verify they pass**

Run:

```bash
cd backend && pytest tests/test_agent_api_orchestrate.py tests/test_execution_runtime.py -q -k "effective_runtime_mode or top_level_control_plane"
```

Expected: PASS.

**Step 5: Checkpoint**

```bash
git diff -- backend/orchestrator/agent_api.py backend/orchestrator/execution_runtime.py backend/orchestrator/ui_control_plane.py backend/tests/test_agent_api_orchestrate.py backend/tests/test_execution_runtime.py
```

---

### Task 2: Превратить `/v1/chat/completions` в compatibility adapter над unified execution core

**Files:**
- Modify: `backend/orchestrator/agent_api.py`
- Test: `backend/tests/test_agent_api_openai_compat.py`

**Step 1: Write the failing tests**

Добавить новый test file для legacy endpoint.

Минимальный набор кейсов:

```python
def test_openai_chat_completions_routes_through_execute_orchestration(client, monkeypatch):
    called = {}

    async def fake_execute(request, deps=None):
        called["request"] = request
        return {
            "assistant_message": "compat answer",
            "trace_id": "t1",
            "state_ref": "trace:t1",
            "pending_action_id": None,
            "ui_effects": {"clear_pending_action": True},
            "sources": [],
            "effective_settings": {"runtime_mode": "auto"},
            "rag_scope": "off",
            "knowledge_collection_id": None,
            "source_scope_summary": "off",
        }

    monkeypatch.setattr("orchestrator.agent_api.execute_orchestration", fake_execute)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "llm-tools-platform",
            "messages": [{"role": "user", "content": "Привет"}],
        },
    )

    assert resp.status_code == 200
    assert called["request"]["message"] == "Привет"
```

```python
def test_openai_chat_completions_no_longer_uses_legacy_run_workflow_stream(...):
    # monkeypatch run_workflow_stream -> raise AssertionError
    # endpoint must still succeed
```

```python
def test_openai_chat_completions_streams_single_compat_answer_delta(...):
    # SSE содержит delta role/content и [DONE]
```

**Step 2: Run tests to verify they fail**

Run:

```bash
cd backend && pytest tests/test_agent_api_openai_compat.py -q
```

Expected:
- endpoint всё ещё зовёт `run_workflow_stream`;
- request mapping к unified execution core отсутствует.

**Step 3: Write minimal implementation**

В `agent_api.py`:

1. Добавить helper’ы:

```python
def _extract_latest_user_query(messages: List[Dict[str, Any]]) -> str: ...
def _discover_openai_attachments(user_query: str) -> List[FileAttachment]: ...
def _build_openai_compat_request(
    *,
    data: Dict[str, Any],
    user_query: str,
    attachments: List[FileAttachment],
) -> OrchestrationRequest: ...
```

2. Переписать `openai_completions(...)` так, чтобы он:
- строил `OrchestrationRequest`;
- резолвил `effective_settings`;
- вызывал `execute_orchestration(...)` с `_build_api_execution_dependencies(...)`;
- формировал OpenAI-compatible SSE поверх `assistant_message`.

3. Удалить execution usage из legacy path:
- не использовать `sessions`;
- не использовать `_get_or_create_session`;
- не использовать `run_workflow_stream`.

Допустимый временный компромисс:
- SSE остаётся, но answer можно отдавать одним assistant delta chunk вместо true token-by-token streaming.
- Этот workaround потом зафиксировать в `TASKS.md`.

**Step 4: Remove dead legacy execution code**

После перевода endpoint:
- удалить `sessions`;
- удалить `_get_or_create_session`;
- удалить `run_workflow_stream`;
- удалить из `agent_api.py` legacy comments и код session-level RAG/direct workflow dispatch, если они больше нигде не используются.

Сохранить только те утилиты, которые реально используются тестами или новым adapter path (`_build_multiturn_prompt`, `determine_document_order` — если ещё нужны).

**Step 5: Run tests to verify they pass**

Run:

```bash
cd backend && pytest tests/test_agent_api_openai_compat.py tests/test_agent_api_orchestrate.py tests/test_execution_runtime.py -q
```

Expected: PASS.

**Step 6: Checkpoint**

```bash
git diff -- backend/orchestrator/agent_api.py backend/tests/test_agent_api_openai_compat.py backend/tests/test_agent_api_orchestrate.py backend/tests/test_execution_runtime.py
```

---

### Task 3: Сделать API dependencies для doc-QA честно backend-совместимыми

**Files:**
- Modify: `backend/orchestrator/agent_api.py`
- Modify: `backend/orchestrator/execution_runtime.py`
- Test: `backend/tests/test_agent_api_orchestrate.py`
- Test: `backend/tests/test_execution_runtime.py`

**Step 1: Write the failing tests**

Добавить тест, который проверяет, что API execution deps не падают в пустые заглушки для doc-QA path.

Минимальный вариант:

```python
def test_execute_orchestration_api_doc_question_returns_honest_no_rag_message_without_stubbed_fake_sources():
    request = OrchestrationRequest(
        message="Что указано в документе?",
        assistant_mode="specific_tasks",
        session_docs={"doc.pdf": {"text": "штраф 10%"}},
        has_session_docs=True,
        active_doc_ids=["doc.pdf"],
        classifier_result={
            "intent": "document_question",
            "confidence": 0.9,
            "margin": 0.5,
            "needs_rag": True,
        },
    )
    response = asyncio.run(execute_orchestration_api(request))
    assert "RAG" in response["assistant_message"] or "проверяемые источники" in response["assistant_message"]
```

Цель: поведение должно быть честным и единым, а не случайным из-за stubbed deps.

**Step 2: Run tests to verify they fail**

Run:

```bash
cd backend && pytest tests/test_agent_api_orchestrate.py tests/test_execution_runtime.py -q -k "doc_question"
```

Expected: сейчас поведение строится на пустых заглушках и не отражает unified backend contract явно.

**Step 3: Write minimal implementation**

Не тащить Chainlit-specific retrieval в API path. Вместо этого:

- в `_build_api_execution_dependencies(...)` ввести честный compatibility behavior:
  - если API path не умеет index/retrieve session docs как Chainlit, он должен возвращать deterministic backend message про отсутствие backend retrieval adapter, а не молча пустые `sources=[]`;
  - вынести это в отдельный helper `build_api_doc_question_unavailable_fallback(...)`.

Пример:

```python
def _api_doc_question_unavailable_payload(query: str) -> Dict[str, Any]:
    return {
        "answer_text": (
            "Для этого API-совместимого пути session-document retrieval пока не подключён "
            "к backend adapter. Используйте Chainlit path или загрузите документы через "
            "целевой orchestration flow."
        ),
        "sources": [],
        "answer_mode": "retrieval_unavailable",
        "fallback_type": "retrieval_unavailable",
        "fallback_reason": "api_execution_dependencies_missing_rag_adapter",
        "confidence": 0.0,
        "confidence_label": "low",
        "confidence_method": "heuristic_v1",
        "confidence_version": "1",
    }
```

Это не финал `B3.33`, но это честный backend contract и отсутствие UI-specific magic.

**Step 4: Run tests to verify they pass**

Run:

```bash
cd backend && pytest tests/test_agent_api_orchestrate.py tests/test_execution_runtime.py -q -k "doc_question"
```

Expected: PASS, поведение явно backend-owned и без фальшивых источников.

**Step 5: Checkpoint**

```bash
git diff -- backend/orchestrator/agent_api.py backend/orchestrator/execution_runtime.py backend/tests/test_agent_api_orchestrate.py backend/tests/test_execution_runtime.py
```

---

### Task 4: Расследовать и стабилизировать `test_chainlit_streaming.py`

**Files:**
- Modify: `backend/orchestrator/chainlit_app.py`
- Modify: `backend/tests/test_chainlit_streaming.py`
- Optional Modify: `backend/services/model_manager/ums_client.py`
- Update: `TASKS.md`

**Step 1: Reproduce the hang and isolate the culprit**

Run:

```bash
cd backend && timeout 30s pytest tests/test_chainlit_streaming.py -q
```

Expected:
- текущая проблема воспроизводится (`124` после `.`).

**Step 2: Add a focused failing/diagnostic test or tighten teardown**

Если причиной окажется reload/import side effect, добавить минимальную защиту в тест:

```python
def test_chainlit_module_import_does_not_leave_background_tasks(...):
    ...
```

или:
- перестать reload’ить весь `orchestrator.chainlit_app`, если достаточно monkeypatch конкретных symbols;
- явно закрывать lingering async generators / clients;
- убрать import-time побочные эффекты из `chainlit_app.py`, если они остаются.

**Step 3: Implement minimal fix**

Предпочтительный порядок:
1. test-side cleanup, если проблема только в harness;
2. module-side fix, если `chainlit_app.py` реально оставляет фоновые таски/clients;
3. `ums_client` cleanup, если висит async stream client.

**Step 4: Verify the test exits cleanly**

Run:

```bash
cd backend && pytest tests/test_chainlit_streaming.py -q
```

Expected:
- pytest завершается нормально.

Если root cause не удастся закрыть полностью в этой фазе:
- сузить проблему до конкретного модуля/teardown path;
- зафиксировать рабочую гипотезу и временный workaround в `TASKS.md`.

**Step 5: Checkpoint**

```bash
git diff -- backend/orchestrator/chainlit_app.py backend/tests/test_chainlit_streaming.py backend/services/model_manager/ums_client.py TASKS.md
```

---

### Task 5: Обновить backlog/docs и прогнать phase-level regression suite

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`
- Modify: `docs/plans/2026-03-12-execution-review-corrective-plan.md`

**Step 1: Update TASKS and phase notes**

Зафиксировать:
- что `/v1/chat/completions` больше не отдельный execution world;
- какой временный workaround принят по SSE/streaming, если true token streaming пока не восстановлен;
- если API doc-QA path пока честно возвращает `retrieval_unavailable`, записать это как explicit follow-up к `B3.33`, а не как скрытую деградацию.

**Step 2: Run regression suite**

Run:

```bash
cd backend && pytest tests/test_agent_api_openai_compat.py tests/test_agent_api_orchestrate.py tests/test_execution_runtime.py tests/test_orchestration_runtime.py tests/test_chainlit_runtime_mode.py tests/test_document_analysis.py -q
```

Затем:

```bash
cd backend && pytest tests/test_chainlit_streaming.py tests/test_unified_model_server_streaming.py tests/test_unified_model_server_startup.py -q
```

Expected:
- phase-level suite проходит;
- hanging streaming test либо починен, либо честно зафиксирован как незакрытый blocker/follow-up с доказательством.

**Step 3: Final verification record**

Сохранить в рабочем отчёте:
- какие тесты запускались;
- что прошло;
- что осталось следующим;
- почему `B3.31` можно или нельзя считать закрытым полностью.

**Step 4: Checkpoint**

```bash
git diff -- TASKS.md docs/plans/2026-03-11-unified-recovery-and-ui-plan.md docs/plans/2026-03-12-execution-review-corrective-plan.md
```
