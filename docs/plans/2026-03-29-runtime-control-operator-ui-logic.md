# 2026-03-29 — Unified Runtime-Control Operator UI Logic

## Цель

Собрать единый operator UI на базе существующих Stitch-экранов:

- `Launch Profiles`
- `Dev Actions & Maintenance`
- `Overview Dashboard v2`
- `Env & Config v2`
- `Services & Logs v2`

Этот UI не должен рекомендовать, какой runtime path “лучше”. Вместо этого он должен
показывать, какие runtime paths вообще доступны на текущей машине, откуда они
берут конфиг, что можно запустить, что заблокировано, и почему.

Цель — превратить существующую shell/runtime логику проекта в понятный UI control
plane, не ломая текущий contract скриптов, env-файлов и runtime status layers.

## Product Model

Центральная сущность UI — не profile сам по себе, а `runtime path availability`.

Есть два равноправных path:

1. `Native Runtime`
2. `Offline Bundle / Container Runtime`

Оба path должны быть видимы всегда.
Если path недоступен, его карточка не исчезает, а затемняется и остаётся
доступной для inspection.

UI опирается на существующие operational layers:

- `scripts/launcher.sh`
- runtime profiles
- hardware detection / preflight
- `backend/.env`, `backend/.env.native`, `backend/.env.runtime`
- `deploy/offline_bundle/*`
- service status / health / logs
- parser/runtime diagnostics

UI не создаёт новый скрытый источник правды. Он должен работать поверх этих
контрактов.

## Runtime Paths

### Native Runtime

Используется для developer/native path проекта.

Источники:

- launch source: `scripts/launcher.sh --target native`
- config sources:
  - `backend/.env`
  - `backend/.env.native`
  - `backend/.env.runtime`
  - `backend/.env.hardware.override` или выбранный override file

Типичные под-path профили:

- `backend only`
- `chainlit dev`
- `native runtime`
- `full local stack`

### Offline Bundle / Container Runtime

Используется для bundle/container/offline path.

Источники:

- launch source: `scripts/launcher.sh --target container`
- bundle/source roots:
  - `deploy/offline_bundle/`
  - related bundle env/config files

Типичные под-path профили:

- `compose local`
- `offline bundle runtime`
- `container validation path`

## Availability Model

Каждый runtime path имеет один из статусов:

- `available`
- `partial`
- `unavailable`

### available

Path можно запускать прямо сейчас.

### partial

Path частично готов: есть часть источников, но запуск возможен не полностью или
требует исправления/дозаполнения.

### unavailable

Path не может быть запущен на текущей машине из-за отсутствия ключевых файлов,
артефактов или обязательных prerequisites.

## Why Unavailable Pattern

Недоступность не объясняется только цветом карточки.

Поведение:

- карточка path остаётся видимой;
- визуально затемняется;
- primary launch action disabled;
- secondary action `Why unavailable?` открывает detail drawer/modal.

Drawer показывает:

- detected launch source
- detected config source
- required files/checks
- missing files/checks
- blocking reasons
- suggested remediation steps

Это обязательный UX pattern для обеих runtime cards.

## Hardware Layer

Hardware detection не выбирает path за пользователя и не навязывает recommendation.

Его роль:

- показать detected machine capabilities;
- показать, какие path вообще возможны;
- предложить safe env/runtime defaults;
- заполнить config tab значениями `suggested` vs `applied`.

UI должен показывать:

- detected device/hardware profile;
- relevant capabilities;
- suggested runtime/env values;
- currently applied values.

## Config Contract

UI не должен хранить параллельный configuration source of truth.

Принципы:

- UI читает реальные env/config files выбранного runtime path;
- UI показывает source path явно;
- пользователь меняет поля в UI;
- запись происходит только после явного действия `Apply` / `Save changes`;
- после apply UI показывает applied/effective values.

### Config Tabs / Groups

Минимальные группы:

