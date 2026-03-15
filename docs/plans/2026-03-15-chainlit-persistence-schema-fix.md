# План: Chainlit Persistence Schema Fix

## Summary

Нужно починить compatibility между текущим `Chainlit` runtime и локальным SQLite bootstrap schema в [chainlit_app.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/chainlit_app.py), чтобы:

- история тредов реально сохранялась;
- sidebar перестал показывать `Диалоги не найдены` после живого чата;
- `steps` и `threads` писались без `sqlite3.OperationalError` / `sqlite3.ProgrammingError`;
- `Chainlit` data layer оставался optional и lightweight, без полного переписывания persistence.

## Research Findings

Живой smoke и [chainlit.log](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/chainlit.log) уже показали три конкретные проблемы:

1. `threads.tags` пишется как Python `list`, а в SQLite bind уходит без сериализации:
   - `Error binding parameter 6: type 'list' is not supported`
2. В bootstrap schema отсутствует `steps.command`:
   - `table steps has no column named command`
3. В bootstrap schema отсутствует `steps.defaultOpen`:
   - `table steps has no column named defaultOpen`

Текущий bootstrap в [chainlit_app.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/chainlit_app.py) создаёт минимальные таблицы, но они больше не соответствуют полям, которые реально пишет установленный `chainlit` data layer.

## Scope

### In scope

- обновить SQLite bootstrap schema в `chainlit_app.py` под текущий `Chainlit` runtime contract;
- сделать migration-safe `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`-style upgrade для уже существующей БД;
- сериализовать list-like поля (`tags`) в JSON-compatible TEXT перед записью, если bootstrap/data layer оставляет это на стороне SQLite;
- добавить targeted tests на schema bootstrap / migration / thread-step persistence compatibility;
- обновить `TASKS.md` с root cause и фиксом.

### Out of scope

- полный rewrite `Chainlit` data layer;
- замена SQLite на Postgres для chat persistence;
- перенос chat history ownership в backend orchestrator store;
- redesign sidebar UX.

## Implementation Steps

### 1. Reconcile schema with actual Chainlit writes

В [chainlit_app.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/chainlit_app.py):

- пересмотреть `_bootstrap_chainlit_sqlite_schema()`;
- довести `threads` и `steps` до реальных полей, которые использует текущий `SQLAlchemyDataLayer`;
- минимум добавить:
  - `steps.command`
  - `steps.defaultOpen`
- проверить, не нужны ли ещё missing columns для `elements` / `threads`.

### 2. Add migration-safe upgrade path

Текущий bootstrap не должен полагаться только на `CREATE TABLE IF NOT EXISTS`.

Нужно:

- читать `PRAGMA table_info(...)`;
- если таблица уже существует, добавлять отсутствующие колонки через `ALTER TABLE`;
- не разрушать существующую локальную БД;
- сделать bootstrap idempotent.

### 3. Fix list serialization for tags

Нужно закрыть `threads.tags` bind error.

Возможные места фикса:

- pre-write adapter around `SQLAlchemyDataLayer`, если это проще и безопаснее;
- либо schema/bootstrap-compatible normalization path, если list field проходит через local code.

Цель:

- в SQLite должен попадать JSON string / TEXT, а не raw Python list.

### 4. Add targeted tests

Добавить новый тестовый срез, например:

- `backend/tests/test_chainlit_persistence_schema.py`

Покрыть:

- bootstrap на пустой SQLite БД;
- upgrade существующей старой схемы;
- наличие нужных колонок в `threads` и `steps`;
- сохранение thread metadata с `tags`;
- отсутствие ошибок на `command/defaultOpen`.

### 5. Validate with live smoke

После unit-level фикса:

- поднять native stack;
- открыть Chainlit;
- создать новый чат;
- отправить starter или обычное сообщение;
- проверить, что history/sidebar больше не разваливаются;
- убедиться, что в `chainlit.log` исчезли ошибки по `tags`, `command`, `defaultOpen`.

## Files

### Code

- [backend/orchestrator/chainlit_app.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/chainlit_app.py)

### Tests

- [backend/tests/test_chainlit_runtime_mode.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_chainlit_runtime_mode.py)
- `backend/tests/test_chainlit_persistence_schema.py` (new)

### Tracking

- [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)

## Verification

### Targeted

```bash
pytest backend/tests/test_chainlit_persistence_schema.py -q
pytest backend/tests/test_chainlit_runtime_mode.py -q
python -m py_compile backend/orchestrator/chainlit_app.py backend/tests/test_chainlit_persistence_schema.py
git diff --check
```

### Live smoke

```bash
./scripts/stop_native.sh
./scripts/run_native.sh --no-attach
```

Далее:

- открыть `http://localhost:3000`
- создать чат
- отправить сообщение
- проверить sidebar/history
- посмотреть:

```bash
tail -n 120 backend/orchestrator/chainlit.log
```

## Risks

1. `Chainlit` может писать больше полей, чем видно по текущим ошибкам.
2. SQLite migration logic легко сделать неидемпотентной.
3. Fix для `tags` нельзя делать ad hoc только под один SQL path, если это сломает уже существующую data layer semantics.

## Exit Criteria

Фаза считается закрытой, если:

- `threads` и `steps` больше не падают на `tags`, `command`, `defaultOpen`;
- sidebar/history реально появляются после живого чата;
- bootstrap upgrade безопасен для существующей SQLite БД;
- фикс покрыт unit tests и подтверждён живым native smoke.
