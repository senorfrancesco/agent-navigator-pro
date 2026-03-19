# План: Chainlit Elements Persistence

## Summary

Нужно закрыть warning:

`SQLAlchemyDataLayer storage client is not initialized and elements will not be persisted`

Цель фазы — включить минимально достаточную persistence для `Chainlit elements` (`cl.File`, `cl.Pdf` и другие file-backed элементы) в local/native runtime без перехода на внешний blob storage.

## Research Findings

Текущий `chainlit 2.9.6` `SQLAlchemyDataLayer` принимает optional `storage_provider`:

- `SQLAlchemyDataLayer(..., storage_provider=...)`

Если провайдер не передан, runtime логирует warning и не персистит elements.

Контракт storage provider минимальный:

- `upload_file(object_key, data, mime, overwrite, content_disposition)`
- `delete_file(object_key)`
- `get_read_url(object_key)`
- `close()`

В репозитории уже есть локальная файловая база для shared uploads:

- `UPLOADS_DIR` в [chainlit_app.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/chainlit_app.py)
- `backend/open_webui_uploads/`

Это означает, что безопасный narrow fix — локальный file-backed storage provider на базе `UPLOADS_DIR`, а не новый S3/GCS/MinIO слой.

## Scope

### In scope

- добавить local file storage provider для `Chainlit elements`;
- прокинуть его в `SQLAlchemyDataLayer` factory;
- обеспечить stable read URLs для persisted elements в local/native path;
- покрыть targeted tests;
- обновить `TASKS.md`.

### Out of scope

- production blob storage abstraction;
- S3/GCS/Azure integration;
- redesign `Chainlit` uploads;
- перенос пользовательских файлов в backend orchestrator store.

## Implementation Steps

### 1. Add local storage provider

Создать lightweight provider, совместимый с `BaseStorageClient`, например рядом с `chainlit_app.py` или в отдельном helper module.

Поведение:

- сохранять файлы в поддиректорию внутри `UPLOADS_DIR`, например `UPLOADS_DIR/chainlit-elements/`;
- `object_key` использовать как stable relative key;
- `upload_file()` возвращает:
  - `object_key`
  - `url`

### 2. Expose read URL for local files

Нужен стабильный read path, который браузер сможет открыть.

Рекомендованный safe slice:

- отдавать элементы через локальный HTTP route в `Chainlit/FastAPI` app-level path;
- либо использовать already-served static path, если это можно сделать без хрупкого coupling.

Главное:

- `get_read_url(object_key)` должен возвращать URL, доступный из браузера.

### 3. Wire provider into data layer

В data layer factory в [chainlit_app.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/chainlit_app.py):

- создавать local storage provider;
- передавать его в `_CompatibleSQLAlchemyDataLayer(...)`.

### 4. Add targeted tests

Добавить test-slice, например:

- `backend/tests/test_chainlit_elements_persistence.py`

Покрыть:

- `upload_file()` пишет файл на диск;
- `get_read_url()` возвращает корректный URL/path;
- data layer factory инициализирует storage provider;
- provider delete path работает безопасно.

### 5. Live smoke

После unit tests:

- поднять `./scripts/run_native.sh --no-attach`
- загрузить/сгенерировать `cl.File` или `cl.Pdf`
- убедиться, что warning про storage client исчез
- проверить, что element открывается после refresh/resume, где это возможно

## Files

### Code

- [backend/orchestrator/chainlit_app.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/chainlit_app.py)
- возможно новый helper module для local storage provider

### Tests

- `backend/tests/test_chainlit_elements_persistence.py`

### Tracking

- [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)

## Verification

```bash
pytest backend/tests/test_chainlit_elements_persistence.py -q
python -m py_compile ...
git diff --check
./scripts/run_native.sh --no-attach
tail -n 80 backend/orchestrator/chainlit.log
```

## Risks

1. `url` format должен совпасть с тем, что реально ожидает фронт `Chainlit`.
2. Нельзя ломать existing shared uploads path.
3. Файловый provider не должен удалять чужие файлы вне своей директории.

## Exit Criteria

Фаза считается закрытой, если:

- warning про `storage client is not initialized` исчезает;
- file-backed elements реально персистятся;
- после refresh/resume element остаётся доступным хотя бы в local/native path;
- fix покрыт targeted tests.

## Status

Completed on 2026-03-15.

Результат:
- добавлен local file-backed storage provider для `Chainlit` elements;
- provider подключён к `_CompatibleSQLAlchemyDataLayer`;
- `Chainlit` read path обслуживается через локальный route `/project/file/{object_key}`;
- targeted tests:
  - `backend/tests/test_chainlit_elements_persistence.py`
- живой native smoke подтвердил исчезновение старого storage warning и появление persisted file element в треде.
