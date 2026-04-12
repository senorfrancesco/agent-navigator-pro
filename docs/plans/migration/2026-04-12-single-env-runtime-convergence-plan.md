# Single `backend/.env` Runtime Convergence Plan

Дата: `2026-04-12`

Статус: `proposed`

Связанные контуры:
- `Open WebUI-first` migration
- native/runtime launcher contract
- operator config/runtime surfaces
- offline bundle export/import

## Контекст

Проект уже перевёл native-path на модель, где пользователь вручную настраивает только `backend/.env`, а runtime-оценщик (`scripts/evaluate_runtime.sh`) лишь рекомендует параметры.

Но `backend/.env.runtime` всё ещё остаётся в нескольких живых слоях:
- `scripts/launcher.sh`
- `scripts/run_all.sh`
- `scripts/stop_all.sh`
- `scripts/runtime_preflight.py`
- `backend/orchestrator/operator_runtime_service.py`
- `backend/orchestrator/operator_config_service.py`
- `deploy/offline_bundle/scripts/export_state.sh`

В результате текущий контракт размыт:
- native-path уже мыслится как single-env;
- container/operator/offline слой всё ещё держит generated runtime env как часть активного механизма;
- UI/operator surfaces продолжают показывать `backend/.env.runtime` как runtime source, хотя для основной модели управления это уже лишний слой.

Отдельный шаблон `backend/.env.native.example` при этом больше не нужен и должен быть удалён как устаревший артефакт старого multi-env контракта.

## Цель

Свести рабочий runtime-контракт проекта к одному user-owned файлу:
- `backend/.env` — единственный постоянный источник настроек runtime;
- `scripts/evaluate_runtime.sh` — советчик, но не применитель;
- `backend/.env.runtime` — либо полностью исчезает из активного runtime, либо остаётся только как строго export-only/offline artifact вне основного product/operator path.

## Что считается успехом

### Product/runtime contract

- `Open WebUI` и operator/runtime docs описывают только один пользовательский runtime-config source: `backend/.env`.
- `run_native.sh`, `launcher.sh --target native` и container runtime не зависят от generated env-файла для нормального запуска.
- нет UX, в котором пользователь должен помнить о `backend/.env.runtime` как об отдельном рабочем слое.

### Operator/runtime surfaces

- `/operator/state`, `/operator/runtime/paths`, `operator_config_service` и связанный UI не показывают `backend/.env.runtime` как канонический runtime source.
- generated env больше не фигурирует как “применённый план”, если сам runtime уже работает из `backend/.env`.

### Offline/export contour

- если `offline_bundle` всё ещё хочет сохранять applied snapshot, это делается как export artifact с явной ролью архива, а не как часть обычного runtime-контракта.
- offline docs явно различают:
  - рабочий config проекта;
  - export snapshot для переноса состояния.

## Не-цели

- не переписывать в этом плане всю модель `offline_bundle`;
- не менять сейчас `backend/open_webui_uploads`;
- не смешивать этот срез с `Qdrant`, `Knowledge`, tool bootstrap или UI redesign;
- не трогать historical docs/plans без необходимости.

## Основные проблемы, которые надо снять

1. `launcher.sh` и `runtime_preflight.py` всё ещё мыслят `.env.runtime` как штатный generated output для container-path.
2. `run_all.sh` и `stop_all.sh` продолжают читать `.env.runtime`.
3. operator services показывают `.env.runtime` как активный runtime source для native-path.
4. docs/forms/operator-review материалов всё ещё проектируют generated env как нормальный слой управления.
5. `offline_bundle` экспортирует `.env.runtime` вместе с остальным env-state, что закрепляет старый контракт.

## Предлагаемая декомпозиция

### Фаза 1. Удалить устаревший шаблон и зафиксировать single-env policy

Файлы:
- удалить `backend/.env.native.example`
- обновить migration index / ссылки на новый план

Acceptance:
- в репозитории больше нет шаблона, который предлагает пользователю `backend/.env.native`;
- новый план добавлен в `docs/plans/migration/`.

