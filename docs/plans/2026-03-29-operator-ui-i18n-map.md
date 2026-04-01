# Operator UI I18N Map

Назначение: единый словарь для `prototype/operator-ui`, чтобы frontend-shell, backend-derived copy и operator API не расходились по терминам.

## Правила

- Навигация, CTA, статусы, warnings, diagnostics и пустые состояния пишутся по-русски.
- Английский сохраняется только для:
  - `env` keys;
  - `script` names;
  - файловых путей;
  - URL и API paths;
  - канонических metric names, если они показываются дословно.
- Если термин уже закреплён как продуктовый или операционный literal, он не переводится в самом артефакте, но оборачивается русским контекстом.

## Навигация

| Canonical term | UI label |
| --- | --- |
| Overview | Обзор |
| Launch | Запуск |
| Config | Конфиг |
| Services | Сервисы |
| Deploy / Build | Сборка / Деплой |
| Dev Actions | Действия |

## Runtime и состояния

| Canonical term | UI label |
| --- | --- |
| Native Runtime | Нативный запуск |
| Offline Bundle / Container Runtime | Offline Bundle / контейнерный запуск |
| available | Готов |
| partial | Частично |
| unavailable / blocked | Недоступно |
| running | В работе |
| degraded | Снижено |
| ready | Готово |
| generated | Сгенерировано |
| stale | Устарело |
| present | На месте |
| missing | Отсутствует |

## CTA

| Canonical term | UI label |
| --- | --- |
| Apply changes | Применить изменения |
| Open config | Открыть конфиг |
| Open logs | Посмотреть логи |
| Why unavailable? | Почему недоступно? |
| Open in Grafana | Открыть в Grafana |
| Open in Prometheus | Открыть в Prometheus |
| Refresh state | Перезагрузить состояние |
| Run smoke | Запустить smoke |

## Observability и diagnostics

| Canonical term | UI label |
| --- | --- |
| Observability | Наблюдаемость |
| Metrics summary | Сводка метрик |
| Service health | Здоровье сервисов |
| Parser diagnostics | Диагностика парсинга |
| Runtime blockers | Блокеры запуска |
| Recent activity | Последние действия |
| Source files | Источники |

## Применение

- `prototype/operator-ui/app.js` остаётся первой точкой применения этого словаря на frontend.
- `backend/orchestrator/operator_ui_api.py`, `operator_runtime_service.py` и `operator_deploy_service.py` должны использовать те же термины в user-facing payload fields.
- Любой новый user-facing label сначала добавляется сюда, затем встраивается в UI shell или backend state builder.
