# План: рефакторинг Operator UI shell, русификация и observability

## Контекст

Текущий `prototype/operator-ui` уже подключён к реальному Python control plane, но интерфейс остаётся промежуточным shell-слоем. Он решает часть operational-задач, но ещё не проходит по критериям интуитивного operator UI:

- визуальная иерархия перегружена карточками и повторяющимися панелями;
- язык интерфейса смешанный: часть UI на английском, часть статусов и operational-понятий читается как design/demo copy;
- overview и launch дублируют друг друга вместо одного ясного entry flow;
- services/logs/diagnostics пока не дают operator-level observability surface;
- отсутствует интеграция с Prometheus/Grafana как canonical metrics layer.

Этот план описывает не новый продукт, а **отделку и рефакторинг уже существующего shell**, чтобы его можно было довести до понятной и устойчивой точки управления runtime/deploy flow.

## Frontend Skill Framing

### Visual thesis

Спокойный operator workspace в духе `Linear`/`Grafana Explore`: тёмная инженерная поверхность, жёсткая типографическая иерархия, минимум декоративной хромировки, один акцентный цвет для действий и один контрастный слой для warnings/blockers.

### Content plan

1. Ориентация: что доступно на машине прямо сейчас.
2. Действие: что можно запустить, применить или задеплоить.
3. Контроль: где увидеть health, metrics, logs и blockers.
4. Восстановление: где диагностировать, починить и повторить запуск.

### Interaction thesis

1. Быстрый mode-switch без перезагрузки контекста между `Обзор`, `Запуск`, `Конфиг`, `Сервисы`, `Сборка/Деплой`.
2. Detail drawer / side inspector для `Почему недоступно?`, `Источник конфига`, `Последний job`.
3. Live status transitions для jobs, readiness и metrics cards без тяжёлой анимации; только короткие state transitions и log append.

## Аудит текущей реализации

### 1. Ошибки визуальной архитектуры

- `Overview` и `Launch` слишком близки по смыслу: обе секции повторяют availability cards, но не формируют разный операторский результат.
- Почти весь UI собран из одинаковых panels/cards. Для operator workspace это создаёт эффект dashboard-mosaic вместо рабочего пространства.
- Header copy и hero copy местами звучат как prototype/demo, а не как production utility UI.
- Sidebar несёт полезную summary-информацию, но не выполняет навигационную и contextual роль достаточно жёстко.
- `Deploy / Build` визуально уже близок к рабочей вкладке, но не связан общим паттерном с `Services` и `Config`.

### 2. Ошибки UX и понятности

- Нет одного канонического сценария первого использования: пользователь должен быстро понимать `что доступно -> что запускать -> где смотреть результат`.
- Вкладка `Config` показывает много env-полей без достаточной semantic grouping на уровне operator intent.
- `Services` и `Logs` ещё не дают ясного ответа на операторский вопрос: `что реально работает? что сломано? что тормозит запуск?`
- `Dev Actions` пока выглядит как toolbox, а не как контекстная секция для safe recovery.
- Ошибки backend actions сейчас уже приходят честнее, но UI ещё не оформляет их как operator-readable failure states.

### 3. Ошибки локализации

- Вся навигация и почти все заголовки англоязычные.
- Operational terms не нормализованы: `Deploy / Build`, `Launch`, `Services`, `Runtime path`, `Apply changes`, `Why unavailable?`.
- Нет словаря терминов для одинаковых понятий: `runtime path`, `profile`, `source`, `apply`, `blocked`, `ready`, `partial`.
- Часть текста всё ещё звучит как engineering note, а не как текст для оператора или разработчика.

### 4. Ошибки observability surface

- `Services` и `Overview` живут в основном на file/layout/probe summary, а не на полноценной time-series observability модели.
- Нет отдельного слоя runtime metrics: CPU, RAM, GPU, queue depth, request latency, service readiness, job duration, import/export progress.
- Нет canonical связи между operator UI и существующими/будущими Prometheus метриками.
- Нет deep-link/embedded-path к Grafana dashboard, Explore или panel view.

## Целевая IA после рефакторинга

### 1. Обзор

Роль: быстрый статус машины и runtime-control summary.

Внутри:

- доступные пути запуска;
- активный job / последний job;
- краткий health summary;
- ключевые warnings/blockers;
- компактный metrics strip;
- последнее действие и его результат.

Чего не должно быть:

- повторения launch cards во втором экземпляре;
- длинных explanatory paragraphs;
- marketing/prototype copy.

### 2. Запуск

Роль: единственная секция выбора и запуска runtime path.

Внутри:

- две большие path surfaces: `Нативный запуск`, `Offline Bundle / Контейнеры`;
- launch presets;
- status badges `Готов`, `Частично доступно`, `Недоступно`;
- кнопка `Почему недоступно?`;
- inspector с prerequisites, launch source и config source.

### 3. Конфиг

Роль: осмысленное управление env/config source of truth.

Внутри:

- группировка не по техническим файлам, а по смыслу:
  - `Режим запуска`
  - `Модели и пути`
  - `GPU / размещение`
  - `Порты и URL`
  - `Профили Chainlit`
  - `Сравнение и парсинг`
- явный показ:
  - рекомендуемое значение;
  - текущее applied;
  - источник (`backend/.env`, `.env.runtime`, `env.bundle`);
- footer bar: `Изменено N параметров`, `Отменить`, `Применить`.

### 4. Сервисы и наблюдаемость

Роль: health + logs + metrics, а не только services list.

Внутри:

- service state table;
- readiness probes;
- parser/runtime diagnostics;
- metrics summary;
- logs feed;
- links to Grafana/Explore.

