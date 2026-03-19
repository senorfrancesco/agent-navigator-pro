## B3.11 — Убрать JSON/XML salvage из DEBUG-POLISH

**Status:** Implemented on 2026-03-13.

### Goal

Убрать ad hoc salvage/repair parsing из `DEBUG-POLISH` path в equipment workflow и заменить его на reusable structured-output helper на backend уровне.

### Current problem

Сейчас `_polish_items_specs_llm()` в `equipment.py`:

- заставляет модель возвращать XML;
- использует regex-based parse / code-fence stripping;
- живёт на `print("[DEBUG-POLISH] ...")` и salvage-like tolerant parsing;
- остаётся отдельной частной схемой, а не reusable platform-level structured-output path.

### Scope

В этой фазе:

1. Добавить reusable helper для strict structured model output:
   - extract response text
   - strict JSON parse
   - type validation
2. Перевести equipment `DEBUG-POLISH` с XML на strict JSON contract.
3. Обновить unit tests для polisher path.

### Non-goals

- не переписывать все `parse_json_garbage` места в `compare` / extract / eval;
- не внедрять полноценный schema-guided decoding в UMS;
- не менять весь equipment workflow.

### Implementation steps

#### Step 1. Shared helper

Добавить backend-level helper, например:

- `backend/orchestrator/structured_output.py`

Минимальные функции:

- `extract_model_text(response)`
- `parse_strict_json(text, expected_type=...)`

Без salvage:

- без regex extraction из середины текста
- без tolerant repair
- без JSON garbage cleanup

#### Step 2. Equipment polisher

В `backend/orchestrator/workflows/equipment.py`:

- заменить XML prompt на strict JSON:
  - `{"results": [{"id": 0, "text": "..."}, ...]}`
- заменить `_parse_polish_xml_results(...)` на strict JSON parser
- сохранить deterministic id mapping
- сохранить fallback на `_format_specs_fallback(...)`, если structured output invalid

#### Step 3. Tests

Обновить:

- `backend/tests/test_equipment_workflow.py`

Нужные кейсы:

1. correct JSON result maps by `id`
2. missing expected `id` -> invalid
3. malformed structured output -> fallback
4. item order in response can differ from request
5. no code-fence/markdown salvage path is required

### Verification

Targeted:

```bash
pytest backend/tests/test_equipment_workflow.py -q -k "polish or structured"
```

Phase regression:

```bash
cd backend && pytest tests/ -q -m "not integration"
```

Static:

```bash
python -m py_compile backend/orchestrator/structured_output.py \
  backend/orchestrator/workflows/equipment.py \
  backend/tests/test_equipment_workflow.py
git diff --check
```

### Done when

- DEBUG-POLISH path не использует tolerant XML/JSON salvage;
- reusable structured-output helper существует на backend уровне;
- equipment polisher работает через strict JSON contract;
- targeted tests зелёные;
- статус зафиксирован в `TASKS.md`.

### Implementation result

- Добавлен reusable backend helper:
  - `backend/orchestrator/structured_output.py`
- `equipment._polish_items_specs_llm()` переведён на strict JSON response contract:
  - `schema_version`
  - `ok`
  - `data.results`
  - `error`
- XML/code-fence tolerant parsing удалён из `DEBUG-POLISH` path.
- Invalid structured output теперь fail-closed и уходит в уже существующий fallback.

### Verification evidence

- `pytest backend/tests/test_equipment_workflow.py -q -k "polish"` -> `13 passed`
- `pytest backend/tests/test_equipment_workflow.py -q` -> `84 passed`
- `python -m py_compile backend/orchestrator/structured_output.py backend/orchestrator/workflows/equipment.py backend/tests/test_equipment_workflow.py`
- `git diff --check`

Pragmatic note:
- broad `cd backend && pytest tests/ -q -m "not integration"` probe still occasionally hits the already-known pytest tail-hang after the suite finishes; this phase did not introduce a new failing surface.
