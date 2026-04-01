# 2026-03-26 — Native Dev Operator UI Plan

## Цель

Сделать отдельное операторское приложение для native development/runtime path, чтобы
локальная разработка не требовала постоянного переключения между shell, `.env`,
ручным запуском сервисов и логами.

Это приложение должно:
- упростить локальный запуск dev-среды;
- показывать состояние backend/UI/services;
- помогать переключать режимы разработки;
- работать как developer control plane, а не как production deploy console.

## Scope

В scope входят:
- native/local runtime path проекта;
- developer env/config management;
- локальный запуск сервисов;
- логи, health, быстрые рестарты;
- вспомогательные developer workflows.

В scope не входят:
- production/offline bundle deploy на сервер;
- замена продуктового `Chainlit` интерфейса;
- полный replacement IDE/editor workflows.

## Отличие от offline UI

Offline operator UI:
- bundle-first
- artifact/deploy/runtime-first
- рассчитан на build-machine и offline/server host

Native dev operator UI:
- developer-first
- быстрее, легче, меньше ceremony
- рассчитан на локальную разработку и проверку фич

## Основные сценарии

### 1. Быстрый старт dev-среды

Разработчик:
- открывает UI;
- видит состояние `.env`/runtime prerequisites;
- запускает нужный профиль:
  - backend only
  - chainlit + backend
  - compose profile
  - native helper flow
- видит, какие сервисы поднялись.

### 2. Работа с конфигом

Разработчик:
- редактирует локальные env-переменные;
- переключает runtime mode/profile;
- управляет model/backend settings;
- видит, какие значения реально применены.

### 3. Debugging

Разработчик:
- смотрит health и логи сервисов;
- перезапускает один сервис;
- видит status model server / document server / agent api / chainlit;
- быстро переходит к проблемному месту.

### 4. Local test/run workflows

Разработчик:
- запускает стандартные smoke checks;
- запускает subset тестов;
- видит structured output без ручного копания в shell.

## Принципы

- Это не “админка для продакшна”, а инструмент локальной работы разработчика.
- UX должен быть быстрее и легче, чем в offline UI.
- Не должно быть дублирования продуктового пользовательского UX.
- Нельзя превращать UI в arbitrary command launcher.

## Обязательные разделы UI

### Overview

- общий статус локальной среды;
- активные сервисы;
- текущий runtime mode;
- quick actions.

### Profiles

- presets:
  - `backend only`
  - `chainlit dev`
  - `compose local`
  - `native runtime`
  - `full local stack`
- запуск/остановка профилей

### Env

- редактор локального `.env` / `.env.native` / dev overrides;
- grouping by domain;
- validation;
- diff against examples/templates.

### Services

- статусы локальных процессов или compose services;
- restart/stop/start по одному сервису;
- health-check summary.

### Logs

- aggregated log view;
- фильтрация по сервису;
- tail и pause/follow mode.

### Dev Actions

- smoke checks;
- standard test entrypoints;
- cache cleanup helpers;
- common maintenance actions.

## Возможные action handlers

- `start_backend`
- `start_chainlit`
- `start_compose_profile`
- `stop_service`
- `restart_service`
- `show_health`
- `show_logs`
- `run_smoke_checks`
- `run_targeted_tests`
- `reload_env`

Все actions должны быть allowlisted и типизированными.

## Env / Config Model

Нужно поддержать:
- `.env.native`
- `.env.example`
- runtime-specific overrides

Группы настроек:
- ports
- model/runtime selection
- uploads/storage paths
- auth/dev flags
- debug/logging
- service URLs

## UX Requirements

- В локальной разработке нужна скорость, не ceremony.
- Экран должен открываться и давать ценность сразу.
- Должны быть быстрые кнопки:
  - `Start stack`
  - `Restart Chainlit`
  - `Restart Backend`
  - `Show Logs`
  - `Run Smoke`

## MVP

MVP должен включать:
- `Overview`
- `Profiles`
- `Env`
- `Services`
- `Logs`
- старт/стоп ключевых локальных сервисов
- быстрый просмотр health
- редактирование основных env-параметров

## Phase 2

- встроенные smoke/test actions
- richer profile system
- persistence of dev presets
- better log search/filtering

## Phase 3

- deep test integration
- richer diagnostics
- optional trace/metrics views
- optional companion terminal/TUI mode

## Testing Strategy

- unit tests для env/config parsing
- unit tests для profile/action layer
- integration tests для status/log adapters
- UI smoke tests для start/restart/log flows

## Критерии готовности

### MVP ready

- Разработчик может запускать основные локальные профили без ручного shell orchestration.
- Разработчик может видеть состояние сервисов и логи.
- Разработчик может менять ключевые env-настройки из UI.

### Production-ready for team use

- Профили стабильны и повторяемы.
- Health/log/status слой покрывает основные локальные сценарии.
- UI реально ускоряет ежедневную разработку, а не дублирует shell без выгоды.

## Рекомендуемый порядок реализации

1. Определить, какие native launch paths считаются каноническими
2. Спроектировать developer profiles
3. Вынести profile/actions layer
4. Реализовать env/config editor
5. Реализовать services/logs views
6. Добавить smoke/test actions
7. Добавить tests и docs
