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
- Считать `Chainlit` основным UI, а `Open WebUI` — legacy-путём. Не откатывать документацию и инструкции обратно к `Open WebUI-first`.
- Все важные решения, спорные места, временные обходы, архитектурные компромиссы и найденный техдолг
  фиксировать в `TASKS.md` по ходу работы, если речь идёт об общем проектном backlog, а не о ночном журнале выполнения.
- Если в ходе сессии появляется временный workaround, его нужно не только озвучить пользователю,
  но и записать в `TASKS.md` как отдельный follow-up, если пользователь явно не запретил это делать.
- При вызовах `notebooklm` MCP не добавлять параметр `timeout`, если пользователь явно не просил и нет подтвержденной технической необходимости.
- Для широких structural changes работать по фазам. По умолчанию одна фаза должна затрагивать не более `5` файлов; более широкий scope допустим только если он явно зафиксирован в плане.
- Перед большим структурным рефакторингом допустим отдельный preparatory cleanup slice. Не смешивать cleanup с функциональным изменением без явной причины.
- Нельзя объявлять задачу завершённой без реальной project-aware verification по затронутому слою:
  - Python: `python -m py_compile` и/или targeted `pytest`
  - Frontend JavaScript: `node --check`
  - Shell: `bash -n`
  - docs / общие текстовые правки: `git diff --check`
- Перед каждым редактированием перечитывать целевой файл. После редактирования перечитывать его снова и убеждаться, что правка применилась корректно.
- После длинной сессии или смены фокуса перечитывать связанные файлы и не полагаться на память о старом состоянии.
- При работе с большими файлами читать их чанками и не предполагать, что один read показал весь файл. Это особенно важно для `backend/orchestrator/*`.
- Если результат поиска или grep подозрительно мал, повторять поиск с более узким scope и учитывать возможную усечённость вывода инструмента.
- При rename/refactor не ограничиваться одним `rg`. Отдельно проверять прямые вызовы, type-level references, string literals, re-exports, tests и mocks.
- Если архитектурный дефект прямо мешает текущей задаче, его нужно либо исправить в рамках текущего slice, либо зафиксировать в `TASKS.md` как follow-up. Не превращать локальную задачу в uncontrolled refactor без явного решения.
- Для изменений, затрагивающих более `5` независимых файлов, рассматривать декомпозицию на независимые slices или подзадачи, а не расширять один diff без границ.
- `TASKS_NIGHT.md` и `workflow.yaml` считать устаревшими policy-артефактами. Не использовать их как source-of-truth для новых agent rules; актуальные правила должны жить в `AGENTS.md`.