### Фаза 2. Снять `.env.runtime` с native/operator semantics

Файлы:
- `backend/orchestrator/operator_runtime_service.py`
- `backend/orchestrator/operator_config_service.py`
- `backend/orchestrator/operator_ui_api.py`
- docs operator/runtime contract

Что сделать:
- убрать формулировки, где native-path использует `backend/.env.runtime` как generated applied plan;
- оставить только `backend/.env` как source-of-truth;
- если нужен runtime snapshot, показывать его как derived/export-only artifact.

Acceptance:
- operator/runtime API не описывает `.env.runtime` как канонический runtime source для native-path.

### Фаза 3. Упростить launcher/container contract

Файлы:
- `scripts/launcher.sh`
- `scripts/run_all.sh`
- `scripts/stop_all.sh`
- `scripts/runtime_preflight.py`
- соответствующие tests

Что сделать:
- убрать обязательность записи `.env.runtime` для container-path;
- перевести preflight в report/advice-only режим и для container orchestration;
- если container path всё ещё нуждается в applied snapshot, использовать временный файл вне репозитория или явный `tmp` artifact, а не canonical `backend/.env.runtime`.

Acceptance:
- launcher/runtime scripts стартуют без зависимости от persisted `.env.runtime`;
- generated env больше не описывается как штатный runtime layer.

### Фаза 4. Развести offline export и рабочий runtime contract

Файлы:
- `deploy/offline_bundle/scripts/export_state.sh`
- `deploy/offline_bundle/docs/*`
- `deploy/offline_bundle/manifest.template.json`
- связанные tests

Что сделать:
- решить, нужен ли `.env.runtime` вообще в export bundle;
- если нужен, переименовать/переописать его как snapshot artifact;
- если не нужен, убрать из export/import manifest.

Acceptance:
- offline docs явно говорят, что exported env snapshot не является штатным config source живого runtime.

### Фаза 5. Финальная зачистка документации и тестов

Файлы:
- `README.md`
- `docs/flags-reference.md`
- `docs/scripts/README.md`
- `docs/deploy-guide.md`
- `docs/runtime_profiles.md`
- `docs/forms/*`
- `backend/tests/test_docs_flags_reference.py`
- runtime/operator tests

Acceptance:
- в актуальной документации нет двойного контракта `backend/.env` + `backend/.env.runtime` как нормы;
- тесты отражают single-env модель.

## Риски

### Риск 1. Сломать offline/export сценарии

Почему:
- `deploy/offline_bundle` сейчас реально копирует `.env.runtime`.

Снижение риска:
- сначала перевести смысл файла в export-only snapshot;
- только потом решать, удалять ли его полностью.

### Риск 2. Сломать operator UI ожидания

Почему:
- operator layer уже показывает `.env.runtime` в runtime paths/config sources.

Снижение риска:
- менять backend contract и docs одновременно;
- прогонять targeted tests operator layer на каждом срезе.

### Риск 3. Смешать этот срез с общим migration/refactor шумом

Почему:
- тема пересекается с launcher, offline bundle, operator UI и docs.

Снижение риска:
- держать фазы маленькими;
- по умолчанию не больше `5` файлов в одном implementation slice.

## Verification Strategy

Для каждого implementation slice:

- shell:
  - `bash -n scripts/launcher.sh scripts/run_all.sh scripts/stop_all.sh scripts/runtime_preflight.py`
- python:
  - `python -m py_compile backend/orchestrator/operator_runtime_service.py backend/orchestrator/operator_config_service.py`
- tests:
  - `pytest backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py -q`
  - `pytest backend/tests/test_operator_runtime_service.py backend/tests/test_operator_config_service.py -q`
  - `pytest backend/tests/test_docs_flags_reference.py -q`
- docs:
  - `git diff --check`

## Решение по текущему шагу

В рамках этого шага делаем только preparatory cleanup:
- удалить `backend/.env.native.example`;
- зафиксировать отдельный migration-план по выводу `.env.runtime` из активного runtime-контракта.

Полный runtime refactor в этом плане пока не выполняется.
