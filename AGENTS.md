# Repository Guidelines

## Project Structure & Module Organization
`backend/` contains the Python application code. Use `backend/orchestrator/` for the FastAPI entrypoint and LangGraph workflows, and `backend/services/` for the document, legal, model-manager, and hardware services. Put tests in `backend/tests/` using the existing `test_*.py` pattern. Shared uploads used by the UI live in `backend/open_webui_uploads/`. Repo-level assets include `docker-compose.yaml`, `scripts/`, `docs/`, and `for_cli/`.

## Build, Test, and Development Commands
- `cd backend && pip install -r requirements.txt`: install backend dependencies.
- `cd backend && pytest tests/ -v -m "not integration"`: run the default unit test suite.
- `cd backend && pytest tests/test_e2e_equipment.py -v -m integration`: run integration coverage against live services.
- `cd backend && uvicorn orchestrator.agent_api:app --reload --port 8000`: start the API locally for workflow work.
- `./scripts/run_all.sh`: start the recommended local stack (`tmux` + backend services + Chainlit).
- `docker compose up -d chainlit`: start the main Chainlit UI on port `3000`.
- `docker compose logs --tail=200 chainlit`: inspect Chainlit runtime logs.
- `docker compose up -d --build --force-recreate chainlit`: rebuild Chainlit when `.chainlit`, `public/`, `Dockerfile.chainlit`, or `chainlit_app.py` changed.
- `docker compose --profile legacy up open-webui`: start the legacy Open WebUI path when needed.

## Coding Style & Naming Conventions
Follow existing Python style: 4-space indentation, type hints on public interfaces, and `snake_case` for modules, functions, and variables. Keep FastAPI and workflow code split by domain instead of creating large utility files. Class names use `PascalCase`; constants use `UPPER_SNAKE_CASE`. No formatter or linter is enforced in the repo today, so keep imports tidy and match surrounding style before submitting changes.

## Testing Guidelines
Pytest is configured in `backend/pytest.ini` with `asyncio_mode = auto` and an `integration` marker. Add new tests under `backend/tests/` and name files `test_<feature>.py`. Prefer narrow unit tests first, then add integration tests only when behavior depends on running microservices, real uploads, or model servers. Before opening a PR, run the relevant targeted test file plus `pytest tests/ -v -m "not integration"`.
For Chainlit/UMS runtime work, also prefer targeted stability checks such as:
- `pytest backend/tests/test_chainlit_streaming.py backend/tests/test_unified_model_server_streaming.py backend/tests/test_unified_model_server_startup.py -q`

## Commit & Pull Request Guidelines
Match the current commit history: use concise Conventional Commit prefixes such as `feat(agent): ...`, `fix(parser): ...`, `refactor(...): ...`, or `docs: ...`. Keep each commit scoped to one logical change. PRs should include a short summary, affected services or workflows, linked issue or task ID when available, and screenshots or sample responses for UI-facing changes. Call out any required `.env` or model-path changes explicitly.

## Security & Configuration Tips
Do not commit `.env`, model weights, uploaded documents, or generated logs. Treat files in `backend/open_webui_uploads/` as sensitive user data. When editing Docker or service URLs, preserve the host/container split documented in `README.md` and `docker-compose.yaml`.
Do not commit local bootstrap artifacts such as `test_logs/`, temporary uploaded documents, or ad-hoc Node/Playwright setup unless the repo is intentionally adopting them as supported tooling.

## Agent-Specific Instructions
Respond to repository collaborators in Russian unless a task explicitly requires another language.
- Каноническая основная ветка репозитория — `v3.0`. Не использовать `main` как базовую ветку для новых работ и не восстанавливать старую branch-модель без явного решения.
- Считать `Chainlit` основным UI, а `Open WebUI` — legacy-путём. Не откатывать документацию и инструкции обратно к `Open WebUI-first`.
- Все важные решения, спорные места, временные обходы, архитектурные компромиссы и найденный техдолг
  фиксировать в `TASKS.md` по ходу работы, если речь идёт об общем проектном backlog, а не о ночном журнале выполнения.
- Если в ходе сессии появляется временный workaround, его нужно не только озвучить пользователю,
  но и записать в `TASKS.md` как отдельный follow-up, если пользователь явно не запретил это делать.
- При вызовах `notebooklm` MCP не добавлять параметр `timeout`, если пользователь явно не просил и нет подтвержденной технической необходимости.

## Night Autonomous Mode
Использовать этот режим, когда агент работает долго без оперативного участия пользователя.

- Перед началом ночного цикла обязательно читать:
  - `AGENTS.md`
  - `TASKS_NIGHT.md`
  - `workflow.yaml`
- `TASKS.md` ночью считать только общим backlog-реестром. Все ночные checkpoint'ы, progress notes, blocker'ы, workaround'ы и follow-up'ы писать не в `TASKS.md`, а в главу `ЛОГИ` в конце `TASKS_NIGHT.md`.
- Работать только по первому незавершённому блоку из `TASKS_NIGHT.md`, не распараллеливать независимые большие направления без явной записи в плане.
- Приоритет ночью:
  - `scripts/`
  - `docs/`
  - `README.md`
  - `TASKS_NIGHT.md`
  - `workflow.yaml`
- Не влезать ночью в широкие refactor'ы `backend/orchestrator`, `backend/services`, `docker-compose.yaml` или runtime-critical state, если это не требуется задачей прямо и не описано в `TASKS_NIGHT.md`.
- Любую risky-операцию считать запрещённой по умолчанию:
  - `git reset --hard`
  - удаление файлов вне временных артефактов агента
  - массовые переименования
  - миграции схемы
  - смена базового runtime path
  - push в удалённый репозиторий без явного запроса
- Любой новый обходной путь, допущение, частичный фикс, blocker и progress note ночью записывать в главу `ЛОГИ` в конце `TASKS_NIGHT.md`.
- После каждого подблока ночью обязательно:
  - обновить статус в `TASKS_NIGHT.md`
  - дописать запись в главу `ЛОГИ`
  - прогнать минимальную релевантную проверку
  - не объявлять блок завершённым без фактической верификации
- Если задача упирается в один из стоп-факторов, агент должен остановиться на safe checkpoint и записать blocker вместо рискованного продолжения:
  - нужен секрет, которого нет в репозитории
  - нужен доступ к внешней сети или реальному аккаунту
  - нужна миграция/удаление данных
  - нужно менять более 5 файлов вне заявленного scope
  - verification нестабильна и причина не локализована
- Commit ночью допустим только если выполнены все условия:
  - завершён один логический slice
  - есть релевантные проверки
  - commit не смешивает unrelated diff
  - message в conventional format
- Если slice не завершён, оставлять изменения в рабочем дереве допустимо, но нужно явно отметить это в `TASKS_NIGHT.md`.
