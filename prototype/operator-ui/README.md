# Operator UI Shell

Backend-served shell для unified runtime-control operator UI.

Расположение:
- `prototype/operator-ui/index.html`

Что уже покрыто:
- равноправные пути запуска: `Нативный запуск` и `Offline Bundle / контейнерный запуск`
- состояния доступности и модалка `Почему недоступно?`
- path-aware редактирование конфига с явным `Применить изменения`
- сводка по железу, рекомендуемым значениям и свежести источников
- более реалистичные сервисы, логи, диагностика и allowlisted действия
- отдельная вкладка `Сборка / Деплой` с режимами:
  - `Сборка bundle`
  - `Импорт / деплой`
  - нижним логом по этапам
  - привязкой к каноническим `deploy/offline_bundle/scripts`
- observability summary и deep links в `Grafana` / `Prometheus`

Быстрый локальный просмотр:

```bash
npm run prototype:operator-ui
```

Потом открыть:
- `http://127.0.0.1:4173`

Backend-served запуск:

- когда поднят `backend/orchestrator/agent_api.py`, тот же UI доступен по:
  - `http://127.0.0.1:8000/operator-ui/`
- ассеты отдаются из:
  - `/operator-assets/*`
- гидратация состояния идёт из:
  - `/operator/state`
- каталог и запуск действий идут через:
  - `/operator/actions/catalog`
  - `/operator/actions/run`
  - `/operator/jobs/{job_id}`
- явное применение конфига идёт через:
  - `/operator/config/{path_key}/apply`
- сводка наблюдаемости и переходы идут через:
  - `/operator/metrics/summary`
  - `/operator/metrics/services`
  - `/operator/metrics/deploy`
  - `/operator/links/grafana`

Словарь терминов:

- `docs/plans/2026-03-29-operator-ui-i18n-map.md`

E2E smoke:

```bash
BASE_URL=http://127.0.0.1:8000 npx playwright test tests/e2e/operator-ui/smoke.spec.ts
```

Модель доставки:
- текущий shell остаётся `web-first`: это HTML/CSS/JS поверхность, которую можно раздавать локально или через backend
- UI не зависит от Chainlit и может позже стать отдельным web app
- если позже понадобится desktop shell, тот же UI можно упаковать в `Electron` или `Tauri` без смены operator UX-модели
