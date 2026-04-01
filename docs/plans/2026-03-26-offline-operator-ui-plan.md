# 2026-03-26 — Offline Operator UI Plan

## Цель

Сделать отдельное операторское web-приложение для `deploy/offline_bundle/`, которое
становится основным entrypoint для build/export/deploy/runtime сценариев вместо
ручного набора shell-команд.

Приложение должно:
- читать и редактировать `deploy/offline_bundle/env.bundle`;
- отображать комплектность bundle и состояние runtime;
- запускать allowlisted bundle actions;
- иметь явную кнопку запуска bundle/runtime;
- вести оператора по шагам build-side и server-side flow.

Это не пользовательский продуктовый UI и не расширение `Chainlit`.
Это отдельный operator control plane.

## Scope

В scope входят:
- `deploy/offline_bundle/env.bundle`
- `deploy/offline_bundle/env.bundle.example`
- `deploy/offline_bundle/compose.offline.yaml`
- `deploy/offline_bundle/manifest.template.json`
- generated `deploy/offline_bundle/manifest.json`
- `deploy/offline_bundle/images/`
- `deploy/offline_bundle/models/`
- `deploy/offline_bundle/state/`
- `deploy/offline_bundle/host_packages/`
- bundle validation / preflight / deploy actions
- Docker compose lifecycle и логирование

В scope не входят:
- продуктовый чатовый UX `Chainlit`
- глубокий рефакторинг backend orchestration logic
- замена bundle contract на неявную внутреннюю логику UI
- произвольный shell от пользователя

## Формат продукта

- Отдельное web UI-приложение.
- Работает как operator console рядом с offline bundle.
- Может запускаться и на connected build-машине, и на offline/server host.
- Имеет одну главную action-кнопку для запуска runtime.

## Product Principles

- UI не скрывает состояние системы, а делает его видимым.
- UI не заменяет bundle contract, а работает поверх него.
- Все действия должны быть типизированы и предсказуемы.
- Build-side и server-side режимы должны быть разделены явно.
- Ошибки должны быть привязаны к шагу flow, а не теряться в логе.

## Основные сценарии

### 1. Build-machine scenario

Оператор:
- открывает UI;
- видит статус `wheelhouse`, `host_packages`, `images`, `models`, `state`, `manifest`;
- редактирует `env.bundle`, если нужно;
- запускает `build_wheelhouse`, `build_host_apt_bundle`, `export_images`, `export_models`, `export_state`;
- генерирует `manifest.json`;
- валидирует bundle;
- собирает архив для переноса на сервер.

### 2. Server scenario

Оператор:
- открывает UI на сервере;
- видит статус bundle и runtime;
- выполняет `validate_bundle`, `preflight_runtime`, `load_images`, `install_host_apt_bundle`;
- запускает runtime одной кнопкой;
- видит `compose ps`, health, GPU/runtime status и логи;
- может остановить или перезапустить runtime.

## Обязательные разделы UI

### Overview

- готовность bundle;
- статус сервисов;
- GPU/Docker/runtime summary;
- последние проверки;
- quick actions.

### Env

- структурированный редактор `env.bundle`;
- группировка по секциям;
- diff against `env.bundle.example`;
- inline validation;
- предупреждения по placeholder-значениям;
- save / reset / validate.

### Artifacts

- `images`
- `models`
- `state`
- `host_packages`
- `manifest`
- completeness, size, mtime, missing signals

### Build

- `build_wheelhouse`
- `build_host_apt_bundle`
- `export_images`
- `export_models`
- `export_state`
- `generate_manifest`
- archive packaging

### Deploy

- `validate_bundle`
- `preflight_runtime`
- `load_images`
- `install_host_apt_bundle`
- главная кнопка запуска runtime

### Services

- `compose ps`
- health status
- stop / restart / recreate
- профили `monitoring` и `vllm`

### Logs

- tail по сервисам:
  - `chainlit`
  - `agent-api`
  - `document-server`
  - `legal-server`
  - `ums`
