# План: Chainlit SQLite Locking Hardening

## Summary

Живой native smoke после фикса `elements` persistence подтвердил новый runtime-хвост:

- `sqlite3.OperationalError: database is locked`
- затем ломается часть thread persistence / auth-resume path для только что созданного диалога

Это не новая архитектурная фаза, а narrow hardening для текущего local `Chainlit SQLite` persistence.

## Scope

### In scope

- уменьшить вероятность `database is locked` в local/native path;
- включить SQLite-friendly настройки для `Chainlit` data layer:
  - `WAL`
  - `busy_timeout`
  - `timeout` в connect args;
- покрыть targeted tests;
- зафиксировать результат в `TASKS.md`.

### Out of scope

- миграция `Chainlit` persistence в Postgres;
- rewrite data layer;
- смена backend-owned orchestration store;
- глобальный redesign thread/history persistence.

## Proposed Fix

1. В `_bootstrap_chainlit_sqlite_schema()`:
   - выставить `PRAGMA journal_mode=WAL`
   - выставить `PRAGMA synchronous=NORMAL`

2. В `_CompatibleSQLAlchemyDataLayer`:
   - для SQLite добавлять `connect_args={"timeout": ...}`
   - вешать `connect` hook на engine для `PRAGMA busy_timeout`

3. Тесты:
   - bootstrap включает WAL
   - subclass передаёт timeout в base SQLAlchemyDataLayer

4. Verification:
   - targeted pytest
   - `./scripts/run_native.sh --no-attach`
   - tail `backend/orchestrator/chainlit.log`

## Exit Criteria

- `database is locked` не воспроизводится на базовом local smoke;
- sidebar/thread persistence не ломается на fresh thread из-за lock error;
- fix покрыт targeted tests.

## Status

Completed on 2026-03-15.

Результат:
- bootstrap включает `WAL` и `synchronous=NORMAL`;
- compatibility SQLAlchemy data layer для SQLite добавляет `timeout` и `busy_timeout`;
- targeted tests обновлены в `backend/tests/test_chainlit_persistence_schema.py`;
- повторный native + Playwright smoke больше не воспроизводит `database is locked` и `Authorization for the thread failed` в `backend/orchestrator/chainlit.log`.