### 5. Сборка и деплой

Роль: build/import/deploy control plane.

Внутри:

- `Собрать bundle`
- `Импортировать и развернуть`
- stage timeline;
- artifacts;
- logs;
- deploy metrics;
- post-deploy verification summary.

### 6. Действия и восстановление

Роль: безопасные recovery/smoke/maintenance операции.

Внутри:

- smoke;
- refresh state;
- cleanup stale state;
- parser diagnostics;
- reload summaries;
- restart safe services.

## План русификации

### Цель

Сделать интерфейс русским по умолчанию для operator/developer workflows внутри этого репозитория, сохранив английский только там, где это имя команды, env key, API path или canonical technical term.

### Правила

- Навигация, заголовки, статусы, кнопки, ошибки и подсказки переводятся на русский.
- Технические пути, env keys, script names и endpoint URLs не переводятся.
- Нужен единый терминологический словарь:
  - `Overview` -> `Обзор`
  - `Launch` -> `Запуск`
  - `Config` -> `Конфиг`
  - `Services` -> `Сервисы`
  - `Deploy / Build` -> `Сборка / Деплой`
  - `Dev Actions` -> `Действия`
  - `available` -> `Готов`
  - `partial` -> `Частично`
  - `blocked/unavailable` -> `Недоступно`
  - `Apply changes` -> `Применить`
  - `Why unavailable?` -> `Почему недоступно?`

### Отдельный deliverable

Нужен `operator-ui-i18n-map` как source-of-truth таблица, чтобы backend messages и frontend labels не расходились.

## План интеграции Prometheus и Grafana

### Принцип

Operator UI не должен пытаться заменить Grafana. Он должен:

- показывать ключевые live metrics в интерфейсе;
- объяснять состояние сервисов и runtime jobs;
- давать быстрый переход в Grafana для глубокой диагностики.

### Источник истины

- `Prometheus` — canonical source для time-series metrics;
- `Grafana` — canonical deep analysis / dashboards / explore UI;
- operator backend — агрегатор и адаптер для operator-facing summary.

### Что подключать в первую очередь

#### Infrastructure metrics

- CPU загрузка
- RAM usage
- GPU utilization
- GPU memory used/free
- disk free space
- docker engine availability

#### Runtime metrics

- UMS readiness
- queue depth
- inference latency
- active requests
- model load state
- compare/doc workflows duration

#### Operator metrics

- action job duration
- last successful launch time
- last failed launch time
- deploy stage duration
- config apply count / last apply result

### UI contract

Нужны новые backend endpoints/operator adapters:

- `/operator/metrics/summary`
- `/operator/metrics/services`
- `/operator/metrics/runtime`
- `/operator/metrics/deploy`
- `/operator/links/grafana`

### UI presentation

- в `Обзор`: компактная metrics strip
- в `Сервисы`: service metrics + sparklines/mini trends
- в `Сборка / Деплой`: stage durations, throughput, last artifact timings
- в `Почему недоступно?`: probe evidence
- deep link buttons:
  - `Открыть в Grafana`
  - `Открыть Explore`
  - `Открыть dashboard сервиса`

### Граница ответственности

- UI не строит PromQL в браузере как primary path;
- browser говорит только с Python operator API;
- backend может либо читать Prometheus напрямую, либо брать агрегированные snapshots;
- прямые ссылки в Grafana допустимы как secondary path.

## Реализационные блоки

### Этап 1. Frontend IA cleanup

- убрать дублирование между `Overview` и `Launch`;
- ослабить card-mosaic и перейти к layout-first composition;
- переписать utility copy под operator language;
- добавить inspector/drawer patterns;
- пересобрать `Services` как health + metrics + logs surface.

### Этап 2. Русификация

- ввести словарь терминов;
- перевести navigation, CTA, states, errors;
- унифицировать backend-generated status/detail strings;
- оставить англоязычными только script names, env names, URLs, endpoint paths.

### Этап 3. Observability adapter layer

- выделить Prometheus/Grafana adapter в Python backend;
- определить минимальный metrics contract;
- пробросить summary метрики в `/operator/state` и специализированные `/operator/metrics/*`;
- связать service rows и readiness с metrics evidence.

### Этап 4. Grafana integration

- добавить dashboard/explore links;
- подготовить mapping `service -> grafana panel/dashboard`;
- дать operator UI быстрый переход к deep inspection без копирования query вручную.

### Этап 5. Finishing pass

- сократить copy ещё на 20-30%;
- выровнять spacing, typography и panel hierarchy;
- добавить 2-3 короткие и полезные motion patterns:
  - section fade/slide on tab switch
  - job status transition
  - log append emphasis / pulse on fresh data

## Acceptance criteria

### Frontend / UX

- оператор понимает первый сценарий без чтения длинных описаний;
- `Overview` и `Launch` больше не дублируют друг друга;
- `Services` воспринимается как observability workspace, а не список карточек;
- UI выглядит как продуктовая рабочая поверхность, а не как prototype/demo.

### Localization

- основная навигация и operational copy русифицированы;
- термины единообразны;
- backend/frontend не расходятся по статусным словам.

### Observability

- ключевые runtime/service/deploy metrics доступны внутри UI;
- Grafana доступна из UI через понятные deep links;
- Prometheus является источником summary metrics, а не только внешним сервисом “где-то рядом”.

## Решение

Этот refactor нужно вести как **единый трек отделки operator UI**, а не как россыпь мелких фиксов. Визуальная архитектура, русификация и observability должны меняться вместе; иначе получится технически рабочий, но всё ещё трудно читаемый интерфейс.