- фильтрация по сервису и уровню

## Action Model

UI не должен запускать произвольный shell.

Нужен allowlisted action layer:
- `validate_bundle`
- `preflight_runtime`
- `verify_runtime`
- `load_images`
- `install_host_apt_bundle`
- `compose_up`
- `compose_down`
- `compose_restart`
- `build_wheelhouse`
- `build_host_apt_bundle`
- `export_images`
- `export_models`
- `export_state`
- `generate_manifest`
- `package_bundle_archive`

Каждый action должен иметь:
- явные входные параметры;
- понятный precondition check;
- structured result;
- stdout/stderr/log summary;
- machine-readable success/failure state.

## Кнопка запуска

Главная action-кнопка должна запускать канонический flow:
1. Проверить `env.bundle`
2. Проверить комплектность bundle
3. При необходимости загрузить images
4. Выполнить preflight
5. Поднять compose
6. Показать итоговый health summary

Если шаг падает:
- UI должен остановиться на этом шаге;
- показать ошибку именно этого шага;
- предложить следующий корректный action, а не просто “посмотрите логи”.

## Data Model

Нужны следующие view-модели:

- `BundleStatus`
  - env status
  - manifest status
  - images status
  - models status
  - state status
  - host_packages status

- `RuntimeStatus`
  - compose services
  - health
  - ports
  - GPU/runtime status

- `EnvSection`
  - ports
  - runtime
  - models
  - uploads/state paths
  - UMS settings
  - monitoring settings

- `ActionResult`
  - started_at
  - finished_at
  - status
  - stdout tail
  - stderr tail
  - structured diagnostics

## UX Requirements

- Не превращать UI в “generic admin panel”.
- Должен быть явный visual hierarchy:
  - readiness
  - warnings
  - destructive actions
  - current mode
- Ошибки должны быть читаемыми и короткими.
- Большой лог не должен забивать экран; нужен summary + expandable tail.
- Должны быть empty states и remediation hints.

## Безопасность

- Не давать пользователю вводить произвольную команду shell.
- Все destructive actions подтверждать.
- Не писать секреты env в UI-логи.
- Разделить read-only diagnostics и mutating actions.

## MVP

MVP должен включать:
- `Overview`
- `Env`
- `Deploy`
- `Logs`
- `Services`
- чтение/запись `env.bundle`
- `validate_bundle`, `preflight_runtime`, `load_images`, `compose up/down`, `verify_runtime`
- главную кнопку запуска runtime

## Phase 2

- build-side вкладка
- artifact inspector
- package/archive actions
- guided remediation для missing artifacts
- profile-aware launch presets

## Phase 3

- polished design system
- сохранённые operator profiles
- история запусков и проверок
- optional TUI companion

## Testing Strategy

- unit tests для env parser/editor
- unit tests для action layer
- integration tests для compose/runtime status layer
- UI smoke tests для основных flow
- tests на error states:
  - missing image tar
  - missing model path
  - invalid env
  - unhealthy service

## Критерии готовности

### MVP ready

- Оператор может без shell стартовать runtime.
- Оператор может увидеть, что отсутствует, до старта.
- Оператор может редактировать `env.bundle`.
- Оператор видит статус сервисов и логи.
- UI не ломает bundle contract.

### Production-ready

- Build-machine и server mode разделены явно.
- Все канонические bundle actions доступны через UI.
- Ошибки привязаны к шагам.
- Документация offline bundle обновлена под новый entrypoint.
- Shell scripts остаются fallback, но не primary UX.

## Рекомендуемый порядок реализации

1. Выбрать UI стек и runtime модель приложения
2. Спроектировать экраны и operator flow
3. Вынести bundle actions в typed application layer
4. Реализовать env editor
5. Реализовать runtime/services/logs views
6. Реализовать deploy flow и главную action-кнопку
7. Добавить tests
8. Обновить docs/runbooks