- runtime/profile
- backend mode
- model/runtime paths
- ports and service URLs
- storage/uploads
- auth/dev flags
- parser / strict JSON controls

### Edit Flow

Для выбранного runtime path:

1. пользователь открывает `Env & Config`;
2. UI показывает source files и grouped fields;
3. пользователь меняет значения;
4. UI показывает dirty state;
5. пользователь нажимает `Apply`;
6. UI пишет изменения обратно в env/config source;
7. UI показывает updated effective values и last apply status.

Auto-save не используется.

## Screens and Their Roles

### 1. Overview Dashboard v2

Роль: общий operational summary.

Что должно показываться:

- доступность двух runtime paths;
- current active/applied path, если есть;
- hardware summary;
- config summary;
- service health summary;
- parser/runtime warnings;
- last action result;
- quick links в `Launch`, `Config`, `Services`, `Maintenance`.

### 2. Launch Profiles

Роль: главная точка запуска.

Главный паттерн:

- две большие runtime cards:
  - `Native Runtime`
  - `Offline Bundle / Container Runtime`

Каждая card должна показывать:

- availability status;
- launch source;
- config source;
- supported profiles;
- quick actions:
  - `Launch`
  - `Inspect Config`
  - `Why unavailable?`

Если path unavailable:

- card затемнена;
- `Launch` disabled;
- `Why unavailable?` остаётся доступным.

### 3. Env & Config v2

Роль: source-aware config editor.

Требования:

- path switcher сверху;
- явное отображение source files;
- grouped editable rows;
- suggested values from hardware/runtime detection;
- applied values рядом;
- button-based writeback through `Apply`.

Важно: пользователь должен понимать, какой path он редактирует и в какой
конкретно env/config source попадут изменения.

### 4. Services & Logs v2

Роль: verification и diagnostics.

Требования:

- статусы сервисов для выбранного path;
- readiness/health summary;
- aggregated logs;
- service filter;
- parser/runtime diagnostics как отдельный first-class блок, а не buried-only log tail.

Диагностика должна включать:

- parser issues;
- strict JSON failures/degraded path;
- config drift / missing sources;
- runtime readiness failures.

### 5. Dev Actions & Maintenance

Роль: allowlisted operator actions.

Действия:

- `Run Smoke`
- `Restart Backend`
- `Restart Chainlit`
- `Reload Env`
- `Inspect Parser Issues`
- `Cleanup / Repair`

UI не должен становиться arbitrary command launcher.

## Visual System Rules

Все 5 экранов должны перейти к одному design language.

Общие правила:

- один top status/header contour;
- одинаковые runtime cards;
- одинаковые status chips;
- одинаковый pattern для disabled state;
- одинаковый pattern для logs/terminal blocks;
- одинаковый pattern для warning/diagnostics cards.

Ключевые состояния:

- `available`
- `partial`
- `unavailable`
- `warning`
- `dirty`
- `applied`

Цветовая семантика:

- neon-lime: primary actions и good state
- cyan: info / source metadata / path provenance
- warm red-orange: warnings, parser/runtime blockers

## Stitch Redesign Intent

Следующий этап после этого документа — редизайн в Stitch по подходу
`Runtime Availability Cards`.

Для Stitch нужно сохранить текущий “kinetic terminal” характер проекта, но
перестроить hierarchy так, чтобы:

- `Launch Profiles` стал центром выбора runtime path;
- `Overview Dashboard v2` стал status summary, а не generic dashboard;
- `Env & Config v2` стал path-aware editor;
- `Services & Logs v2` стал surface для service + parser diagnostics;
- `Dev Actions & Maintenance` стал понятным maintenance toolbox.

## Acceptance

Документ считается достаточным source of truth, если implementer без новых
решений понимает:

- какие runtime paths существуют;
- как считается их availability;
- как объясняется unavailable state;
- как работает config writeback;
- какие роли у всех 5 экранов;
- как hardware detection и parser diagnostics поднимаются в UI;
- что именно должен отразить редизайн в Stitch.
