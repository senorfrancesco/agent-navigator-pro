# План: polish operator UI, typed config controls, launch monitoring и bilingual settings

## Контекст

Текущий backend-served operator UI уже умеет:

- показывать `native` и `offline bundle / container` path;
- запускать actions через Python control plane;
- применять env/config changes;
- показывать jobs/logs;
- работать с variant/preset flow в `Config`.

Но по результатам живого прогона остаются 4 связанных класса проблем:

1. `Launch` и container runtime сейчас не дают оператору ясного ответа, запустилась ли система или упала на build/deploy этапе.
2. `Services` для `offline bundle / containers` ломает layout по ширине и даёт горизонтальный скролл.
3. `UI Settings` оформлены слабо, стоят не на своём месте и не доведены до полноценного bilingual shell.
4. `Config` всё ещё использует слишком много свободного text input там, где у поля есть конечный набор допустимых вариантов.

## Best Practices, на которые опираемся

### Settings UX

- Настройки нужно группировать по смыслу, не смешивать с основным рабочим flow и не перегружать верхний уровень. Источник: Microsoft app settings guidelines  
  https://learn.microsoft.com/en-us/windows/apps/design/app-settings/guidelines-for-app-settings
- Recommended values лучше показывать рядом с конкретным полем или группой, а не отдельной абстрактной сводкой. Источник: Microsoft Intune settings insight  
  https://learn.microsoft.com/en-us/intune/intune-service/fundamentals/settings-insight

### Selection controls

- Для взаимоисключающих вариантов лучше использовать selection controls, а не свободный text input. Источник: Material 3 selection controls  
  https://m3.material.io/components/menus/guidelines  
  https://m3.material.io/components/segmented-buttons/overview
- Text input надо оставлять только там, где значения действительно произвольные. Источник: GOV.UK Design System / USWDS form guidance  
  https://design-system.service.gov.uk/components/select/  
  https://designsystem.digital.gov/components/text-input/accessibility-tests/

### I18n

- `html lang` и системный язык интерфейса должны быть согласованы; локализация не должна быть набором случайных frontend patch-ов. Источник: W3C i18n best practices  
  https://www.w3.org/International/geo/html-tech/tech-lang.html

### Responsive operator UI

- Горизонтальный скролл на основной рабочей поверхности допустим только для осознанных data tables, но не для service workspace целиком.
- Главный операторский layout должен ужиматься за счёт wrap/grid reflow, а не выталкивать всю страницу в ширину.

## Аудит текущих проблем

### 1. Launch / runtime execution feedback

Живой лог показывает, что container path не “запустился и закрылся сам”, а упал на Docker build step:

- `failed to resolve source metadata for docker.io/library/python:3.11-slim`
- `lookup registry-1.docker.io ... server misbehaving`

То есть UI должен различать минимум 3 состояния:

- job accepted;
- build/deploy still running;
- runtime not healthy because prerequisite build/import failed.

Сейчас пользователю не хватает:

- pinned job state в `Launch`;
- явного summary `build failed`, `deploy failed`, `runtime healthy`, `runtime not started`;
- system checkmark, который опирается не только на job queue, но и на реальные runtime probes.

### 2. Services layout ломается по ширине

Скорее всего расширение идёт из-за сочетания:

- `mono-line` / длинных endpoint/source строк;
- двухколоночных grids;
- service/meta pills и source blocks без достаточного wrap/min-width guard.

Нужен отдельный responsive hardening для `Services`, особенно для container path.

### 3. UI Settings оформлены не по месту

Сейчас язык показан прямо в topbar и отдельная кнопка settings визуально живёт отдельно от общей иерархии действий. Это делает topbar шумным.

Лучший путь:

- убрать language selector из верхней панели;
- оставить одну компактную кнопку `Настройки интерфейса`;
- внутри drawer/modal держать:
  - язык;
  - show env keys;
  - show recommended values;
  - show source files;
  - metrics density.

### 4. Русский и английский слой неполный

Сейчас часть shell copy локализуется, но часть backend-derived labels, action texts, source strings, preset descriptions и button labels остаётся смешанной.

Нужен единый bilingual contract:

- либо backend отдаёт semantic keys;
- либо backend отдаёт canonical English strings, а frontend переводит их через словарь.

Для этого проекта рекомендуемый путь:

- backend возвращает stable canonical labels/keys;
- frontend локализует shell, section titles, CTA, preset titles/descriptions/reasons и config-group labels.

### 5. Typed config controls недоведены

Сейчас text inputs стоят и там, где есть конечные варианты.

Нужно разделить поля на:

- `select`
  - runtime profile
  - device mode
  - strict JSON on/off
  - retry count из ограниченного диапазона
  - known port profile selectors
- `segmented / toggle`
  - parser strict vs safe
  - monitoring on/off
  - single-GPU vs multi-GPU
- `text input`
  - model paths
  - URLs
  - arbitrary tool/model params
  - secrets/passwords

## Решение

### A. Launch becomes execution workspace

`Launch` должен стать местом, где оператор видит:

- active job state;
- last job result;
- runtime health for selected path;
- shortcut to logs.

Что делаем:

- добавить над profile cards `Launch status strip`;
- показывать:
  - `Контур online`
  - `Задача идёт / завершена / упала`
  - `Runtime healthy / not started / blocked`
- в container path при build/deploy failure показывать краткий error-summary прямо в `Launch`, а не только в `Services`.
- лог `Launch` должен иметь компактный console block с последними lines активной job.

### B. Runtime health model becomes explicit

Нужна отдельная backend-модель `runtime health`, а не только job state.

Контракт:

- `not_started`
- `building`
- `deploying`
- `running`
- `degraded`
- `failed`
- `blocked`

Для container path:

- если Docker build упал из-за registry/DNS/network, status = `failed`, reason = `image-build-failed`;
- если deploy не начинался после failed build, не показывать fake “system running”.

### C. Settings move into one UI drawer

Topbar должен содержать:

- system status;
- current job status;
- primary action;
- одну кнопку `Настройки интерфейса`.

В settings drawer:

- language `ru/en`
- show env keys
- show recommended values
- show source files
- metrics density

Language selector из topbar убирается.

### D. Typed config controls

Добавить schema-driven control type для config fields:

- `control = select | toggle | text | password`
- `options[]` если выбор конечный

Первая волна typed fields:

- `UMS_RUNTIME_PROFILE` -> `select`
- `DEVICE_MODE` -> `select`
- `COMPARE_SINGLE_ITEM_STRICT_JSON` -> `select(bool)`
- `COMPARE_SINGLE_ITEM_RETRY_COUNT` -> `select(0..3)` или ограниченный набор
- `OFFLINE_COMPARE_STRICT_JSON` -> `select(bool)`
- `BUNDLE_PREFLIGHT_REQUIRED` -> `select(bool)`
- `BUNDLE_PARITY_SMOKE_REQUIRED` -> `select(bool)`

Порты оставляем text input, но рядом сохраняем preset-driven flow.

### E. Responsive hardening for Services

Нужно убрать общий horizontal overflow:

- длинные source/endpoint значения переводить в wrap/truncate внутри карточек;
- `services-layout` на container path принудительно вести как 1-column workspace при недостатке ширины;
- `mono-line` использовать только там, где реально нужен single-line evidence, а не для основного layout.

### F. Bilingual cleanup

Ввести один operational dictionary для:

- navigation
- CTA
- section headers
- config variant titles
- preset titles/descriptions/reasons
- empty states
- runtime status labels

Acceptance: при переключении `ru/en` пользователь не видит смешанного shell copy, кроме literal env keys, paths, script names и raw logs.

## Execution order

### Slice 1

- Launch status strip + launch log panel
- backend runtime health contract
- failure classification for container build/deploy

### Slice 2

- settings drawer
- убрать language selector из topbar
- bilingual cleanup for shell and config/preset texts

### Slice 3

- typed config controls
- preserve staged/apply flow
- manual text inputs only where variants do not apply

### Slice 4

- responsive hardening for `Services`
- remove full-page horizontal scrolling
- recheck container path on narrow widths

## Acceptance criteria

- `Launch` показывает, идёт ли задача, чем она закончилась и работает ли runtime.
- Container build failure из-за Docker/network не выглядит как “система просто исчезла”.
- `Services` не расширяет всю страницу в ширину для container path.
- Язык интерфейса живёт только в UI settings drawer, а не в topbar.
- RU/EN shell переключается целиком и без заметного смешивания.
- Поля с конечными вариантами используют `select/toggle`, а не свободный text input.
- Text input остаётся только для путей, URL, параметров моделей/инструментов и других genuinely free-form значений.

