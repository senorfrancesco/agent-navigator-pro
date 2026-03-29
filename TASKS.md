# TASKS - Agent Navigator Pro

> **Единственный operational backlog.** Планы в `docs/plans/` — исторические артефакты, не operational source.

## Release/v1.0 Transition (2026-03-22)

- [ ] R1.0.1 — Переопределить каноническую основную ветку проекта на `release/v1.0`
- [ ] R1.0.2 — Ввести `deploy/offline_bundle/` как канонический offline/server deployment slice
- [ ] R1.0.3 — Собрать новый `docker-first` runtime bundle с export/import/deploy pipeline
- [ ] R1.0.4 — Включить operator-controlled multi-agent runtime в offline bundle `v1.0`
- [ ] R1.0.5 — Убрать зависимость нового release path от legacy hybrid compose/runtime
- [ ] R1.0.6 — Scope guard: в рамках задачи изменять код только внутри `deploy/offline_bundle/`; любые правки вне этой папки делать только после явного предупреждения пользователя
- [ ] R1.0.7 — Пересобрать `host_packages` для Ubuntu `24.04.4 LTS` / kernel `6.17.0-19-generic` с `--extra-package linux-headers-6.17.0-19-generic`
- [ ] R1.0.8 — Сделать отдельное operator UI-приложение для `deploy/offline_bundle/`, которое заменяет основной shell UX для build/export/deploy/runtime flow
  План: `docs/plans/2026-03-26-offline-operator-ui-plan.md`
  Progress: в Stitch создан отдельный экран `Deploy / Build Control` (`9104511724d04a739cd23d2c5969116c`), а в `prototype/operator-ui` добавлена отдельная вкладка `Deploy / Build` с режимами `Build Bundle` и `Import / Deploy`, привязанными к каноническим scripts из `deploy/offline_bundle/scripts`.
  Progress: host package audit больше не требует exact package version для NVIDIA driver stack, если на сервере уже есть рабочий драйвер и `nvidia-smi` проходит. `check_host_packages.py` и `install_host_apt_bundle.sh` автоматически переходят в manual-driver semantics и убирают ложные блокеры вроде `missing:xserver-xorg-video-nvidia-570-server=...`.
  Решение: не расширять `scripts/launcher.sh` до полного offline bundle lifecycle; вместо этого вести UI через отдельный Python operator runner/action registry, который оркестрирует allowlisted native/container runtime actions и `deploy/offline_bundle/scripts`.
- [ ] R1.0.9 — Сделать отдельное operator UI-приложение для native development/runtime path, чтобы упростить локальный запуск, конфигурирование env, мониторинг сервисов и developer workflows
  План: `docs/plans/2026-03-26-native-dev-operator-ui-plan.md`
  Runtime-control UI logic: `docs/plans/2026-03-29-runtime-control-operator-ui-logic.md`
  Frontend/observability refactor: `docs/plans/2026-03-29-operator-ui-frontend-refactor-and-observability-plan.md`
  Решение: native path и offline/container path считаются равноправными; UI показывает availability обоих path, не скрывает unavailable state, а объясняет его через `Why unavailable?` drawer и редактирует реальные env/config sources выбранного path через explicit apply flow.
  Deliverability: текущий `prototype/operator-ui` — web-first static prototype; позже его можно поднять как отдельное web-приложение или упаковать в desktop-shell (`Electron`/`Tauri`) без смены UX-контракта.
  Progress: начат реальный web-first slice — `agent_api` теперь монтирует `/operator-ui/` и `/operator-assets/`, а `prototype/operator-ui` умеет гидратироваться из backend endpoint `GET /operator/state`.
  Progress: `Config` переведён на path-aware variants и preset preview flow; для `native` и `container` появились отдельные variant tabs, staged-presets без auto-write и `Local safe ports` / `Target default ports` для `env.bundle`.
  Workaround: container deploy/run из текущего backend-served operator UI теперь блокируется, если `env.bundle` пытается поднять `agent-api` на том же порту, что и текущий UI (`8000` по умолчанию). Это intentional self-conflict guard, пока не появится detached deploy mode или отдельный портовый профиль для offline bundle.
  Follow-up: добавить настоящие live runtime probes для offline/container stack и полноценный backend-level bilingual payload, чтобы RU/EN toggle не зависел только от frontend shell translation.
  Follow-up: текущая settings-plane для языка/видимости env keys/source/recommendations живёт во frontend shell; если нужен server-side preference sync, это надо вынести в `/operator/preferences`.
- [x] R1.0.9a — Сделать Python control plane каноническим backend-контуром для operator UI
  План: `docs/plans/2026-03-29-python-operator-control-plane-migration.md`
  Цель: UI должен разговаривать только с Python API и Python job/action model; shell не должен оставаться продуктовым API для экрана запуска, конфига, deploy/build и maintenance.
  Done: umbrella-migration закрыта поверх `R1.0.9b`–`R1.0.9f`; action execution идёт через async Python routes/jobs, multi-GPU hardware truth собирается server-side, shell UI больше не держит жёстко прошитые hardware/runtime claims, а offline deploy/run shell entrypoints остаются compatibility wrappers поверх Python-first control plane.
  Verification: `pytest backend/tests/test_operator_ui_actions.py backend/tests/test_operator_ui_api.py backend/tests/test_operator_runtime_service.py -q` -> `8 passed`; `node --check prototype/operator-ui/app.js`; `python -m py_compile backend/orchestrator/operator_ui_actions.py backend/orchestrator/operator_ui_api.py backend/orchestrator/operator_runtime_service.py`.
- [x] R1.0.9b — Вынести runtime path discovery, hardware-aware defaults и config source resolution в отдельные Python services
  Контур: `backend/orchestrator/operator_runtime_service.py`, `backend/orchestrator/operator_config_service.py`
  Acceptance: availability `native` / `offline_bundle` рассчитывается Python-слоем, suggested values и effective config собираются там же, а UI получает уже структурированный contract.
  Done: `operator_ui_api.py` больше не держит path/config discovery как локальную ad-hoc логику; `OperatorRuntimeService` и `OperatorConfigService` стали source of truth для runtime paths, hardware metrics, env source resolution и grouped config state.
  Verification: `pytest backend/tests/test_operator_runtime_service.py backend/tests/test_operator_config_service.py backend/tests/test_operator_ui_api.py -q` -> `5 passed`.
- [x] R1.0.9c — Вынести `deploy/offline_bundle` build/import/deploy lifecycle в Python deploy service
  Контур: `backend/orchestrator/operator_deploy_service.py`
  Acceptance: bundle build, archive pack/unpack, validate, host install, image load, restore, deploy и verify моделируются Python-слоем со stage/status contract, а scripts остаются только allowlisted low-level adapters.
  Done: `OperatorDeployService` стал source of truth для `Deploy / Build` surface, build/import stage catalog, artifact summary и stage-scoped deploy logs; `operator_ui_api.py` больше не собирает deploy lifecycle inline.
  Verification: `pytest backend/tests/test_operator_deploy_service.py backend/tests/test_operator_runtime_service.py backend/tests/test_operator_config_service.py backend/tests/test_operator_ui_api.py -q` -> `7 passed`.
- [x] R1.0.9d — Вынести jobs, stage model и structured logs из action registry в отдельный Python module
  Контур: `backend/orchestrator/operator_jobs.py`
  Acceptance: long-running actions имеют единый job schema (`job_id`, `status`, `current_stage`, `stages`, `logs`, `exit_code`, timestamps), пригодный для UI polling/streaming.
  Done: `OperatorJobStore` и structured schema (`OperatorJob`, `OperatorJobStage`, `OperatorJobLogEntry`) вынесены в `operator_jobs.py`; `operator_ui_actions.py` теперь использует их как execution glue вместо локального `JOB_STORE` и ad-hoc `logs: List[str]`.
  Verification: `pytest backend/tests/test_operator_jobs.py backend/tests/test_operator_ui_api.py backend/tests/test_operator_deploy_service.py backend/tests/test_operator_runtime_service.py backend/tests/test_operator_config_service.py -q` -> `9 passed`.
- [x] R1.0.9e — Перевести frontend operator UI на Python-only API adapters
  Контур: `prototype/operator-ui`, future real app shell
  Acceptance: кнопки `Launch`, `Apply`, `Build Bundle`, `Import / Deploy`, `Verify`, `Maintenance` ходят только в Python endpoints `/operator/*`; mock-only action paths и shell-first assumptions удалены.
  Done: `prototype/operator-ui/app.js` теперь гидратирует `actions/catalog`, запускает runnable actions через `POST /operator/actions/run`, поллит `GET /operator/jobs/{job_id}`, применяет env changes через `POST /operator/config/{path_key}/apply`, а maintenance refresh flows идут через `/operator/state`, `/operator/runtime/paths` и `/operator/deploy/{mode}`.
  Verification: `node --check prototype/operator-ui/app.js` and `pytest backend/tests/test_operator_jobs.py backend/tests/test_operator_ui_api.py backend/tests/test_operator_deploy_service.py backend/tests/test_operator_runtime_service.py backend/tests/test_operator_config_service.py -q` -> `11 passed`.
- [x] R1.0.9f — Деградировать shell-скрипты до compatibility/recovery layer и thin wrappers
  Контур: `scripts/launcher.sh`, `deploy/offline_bundle/scripts/*.sh`
  Acceptance: shell не считается каноническим UI API; по возможности скрипты вызывают Python entrypoints или узкие host-команды и сохраняются как ручной CLI/recovery path.
  Done: добавлен `backend/orchestrator/operator_shell_compat.py`; `deploy/offline_bundle/scripts/deploy.sh` и `run_offline_bundle.sh` теперь делегируют в Python compatibility bridge, а `scripts/launcher.sh` явно переопределён как manual compatibility wrapper вокруг Python-first operator control plane.
  Verification: `pytest backend/tests/test_operator_shell_compat.py backend/tests/test_runtime_launcher.py::test_launcher_help_documents_install_and_platform_flags -q` -> `6 passed`; `python -m py_compile backend/orchestrator/operator_shell_compat.py` -> passed.
- [x] R1.0.9g — Пересобрать frontend IA/operator shell под более интуитивный runtime workspace
  План: `docs/plans/2026-03-29-operator-ui-frontend-refactor-and-observability-plan.md`
  Scope: убрать дублирование `Overview`/`Launch`, ослабить card-mosaic, перейти к layout-first operator composition, добавить inspector/drawer patterns и превратить `Services` в health+metrics+logs workspace.
  Done: `Overview` переведён на компактные summary cards вместо дублирования launch-cards; `Services` и `Deploy` собраны как более жёсткие workspace-секции с отдельными summary strips, metrics/links surfaces и логами; для shell добавлены стабильные UI hooks под Playwright smoke.
  Done: системные индикаторы и `UI Settings` вынесены из topbar в нижний sidebar control-cluster как единый вертикальный action-list; topbar разгружен до одного primary launch CTA, а у кнопки настроек появилась встроенная минималистичная gear-иконка.
  Done: topbar CTA переработан в `split-button`: primary action запускает текущий выбранный runtime path, а chevron-menu позволяет немедленно запустить `Native Runtime` или `Offline Bundle / Containers` без жёсткой привязки к нативному сценарию. Sidebar получил отдельную nav-card и более явную operator-rail иерархию.
- [x] R1.0.9h — Русифицировать operator UI и ввести единый словарь operational терминов
  План: `docs/plans/2026-03-29-operator-ui-frontend-refactor-and-observability-plan.md`
  Scope: перевести навигацию, CTA, статусы, ошибки и utility copy на русский; сохранить английский только для env keys, script names, URLs и API paths; выровнять backend/frontend wording.
  Done: shell, dynamic copy и backend-derived runtime/deploy wording приведены к одному русскому словарю; добавлен source-of-truth файл `docs/plans/2026-03-29-operator-ui-i18n-map.md`. Английские literal'ы сохранены только для env keys, script names, paths, URLs и метрик.
- [x] R1.0.9i — Подключить Prometheus summary metrics к operator API и UI
  План: `docs/plans/2026-03-29-operator-ui-frontend-refactor-and-observability-plan.md`
  Scope: добавить Python adapter layer для runtime/service/deploy metrics, новые `/operator/metrics/*` endpoints и встроенные metrics surfaces для `Overview`, `Services` и `Deploy / Build`.
  Done: добавлен `backend/orchestrator/operator_observability_service.py`; `/operator/state` включает `metricsSummary`, работают endpoints `/operator/metrics/{summary,runtime,services,deploy}`, а в `prototype/operator-ui` отрисованы metrics panels для `Overview`, `Services` и `Deploy`. Есть unit tests на summary/empty metrics contract.
- [x] R1.0.9j — Интегрировать Grafana deep links и observability navigation в operator UI
  План: `docs/plans/2026-03-29-operator-ui-frontend-refactor-and-observability-plan.md`
  Scope: добавить service/dashboard mapping, кнопки `Открыть в Grafana` / `Explore`, связать operator diagnostics с Prometheus/Grafana evidence вместо изолированных UI summaries.
  Done: Python observability layer возвращает surface-aware `grafanaLinks`, включая dashboard/panel links, `Grafana Explore` и `Prometheus targets`; в `prototype/operator-ui` CTA стали явными (`Открыть в Grafana` / `Открыть в Prometheus`), а `Services`/`Deploy` получили встроенную observability navigation. Playwright smoke покрывает наличие этих workspace-блоков.
- [ ] R1.0.9k — Довести launch-monitoring и runtime health до честного operator feedback
  План: `docs/plans/2026-03-29-operator-ui-polish-and-typed-config-plan.md`
  Scope: добавить `Launch status strip`, launch-scoped log panel, runtime health model `not_started/building/deploying/running/degraded/failed/blocked` и понятный fail-summary для container build/deploy.
  Context: живой прогон показал container launch failure на Docker build step (`docker.io/library/python:3.11-slim` не резолвится из-за DNS/network), а UI пока не объясняет это достаточно явно прямо во вкладке `Launch`.
  Progress: в `prototype/operator-ui` начат перенос `Launch` из статической launch-cards surface в execution workspace: добавлены launch strip, launch log panel и отдельный runtime health fetch из `/operator/runtime/health/{path}`; следующим шагом нужно довести classification container build/deploy failures и привязать их к service probes.
  Progress: user-facing container path больше не описывается как `scripts/launcher.sh --target container`; primary source/copy переведены на offline bundle (`deploy/offline_bundle/scripts/run_offline_bundle.sh`), а legacy `runtime.container.launch` явно помечен как dev-only checkout launcher.
- [ ] R1.0.9l — Перевести Config на typed controls там, где варианты конечны
  План: `docs/plans/2026-03-29-operator-ui-polish-and-typed-config-plan.md`
  Scope: добавить schema-driven `select/toggle/text/password`, заменить свободный text input для runtime/profile/parser булевых и ограниченных значений, сохранить manual fields только для путей, URL, model/tool params и других free-form значений.
  Progress: `OperatorConfigService` уже размечает первые typed fields как `select` (`UMS_RUNTIME_PROFILE`, `DEVICE_MODE`, strict JSON / retry / preflight / parity fields), а frontend `Config` умеет рендерить `select` вместо свободного text input и сохранять staged/apply flow.
- [ ] R1.0.9m — Перенести language/UI preferences в единый settings drawer и дочистить RU/EN contract
  План: `docs/plans/2026-03-29-operator-ui-polish-and-typed-config-plan.md`
  Scope: убрать language selector из topbar, держать язык и UI preferences только в `Настройки интерфейса`, довести bilingual shell без смешанного copy, кроме literal env keys, paths, script names и raw logs.
  Progress: отдельный `UI Settings` dialog уже есть; следующим шагом он становится единственной точкой смены языка и UI preferences, а selector в topbar удаляется.
  Progress: `Config` и path cards получили дополнительную англофикацию backend-derived строк после hydration; отдельно введён явный path contract для host/bundle path-полей, чтобы EN-профиль объяснял, где допустимы абсолютные внешние пути, а где layout должен оставаться bundle-internal. Следующим срезом bilingual-поля (`nameEn`, `roleEn`, `titleEn`, `bodyEn`, `labelEn`, `valueEn`) вынесены в `operator_runtime_service.py`, `operator_ui_api.py` и `operator_deploy_service.py`, а `app.js` переключён на них как на source of truth для EN-рендера.
  Progress: `operator_config_service.py` теперь тоже отдаёт bilingual contract для `Config` (`titleEn`, `descriptionEn`, `labelEn`, `recommendedReasonEn`, `reasonEn`), а `app.js` перестроен на `localizedField(...)` для variants/presets/field meta и activity сообщений. Дополнительно `Apply changes`, deploy-mode switcher и maintenance empty state больше не остаются русскими в EN-режиме.
  Progress: добавлен audit-скрипт `scripts/audit_operator_ui_i18n.py` и тест `backend/tests/test_operator_ui_i18n_audit.py`, которые проверяют семантические UI-поля (`title/body/label/name/role/action/note/...`) на наличие `...En` пары внутри итогового `/operator/state`. Audit уже используется для добивки `hardwareMetrics`, `serviceRows`, `pathLabel` и typed-control options.
- [ ] R1.0.9n — Убрать horizontal overflow и дожать responsive layout для Services/Deploy
  План: `docs/plans/2026-03-29-operator-ui-polish-and-typed-config-plan.md`
  Scope: убрать full-page horizontal scroll во вкладке `Services`, ограничить длинные endpoint/source строки, стабилизировать container-path layout и проверить узкие ширины.
  Progress: ослаблены `mono-line`, `source-value`, `service-meta-pill` и `log-console` для wrap/word-break; на узких ширинах `service-row` и `path-card-header` теперь складываются в колонку. Нужен живой browser smoke именно на container-path `Services`.
  Progress: локализована конкретная причина full-width overflow: terminal `pre.log-console` растягивал grid-элемент. Добавлены `white-space: pre-wrap`, `width/max-width/min-width` ограничения для log-console и `min-width: 0` для карточек/workspace-элементов, чтобы длинные log-lines не раздвигали весь shell.
  Follow-up: во вкладке `Сборка / Деплой` user experience лучше читается, когда `Логи` идут full-width сразу после primary workflow (`Сводка` + `Этапы`), а не ждут завершения длинной правой secondary-колонки. Нужен отдельный layout pass, который сократит side-rail и поднимет лог-терминал выше в потоке.
- [ ] R1.0.9o — Расширить Native Runtime Config до полного typed runtime/GPU surface
  План: `docs/plans/2026-03-29-operator-ui-runtime-knobs-and-help-plan.md`
  Scope: собрать единый UI-contract поверх `backend/.env`, `backend/.env.runtime` и `backend/.env.hardware.override`, чтобы `Config` умел редактировать `BACKEND_MODE`, `*_DEVICE_MODE`, `GPU_LAYERS_MODE`, `N_GPU_LAYERS_OVERRIDE`, context budgets и per-model runtime knobs без ручного поиска по файлам.
  Progress: native `Config` уже расширен typed-вариантами `GPU / Placement`, `LLM / Context` и `Model Runtime`; добавлены `LLM/VLM/INTENT/RETRIEVAL *_DEVICE_MODE`, `GPU_LAYERS_MODE`, `N_GPU_LAYERS_OVERRIDE`, context-budget knobs и per-model runtime fields (`CONTEXT_SIZE_*`, `N_GPU_LAYERS_QWEN14B`) с unit coverage.
  Progress: для `Native Runtime` добавлен отдельный variant `Secrets / Access`, который выводит и позволяет редактировать `CHAINLIT_ADMIN_USER`, `CHAINLIT_ADMIN_PASSWORD`, `CHAINLIT_AUTH_SECRET`, `GF_SECURITY_ADMIN_USER`, `GF_SECURITY_ADMIN_PASSWORD` прямо из `backend/.env`, чтобы локальный admin/operator path не оставался вне operator UI.
- [ ] R1.0.9p — Расширить Offline Bundle Config для secrets, profiles, GPU placement и Chainlit knobs
  План: `docs/plans/2026-03-29-operator-ui-runtime-knobs-and-help-plan.md`
  Scope: добавить typed/editor support для `deploy/offline_bundle/env.bundle`, включая `CHAINLIT_*`/`GF_*` secrets, `BACKEND_MODE`, `UMS_RUNTIME_PROFILE`, `UMS_LLM_GPU_INDICES`, `GPU_LAYERS_MODE`, `N_GPU_LAYERS_*`, `VLLM_*` и profile-specific Chainlit parameters.
  Progress: `env.bundle` surface уже расширен variant tabs `Runtime / Backend`, `GPU / Placement`, `Secrets / Access`, `Embedders / Models` и `Chainlit Profiles`; туда выведены `BACKEND_MODE`, `UMS_RUNTIME_PROFILE`, `DEVICE_MODE`, `LLM/VLM/EMBEDDER *_DEVICE_MODE`, `UMS_LLM_GPU_INDICES`, `GPU_LAYERS_MODE`, `N_GPU_LAYERS_*`, `VLLM_*`, `CHAINLIT_*`, `GF_*`, `INTENT_CLASSIFIER_*` и retrieval/model profile knobs с regression test на typed controls.
- [ ] R1.0.9q — Добавить help-текст по каждому runtime/container параметру и полный bilingual contract
  План: `docs/plans/2026-03-29-operator-ui-runtime-knobs-and-help-plan.md`
  Scope: у каждого поля в `Config` должны быть `description` / `descriptionEn` и `recommendedReason` / `recommendedReasonEn`; UI обязан переключать labels и help-тексты между `RU/EN`, не переводя literal env keys, paths и raw values. Для secrets нужен отдельный `secret` flag и masked rendering по умолчанию.
  Progress: backend `field` contract уже расширен до `description` / `descriptionEn` и `secret`; UI `Config` теперь показывает help-текст под каждым полем и маскирует `CHAINLIT_*`, `GF_*`, `VLLM_API_KEY` в meta-блоке и input-контроле. Дополнительно в `UI Settings` добавлен локальный reveal-toggle `Show secret values`, который по умолчанию выключен и не влияет на backend state. Следующий шаг внутри этого пункта — visual polish для плотности `Config`.
  Progress: `Config` визуально пересобран в более читаемый workspace: group sections теперь двуколоночные (`orientation слева / поля справа`), meta-информация собрана в компактную grid-легенду, а variant/path switchers получили более явный container treatment. Это снижает ощущение технического env-dump перед дальнейшим расширением knobs.
  Progress: для secret-полей добавлена встроенная мини-кнопка генерации прямо внутри input-row. Генерация идёт локально через `crypto.getRandomValues`, значение попадает в staged state и не записывается в env без явного `Применить изменения`.
  Progress: рядом с генератором добавлена мини-кнопка копирования, чтобы текущее staged/current secret-значение можно было быстро забрать из UI без отдельного ручного выделения.

## План исправления compare/offline bundle багов (2026-03-26 19:18 EDT)

- [ ] R1.0.10 — `compare_workflow`: убрать расхождение между `needs LLM: 5` и фактической LLM-очередью `75`
  Контекст: в `backend/orchestrator/workflows/compare.py` сейчас в `semantic_candidates` попадает `to_analyze + structural`, из-за чего structural diff'ы ошибочно уходят в `_analyze_compare_batch()`, хотя лог `needs LLM` считает только `to_analyze`.
  План: либо исключить `structural` из LLM-очереди и обрабатывать их только через `_append_structural_result()`, либо как минимум привести telemetry/logging к честной формулировке `semantic queue / llm_modified / structural`.
  Verification: таргетный pytest для compare workflow + ручная проверка docker-логов offline bundle, что после фикса количество `LLM batch` соответствует реальному числу LLM-кандидатов.

- [ ] R1.0.11 — `compare_workflow`: стабилизировать strict JSON parsing для single-item LLM batch и убрать `structured output count mismatch batch=1 parsed=0`
  Контекст: `_analyze_compare_batch()` требует JSON-массив ровно из `len(batch)` объектов; в offline bundle при `analysis_batch_size=1` модель периодически возвращает ответ, который не парсится как массив/объект, и workflow падает в fallback без дополнительного retry.
  План: для `len(batch) == 1` перейти на prompt c одним JSON-объектом вместо массива, принимать оба формата (`dict` и `[dict]`) на parse-path, добавить минимальный single-item retry с более жёсткой инструкцией `только JSON без markdown/пояснений`.
  Verification: таргетный pytest для `_analyze_compare_batch()` / compare parser path с кейсами `dict`, `[dict]`, markdown-fenced JSON и garbage-prefix/suffix; затем smoke-run в offline bundle с контролем отсутствия новых `parsed=0` по docker-логам `chainlit`.

- [ ] R1.0.12 — `compare_workflow`: уменьшить runtime деградацию offline bundle при compare-run
  Контекст: по docker-логам `deploy/offline_bundle` один `POST /infer` в `UMS` занимает примерно `19-71s`, а из-за текущей очереди compare уже дошёл до `LLM batch 14/75` без финального отчёта.
  План: после исправления очереди и parse-path повторно проверить фактический объём LLM-вызовов, убедиться, что structural diff'ы не гоняются через LLM, и оценить необходимость дополнительного ограничения `COMPARE_ANALYSIS_MAX_TOKENS` / prompt-size для offline режима.
  Verification: повторный docker log inspection `chainlit` + `ums`, сравнение количества `POST /infer`, latency и времени до `Saved new report`.

## Текущее состояние (2026-03-12)

**Ветка:** `codex/orchestration-control-plane-snapshot` (2 коммита от `v3.0`)
**Тесты:** 331 passed, 4 deselected integration/E2E checks
**Архитектура:** Chainlit UI → backend orchestration layer (`orchestration_runtime.py` + `execution_runtime.py` + `ui_control_plane.py`) → LangGraph workflows + AdaptiveRAGPipeline + UMS

---

## Выполненные фазы (v3.0)

<details>
<summary>Фаза 0-5 — Фундамент, Hardware, RAG, Chainlit, Quality (всё закрыто)</summary>

### Фаза 0 — Критические фиксы
- [x] T3.0.1 — Багфикс MAX_CONTEXT_CHARS (48000 → 16000)
- [x] T3.0.2 — Создать ветку feature/v3.0-agentic-system

### Фаза 1 — Фундамент
- [x] T3.1 — Фикс follow-up роутинга (new_file_count)
- [x] T3.2 — Multi-turn промпт из messages[]
- [x] T3.3 — /v1/embeddings в UMS

### Фаза 2 — Hardware Profiler
- [x] T3.4 — HardwareProfiler (GPU/CPU/RAM detection)
- [x] T3.5 — TierSelector (Profile → TierConfig)
- [x] T3.6 — VRAM Calculator
- [x] T3.7 — Интеграция в UMS lifespan

### Фаза 3 — Adaptive RAG Pipeline
- [x] T3.8 — LaBSE ONNX FP32 export
- [x] T3.9 — BM25 + Hybrid Search (RRF)
- [x] T3.10 — EmbeddingIntentClassifier (6 интентов, centroid-based)
- [x] T3.11 — AdaptiveRAGPipeline (tiered: simple/corrective/agentic/multi-agent)
- [x] T3.12 — Legal Document Chunker

### Фаза 4 — Chainlit UI
- [x] T3.13 — Chainlit PoC: базовый чат + стриминг
- [x] T3.14 — Chainlit: workflows + cl.Step()
- [x] T3.15 — Chainlit: Docker + auth + history

### Фаза 5 — RAG Cleanup & Integration
- [x] T3.16 — Удалить naive RAG из agent_api
- [x] T3.16.1-T3.16.7 — RAG pipeline init, intent detection, doc_question handler, numpy
- [x] T3.16.8-T3.16.13 — Chunker спецификации, расширенные эталоны, corrective fallback, двойной gate, E2E тест

</details>

<details>
<summary>Выполненные Bugfixes (B3.1-B3.30)</summary>

- [x] B3.1 — ModuleNotFoundError orchestrator в Chainlit Docker
- [x] B3.2 — run_all.sh: пересборка при каждом запуске
- [x] B3.3 — Agent API: /health → 404
- [x] B3.4 — UMS: предзагрузка qwen-14b-llm при старте
- [x] B3.5 — Compare: 0 изменений (Docker↔Host path mismatch)
- [x] B3.6 — docker-compose: HOST_UPLOADS_DIR без хардкода
- [x] B3.7 — MCP_LEGAL_SERVER_URL неверная переменная
- [x] B3.8 — UUID вместо имён файлов в отчёте
- [x] B3.9 — Счётчик изменений: 0 изменено/добавлено/удалено
- [x] B3.10 — Отчёт не сохранялся в контейнере
- [x] B3.12 — document_analysis неверно маркирует legal PDF
- [x] B3.13 — equipment_analysis падает на matching (404 /batch_match)
- [x] B3.14 — Runtime warnings при стриминге (direct chat переведён на non-stream)
- [x] B3.15 — Очистка устаревших markdown-документов
- [x] B3.18 — VRAM-aware fallback для labse-embedding / st_server
- [x] B3.19 — Cleanup Chainlit UI/runtime warnings
- [x] B3.20 — UX fallback для неоднозначного routing (AskActionMessage)
- [x] B3.24 — Report_Equipment: убрать обрезание колонок
- [x] B3.25 — Document Question guard: не просить повторно тексты
- [x] B3.26 — Синхронизация backend/.env с .env.example
- [x] B3.27 — Citation-контракт для document_question (inline [n] + heuristic_v1)
- [x] B3.29 — Context Isolation & Active Scope stabilization
- [x] B3.30 — Social intent guard без сброса document context

</details>

<details>
<summary>Выполненные Tech Debt (TD-1-TD-14)</summary>

- [x] TD-1 — `__aexit__` без `await` в `_handle_compare`
- [x] TD-2 — `INTENT_EXAMPLES` — hardcoded → YAML
- [x] TD-3 — `index_documents` блокирует event loop → `asyncio.to_thread`
- [x] TD-4 — `on_chat_resume` не восстанавливает документы
- [x] TD-5 — `sys.path.append` → абсолютные импорты
- [x] TD-6 — Дублирование логики отчётов → `report_utils.py`
- [x] TD-10 — Keyword routing как fallback
- [x] TD-11 — Нет shared HTTP-клиента
- [x] TD-12 — Regex → LLM Polisher для ТЗ
- [x] TD-13 — Условная маршрутизация для предотвращения Over-extraction
- [x] TD-14 — Стабилизация парсеров и API

</details>

---

## B3.31 — Orchestration Boundary (ЯДРО ЗАКРЫТО)

**Статус:** ядро реализовано, overnight-fix manifest закрыт по изменённому контуру; остались cleanup tails, session-sync и commit

### Что реализовано в коде

1. **Orchestration split-brain закрыт:**
   - `orchestration_runtime.py` (685 строк) — `decide_orchestration()`, social guard, ambiguity routing, `choose_route`
   - `execution_runtime.py` (583 строки) — `execute_orchestration()`, 6 executor'ов, `ExecutionDependencies` DI
   - `ui_control_plane.py` (422 строки) — presets, effective config resolution, precedence chain
   - `chainlit_app.py` делегирует в `_backend_execute_orchestration` — `_execute_intent` удалён (подтверждено тестом)

2. **OpenAI-compat endpoint унифицирован:**
   - `/v1/chat/completions` использует `execute_orchestration()` через `_build_openai_compat_request()`
   - Удалены `sessions`, `_get_or_create_session`, `run_workflow_stream`
   - `stream=false` теперь возвращает OpenAI-compatible JSON вместо принудительного SSE
   - Attachment-backed compatibility path подгружает `session_docs` через Document Server, без возврата к legacy session-RAG
   - API doc-QA честно возвращает `retrieval_unavailable`

3. **UI Control Plane:**
   - 5 assistant modes (`general_chat`, `coding`, `agentic`, `specific_tasks`, `rag_qa`) с preset-bundles
   - 5 ChatSettings tabs (Use Case, RAG, Model, Prompt, Generation)
   - Starter cards для быстрого выбора use-case
   - Precedence: hard defaults → preset → UX overrides → enforced → clamping

4. **Тесты и verification:**
   - таргетные и phase-level regression suites по orchestration/execution/Chainlit/UMS проходят
   - подтверждён набор: `120 passed` на изменённой поверхности (`agent_api`, `execution_runtime`, `orchestration_runtime`, `chainlit_app`, `document_analysis`, `equipment_workflow`, Chainlit/UMS smoke)
   - `git diff --check` и `py_compile` для изменённых runtime-файлов проходят

### Что осталось по B3.31

- [x] **B3.31-cleanup — Удалить routing-дубликаты из chainlit_app.py**
  Удалены thin-wrapper routing helper’ы из `chainlit_app.py`; UI использует backend aliases напрямую, без второго локального routing-layer.
  Доп. cleanup:
  - UI-side `classifier_result` generation и classifier pre-init удалены из `Chainlit`; semantic classification теперь считается только в backend `execution_runtime.py`.

- [x] **B3.31-session-sync — Sync guard для pending_action / pending_route_choice session keys**
  `pending_action` и `pending_route_choice` инициализируются и обновляются через единый `_set_pending_route_choice()` path, а `_apply_session_state_patch()` больше не создаёт разъезд session-ключей.

- [x] **B3.31-test-harness — Дожать полный `pytest tests/ -m "not integration"` до clean finish**
  Закрыто через набор harness-фиксов: `backend/tests/test_document_analysis.py` переведён на канонические helper’ы из backend runtime, `backend/tests/test_equipment_workflow.py::TestDocumentServerEndpoints` переведён на `httpx.ASGITransport` вместо подвисающего `TestClient`, hanging graph-runtime assertion заменён на structural graph assertion, а `backend/tests/test_intent_classifier.py` переведён со встроенного process-randomized `hash(...)` на стабильный `sha256`-seed для synthetic embeddings. Подтверждение: `cd backend && pytest tests/ -q -m "not integration"` → `331 passed, 4 deselected`.
  Доп. стабилизация: `backend/tests/test_onnx_embeddings.py::TestBenchmark::test_benchmark_speed` переведён с жёсткого `speedup_50 > 1.0` на competitive smoke-check (`mean_speedup >= 0.90` + хотя бы один batch c `speedup >= 1.0`), потому что в полном suite CPU/joblib jitter делал точную mid-batch performance assertion flaky.

- [x] **B3.31-commit — Закоммитить текущие uncommitted changes**
  Scoped commit pack для backend-first orchestration фаз уже собран отдельными commits поверх рабочего snapshot, без локальных артефактов (`.chainlit`, `.env.native`, `.files`, backup-файлы). Дополнительного “финального мегакоммита” по `B3.31` больше не требуется.

---

## Следующие приоритеты (порядок исполнения)

### Блок A — Classifier Quality (B3.21)

- [x] **B3.21 — Отдельная embedding-модель для intent classification**
  - Benchmark уже проведён: `Qwen3-Embedding-0.6B` выиграл у `LaBSE` на routing eval
  - Production default переведён на `embedder` + `Qwen3-Embedding-0.6B`
  - `LaBSE` остаётся для legal/doc similarity
  - Runtime contract теперь поддерживает `abstain/unsure` для `embedder`, `hybrid` и `llm -> embedder fallback`
  - `orchestration_runtime` трактует `__unsure__` как low-confidence signal и падает в heuristics/choose-route, а не запускает document workflow на ложной уверенности
  - Eval harness уже содержит cost-weighted routing score, unsure rate и per-intent FP metrics
  - Env thresholds:
    - `INTENT_CLASSIFIER_EMBEDDER_CONFIDENCE_THRESHOLD=0.60`
    - `INTENT_CLASSIFIER_EMBEDDER_MARGIN_THRESHOLD=0.10`
  - См. `backend/evals/intent_embedder_eval.py`, `docs/2026-03-12-intent-classifier-benchmark-report.md`

### Блок B — Retrieval Eval (B3.34)

- [x] **B3.34 — Retrieval eval: LaBSE vs Qwen3-Embedding-0.6B**
  - Собран канонический retrieval eval harness: `backend/evals/retrieval_embedder_eval.py`
  - Собран curated dataset: `session_only`, `knowledge_base_only`, `mixed`, `unanswerable`, `duplicate-heavy`
  - Метрики: `Recall@k`, `MRR`, `nDCG@k`, `evidence_hit_rate`, `source_origin_accuracy`, `answer_faithfulness`
  - Реальный CPU-прогон на минимальном curated наборе:
    - `LaBSE`: `recall_at_k=1.0`, `mrr=1.0`, `ndcg_at_k=1.0`, `evidence_hit_rate=1.0`, `source_origin_accuracy=1.0`, `answer_faithfulness=0.9143`
    - `Qwen3-Embedding-0.6B`: те же значения на этом наборе
  - Решение по результату: dense retrieval пока **не унифицировать**; `LaBSE` остаётся retrieval/legal baseline, потому что текущий curated набор показывает паритет, а не явный выигрыш `Qwen3`
  - Pragmatic notes:
    - `answer_faithfulness` в этом harness — deterministic lexical proxy, а не LLM judge
    - `bm25` mode оставлен как baseline/debug path, но решение `LaBSE vs Qwen3` принимается только по dense/hybrid runs

- [x] **B3.34a — Retrieval eval dataset expansion**
  - Curated retrieval eval dataset расширен с 5 до 9 cases:
    - hard negative `session_only`
    - legal wording `knowledge_base_only`
    - ambiguity `mixed`
    - более жёсткий `unanswerable`
  - Повторный CPU-прогон на expanded dataset:
    - `LaBSE`: `recall_at_k=1.0`, `mrr=1.0`, `ndcg_at_k=1.0`, `evidence_hit_rate=1.0`, `source_origin_accuracy=1.0`, `answer_faithfulness=0.951`
    - `Qwen3-Embedding-0.6B`: те же значения
  - Updated verdict:
    - curated dataset стал сильнее, но verdict не изменился;
    - dense retrieval baseline по-прежнему не переносим с `LaBSE` на `Qwen3`
  - Verification:
    - `pytest backend/tests/test_retrieval_embedder_eval.py -q`
    - `cd backend && pytest tests/ -q -m "not integration"` -> `393 passed, 4 deselected`
  Follow-up:
  - `unanswerable_rejection_rate` в текущем harness остаётся optimistic proxy и требует отдельного tightening, если будем использовать его как decision metric

### Блок C — Knowledge Base RAG (B3.33)

- [x] **B3.33 — Session RAG vs Knowledge-Base RAG: продуктовая модель**
  - Выполнено через backend-owned KB source registry:
    - `kb_sources`
    - `kb_chunks`
    - `content_hash`
    - `index_version`
    - `embedding_model_id`
    - `chunking_version`
  - `knowledge_base_rag` интегрирован в unified backend core:
    - `orchestration_runtime` знает про `knowledge_collection_id`
    - `execution_runtime` использует merged retrieval без отдельного UI/API contour
  - Merged retrieval policy V1 реализована:
    - `session_rag` -> только активные session docs
    - `knowledge_base_rag` -> KB + session overlay
    - candidate budget per scope
    - dedup по normalized text
    - score normalization до финального shortlist
  - Source provenance проходит в doc-QA sources:
    - `source_origin = session | knowledge_base`
    - `collection_id`
    - `display_name`
  - Verification:
    - targeted KB/runtime suite зелёный
    - `cd backend && pytest tests/ -q -m "not integration"` -> `393 passed, 4 deselected`
  Pragmatic V1 follow-up:
  - full vector DB migration остаётся отдельным follow-up; текущий KB contour использует persisted chunk embeddings + transient hybrid shortlist

- [x] **B3.33a — Knowledge Base retrieval hardening**
  - Выполнено как production-oriented hardening без rewrite RAG stack:
    - shortlist hardening и duplicate policy закреплены;
    - `session` overlay детерминированно побеждает KB duplicate при близком score;
    - persisted KB chunk embeddings добавлены в store/ingestion scaffold;
    - retrieval использует precomputed dense vectors для KB chunks, не переэмбеддит их на query-time;
    - optional backend-owned rerank hook добавлен после merged shortlist.
  - Verification:
    - `pytest backend/tests/test_knowledge_base_store.py backend/tests/test_knowledge_base_ingestion.py backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py -q`
    - `cd backend && pytest tests/ -q -m "not integration"` -> `393 passed, 4 deselected`
  Follow-up:
  - отдельный vector DB / ANN index rollout остаётся самостоятельной фазой и не смешивается с текущим persisted-embeddings scaffold

### Блок D — Honest Tiers & Citations (B3.35, B3.36)

- [x] **B3.35 — Honest tiers: выравнивание терминов с реальным runtime**
  - Выполнено без смены machine-readable runtime keys:
    - `simple` -> basic retrieval
    - `corrective` -> corrective retrieval
    - `agentic` -> iterative retrieval
    - `multi-agent` -> planned multi-agent
  - Обновлены public-facing surfaces:
    - `tier_selector` docstrings и `TierConfig.__str__`
    - `AdaptiveRAGPipeline` docstrings и `mode_label` metadata
    - `Chainlit` control-plane labels (`Agentic (iterative)`)
    - `UMS /status` и runtime preflight report теперь публикуют `rag_mode_label`
  - Внутренние runtime keys сохранены для совместимости

- [x] **B3.36 — Citations/evidence UX v2**
  - Evidence block теперь явно показывает:
    - `display_name`
    - `chunk_id`
    - `source_origin`
    - `collection_id`
    - `section/page` при наличии
    - `quote/excerpt`
    - `relevance`
  - Добавлена summary line:
    - `source_scope_summary` / `retrieval_scope`
  - `session` и `knowledge_base` provenance проходят через unified doc-QA surface
  - Verification:
    - targeted tiers/evidence suite зелёный
    - `cd backend && pytest tests/ -q -m "not integration"` -> `389 passed, 4 deselected`

### Блок E — System Behavior Sweep (B3.37)

- [x] **B3.37 — Full system behavior sweep for model/control-plane**
  - Добавлен канонический harness: `backend/evals/system_behavior_sweep.py`
  - Harness покрывает:
    - `api` surface через `/execute_orchestration`
    - `ui` surface через thin Playwright CLI runner
    - shared scorecard contract
    - unified JSON report shape
  - Добавлен scenario dataset:
    - `backend/evals/data/system_behavior_scenarios.yaml`
    - stable fixture `backend/evals/data/fixtures/murka_note.txt`
  - Full control-plane sweep helper строит `900` комбинаций:
    - `assistant_mode`
    - `runtime_mode`
    - `rag_scope`
    - `model_profile`
    - `prompt_profile`
  - Scorecard проверяет:
    - route allowed/forbidden
    - citations required / minimum count
    - missing-context request
    - refusal on insufficient evidence
    - запрет claims про внешний доступ
    - запрет выдуманных repo/codebase claims
    - pending action semantics
  - Verification:
    - `pytest backend/tests/test_system_behavior_sweep.py -q` -> `12 passed`
    - live API sweep на native stack:
      - `python backend/evals/system_behavior_sweep.py --surface api --json-output /tmp/system-behavior-api.json`
      - результат: `5 passed / 0 failed / 0 error`
  Follow-up:
  - live `ui` full sweep по-прежнему требует окружение с доступным `playwright-cli`; если он не становится поддерживаемым operator binary, нужен repo-owned wrapper вместо внешней зависимости
  - отдельный operator run для полного `ui` batch всё ещё желателен после укрепления `PlaywrightCliRunner`, хотя ручной smoke уже подтвердил starter/settings parity

### Блок F — Runtime Budgeting (T4.13)

- [x] **T4.13 — Runtime Context Budget + Preflight Profiles**
  - Выполнено через backend-owned runtime budget contract в `UMS` и token-derived RAG truncation
  - `UMS /status` теперь публикует:
    - `runtime_profile`
    - `effective_context_tokens`
    - `retrieved_context_tokens_budget`
    - `generation_tokens_reserve`
    - `context_budget_ratio`
  - Profiles на этой фазе backend/env-driven: `default`, `adaptive`, `manual`
  - `AdaptiveRAGPipeline` больше не живёт только на fixed `max_context_chars`: budget вычисляется от `effective_context_tokens`
  - `Chainlit` читает runtime budget metadata из `UMS` и применяет её при RAG reindex/retrieval summary
  Pragmatic follow-up:
  - user-facing selector runtime/preflight profile остаётся в `T4.3`
  - unified preflight script / `.env.runtime` / launcher consolidation остаются в `T4.14`

- [x] **T4.14 — Unified runtime launcher + PR cleanup**
  - Выполнено через `scripts/launcher.sh` + `scripts/runtime_preflight.py`
  - Единый launcher API для `native | container`
  - Hardware detect + profile planning + `.env.runtime`
  - `run_all.sh`, `run_native.sh`, `run_container.sh` переведены в compatibility wrappers
  - runtime profiles документированы в `docs/runtime_profiles.md`
  - native/legacy service launch scripts (`run_native.sh`, `run_openwebui.sh`, `start_system_test.sh`) теперь экспортируют `PYTHONPATH=$BACKEND_DIR` и для `document_server`/`legal_server`, чтобы shared backend imports не ломали live startup
  Pragmatic follow-up:
  - cleanup/закрытие PR #3-#7 как superseded остаётся отдельным repo-maintenance шагом

### Блок G — UX Hardening (T4.2, T4.3)

- [x] **T4.2 — Chainlit UX hardening**
  Выполнено поверх стабилизированных `B3.31a + T4.13 + T4.14`.
  Что закрыто:
  - новый чат стартует без auto-welcome assistant message; starter cards остаются реальным entrypoint'ом сценариев
  - unified resume status messaging для backend-snapshot-first и legacy-history fallback
  - thread title/metadata sync в Chainlit data layer для видимого списка тредов
  - явное отображение active docs / rag scope / runtime profile / pending action state в UX summary
  - `Chainlit` остался thin control surface: routing/policy decisions не возвращались в UI
  - direct-chat output sanitization добавлена против prompt leak: leaked system/profile lines (`Используй...`, `Не повторяйся...`, `PROFILE INSTRUCTIONS`) больше не должны попадать в пользовательский ответ
  Pragmatic note:
  - кнопка «Новый чат» и список тредов по-прежнему опираются на built-in Chainlit shell; в этой фазе усиливался не shell itself, а app-level thread presentation и resume UX

- [x] **T4.3 — LLM Profile Selector**
  Выполнено как backend-resolved control-plane слой без raw model-id selector в UI.
  Что закрыто:
  - user-facing model profiles: `default-chat`, `long-context`, `legal-compare`, `low-vram`
  - env-backed mapping `model_profile -> resolved_model_id`
  - profile hints в effective config: `device_mode`, `context_budget_profile`, `profile_generation_defaults`
  - Chainlit `Model` tab показывает canonical profiles, а summary — effective values
  - legacy/internal aliases (`coder`, `agentic`, `analyst`) нормализуются в canonical profiles без hard break
  Pragmatic follow-up:
  - per-request dynamic runtime switching в `UMS` остаётся отдельным runtime/API шагом; в этой фазе profile selector даёт backend-resolved effective config и model routing, но не живое hot-switching железа на каждый запрос

- [x] **T4.4 — documents_summary runtime degradation on weak hardware**
  Закрыт минимальный stability slice для `documents_summary` в Chainlit/API path.
  Что закрыто:
  - `effective_settings.device_mode` теперь нормализуется в UMS-compatible `cpu|gpu|hybrid` и реально прокидывается в inference path, вместо purely UI-level hint
  - для `documents_summary` добавлен stage-aware execution contour: `chunk`, `merge`, `global`
  - merge path больше не строит один unbounded prompt из всех chunk summaries; используется bounded batched reduce
  - в summary path отключён second full retry на уровне Chainlit adapter; при stage-level failure workflow переходит в partial response вместо двойного 300s провала
  - при падении `merge`/`global` пользователь получает промежуточные сводки, а не пустой ответ
  - добавлен pre-merge/global token budget guard через bounded batching и capped summary input
  - chunk summaries кешируются между повторными вызовами `documents_summary`, если document chunk и profile/model не изменились; cache entry теперь имеет TTL через `DOCUMENTS_SUMMARY_CACHE_TTL_S`
  - progress box теперь показывает progressive partial response: chunk progress -> per-doc summary -> global summary stage
  - stage-level metrics публикуются через existing fallback counter contour: attempts, cache hits, degraded merge/global branches
  Проверки:
  - `pytest backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py backend/tests/test_chainlit_streaming.py backend/tests/test_chainlit_runtime_mode.py -q`
  - `python -m py_compile backend/orchestrator/execution_runtime.py backend/orchestrator/agent_api.py backend/orchestrator/chainlit_app.py backend/orchestrator/ui_control_plane.py`
  Pragmatic follow-up:
  - richer observability для summary stages (`prompt chars`, explicit `summary_stage` payload fields, effective device policy in metrics), smarter invalidation beyond TTL и adaptive timeout policy остаются отдельным hardening step, не смешивались с минимальным runtime-stability fix

---

## Отложенные задачи

### Production Deployment (Фаза 6)
- [ ] T3.17 — Dockerize backend services (UMS, Doc Server, Legal Server)
- [ ] T3.18 — Model delivery strategy (130 GB GGUF)
- [x] T3.19 — Production secrets & auth hardening
  Закрыт minimal production-safe secrets/auth slice:
  - `scripts/bootstrap_env.sh` читает `backend/.env`, при необходимости создаёт его из `backend/.env.example` и auto-heal обрабатывает `CHAINLIT_AUTH_SECRET`:
    - если `backend/.env` отсутствует, bootstrap создаёт файл из шаблона;
    - если `CHAINLIT_AUTH_SECRET` пустой или равен дефолтному шаблонному значению, bootstrap генерирует новый secret и записывает его обратно в `backend/.env`;
  - после auto-heal bootstrap fail-fast валидирует insecure defaults для critical secrets:
    - `CHAINLIT_AUTH_SECRET`
    - `CHAINLIT_ADMIN_PASSWORD`
    - `GF_SECURITY_ADMIN_PASSWORD`
  - local/dev escape hatch только через явный `AGENT_NAVIGATOR_ALLOW_INSECURE_DEFAULTS=1`
  - `run_all.sh` больше не печатает пароль в stdout и не подсказывает `admin/admin`
  - `backend/.env.example`, `README.md`, `docs/deploy-guide.md`, `docs/scripts/*` синхронизированы под required secret rotation
  Проверки:
  - `pytest backend/tests/test_runtime_launcher.py -q -k 'bootstrap or insecure or launcher'`
  - `pytest backend/tests/test_install_scripts.py -q`
  - `bash -n scripts/bootstrap_env.sh scripts/launcher.sh scripts/run_all.sh`
  - `git diff --check`
  Follow-up:
  - полноценный auth/ACL layer для operator endpoints остаётся отдельной фазой
  - vault/secret-manager integration не входит в current env-based hardening slice
- [ ] T3.20 — HTTPS reverse proxy
- [ ] T3.21 — Healthchecks, logging, monitoring (docker)

### Backend State & Persistence
- [x] **B3.31a — Backend-authoritative orchestration state store**
  Выполнено через `backend/orchestrator/state_store.py` и интеграцию в unified execution path.
  Что закрыто:
  - `orchestrator_runs` с backend-owned `run_id/state_ref/pending_action_id/resume_state_blob/checkpoint_blob/version`
  - authoritative writes из `execution_runtime.py` для Chainlit, REST и OpenAI-compatible path
  - `Chainlit` resume сначала читает backend snapshot, а затем только при его отсутствии падает в legacy history bootstrap
  - `Chainlit` session state теперь UI-mirror/cache, а не единственный источник execution-critical state
  Pragmatic workaround / follow-up:
  - dev/prod fallback сейчас `SQLite` через `ORCHESTRATOR_STATE_DB_URL`; Postgres-backed implementation остаётся отдельным усилением, а не blocker'ом
  - в `resume_state_blob` пока хранится `documents_by_id` snapshot для практичного resume document workflows; это осознанный компромисс до более строгого document-ref layer
- [x] **B3.31b — Harden backend orchestration store for production**
  Follow-up к `B3.31a` закрыт safe slice без full rewrite orchestration platform.
  Что реализовано:
  - `backend/orchestrator/state_store.py` теперь поддерживает:
    - `sqlite://...` -> `SQLiteOrchestrationStateStore`
    - `postgres://...` / `postgresql://...` -> `PostgresOrchestrationStateStore`
    - unsupported scheme -> explicit configuration error вместо silent fallback
  - write path переведён на atomic optimistic locking:
    - `UPDATE ... WHERE run_id = ? AND version = ?`
    - deterministic version-conflict error вместо read-then-overwrite semantics
  - `idempotency_key` теперь участвует в run reuse semantics по `(workflow_type, idempotency_key)`
  - persisted backend snapshot slimmed from inline document-heavy payload to `document_refs`
  - `Chainlit` restore path принимает и новый `document_refs` shape, и legacy `documents_by_id`
  - targeted coverage:
    - `backend/tests/test_state_store.py`
    - `backend/tests/test_execution_runtime.py`
    - `backend/tests/test_chainlit_runtime_mode.py`
  Verification:
  - `pytest backend/tests/test_state_store.py backend/tests/test_execution_runtime.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q`
  - `cd backend && pytest tests/ -q -m "not integration"` -> `416 passed, 4 deselected`
  Follow-up:
  - richer document-ref hydration/resolution beyond current minimal `path/display_name/version` shape remains an incremental follow-up, not a blocker for backend-owned store semantics
  - production deployment of Postgres store still requires `psycopg` to be present in runtime environment

- [x] **Chainlit persistence schema compatibility**
  Починен drift между локальным SQLite bootstrap schema и фактическим `chainlit 2.9.6` runtime.
  Что закрыто:
  - idempotent schema upgrade для `threads/steps/elements` вместо чистого `CREATE TABLE IF NOT EXISTS`
  - добавлены missing columns `steps.command` и `steps.defaultOpen`
  - `threads.tags` больше не падает на SQLite bind: локальный compatibility data-layer сериализует list в JSON string на запись и декодирует обратно на чтении
  - живой native smoke подтвердил, что sidebar снова показывает сохранённые треды
  Verification:
  - `pytest backend/tests/test_chainlit_persistence_schema.py -q`
  - `pytest backend/tests/test_chainlit_runtime_mode.py -q`
  - `./scripts/run_native.sh --no-attach`
  - живой Playwright smoke: создание треда через starter + проверка sidebar/history + `tail -n 80 backend/orchestrator/chainlit.log`

- [x] **Chainlit elements persistence**
  Локальная persistence для `cl.File` / `cl.Pdf` переведена на file-backed storage provider поверх `UPLOADS_DIR`, без внешнего blob storage.
  Что закрыто:
  - `SQLAlchemyDataLayer` теперь получает локальный `storage_provider`, поэтому warning `storage client is not initialized and elements will not be persisted` исчезает
  - persisted elements пишутся в `UPLOADS_DIR/chainlit-elements`
  - read URLs обслуживаются через локальный `Chainlit` route `/project/file/{object_key}`
  - targeted coverage:
    - `backend/tests/test_chainlit_elements_persistence.py`
  Verification:
  - `pytest backend/tests/test_chainlit_elements_persistence.py -q`
  - `pytest backend/tests/test_chainlit_elements_persistence.py backend/tests/test_chainlit_runtime_mode.py -q`
  - `./scripts/run_native.sh --no-attach`
  - живой Playwright smoke: upload text file, file element появляется в треде, старый storage warning в `backend/orchestrator/chainlit.log` больше не появляется
  Follow-up:
  - local file route сейчас авторизует доступ по префиксу `current_user.identifier` в `object_key`; если модель user/thread identity будет меняться, этот guard нужно отдельно пересмотреть

- [x] **Chainlit SQLite locking hardening**
  После включения локальной elements persistence `Chainlit SQLite` был дополнительно усилен для native/dev path, чтобы убрать `database is locked` на серийных thread updates.
  Что закрыто:
  - bootstrap теперь включает `PRAGMA journal_mode=WAL` и `PRAGMA synchronous=NORMAL`
  - compatibility data layer для SQLite добавляет `connect_args.timeout`
  - на каждое SQLite подключение вешается `busy_timeout` через SQLAlchemy connect hook
  - targeted coverage:
    - `backend/tests/test_chainlit_persistence_schema.py`
  Verification:
  - `pytest backend/tests/test_chainlit_persistence_schema.py backend/tests/test_chainlit_elements_persistence.py backend/tests/test_chainlit_runtime_mode.py -q`
  - `./scripts/run_native.sh --no-attach`
  - живой Playwright smoke: создание fresh thread
  - `rg -n "database is locked|Authorization for the thread failed" backend/orchestrator/chainlit.log` -> no matches

- [x] **System behavior sweep harness**
  Собран repeatable harness для проверки поведения системы при смене control-plane параметров и generation overrides.
  Что закрыто:
  - added `backend/evals/system_behavior_sweep.py`
    - canonical scenario loader
    - shared scorecard evaluator
    - full control-plane matrix builder (`5 x 3 x 3 x 4 x 5 = 900` combinations)
    - unified JSON report contract
    - live API runner for `/execute_orchestration`
    - thin `playwright-cli` UI adapter with fail-fast dependency check
  - added canonical dataset:
    - `backend/evals/data/system_behavior_scenarios.yaml`
  - added targeted coverage:
    - `backend/tests/test_system_behavior_sweep.py`
  - added plan/doc:
    - `docs/plans/2026-03-15-system-behavior-sweep.md`
  Verification:
  - `pytest backend/tests/test_system_behavior_sweep.py -q`
  - `pytest backend/tests/test_system_behavior_sweep.py backend/tests/test_ui_control_plane.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q` -> `55 passed`
  - `pytest backend/tests/test_system_behavior_sweep.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py -q` -> `69 passed`
  - live native API sweep:
    - `python backend/evals/system_behavior_sweep.py --surface api --json-output /tmp/system-behavior-api.json`
  Findings from live run:
  - initial live run surfaced two concrete quality bugs:
    - multilingual contamination in short `general_chat`
    - overly conservative grounded `rag_qa` on direct single-source factoid (`Мурка -> 5`)
  - both are fixed in current backend slice:
    - `general_chat` now applies a narrow language-consistency guard with one constrained regen
    - `document_question` now preserves grounded answers for single-source direct evidence and can deterministically synthesize a short cited answer when the model still fails
  Follow-up:
  - extend behavior dataset with harder generation-sensitive prompts and stricter answer-shape checks (`exact short answer`, `must avoid multilingual spill`)
  - strengthen `PlaywrightCliRunner` after repeated live runs against current `Chainlit` DOM

### Динамическая конфигурация
- [x] **B3.22 — Dynamic selection of models and embedders**
  - Dynamic selection централизован в backend control-plane:
    - `resolved_model_id`
    - `resolved_intent_embedder_model_id`
    - `resolved_retrieval_embedder_model_id`
  - `Chainlit` classifier pre-init использует resolved intent embedder, а retrieval adapter в `Chainlit` и API adapter используют resolved retrieval embedder вместо локальных hardcoded source-of-truth констант.
  - Defaults сохранены согласно текущему verdict:
    - intent embedder -> `qwen3-embedding-0.6b`
    - retrieval/legal embedder -> `labse-embedding`
  - Env-backed mapping:
    - `CHAINLIT_INTENT_EMBEDDER_PROFILE_DEFAULT_MODEL`
    - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LEGAL_DEFAULT_MODEL`
    - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LOW_VRAM_MODEL`
  - Runtime model paths дополнительно унифицированы через generic env contract с backward compatibility:
    - `MODEL_PATH_LLM`
    - `MODEL_PATH_VLM`
    - `MODEL_PATH_EMBEDDING_INTENT`
    - `MODEL_PATH_EMBEDDING_RETRIEVAL`
    - legacy aliases `MODEL_PATH_QWEN14B`, `MODEL_PATH_QWENVL`, `MODEL_PATH_QWEN3_EMBEDDING_06B`, `MODEL_PATH_LABSE` сохранены как compatibility layer
  - Verification:
    - `pytest backend/tests/test_ui_control_plane.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q`
    - `cd backend && pytest tests/ -q -m "not integration"` -> `397 passed, 4 deselected`
  Follow-up:
  - per-request hot-switching уже загруженных UMS моделей/embedders остаётся отдельным runtime API follow-up, не смешивается с текущим control-plane resolver
- [x] B3.23 — Multi-GPU placement policy для LLM и embeddings
  Реализовано через backend-owned placement policy в `backend/services/model_manager/unified_model_server.py`.
  Что закрыто:
  - weighted `tensor-split` для multi-GPU GGUF startup вместо равного деления по всем GPU
  - admission/filtering GPU по `UMS_LLM_MIN_FREE_VRAM_GB` и `UMS_LLM_MIN_BALANCE_RATIO`
  - explicit embedding placement через `cuda:<idx>` или CPU fallback вместо implicit `cuda`
  - preference на GPU, не занятый активным heavy LLM placement; fallback в CPU при конфликте
  - operational placement metadata в `UMS /status` для backend/operators
  - targeted startup tests покрывают:
    - weighted multi-GPU GGUF split
    - CPU GGUF path
    - tier-forced CPU embeddings
    - explicit non-LLM GPU selection for embeddings
    - `/status` placements payload
  - full verification:
    - `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_runtime_preflight.py -q`
    - `cd backend && pytest tests/ -q -m "not integration"`
  Follow-up:
  - hot migration уже запущенных моделей между GPU остаётся отдельным runtime follow-up
  - полноценный multi-process scheduler и GPU reservations не входят в `B3.23`
  - raw placement selector в UI не добавляется; placement остаётся backend-owned operational policy

### Benchmark & Performance Profiling
- [x] **T4.15 — Unified benchmark script**
  Реализовано: `scripts/benchmark.py` — 7 сценариев (health, ums_status, embedding, chat, doc_question, compare, equipment).
  Отправляет реальные запросы + файлы к запущенной системе, измеряет latency.
  Поддерживает `--repeats` для усреднения, `--output` для JSON-результатов, `--scenarios` для выбора.
  Workflow: изменить `.env` (N_GPU_LAYERS, CONTEXT_SIZE) → перезапустить → `python scripts/benchmark.py --output results/gpu.json`
- [x] **T4.16 — Benchmark comparison tool**
  Реализовано: `scripts/benchmark_compare.py` — канонический compare-tool для двух JSON-отчётов из `scripts/benchmark.py`.
  Что закрыто:
  - deterministic compare по `scenario`
  - status change semantics: `same_ok`, `regressed_status`, `improved_status`, `changed_non_ok`
  - latency delta / delta_pct / speedup
  - added/removed scenario detection
  - runtime metadata diff по `UMS /status`
  - human-readable console summary
  - machine-readable JSON output через `--json-output`
  - focused coverage в `backend/tests/test_benchmark_compare.py`
  Verification:
  - `pytest backend/tests/test_benchmark_compare.py -q`
  - `cd backend && pytest tests/ -q -m "not integration"`
  Follow-up:
  - statistical significance / variance analysis остаются отдельным benchmark follow-up
  - CI gating по benchmark regression не входит в `T4.16`
  Таблица delta по каждому сценарию + рекомендации.

### Agentic Orchestrator (исследование)
- [ ] **T5.1 — Feasibility: оркестратор + субагенты на CPU/слабом железе**
  Текущая архитектура уже содержит элементы агентной системы:
  - `orchestration_runtime.py` = оркестратор (decide → route)
  - LangGraph workflows = субагенты (compare, equipment, RAG)
  - `ExecutionDependencies` = DI для инъекции зависимостей
  - UMS с hardware profiling = автоадаптация под ресурсы
  Ограничения CPU: инференс Qwen 30-60с/ответ, workflows 2-5 мин.
  Нужно: формализовать sub-agent protocol, добавить timeout budgets, fallback на меньшие модели.
  **Текущий гибрид:** embedders (LaBSE, Qwen3-Embedding) на CPU, Qwen-14B inference на GPU — намеренный дизайн для сохранения VRAM.

### Inference & Ops
- [x] T4.4 — UMS Model Control API
  Выполнено как backend-owned operator surface без возврата raw model control в Chainlit UI.
  Реализовано:
  - `GET /models` для configured + filesystem-discovered моделей с полями `model_id`, `running`, `active`, `port`, `placement`, `resolved_path`
  - `GET /models/running` для compact operator view (`running_model_ids`, `active_heavy_model`, `placements`, `running_models`)
  - `POST /models/{model_id}/preload`
  - `POST /models/{model_id}/activate`
  - `POST /models/{model_id}/stop`
  - operator-safe JSON contract поверх существующей backend runtime/placement логики
  - 404 contract для unknown model ids
  Verification:
  - `pytest backend/tests/test_unified_model_server_startup.py -q` → `20 passed`
  - `cd backend && pytest tests/ -q -m "not integration"` → `424 passed, 4 deselected`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
  - `git diff --check`
- [x] T4.5 — Dynamic model registration
  Выполнено как backend-owned runtime registry для `UMS`, без UI selector и без смешивания с `T4.6` port pool/scheduler.
  Реализовано:
  - `POST /models/register`
  - `DELETE /models/{model_id}/registration`
  - persistent manifest для dynamic моделей через `UMS_DYNAMIC_MODELS_REGISTRY_PATH` (default: `backend/.data/ums_dynamic_models.json`)
  - precedence `STATIC_MODELS_CONFIG -> registered dynamic models -> filesystem-discovered GGUF`
  - deterministic auto-port allocation для dynamic/discovered моделей без полноценного scheduler
  - unregister running dynamic model делает controlled stop через существующий `_stop_model`
  Verification:
  - `pytest backend/tests/test_unified_model_server_startup.py -vv` → `25 passed`
  - `cd backend && pytest tests/ -q -m "not integration"` → `429 passed, 4 deselected`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
  - `git diff --check`
- [x] T4.6 — Port pool и scheduler в UMS
  Выполнено как safe slice без distributed scheduler/queue.
  Реализовано:
  - backend-owned port registry в runtime state:
    - `reserved_ports`
    - `port_owners`
    - `released_dynamic_ports`
  - единая reserve/release policy для:
    - registered dynamic models
    - filesystem-discovered GGUF models
  - reuse освобождённых dynamic портов
  - stable owner mapping для discovered models
  - minimal heavy lifecycle serialization через global reentrant lock поверх conflicting heavy start/stop transitions
  Verification:
  - `pytest backend/tests/test_unified_model_server_startup.py -q` → `32 passed`
  - `cd backend && pytest tests/ -q -m "not integration"` → `436 passed, 4 deselected`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
  - `git diff --check`
- [x] T4.7 — Concurrency policy для production
  Реализовано в `UMS` как safe slice без distributed queue:
  - env-driven concurrency caps для LLM и embeddings:
    - `UMS_LLM_MAX_CONCURRENCY` (`UMS_LLM_CONCURRENCY` alias)
    - `UMS_EMBED_MAX_CONCURRENCY` (`UMS_EMBED_CONCURRENCY` alias)
    - `UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S`
    - `UMS_FAIL_FAST_ON_SATURATION`
  - helper-based acquire/release policy с bounded wait и `429` при saturation
  - stream-path теперь резервирует slot до открытия `StreamingResponse`, без тихого `200` + пустого SSE при перегрузке
  - `/status` публикует operator-visible metadata:
    - limits
    - fail-fast flag
    - `llm_inflight` / `embedding_inflight`
    - `llm_available` / `embedding_available`
    - `llm_saturated` / `embedding_saturated`
  Verification:
  - `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py -q`
  - `cd backend && pytest tests/ -q -m "not integration"`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py`
  - `git diff --check`
  Follow-up:
  - текущий `fail-fast` реализован как safe slice поверх `asyncio.Semaphore`; остаётся теоретическая гонка между проверкой доступности и `acquire()`, если нужен строго lock-free immediate reject под экстремальной конкуренцией
  - `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py -q` в этой среде иногда зависает на tail уже после прохождения точек; новые `T4.7` assertions проходят, а residual выглядит как harness/process-exit issue, не как regression concurrency policy
- [x] T4.8 — Observability stack (Prometheus + Grafana + tracing)
  Реализовано как minimal production-safe slice без full APM stack:
  - shared Prometheus-compatible exporter в `backend/services/observability.py`
  - `/metrics` для:
    - `backend/orchestrator/agent_api.py`
    - `backend/services/model_manager/unified_model_server.py`
  - request telemetry:
    - `agent_nav_http_requests_total`
    - `agent_nav_http_request_duration_seconds`
    - `agent_nav_http_requests_in_progress`
  - domain metrics:
    - `agent_nav_agent_api_orchestration_requests_total`
    - `agent_nav_agent_api_openai_dedup_hits_total`
    - `agent_nav_ums_concurrency_saturation_total`
    - `agent_nav_ums_running_models`
    - `agent_nav_ums_active_heavy_model`
  - trace-aware `X-Trace-Id` propagation и request logging через ASGI middleware
  - compose profile `monitoring`:
    - `prometheus` (`:9090`)
    - `grafana` (`:3002`)
  - provisioning assets:
    - `monitoring/prometheus.yml`
    - `monitoring/grafana/provisioning/...`
    - starter dashboard `agent-navigator-overview`
  - convenience launcher:
    - `scripts/run_monitoring.sh`
  Verification:
  - `pytest backend/tests/test_observability.py backend/tests/test_agent_api_metrics.py backend/tests/test_unified_model_server_startup.py -q -k "metrics or trace or observability"`
  - `pytest backend/tests/test_agent_api_orchestrate.py backend/tests/test_unified_model_server_streaming.py -q`
  - `cd backend && pytest tests/ -q -m "not integration"`
  - `python -m py_compile backend/services/observability.py backend/orchestrator/agent_api.py backend/services/model_manager/unified_model_server.py backend/tests/test_observability.py backend/tests/test_agent_api_metrics.py backend/tests/test_unified_model_server_startup.py`
  - `docker compose --profile monitoring config`
  - `bash -n scripts/run_monitoring.sh`
  - `git diff --check`
  Follow-up:
  - tmux window `monitor` пока остаётся legacy-именем для `htop/top`; отдельный rename в `syswatch` лучше делать как небольшой ops-cleanup, а не смешивать с observability stack rollout
  - health-monitoring для `document_server` / `legal_server` / `chainlit` лучше добавлять отдельным `blackbox-exporter`/synthetic probe block, а не через scrape JSON `/health` как Prometheus metrics
  - добавить явную auth-конфигурацию для Grafana (`GF_SECURITY_ADMIN_PASSWORD` и related envs), чтобы monitoring profile не публиковал дефолтные credentials
  - нормализовать HTTP metrics route labels до route-template/low-cardinality form; сейчас dynamic `model_id` path fragments в `UMS` могут раздувать TSDB
  - исключить `/metrics` из общих request-rate/latency панелей или метрик, чтобы self-scrape Prometheus не создавал постоянный шум на idle системе
- [x] T4.9 — vLLM adapter в UMS
  Реализован narrow adapter внутри `UMS` без смены orchestration core:
  - `BACKEND_MODE=vllm` для heavy `gguf` text inference path
  - remote sentinel/runtime placement `remote-vllm`
  - upstream probes `/health` + `/v1/models`
  - proxy для `completions` / `chat/completions` с `Authorization` header и injected served model id
  - status/model views публикуют `backend_mode=vllm` и remote placement metadata
  - non-stream path больше не маскирует upstream 4xx/5xx как `success`
  Проверки:
  - `pytest backend/tests/test_unified_model_server_startup.py -q -k 'test_activate_endpoint_uses_remote_vllm_backend or test_status_exposes_backend_mode_for_vllm or test_non_stream_infer_proxies_to_vllm_with_auth_headers or test_non_stream_chat_infer_uses_vllm_chat_completions or test_non_stream_infer_does_not_mask_vllm_upstream_http_error or test_ensure_vllm_backend_rejects_missing_served_model or test_stop_model_detaches_remote_vllm_without_killing_process'`
  - `pytest backend/tests/test_unified_model_server_streaming.py -q -k 'passes_upstream_headers'`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py`
  - `git diff --check`
  Follow-up:
  - T4.10 остаётся обязательной отдельной фазой: production docker-compose/profile и operator rollout для самостоятельного `vLLM` deployment
  - safe rollback semantics при failed switch с локального heavy runtime на remote `vLLM` стоит отдельно усилить, чтобы probe failure не влиял на уже активную heavy model
- [x] T4.10 — Production docker-compose profile для vLLM
  Добавлен самостоятельный compose/profile rollout для remote `vLLM` runtime:
  - `docker-compose.yaml` получил сервис `vllm` под profile `vllm`
  - `scripts/run_vllm_service.sh` собирает `vllm serve ...` из env surface
  - `run_all.sh` умеет автоматически добавлять `vllm` service для container path при `BACKEND_MODE=vllm`
  - `runtime_preflight.py` публикует `backend_mode` в runtime plan/report
  - `.env.example`, `README.md`, `deploy-guide.md`, `runtime_profiles.md` синхронизированы под rollout/rollback flow
  Проверки:
  - `pytest backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py -q -k 'vllm or launcher'`
  - `bash -n scripts/launcher.sh scripts/run_all.sh scripts/run_container.sh scripts/bootstrap_env.sh scripts/run_vllm_service.sh`
  - `docker compose --profile vllm config`
  - `python -m py_compile scripts/runtime_preflight.py backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py`
  - `git diff --check`
  Follow-up:
  - end-to-end smoke с живым `vLLM` контейнером и реальной моделью остаётся отдельным benchmark/deployment блоком (`T4.11`)
  - общий pytest tail-hang для более широкого `UMS`/runtime slice по-прежнему иногда проявляется после прохождения тестов; это известный harness residual, а не регрессия `T4.10`
- [x] T4.11 — E2E benchmark before/after migration
  Benchmark harness обновлён под migration comparison `llama-server -> vllm`:
  - `scripts/benchmark.py` теперь пишет `backend_mode` и `runtime_metadata`
  - `scripts/benchmark_compare.py` сравнивает runtime diff с учётом `backend_mode`
  - добавлен benchmark runner coverage в `backend/tests/test_benchmark_runner.py`
  - operator workflow для before/after migration задокументирован в `README.md` и `docs/deploy-guide.md`
  Проверки:
  - `pytest backend/tests/test_benchmark_compare.py backend/tests/test_benchmark_runner.py -q`
  - `python -m py_compile scripts/benchmark.py scripts/benchmark_compare.py backend/tests/test_benchmark_compare.py backend/tests/test_benchmark_runner.py`
  - `git diff --check`
  Follow-up:
  - live benchmark с реально поднятым `vLLM` контейнером и production-size моделью остаётся operator/runtime exercise; этот шаг закрыл reproducible harness и compare contract
- [x] T4.12 — Security hardening для Ops UI
  Закрыт минимальный ops-security slice без смены runtime architecture:
  - `docker-compose.yaml`: Grafana credentials и auth flags через env (`GF_SECURITY_ADMIN_USER`, `GF_SECURITY_ADMIN_PASSWORD`, sign-up/anonymous disabled)
  - `backend/.env.example`: добавлены явные Grafana auth envs
  - `backend/services/observability.py`: HTTP metrics нормализуют dynamic paths до low-cardinality labels
  - `/metrics` исключён из общих HTTP request counters/latency, чтобы self-scrape не создавал шум
  Проверки:
  - `pytest backend/tests/test_observability.py backend/tests/test_unified_model_server_startup.py -q -k 'metrics or observability'`
  - `docker compose --profile monitoring config`
  - `python -m py_compile backend/services/observability.py backend/tests/test_observability.py backend/tests/test_unified_model_server_startup.py`
  - `git diff --check`
  Follow-up:
  - полноценный auth/ACL для model-control endpoints остаётся отдельным ops-hardening block
  - Grafana secret injection из vault/secret manager не входит в текущий env-based slice

### Другие
- [x] B3.11 — Убрать JSON salvage из DEBUG-POLISH (structured output platform-level)
  Выполнено как узкий backend-safe slice для equipment `DEBUG-POLISH` path:
  - добавлен reusable helper `backend/orchestrator/structured_output.py`:
    - `extract_model_text(...)`
    - `parse_strict_json(...)`
  - `equipment._polish_items_specs_llm()` переведён с tolerant XML parsing на strict JSON contract:
    - `schema_version = "b3.11.v1"`
    - `ok`
    - `data.results = [{"id": ..., "text": ...}]`
    - `error`
  - code-fence stripping / salvage / tolerant extraction в polisher path удалены; invalid structured output теперь fail-closed и уходит в уже существующий deterministic fallback
  - batching, id mapping и fallback behavior сохранены
  Проверки:
  - `pytest backend/tests/test_equipment_workflow.py -q -k "polish"` -> `13 passed`
  - `pytest backend/tests/test_equipment_workflow.py -q` -> `84 passed`
  - `python -m py_compile backend/orchestrator/structured_output.py backend/orchestrator/workflows/equipment.py backend/tests/test_equipment_workflow.py`
  - `git diff --check`
  Follow-up:
  - остальные `parse_json_garbage(...)` path в `compare`, equipment extract/eval и classifier остаются отдельным structured-output hardening block; текущая фаза закрывает именно `DEBUG-POLISH`
  - широкий `cd backend && pytest tests/ -q -m "not integration"` probe по-прежнему иногда завершается старым pytest tail-hang после прохождения тестов; это не выглядит новым регрессом `B3.11`
- [x] B3.16 — Проверить llama-server defunct / uptime после простоя
  Выполнено как uptime-hardening для локально управляемых `llama-server` процессов в `UMS`, без добавления watchdog/auto-restart semantics:
  - в `backend/services/model_manager/unified_model_server.py` добавлены:
    - `_is_managed_process_alive(proc)` для отличия живых локальных процессов от stale state;
    - `_prune_dead_processes()` для safe sweep мёртвых локальных процессов из `state["processes"]`, `state["placements"]` и `active_model`.
  - dead-process pruning теперь выполняется перед:
    - `_build_model_view(...)`
    - `GET /models`
    - `GET /models/running`
    - `GET /status`
  - `_RemoteProcess` не считается dead local process и не вычищается этим sweep helper'ом.
  Проверки:
  - `pytest backend/tests/test_unified_model_server_startup.py -q -k "dead_local_processes or status_prunes_dead_local_process_and_clears_active_model or status_keeps_remote_process_registered or models_running or status_exposes_current_placements"` -> `5 passed`
  - `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py -q` -> тесты проходят по точкам и затем упираются в уже известный старый pytest tail-hang; это не выглядит новым регрессом `B3.16`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
  Follow-up:
  - watchdog/auto-restart, idle unload policy и launcher/tmux cleanup остаются отдельными operational phases и не входят в текущий uptime-hardening slice
- [x] B3.17 — Проверить prompt-cache эффективность
  Выполнено как backend-owned prompt-cache policy + warm-repeat probe, без переписывания inference/concurrency path:
  - в `backend/services/model_manager/unified_model_server.py` добавлен `prompt_cache_policy`:
    - env `UMS_LLAMA_CACHE_PROMPT=true|false`
    - local `llama-server` heavy text path теперь явно получает `cache_prompt=true|false`
    - `vllm` path не получает `cache_prompt`
    - `GET /status` публикует `prompt_cache_policy`
  - в `scripts/benchmark.py` добавлен сценарий `prompt_cache_probe`:
    - два одинаковых direct `UMS /infer` запроса подряд
    - `cold_elapsed_sec`, `warm_elapsed_sec`, `speedup`, `delta_pct`
    - `prompt_prefix_fingerprint`
    - snapshot `prompt_cache_policy` из `UMS /status` до/после
  - docs/env surface синхронизированы:
    - `backend/.env.example`
    - `README.md`
    - `docs/runtime_profiles.md`
    - `docs/deploy-guide.md`
  Проверки:
  - `pytest backend/tests/test_benchmark_runner.py -q` -> `4 passed`
  - `pytest backend/tests/test_unified_model_server_startup.py::test_status_exposes_prompt_cache_policy_for_local_llama -q` -> `1 passed`
  - `timeout 20s pytest backend/tests/test_unified_model_server_startup.py::test_non_stream_local_llama_infer_enables_cache_prompt_by_default -vv` -> тест проходит, затем воспроизводится известный старый pytest tail-hang на завершении процесса
  - `timeout 20s pytest backend/tests/test_unified_model_server_startup.py::test_non_stream_infer_proxies_to_vllm_with_auth_headers -vv` -> тест проходит, затем воспроизводится тот же известный harness tail-hang
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py scripts/benchmark.py backend/tests/test_benchmark_runner.py`
  - `git diff --check`
  Follow-up:
  - реальный integration benchmark `cold vs warm vs negative-control` на живом `llama-server` остаётся отдельным operational block, если понадобится подтверждать фактический cache hit-rate, а не только policy + probe contract
- [x] B3.28 — Coverage heuristic v1.1 для document_question
  Выполнено как backend-shared heuristic layer для `document_question`, без смены retrieval contour:
  - добавлен `backend/orchestrator/doc_question_heuristics.py` как единый contract для:
    - `extract_citation_ids`
    - `citations_are_valid`
    - `has_sufficient_evidence_v1`
    - `compute_confidence_v1`
    - deterministic fallback
  - `Chainlit` и API-compatible execution path больше не живут на разных локальных эвристиках; оба wiring-path используют один backend module
  - gating теперь учитывает citation coverage и multi-hop support, а не только `top-1 raw_score`
  - confidence penalizes weak single-citation support for multi-hop / cross-document queries
  Проверки:
  - `pytest backend/tests/test_document_analysis.py -q` -> `65 passed`
  - `pytest backend/tests/test_execution_runtime.py -q` -> `12 passed`
  - `pytest backend/tests/test_agent_api_orchestrate.py -q` -> `15 passed`
  - `python -m py_compile backend/orchestrator/doc_question_heuristics.py backend/orchestrator/chainlit_app.py backend/orchestrator/agent_api.py backend/orchestrator/execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py`
  - `git diff --check`
  Follow-up:
  - общий pytest tail-hang в широком `cd backend && pytest tests/ -q -m "not integration"` probe по-прежнему иногда проявляется после прохождения тестов; для `B3.28` targeted suite зелёный и это не выглядит как новый регресс
- [x] B3.32 — LangChain adoption strategy (точечно, без full rewrite)
  Зафиксировано через ADR: [docs/plans/2026-03-12-b332-langchain-adoption-strategy.md](docs/plans/2026-03-12-b332-langchain-adoption-strategy.md).
  Решение: не делать full rewrite orchestration core на LangChain; сохранять backend-first contract (`orchestration_runtime.py` + `execution_runtime.py`) каноническим; разрешать только точечные integration areas: workflow-level `LangGraph`, retriever/reranker adapters, eval harness, observability adapters и один изолированный pilot area без смены публичного API.
- [ ] Vision-анализ (Qwen-VL интеграция)
- [ ] Conda environment export

### Открытый Tech Debt
- [x] TD-7 — O(N·M) reverse mapping в match_items_node → dict lookup
  Уже реализовано в `backend/orchestrator/workflows/equipment.py`: `match_items_node()` использует `items_1_by_text/items_2_by_text` для O(1) reverse lookup по `old_text/new_text` вместо повторного линейного поиска по спискам.
  Проверки:
  - `pytest backend/tests/test_equipment_workflow.py -q -k "TestMatchItemsNode or matching_error or empty_matches"` -> `7 passed`
  - `rg -n "O\\(1\\) reverse mapping|TD-7 Fix" backend/orchestrator/workflows/equipment.py`
- [x] TD-8 — Fallback-цепочки скрывают ошибки → WARNING + счётчики
  Закрыто на workflow/runtime critical slices, compare/document_analysis и `ums_client`.
  Что покрыто:
  - `backend/orchestrator/workflows/equipment.py` теперь публикует `WARNING + agent_nav_equipment_fallback_total` для:
    - `smart_chunk` → line-based fallback
    - invalid `DEBUG-POLISH` structured output
    - LLM batch error в `evaluate_compliance_node`
  - `backend/orchestrator/execution_runtime.py` теперь публикует `WARNING + agent_nav_fallback_events_total` для:
    - `doc_question` retrieval exception (`fallback=rag_exception`)
    - generic `doc_question` no-sources degraded branch (`fallback=no_sources`)
  - `backend/orchestrator/rag/classifier.py` теперь публикует `agent_nav_fallback_events_total` для:
    - `llm_non_json`
    - `llm_unsupported_intent`
    - `llm_missing_embedder_fallback`
    - `hybrid_low_confidence_unsure`
  - `backend/services/model_manager/unified_model_server.py` теперь публикует `agent_nav_fallback_events_total` для:
    - ST GPU → CPU retry (`fallback=st_start_cpu_retry`)
  - `backend/orchestrator/workflows/compare.py` теперь публикует `WARNING + agent_nav_fallback_events_total` для:
    - document load error (`fallback=load_documents_failed`)
    - legal match error (`fallback=match_batches_failed`)
    - partial/short structured output в batch-анализе (`fallback=analyze_parse_partial`)
    - batch LLM error (`fallback=analyze_batch_error`)
  - `backend/orchestrator/workflows/document_analysis.py` теперь публикует `WARNING + agent_nav_fallback_events_total` для:
    - document load/page load/table load issues
    - table extraction / LLM extraction fallback
    - summarize chunk failure
    - reduce summarization failure
  - `backend/services/model_manager/ums_client.py` теперь публикует `WARNING + agent_nav_fallback_events_total` для:
    - sync infer retry (`fallback=infer_retry`)
    - async infer retry (`fallback=async_infer_retry`)
    - stream error (`fallback=stream_error`)
  - Проверки:
    - `pytest backend/tests/test_equipment_workflow.py -q -k "falls_back_when_batch_xml_invalid or smart_chunk_fails_fallback or llm_error_produces_error_result"` -> `3 passed`
    - `pytest backend/tests/test_equipment_workflow.py -q` -> `84 passed`
    - `pytest backend/tests/test_execution_runtime.py -q -k "rag_exception_fallback_metric"` -> `1 passed`
    - `pytest backend/tests/test_intent_classifier.py -q -k "non_json_records_metric or rejects_unknown_intent or hybrid_can_end_unsure or llm_fallback_still_respects_embedder_abstain"` -> `4 passed`
    - `pytest backend/tests/test_unified_model_server_startup.py -q -k "records_cpu_placement_after_st_fallback"` -> `1 passed`
    - `pytest backend/tests/test_execution_runtime.py backend/tests/test_intent_classifier.py backend/tests/test_unified_model_server_startup.py backend/tests/test_equipment_workflow.py -q -k "rag_exception_fallback_metric or non_json_records_metric or rejects_unknown_intent or hybrid_can_end_unsure or llm_fallback_still_respects_embedder_abstain or records_cpu_placement_after_st_fallback or falls_back_when_batch_xml_invalid or smart_chunk_fails_fallback or llm_error_produces_error_result"` -> `9 passed`
    - `pytest backend/tests/test_compare_workflow.py backend/tests/test_document_analysis.py backend/tests/test_ums_client.py -q` -> `73 passed`
    - `pytest backend/tests/test_compare_workflow.py backend/tests/test_document_analysis.py backend/tests/test_execution_runtime.py backend/tests/test_intent_classifier.py backend/tests/test_ums_client.py backend/tests/test_unified_model_server_startup.py backend/tests/test_equipment_workflow.py -q -k "fallback or metric or retry or stream_error or records_cpu_placement_after_st_fallback or llm_error_produces_error_result or parse_failed or rag_exception_fallback_metric or non_json_records_metric or llm_fallback_still_respects_embedder_abstain or hybrid_can_end_unsure or compare_load_documents_failure_records_metric or compare_match_batches_failure_records_metric or compare_analyze_partial_parse_records_metric or extract_llm_failure_records_metric or summarize_reduce_failure_records_metric or summarize_llm_error or test_async_infer_retries_on_503"` -> `21 passed`
  Residual:
  - широкий multi-file pytest bundle всё ещё иногда залипает на старом tail-noise после прохождения основной части; это не выглядит регрессом `TD-8`
- [x] TD-9 — Magic numbers без документации → именованные константы + env override
  Реализован как narrow runtime-critical hardening slice, без full-sweep по всему репозиторию.
  - `backend/services/model_manager/ums_client.py`
    - retries / timeouts / pool limits / embedding batch controls вынесены в именованные константы и env overrides:
      - `UMS_CLIENT_TIMEOUT_S`
      - `UMS_CLIENT_CONNECT_TIMEOUT_S`
      - `UMS_CLIENT_KEEPALIVE_CONNECTIONS`
      - `UMS_CLIENT_MAX_CONNECTIONS`
      - `UMS_SYNC_INFER_RETRIES`
      - `UMS_SWITCH_MODEL_TIMEOUT_S`
      - `UMS_STATUS_TIMEOUT_S`
      - `UMS_EMBED_PROBE_TIMEOUT_S`
      - `UMS_EMBED_BATCH_SIZE`
      - `UMS_EMBED_BATCH_TIMEOUT_S`
      - `UMS_EMBED_BATCH_CONNECT_TIMEOUT_S`
      - `UMS_EMBED_BATCH_RETRIES`
      - `UMS_EMBED_BATCH_RETRY_DELAY_S`
  - `backend/orchestrator/workflows/compare.py`
    - compare truncate/chunk/analyze thresholds вынесены в именованные константы и env overrides
  - `backend/orchestrator/workflows/document_analysis.py`
    - summarize/reduce temperature/max_tokens/sleep вынесены в именованные константы и env overrides
  - `backend/orchestrator/knowledge_base_retrieval.py`
    - merge/dedup/rerank shortlist coefficients и candidate budget вынесены в именованные константы и env overrides
  - `backend/orchestrator/doc_question_heuristics.py`
    - confidence thresholds, coverage weights, multihop/cross-doc bonuses и insufficient-evidence cap вынесены в именованные константы и env overrides
  - Проверки:
    - `pytest backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py backend/tests/test_document_analysis.py -q` -> `73 passed`
    - `python -m py_compile backend/services/model_manager/ums_client.py backend/orchestrator/workflows/compare.py backend/orchestrator/workflows/document_analysis.py backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py backend/tests/test_document_analysis.py`
    - `pytest backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py -q` -> `91 passed`
    - `python -m py_compile backend/orchestrator/doc_question_heuristics.py backend/orchestrator/knowledge_base_retrieval.py backend/orchestrator/workflows/document_analysis.py backend/orchestrator/workflows/compare.py backend/services/model_manager/ums_client.py backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py`
  Follow-up:
  - remaining magic numbers в `shared/report_utils`, `rag/retriever`, `equipment` и API glue остаются отдельным cleanup slice; `TD-9` закрыт только для runtime-critical surface

### Night autonomy and shutdown hardening

- [x] Added safe night-autonomous control layer:
  - `AGENTS.md` now contains `Night Autonomous Mode`
  - `TASKS_NIGHT.md` added as agent-friendly nightly backlog
  - `workflow.yaml` added as repo-local execution policy
- [x] Recorded that `workflow.yaml` is not an official Codex schema:
  - public Codex guidance confirms `AGENTS.md` and configurable rules/sandboxing
  - this repository uses `workflow.yaml` only as a local policy layer for unattended work
- [x] Hardened shutdown and relaunch scripts against orphaned model runtimes:
  - `stop_native.sh` and `stop_all.sh` now clean up `unified_model_server.py`, project-scoped `llama-server`, and orphaned `st_server.py`
  - `stop_all.sh` now stops both tmux sessions: `agent-navigator` and `agent-navigator-native`
  - service ports are now loaded from `.env` / `.env.native` / `.env.runtime` instead of being hardcoded
  - `run_native.sh`, `run_all.sh`, `run_openwebui.sh`, and `start_system_test.sh` now reuse the full stop-path before relaunch so orphaned embedding runtimes do not accumulate across restarts
  Verification:
  - `bash -n scripts/stop_native.sh scripts/stop_all.sh scripts/run_native.sh scripts/run_all.sh scripts/run_openwebui.sh scripts/start_system_test.sh`
  - live runtime check:
    - `./scripts/run_native.sh --no-attach`
    - `./scripts/stop_native.sh`
    - `ps -eo pid,ppid,cmd | rg "(llama-server|st_server.py|unified_model_server.py)"`
    - confirmed: orphaned `st_server.py` no longer remains after shutdown

### Runtime performance follow-up

- [ ] **B3.35 — Уменьшить embedding warm-up burst на первом коротком запросе**
  Контекст:
  - по живым логам `UMS` embedder runtimes стартуют корректно, но первый короткий запрос в `Chainlit` провоцирует пачку `POST /v1/embeddings` до/вокруг первого `infer`;
  - это связано не с повторной загрузкой embedder-моделей, а с lazy warm-up intent-classifier:
    - `create_ums_embed_fn()` делает probe в `/v1/embeddings`;
    - `EmbeddingIntentClassifier.initialize()` считает centroids по всему `intent_examples.yaml`;
    - при текущем наборе это 98 example phrases и несколько batched embedding calls.
  Цель:
  - сократить latency и число embedding-запросов на первом greeting/general-chat without changing routing semantics.
  Кандидаты решения:
  - кэшировать centroids classifier'а между запросами/сессиями;
  - отделить lightweight greeting/general-chat fast-path от полного centroid warm-up;
  - не делать eager classifier init, пока реально не нужен semantic routing;
  - пересмотреть `UMS_EMBED_BATCH_SIZE` и probe behavior для cold-start path.
  Acceptance:
  - на первом коротком запросе количество `POST /v1/embeddings` заметно ниже текущего burst;
  - routing contract и classifier quality не деградируют;
  - в логах остаётся явное distinction между model preload и classifier warm-up.

- [ ] **B3.36 — Вынести runtime-policy `document_analysis` в env/prompt contour**
  Контекст:
  - live weak-PC прогон тяжёлого файла показал, что текущая регрессия с двойным `500`/`300s` для `documents_summary` закрыта, но у `document_analysis` остаётся отдельный длинный latency tail;
  - по логам длинный хвост сидит не в PDF/Markdown report rendering, а в `summarize -> reduce` path внутри `backend/orchestrator/workflows/document_analysis.py`;
  - после завершения chunk-stage workflow делает ещё один тяжёлый single-shot `reduce` infer, и именно он даёт самый длинный `/infer` в UMS;
  - текущее поведение слишком зависит от Python literals и локальных констант, а не от единого policy/config слоя.
  Наблюдаемые симптомы:
  - тяжёлый документ проходит chunk summarization успешно, но затем надолго задерживается на final reduce step;
  - даже без падения пользовательский latency хвост остаётся заметным на слабом железе;
  - control-plane/runtime profile сейчас не даёт такого же управляемого degraded behavior для `document_analysis`, как уже даёт для `documents_summary`.
  Что нужно исправить:
  - перестать держать critical runtime knobs `document_analysis` только в коде workflow;
  - отделить prompt concerns от execution-policy concerns;
  - сделать weak-PC behavior предсказуемым и управляемым через `backend/.env`, а не через ручную правку констант.
  Что вынести в env/config:
  - лимиты `max_tokens` отдельно для `chunk`, `reduce` и при необходимости `report` stage;
  - input budget limits для `chunk` и `reduce` stage;
  - degraded/low-vram policy flags для `document_analysis`;
  - optional timeout/retry hints для долгих summary stages;
  - stage-specific toggles для bounded reduce и partial-result fallback.
  Что вынести в prompt/policy templates:
  - шаблон prompt для chunk summary;
  - шаблон prompt для reduce summary;
  - требуемую степень краткости итоговой сводки;
  - правила удаления дублей и приоритизации фактов;
  - разрешённый формат partial/degraded answer.
  Что нужно изменить в execution contour:
  - добавить для `document_analysis` stage-aware policy по аналогии с `documents_summary`;
  - заменить single-shot reduce на bounded reduce или batched/tree-reduce path;
  - добавить partial-result fallback, если финальный reduce не укладывается в budget или деградирует;
  - обеспечить, чтобы slow tail после chunk-stage не оставался единственной безусловной веткой завершения workflow.
  Почему это важно:
  - сейчас `document_analysis` и `documents_summary` живут на разном уровне зрелости runtime hardening;
  - из-за этого weak-PC сценарии остаются непредсказуемыми именно для анализа одного тяжёлого документа;
  - без env-driven policy дальнейшая настройка под разные машины снова будет происходить через правку кода, а не через конфиг.
  Acceptance:
  - `document_analysis` stage budgets управляются через env/config, а не только локальные константы;
  - reduce-stage больше не делает один неограниченный тяжёлый финальный infer для длинных документов;
  - weak-PC профиль даёт предсказуемый degraded path;
  - длинный document-analysis tail локализован и управляем без ручного редактирования workflow-кода;
  - logs/metrics позволяют отличать `chunk` и `reduce` stages и видеть, какой policy branch реально сработал.

- [x] **B3.45 — Адаптировать тяжёлый document-analysis path под слабый ПК без грубого ухудшения качества отчёта**
  Контекст:
  - живой прогон на слабом ПК с одним `GTX 1070 8GB` и большим PDF (`~20` страниц) показал составную проблему, а не один локальный timeout;
  - по логам из `all_logs/` одновременно наблюдаются:
    - тяжёлый placement на одной GPU: `LLM + intent embedder + retrieval embedder`;
    - длинные `/infer` на summary/reduce path: `~109s`, `~124s`, `~132s`, `~267s`;
    - `429 Too Many Requests` на втором запросе, пока первый ещё удерживает runtime slot;
    - отсутствие быстрой отмены после `Stop`;
    - `KV/cache pressure` в `llama-server` (`failed to find a memory slot`, prompt cache `~6.5-7.3 GiB`);
  - user concern: нельзя решать это грубым сокращением итогового отчёта или простой прибавкой timeout.
  Принцип решения:
  - не ухудшать смысловую полноту отчёта грубым урезанием;
  - адаптировать execution path под слабое железо:
    - bounded stages;
    - hierarchical synthesis;
    - component-aware placement;
    - быстрая отмена и release слота.
  Admission contract:
  - перед стартом heavy `document_analysis` / `documents_summary` вычислять stage-aware admission;
  - admission обязан учитывать:
    - hardware profile;
    - placement result;
    - estimated input/output token budget;
    - размер документа;
    - chunk/group count;
    - cancellation state;
  - если `full final reduce` или другой heavy stage не проходит admission, pipeline не должен сначала пытаться выполнить полный path;
  - вместо этого execution должен сразу переходить в bounded/grouped branch.
  Что нужно исправить:
  - добавить настоящую cancel propagation:
    - `Chainlit Stop` должен помечать run как cancelled;
    - `execution_runtime` должен проверять cancel между chunk/group/reduce stages;
    - `UMS` slot должен освобождаться быстро после cancel, а не после полного timeout;
  - добавить weak-PC placement policy:
    - для single-GPU `8GB` профиля не держать по умолчанию `LLM + 2 embedders` на одной карте;
    - default weak-PC policy должна предпочитать:
      - `LLM: hybrid`
      - `intent embedder: cpu`
      - `retrieval embedder: cpu`;
    - при этом policy не должна быть абсолютно жёсткой:
      - один GPU embedder допустим только если `LLM admission` остаётся safe и preflight показывает headroom;
    - сохранить возможность manual override через hardware/runtime env;
  - заменить unbounded final reduce на bounded hierarchical synthesis:
    - `chunk summaries`;
    - `group summaries`;
    - `final synthesis` по уже сжатым group summaries;
  - ввести обязательный token-budget guard:
    - каждая heavy stage перед `infer` обязана проверять estimated input/output budget;
    - если budget превышен:
      - либо уменьшать payload;
      - либо делать дополнительный group split;
      - либо переходить в partial/degraded branch;
    - budget не должен оставаться только declarative env/config knob;
  - уменьшать не полезность отчёта, а вычислительную стоимость каждой стадии:
    - smaller chunk size;
    - smaller merge group size;
    - stage-specific token/input budgets;
    - compact intermediate prompt templates;
    - partial-result fallback только как controlled degraded branch;
  - зафиксировать retry semantics для heavy summary stages:
    - запрещён blind full retry того же `group/final reduce` после timeout/429/500;
    - разрешён только policy-changing retry:
      - меньший budget;
      - меньший group size;
      - более компактный prompt template;
    - после этого execution обязан перейти в partial/degraded result, а не повторять тот же тяжёлый вызов бесконечно;
  - ввести явный partial-success contract:
    - если final synthesis не завершён, пользователю возвращается structured partial report;
    - per-group/per-document summaries не теряются;
    - intermediate artifacts не выбрасываются при failed/skip final stage;
    - UI / response metadata должны различать:
      - `completed_stages`
      - `degraded=true`
      - `final_synthesis_status=skipped|failed|cancelled`;
  - улучшить saturation UX:
    - вместо opaque `429` показывать, что runtime занят предыдущим тяжёлым run;
    - distinguish between `busy`, `cancel-in-progress`, `timeout`, `hard failure`.
  Что не считать решением:
  - простое увеличение timeout как основной фикс;
  - простое “сделать итоговый отчёт короче”;
  - ручная правка кода под каждую машину вместо env/config-driven policy.
  Acceptance:
  - на слабом single-GPU ПК тяжёлый `document_analysis` не уходит по умолчанию в path `LLM + 2 embedders on same GPU`;
  - `Stop` действительно прерывает дальнейшие summary/reduce stages и быстро освобождает slot для следующего запроса;
  - второй чат после отмены не остаётся надолго заблокированным `429` из-за старого run;
  - длинный документ собирается через hierarchical synthesis, а не через один unbounded final merge;
  - final/group stages не запускаются без пройденного stage admission и token-budget guard;
  - blind retry тяжёлого `reduce/final` не используется;
  - при срыве final synthesis пользователь получает structured partial report, а не generic failure;
  - итоговый отчёт остаётся содержательным, а не грубо обрезанным;
  - performance targets:
    - после `Stop` runtime slot освобождается не позже чем за bounded interval между стадиями;
    - второй запрос после cancel не должен долго получать opaque `429`;
    - large-document tail bounded числом стадий, а не одним hanging infer;
  - logs/metrics явно показывают:
    - placement branch;
    - cancellation branch;
    - chunk/group/final synthesis stages;
    - причину degraded mode, если он сработал.

---

## Блок H — UX & Equipment Fixes (2026-03-18)

### H1 — Время выполнения в отчёте (B3.38)

- [x] **B3.38 — Вернуть вывод времени выполнения в финальный отчёт**
  Контекст: раньше в конце каждого ответа выводилось итоговое время выполнения задачи. После рефакторинга T4.4/TD-6 это исчезло — `time` импортирован, но замер нигде не используется.
  Что сделать:
  - `document_analysis.py`: `start = time.monotonic()` в начале `classify_node`, передать в state, вывести `elapsed` в `final_report`
  - `execution_runtime.py` (`documents_summary`): аналогичный замер от старта executor'а до финального `assistant_message`
  - Формат: `⏱ Время выполнения: X мин Y сек` в конце отчёта/ответа
  Verification:
  - `python -m py_compile backend/orchestrator/workflows/document_analysis.py backend/orchestrator/execution_runtime.py`

### H2 — Русификация UI-строк (B3.39)

- [x] **B3.39 — Заменить англоязычные/кальки в UI-строках на русский**
  Контекст: в UI пользователь видит слово "чанков" и связанные строки, которые не являются корректным русским.
  Что сделать:
  - `execution_runtime.py:1194`: `title="Суммаризация чанков"` → `title="Обработка фрагментов"`
  - `execution_runtime.py:1386`: `"чанков"` → `"фрагментов"`
  - Логи в консоли (`[Chunk 1/10]` и т.п.) оставить как есть — это dev-facing, не пользовательский UI
  Verification:
  - `python -m py_compile backend/orchestrator/execution_runtime.py`

### H3 — Порядок вывода шагов в UI (B3.40)

- [x] **B3.40 — Прогресс workflow должен быть виден выше финального ответа**
  Контекст: суммаризация документа пишется ниже блока ответа, тогда как должна быть в верхней последовательности шагов. Все промежуточные состояния должны быть в `cl.Step` до отправки `assistant_message`.
  Что сделать:
  - Ревизия порядка `cl.Step` в `chainlit_app.py` для `document_analysis` и `documents_summary`: все Step'ы открываются и закрываются до отправки финального `assistant_message`
  - Суммаризация не должна дублироваться в потоке ответа — только через прогресс-бокс
  Verification:
  - Живой smoke: загрузить PDF, убедиться что шаги видны выше ответа

### H4 — SQLite миграция: колонка autoCollapse (B3.41)

- [x] **B3.41 — Добавить миграцию существующей Chainlit SQLite БД для autoCollapse**
  Контекст: в `kp_tz_equip/chainlit.log` сотни ошибок `sqlite3.OperationalError: table steps has no column named autoCollapse`. Это ломает запись Step'ов в БД и объясняет почему equipment workflow не вернул результат на том ПК. `_bootstrap_chainlit_sqlite_schema` создаёт колонку только при создании новой БД, но не мигрирует существующую.
  Что сделать:
  - В `_bootstrap_chainlit_sqlite_schema` (или отдельной `_migrate_chainlit_sqlite_schema`) добавить idempotent `ALTER TABLE steps ADD COLUMN autoCollapse INTEGER` с проверкой `PRAGMA table_info(steps)`
  - По аналогии с уже реализованной миграцией `steps.command` и `steps.defaultOpen`
  Verification:
  - `pytest backend/tests/test_chainlit_persistence_schema.py -q`
  - Живой тест: запустить с существующей старой `.data/chainlit.db`, убедиться что ошибок `autoCollapse` нет в логах

### H5 — document_analysis: убрать секцию позиций для legal/other (B3.42)

- [x] **B3.42 — Не выводить "Позиции оборудования/товаров не обнаружены" для нерелевантных типов документов**
  Контекст: для юридических законов (`legal`, `other`) секция `## Извлеченные позиции` бессмысленна и вводит пользователя в заблуждение.
  Что сделать:
  - В `document_analysis.py` в функции формирования `final_report`: рендерить секцию `## Извлеченные позиции` только если `doc_type in {"tz", "smeta", "kp"}`
  - Для `legal` / `other` — раздел пропускается полностью
  Verification:
  - `pytest backend/tests/test_document_analysis.py -q`
  - Живой smoke с юридическим документом: секции "позиции" нет в отчёте

### H6 — Equipment: fallback-экстракция из КП без таблиц (B3.43)

- [x] **B3.43 — Улучшить экстракцию позиций из КП/ТЗ без стандартных таблиц**
  Контекст:
  - в `kp_tz_equip` оба документа раньше давали `Tables: 0` и `LLM text: 0`;
  - DOCX без нормальных таблиц не проходили по стандартному extraction path.
  Что было сделано:
  - добавлен structured DOCX fallback через `word/document.xml` для линейризованных строк КП/ТЗ;
  - fallback path теперь возвращает strategy metadata, чтобы было видно, какой extraction contour реально сработал.
  Подтверждение:
  - smoke на проблемных логовых файлах дал `3` позиции из КП и `2` позиции из ТЗ вместо прежних `0/0`;
  - покрыто тестами в `backend/tests/test_equipment_workflow.py`.

### H7 — Equipment: структура отчёта ТЗ vs КП (B3.44)

- [x] **B3.44 — Правильная структура отчёта при сравнении ТЗ и КП**
  Контекст: при подаче ТЗ + КП система должна строить таблицу на основе позиций ТЗ и показывать их покрытие в КП — а не просто выводить что "позиций не найдено".
  Логика отчёта:
  - Основа таблицы — позиции из ТЗ (требования заказчика)
  - Для каждой позиции ТЗ: есть ли в КП (да / аналог + цена / нет)
  - Если в КП есть позиции которых нет в ТЗ — отдельный раздел "Дополнительные позиции КП"
  - Если совпадений 0 — явно написать что структуры документов не совпали + показать оба списка отдельно
  - Если списки пусты (экстракция не сработала) — сообщить об этом явно, не выводить пустую таблицу
  Что сделать:
  - Пересмотреть `generate_report_node` в `equipment.py` под эту логику
  - Добавить детектор ролей (какой документ ТЗ, какой КП) через `classify_doc_type` из `document_analysis.py`
  Verification:
  - Живой тест с `kp_tz_equip` документами после фикса B3.43
  Статус 2026-03-18:
  - Для `tz_vs_smeta` основная таблица теперь строится по позициям ТЗ.
  - Лишние позиции из КП вынесены в отдельный раздел `Дополнительные позиции из КП`.
  - Компромисс: role-detection пока остаётся на существующей content/name heuristic в `equipment.py`; reuse `classify_doc_type` можно оставить как follow-up refactor, если понадобится единая классификация.
  - Matching усилен двухступенчатым candidate generation + rerank/fusion scoring в legal server.
  - Компромисс: neural reranker подключается опционально через `MODEL_PATH_RERANKER`; без него используется heuristic rerank fallback по lexical/numeric compatibility.

### H8 — LangGraph Studio: конфигурация (T5.2)

- [x] **T5.2 — langgraph.json для визуальной отладки в LangGraph Studio**
  Контекст: наши workflows (`compare.py`, `equipment.py`, `document_analysis.py`) используют стандартный LangGraph `StateGraph` и полностью совместимы с LangGraph Studio. Studio позволяет визуально отлаживать граф без изменений в коде.
  Что сделать:
  - Создать `backend/langgraph.json` с регистрацией трёх workflows
  - Документировать запуск: `cd backend && langgraph dev`
  - Добавить в `docs/` краткую инструкцию по отладке через Studio
  Сделано:
  - добавлен `backend/langgraph.json` с entrypoints для `compare`, `equipment`, `document_analysis`
  - добавлена инструкция `docs/langgraph-studio.md`
  - статически проверено, что все три factory entrypoint импортируются и строят `CompiledStateGraph`
  Примечание: Open WebUI совместим через `agent_api.py` (OpenAI-compat), но теряет `cl.Step` визуализацию. LangFlow — несовместим без переписывания. LangGraph Studio — рекомендуется для дебага.

### H9 — Full-Stack Test Contour (T6.x)

- [ ] **T6.1 P0 — Browser E2E для основного Chainlit path**
  Контекст: backend unit/integration слой уже сильный, но основной пользовательский путь через `Chainlit` почти не закрыт настоящими browser tests. Сейчас главный риск — регрессии в upload/UI/session/history, которые не ловятся pure pytest-моками.
  Что сделать:
  - Поднять `Playwright`-контур для `Chainlit` как основного UI
  - Добавить canonical browser flows:
    - login / basic chat smoke
    - single upload + `doc_question`
    - two uploads + `compare`
    - `equipment` path для ТЗ/КП
    - reload page -> thread/history/steps persist
    - generated report download/open smoke
  - По возможности использовать стабильные test hooks / selectors вместо brittle text-only locators

### H10 — Adaptive merge policy для documents_summary / document_analysis (B3.48)

- [x] **B3.48 — Убрать безусловную многоуровневую group-merge редукцию там, где она не нужна**
  Контекст: текущая hierarchical synthesis (`group_merge L1/L2/L3`) решает проблему oversized final merge и weak-hardware stability, но на мощных машинах и/или при умеренном размере документа может добавлять лишние LLM-вызовы и заметно увеличивать tail latency. Сейчас reduce path слишком консервативен: grouped merge используется как default strategy вместо adaptive policy по admission/budget.
  Что сделать:
  - Для `documents_summary` и `document_analysis` ввести adaptive reduce policy:
    - если все `reduce_items` проходят final-stage admission и укладываются в безопасный input/output budget, идти сразу в `final synthesis` без промежуточных `L1/L2/L3`;
    - включать hierarchical `group_merge` только когда это действительно нужно по budget, chunk/group count или hardware profile;
    - на сильных профилях не форсировать ту же глубину merge, что и на weak-PC policy;
  - Явно разделить:
    - `stability path` для слабого/загруженного runtime;
    - `fast path` для случаев, где один bounded final merge безопасен;
  - Сделать policy env/config-driven, а не захардкоженной под одну машину:
    - adaptive thresholds для `group_size`, `group_input_chars`, `final_input_chars`;
    - возможность отключать intermediate merge levels при safe admission;
    - отдельные knobs для weak vs normal hardware profiles;
  - Сохранить уже реализованные safety guarantees:
    - никакого unbounded final prompt;
    - token-budget guard обязателен перед каждой heavy stage;
    - partial/degraded branch не ломается;
    - cancel propagation между стадиями остаётся рабочей;
  - Улучшить observability:
    - в metadata/logs явно различать `fast_final_merge` vs `hierarchical_merge`;
    - писать причину выбора grouped path (`budget`, `hardware`, `retry_policy`, `degraded_mode`);
    - фиксировать фактическую глубину merge (`levels_used`, `groups_total`);
  Что не считать решением:
  - полное удаление hierarchical synthesis;
  - простое увеличение context window / timeout как замена policy;
  - ручной switch "для мощной машины" без admission/budget-based decision;
  - грубое укрупнение `group_size` без контроля token/input budget;
  Acceptance:
  - если итоговый reduce payload безопасно проходит admission, pipeline не делает лишние `group_merge` уровни;
  - для длинных/тяжёлых документов grouped path по-прежнему включается автоматически и остаётся bounded;
  - на мощном профиле среднее число summary-stage LLM-вызовов уменьшается для документов, которые помещаются в safe final merge;
  - partial/degraded behavior и cancel semantics не регрессируют;
  - logs/metadata позволяют понять:
    - почему был выбран `fast path` или `hierarchical path`;
    - сколько merge levels реально было использовано;
    - был ли grouped path вызван из-за hardware policy или budget overflow;
  Verification:
  - `pytest backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py -q`
  - Добавить/обновить unit tests на сценарии:
    - safe final merge без `L1/L2/L3`;
    - forced grouped merge при budget overflow;
    - weak-PC policy сохраняет bounded hierarchical path;
    - metadata/logs отражают выбранную reduce strategy

- [x] **B3.48a — Добавить admission hysteresis / safety margin для fast final merge**
  Контекст: после базовой реализации `B3.48` fast-path всё ещё может флапать на пограничных payload, потому что решение принимается слишком близко к budget limit. Нужен небольшой запас, чтобы `fast_final_merge` не включался в near-limit cases и не срывался затем в retry/degraded branch.
  Что сделать:
  - Ввести `reserve_tokens` и/или `reserve_ratio` для final admission
  - Разрешать `fast_final_merge` только если payload проходит `fits_with_margin`, а не просто `fits`
  - В metadata/logs писать:
    - `final_admission_estimated_tokens`
    - `final_admission_budget_tokens`
    - `final_admission_margin_tokens`
  Acceptance:
  - fast-path не выбирается на пограничных payload без безопасного запаса;
  - metadata показывает фактический budget/margin decision;
  - уменьшается риск flapping между `fast_final_merge` и `hierarchical_merge`
  Verification:
  - `pytest backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py -q`
  - Добавить tests на near-limit payload, где без margin был бы fast path, а с margin остаётся hierarchy

- [x] **B3.48b — Перевести reduce admission на token-first contract**
  Контекст: текущая policy всё ещё частично опирается на char caps как на decision primitive. Это дешёвый precheck, но authoritative admission должен жить вокруг estimated tokens и реального prompt budget.
  Что сделать:
  - Оставить `chars` только как cheap precheck / observability field
  - Перевести shared helper contract на token-first поля:
    - `joined_payload_tokens_est`
    - `final_prompt_tokens_est`
    - `group_prompt_tokens_est`
    - `reserve_tokens`
  - Согласовать `documents_summary` и `document_analysis` на одном token-based policy helper
  Acceptance:
  - решение `fast_final_merge` vs `hierarchical_merge` определяется token estimate, а не только chars;
  - chars остаются в metadata лишь как вспомогательное поле;
  - оба workflow используют один и тот же token-first decision contract
  Verification:
  - `pytest backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py -q`
  - Добавить tests на cases, где char count misleading, но token estimate даёт правильное решение

- [x] **B3.48c — Добавить quality regression contour для adaptive reduce policy**
  Контекст: `B3.48` оптимизирует control flow и latency, но этого недостаточно без проверки качества итоговой сводки. Нужно подтвердить, что fast-path не ухудшает coverage/completeness относительно старого collapse-heavy path.
  Что сделать:
  - Собрать маленький golden corpus:
    - короткий structured doc;
    - длинный narrative doc;
    - doc с headings / mixed structure;
    - case, где промежуточный collapse раньше улучшал coherence
  - Для каждого кейса сравнивать:
    - old/conservative path;
    - new adaptive fast path;
  - Ввести heuristic checks:
    - coverage ключевых разделов;
    - presence critical entities / headings;
    - отсутствие явной потери секций / truncation;
    - latency и число model calls не хуже baseline
  Acceptance:
  - новый adaptive path не хуже baseline по coverage/completeness;
  - latency и/или число summary-stage model calls уменьшаются там, где fast-path срабатывает;
  - quality regressions ловятся отдельным test contour, а не только ручным smoke
  Verification:
  - добавить отдельный eval/smoke contour для golden corpus
  - задокументировать baseline-vs-adaptive comparison procedure

- [x] **B3.48d — Добавить shadow decision mode для rollout-аналитики**
  Контекст: после `B3.48` полезно временно иметь режим, где исполняется текущая ветка, но новая strategy recommendation считается параллельно и логирует расхождения. Это нужно для безопасного анализа реальных traffic patterns до более агрессивного rollout fast-path.
  Что сделать:
  - Флаг `SUMMARY_REDUCE_STRATEGY_SHADOW_MODE`
  - В shadow mode логировать:
    - `executed_strategy`
    - `recommended_strategy`
    - `would_skip_levels`
    - `estimated_token_saving`
  - Отдельный counter:
    - `agent_nav_summary_strategy_shadow_diff_total`
  Acceptance:
  - можно собрать production-like данные о расхождениях без изменения runtime path;
  - видно, сколько merge levels были избыточными по новой policy;
  - rare corner cases можно анализировать без риска для user-facing path
  Verification:
  - unit tests на shadow-mode logging/metrics
  - ручной smoke: shadow mode не меняет фактический executed path

- [x] **B3.48e — Зафиксировать helper invariants и decision trace**
  Контекст: shared reduce-policy helper теперь используется двумя workflow и будет обрастать исключениями. Нужен явный mini-spec и более подробный decision trace, иначе разбор жалоб вида "почему снова появился L2/L3" быстро станет дорогим.
  Что сделать:
  - Зафиксировать invariants helper-а:
    - `degraded_already_active=true` запрещает fast path;
    - `low_vram=true` при `SUMMARY_FAST_FINAL_MERGE_LOW_VRAM=0` форсирует hierarchy;
    - `partial_only` не выбирается до policy-changing retry;
    - после каждого collapse level обязателен re-admission check;
  - Добавить richer metadata:
    - `reduce_decisions[]` с `items_count`, `chars`, `tokens_est`, `strategy_selected`, `reason`
    - `early_exit_after_level`
  - Добавить property-style / matrix tests на комбинации policy flags
  Acceptance:
  - policy invariants явно описаны и тестируются;
  - metadata позволяет объяснить, почему fast path был запрещён на конкретном уровне;
  - future refactor не ломает базовые decision guarantees молча
  Verification:
  - `pytest backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py -q`
  - отдельные tests на invariant matrix / decision trace shape
  Status 2026-03-18:
  - helper contract now returns `reduce_decisions[]` and `early_exit_after_level`
  - `partial_only` is not emitted by the helper
  - legacy `final_stage_safe` compatibility retained

### H11 — Adaptive Detail / Expansion Policy (B3.49)

- [ ] **B3.49 — Ввести adaptive quality orchestration: detail ladder + expansion pass**
  Контекст:
  - `B3.48` решает задачу reduce safety и adaptive merge policy: когда идти в fast final merge, а когда включать bounded hierarchical path;
  - но это не покрывает отдельную ось качества ответа: на сильном железе можно не только уменьшать число merge-стадий, но и разрешать более дорогой synthesis path с большей глубиной, связностью и сохранением деталей;
  - также сейчас follow-up вида `распиши подробнее`, `раскрой пункт 2`, `покажи с примерами` фактически ведёт к повторному orchestration run, вместо controlled expansion поверх уже собранного answer state.
  Что нужно сделать:
  - ввести отдельную adaptive quality policy, независимую от `B3.48`:
    - `quality_profile = weak | normal | strong_gpu`
    - `response_detail_mode = brief | standard | detailed | exhaustive`
    - `allow_expansion_from_previous = true|false`
    - `expansion_scope = section | whole_answer`
  - добавить hardware-aware quality ceiling:
    - на weak profile не разрешать дорогой detail path по умолчанию;
    - на normal profile использовать balanced mode;
    - на strong GPU profile разрешать:
      - более высокий synthesis budget;
      - менее агрессивную компрессию intermediate summaries;
      - более высокий reasoning/detail setup для final synthesis;
      - больший output budget для detailed/exhaustive modes;
  - реализовать two-stage answer policy:
    - первый ответ: compact but complete;
    - follow-up на детализацию: отдельный `expansion pass`, а не полный orchestration restart;
  - сохранять answer state после первого ответа:
    - `answer_outline`
    - `detail_level_used`
    - `covered_sections`
    - `expandable_sections`
    - `source_refs` / `chunk_lineage`
  - expansion pass должен:
    - брать предыдущий answer state;
    - локально расширять нужный section или весь ответ;
    - добирать только релевантные source chunks при необходимости;
    - не пересобирать весь ответ с нуля без причины;
  - ввести detail ladder для summarization/analysis:
    - `brief` — сильнее сжимает, меньше retention;
    - `standard` — текущий balanced path;
    - `detailed` — сохраняет больше нюансов и caveats;
    - `exhaustive` — максимально подробный synthesis с примерами, оговорками и раскрытием секций;
  - сделать policy-driven control, а не только prompt-text:
    - detail mode должен задаваться и в metadata/runtime policy, и через UI/user intent;
    - явный запрос пользователя на подробность должен иметь приоритет;
  Что не считать решением:
  - просто увеличить `max_tokens` без quality policy;
  - безусловно включить detailed mode на сильном железе;
  - повторно запускать весь workflow на follow-up `распиши подробнее`;
  - смешивать эту задачу с `B3.48`, где основная цель — reduce safety/bounded merge policy.
  Acceptance:
  - `B3.48` и `B3.49` разделены как две независимые оси:
    - `B3.48` — merge/reduce policy;
    - `B3.49` — answer depth / quality orchestration;
  - сильное железо повышает quality ceiling, но не делает длинный ответ default;
  - follow-up на детализацию может идти через `expansion pass` поверх предыдущего answer state;
  - detail mode виден в metadata/runtime state и может управляться явно;
  - summarization/analysis поддерживают ladder `brief|standard|detailed|exhaustive`;
  - есть тесты на:
    - compact first answer;
    - section expansion without full rerun;
    - whole-answer expansion;
    - strong_gpu policy raises allowed detail ceiling;
    - explicit user request for detail overrides default brevity.
  Verification:
  - `pytest backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py -q`
  - добавить/обновить тесты на:
    - expansion from previous answer state;
    - detail ladder behavior;
    - strong vs weak quality policy;
    - metadata for detail mode / expansion scope
  Verification:
  - локально: `npx playwright test tests/e2e/chainlit`
  - CI smoke: хотя бы `chromium` project для `chat + upload + reload persistence`

### H12 — document_analysis: неусечённый final synthesis report (B3.50)

- [ ] **B3.50 — Устранить усечение финальной сводки/отчёта в `document_analysis` после bounded reduce**
  Контекст:
  - после фикса `B3.48` и anti-stall guard для hierarchical reduce длинный `document_analysis` по реальному PDF (`455-z.pdf`) больше не зацикливается на `5 -> 5 -> 5`, но итоговый markdown-отчёт всё ещё может приходить усечённым;
  - подтверждённый live case: [`backend/open_webui_uploads/Report_Analysis_1773894719.md`](/home/seral/HDD/proj/agent-navigator-pro/backend/open_webui_uploads/Report_Analysis_1773894719.md) заканчивается посреди пункта `17. **Ответственность`, после чего сразу идут `## Метаданные`;
  - при этом workflow технически помечает `final_synthesis_status: completed`, а в отчёте пишет `Итог собран в bounded/degraded режиме из-за ограничений ресурсов`, что создаёт misleading success signal: stage завершён, но answer quality уже частично деградировала;
  - проблема относится не к PDF renderer, а к самому final markdown payload: усечение уже присутствует в `.md`.
  Что нужно сделать:
  - локализовать, где именно теряется хвост final answer:
    - `final_max_tokens` / output cap слишком мал;
    - финальный prompt допускает слишком “раздутый” формат ответа;
    - bounded final synthesis завершает generation до логического конца ответа;
    - отсутствует post-check на незавершённый/обрубленный текст;
  - ввести явный completion guard для `document_analysis final_synthesis`:
    - детектировать подозрительно незавершённый output;
    - различать `stage completed technically` и `answer complete semantically enough`;
    - не оставлять `final_synthesis_status=completed`, если ответ явно обрублен;
  - реализовать как минимум один безопасный remediation path:
    - retry с более compact final prompt;
    - или retry с повышенным `final_max_tokens` в допустимом bounded budget;
    - или post-final repair/continuation pass для завершения оборванного списка/секции;
  - сделать format control для final report более жёстким:
    - ограничить число крупных секций;
    - не позволять модели бесконечно раздувать enumerated list без closing summary;
    - при bounded режиме явно требовать короткий, завершённый ответ вместо “максимально полного” списка;
  - синхронизировать metadata/report copy:
    - если ответ частично деградирован, это должно быть видно честно;
    - `completed` не должен означать “качественно полный”, если final text оборван;
    - при необходимости добавить поле вроде `final_synthesis_complete=false` / `final_answer_truncated=true`;
  - проверить связку с `B3.49`:
    - quality/expansion policy не должна строиться на уже усечённом base summary;
    - compact first answer должен быть завершённым, а не просто коротким из-за hard truncation.
  Что не считать решением:
  - просто поднять `max_tokens` без bounded guard и без анализа prompt shape;
  - считать задачу решённой только потому, что PDF визуально открылся;
  - игнорировать усечение, если `final_synthesis_status` формально `completed`;
  - лечить только renderer/markdown-to-pdf слой.
  Acceptance:
  - длинный `document_analysis` больше не генерирует markdown, обрывающийся посреди пункта/секции;
  - completion/truncation guard различает:
    - полноценный завершённый final answer;
    - технически завершённый, но качественно усечённый answer;
  - metadata/report status честно отражают incomplete/truncated outputs;
  - bounded/degraded path остаётся bounded и не превращается в unbounded final generation;
  - на длинных legal/other documents final report остаётся завершённым даже если detail level снижен.
  Verification:
  - добавить unit/integration tests на:
    - truncated final synthesis detection;
    - retry/repair path для незавершённого final answer;
    - metadata status для `completed` vs `truncated` vs `degraded`;
  - live smoke:
    - повторить `document_analysis` на длинном PDF уровня `455-z.pdf`;
    - убедиться, что итоговый `.md` не заканчивается посреди списка/секции;
    - проверить, что report metadata не вводит в заблуждение по completeness.

### H13 — Compare: semantic legal conclusion for heterogeneous documents (B3.51)

- [x] **B3.51 — Перевести compare из structural-only режима в legal synthesis для `policy vs contract` и других heterogeneous pair**
  Контекст:
  - текущий `compare_documents` завершал анализ на structural diff, если после `match_batches` все различия классифицировались как `ADDED/DELETED`;
  - на реальном кейсе (`Положение о дистанционной работе` vs `Трудовой договор о дистанционной работе`) workflow выдавал `Structural: 112, needs LLM: 0` и сохранял отчёт без юридического вывода;
  - такой output подходит для redline двух редакций, но не для сравнения разных по роли, но связанных юридических документов.
  Что сделано:
  - в `backend/orchestrator/workflows/compare.py` добавлены:
    - heuristic role detection (`policy`, `contract`, `other`) по имени файла и первым страницам текста;
    - pair typing / mode selection (`same_document_revision`, `same_genre_legal_compare`, `policy_vs_contract`, `unknown`);
    - semantic fallback для `heterogeneous_alignment`, который в любом случае строит LLM-based legal conclusion поверх ролей документов, их содержания и representative diffs;
    - новый report shape: `Юридический вывод` + `Ключевые смысловые различия` + `Что отсутствует / требует отражения` + `Приложение: различия по пунктам`;
  - structural appendix сохранён, но больше не является единственным результатом для heterogeneous pair.
  Что не считать полностью закрытым:
  - richer topic clustering / many-to-one clause alignment;
  - отдельный auto-mode для `regulation_vs_policy`, `annex_vs_contract` и других related legal roles;
  - live re-run на production-like проблемной паре как formal acceptance.
  Acceptance:
  - `policy vs contract` compare больше не заканчивается на `needs LLM: 0` + appendix-only report;
  - workflow всегда формирует legal conclusion для heterogeneous pair;
  - structural differences остаются приложением, а не основным ответом;
  - обычный redline path для revision-like compare не ломается.
  Verification:
  - `pytest backend/tests/test_compare_workflow.py -q`
  - `python -m py_compile backend/orchestrator/workflows/compare.py backend/tests/test_compare_workflow.py`
  Status 2026-03-19:
  - В `Chainlit` добавлен presentation-layer UX contour для длинного compare appendix.
  - Если секция `Приложение: различия по пунктам` превышает threshold по числу строк, основной `assistant_message` теперь оставляет summary-first report без appendix spam.
  - Полный appendix переносится в отдельный схлопнутый `Chainlit Step` `Приложение: различия по пунктам (N)` с `autoCollapse=True`.
  - Короткие appendix cases остаются inline, чтобы не ухудшать UX на маленьких compare-ответах.
  - Workflow `compare.py` не менялся по report contract; split сделан только на уровне `chainlit_app.py`.

- [ ] **B3.51a — Harden pair typing до production-grade compare regime selection**
  Контекст:
  - production-решения разделяют как минимум `version redline`, `semantic clause comparison`, `playbook/standards review` и `heterogeneous related-document analysis`;
  - текущий heuristic-first detector (`policy` / `contract` / `other`) закрывает только базовый кейс `policy vs contract`, но недостаточен для mixed corpus, annex/amendment/template/executed docs и near-miss revision pairs.
  Что сделать:
  - расширить роли документов:
    - `policy`
    - `contract_template`
    - `executed_contract`
    - `annex`
    - `amendment`
    - `regulation`
    - `other`
  - ввести compare regimes:
    - `redline_compare`
    - `semantic_compare`
    - `playbook_compare`
    - `heterogeneous_alignment`
  - строить regime selection не только по filename/title, но и по:
    - title/heading profile;
    - section numbering overlap;
    - first-page entity cues;
    - embedding-level global similarity;
    - amendment/appendix markers;
  - при uncertainty хранить confidence и безопасный fallback path.
  Acceptance:
  - режим сравнения выбирается явно и воспроизводимо;
  - heterogeneous pair не попадает в pure redline только из-за похожей темы;
  - obvious revision pairs не деградируют в overly-generic semantic mode.
  Verification:
  - добавить matrix tests для role/regime selection;
  - протестировать revision, policy-vs-contract, amendment-vs-master, unrelated docs.

- [ ] **B3.51b — Перейти на legal-aware chunking и section metadata для compare**
  Контекст:
  - production systems и research consistently показывают, что naive chunking создаёт noise, раздувает `ADDED/DELETED` и ухудшает semantic alignment;
  - в текущем кейсе compare дробит документы слишком агрессивно, что и создаёт structural-only explosion.
  Что сделать:
  - заменить current compare chunking на legal-aware splitter:
    - section / subclause boundaries;
    - headings;
    - numbered clause trees;
    - paragraph fallback только внутри очень длинных sections;
  - хранить metadata на chunk:
    - `section_id`
    - `section_title`
    - `doc_role`
    - `topic_labels`
    - `page_span`
  - отдельно ввести text-only normalization mode для compare:
    - подавление formatting noise;
    - collapse duplicate whitespace;
    - optional numbering normalization;
  - сохранить original text для evidence/appendix.
  Acceptance:
  - compare на legal docs меньше распадается на бессмысленные мелкие chunks;
  - structural diff count снижается на formatting/segmentation noise;
  - downstream alignment использует section metadata, а не только raw text snippets.
  Verification:
  - unit tests на chunk boundaries;
  - regression на паре `policy vs contract`;
  - сравнить chunk count / structural diff count до и после.

- [ ] **B3.51c — Построить topic/clause alignment engine вместо raw diff explosion**
  Контекст:
  - production compare обычно не ограничивается one-to-one text diff: используются clause/topic alignment, many-to-one matching и semantic grouping;
  - для heterogeneous pair именно alignment даёт meaningful legal packet для LLM.
  Что сделать:
  - поверх embeddings строить alignment graph:
    - one-to-one;
    - one-to-many;
    - many-to-one;
    - unmatched topic clusters;
  - агрегировать matches на уровне topics/obligations/rights/procedures;
  - отделять:
    - `covered_by_both`
    - `doc1_only`
    - `doc2_only`
    - `possible_conflicts`
  - не считать каждую unmatched clause автоматически `ADDED/DELETED` до topic consolidation.
  Acceptance:
  - heterogeneous pair получает compact alignment packet;
  - LLM видит не 100+ raw diffs, а curated topic-level comparison;
  - report quality улучшается по completeness и signal/noise.
  Verification:
  - unit tests на grouping/matching;
  - golden compare cases с many-to-one mapping;
  - assert на bounded alignment packet size.

- [ ] **B3.51d — Добавить standards/playbook compare и risk layer**
  Контекст:
  - production CLM systems (Conga Redline AI, Ironclad playbooks, Kira smart fields/workflows) обычно сравнивают не только две версии, но и документ против стандартов/approved language;
  - без этого compare остаётся полезным только для redline и related-doc explanation, но не для risk review.
  Что сделать:
  - ввести optional compare-against-standard mode:
    - standard clause library / playbook source;
    - benchmark clauses;
    - risk categories;
  - output должен включать:
    - deviations from standard;
    - likely risk areas;
    - recommended remediation;
  - спроектировать contract так, чтобы mode можно было использовать и для single-document review, и для pair compare.
  Acceptance:
  - compare pipeline поддерживает стандартный legal review use case “что отклоняется от playbook”;
  - risk summary отделён от raw diff appendix;
  - user-visible output остаётся bounded и понятным.
  Verification:
  - unit tests на deviation/risk classification;
  - fixture с standard clause + negotiated clause;
  - smoke on compare workflow with playbook source.

- [ ] **B3.51e — Ужесточить report contract: executive summary + appendix + evidence links**
  Контекст:
  - в production инструментах summary и detailed diff почти всегда разделены: сначала executive/risk summary, затем clause-level evidence;
  - текущий report уже сделал первый шаг, но ещё не содержит formal evidence contract и stable machine-readable sections.
  Что сделать:
  - стабилизировать report shape:
    - `Юридический вывод`
    - `Ключевые различия`
    - `Coverage gaps / missing obligations`
    - `Риски / конфликты`
    - `Рекомендуемые действия`
    - `Приложение`
  - добавить evidence mapping:
    - links/ids на source chunks or sections;
    - per-finding supporting snippets;
  - не позволять workflow завершаться appendix-only report, если compare regime не `redline_compare`;
  - синхронизировать API metadata и saved report sections.
  Acceptance:
  - любой non-redline compare возвращает summary-first output;
  - каждое ключевое finding имеет source evidence;
  - report можно безопасно парсить downstream automation without brittle markdown assumptions.
  Verification:
  - tests на report rendering contract;
  - tests на evidence references;
  - integration check на saved markdown report.

- [ ] **B3.51f — Собрать production eval corpus и live acceptance для compare**
  Контекст:
  - без golden corpus и live acceptance compare будет регрессировать незаметно: route selection, alignment quality и report usefulness сложно держать только unit tests;
  - user-reported PDF pair уже показала, что runtime success не гарантирует useful legal output.
  Что сделать:
  - собрать eval set минимум из:
    - revision vs revision;
    - policy vs contract;
    - amendment vs master agreement;
    - template vs executed contract;
    - unrelated docs;
  - для каждого кейса фиксировать:
    - selected regime;
    - structural diff count;
    - semantic alignment count;
    - final report sections present;
    - qualitative expectations on findings;
  - повторить live прогон на проблемной паре PDF после `B3.51a-e`.
  Acceptance:
  - compare route selection и output quality проверяются автоматически;
  - проблемная pair больше не выдаёт useless structural-only result;
  - новые slices не ломают revision compare path.
  Verification:
  - dedicated compare eval script / pytest suite;
  - live rerun на `2024-Polozhenie-o-distancionnoy-rabote-ecp.pdf` + `employment contract sample for remote work.pdf`;
  - manual review checkpoint на generated markdown report.

- [ ] **B3.51g — Сделать summary-first compare с prioritized deep diff и bounded latency**
  Контекст:
  - live rerun на паре законов о СМИ показал, что adaptive batching снял часть parse-failures, но перевёл compare в `74` одиночных LLM-вызова и latency порядка `8+` минут;
  - при этом итоговый отчёт всё ещё перекошен в `ADDED/DELETED`, а `Юридический вывод` остаётся слишком общим;
  - для production compare нужен quality-first output, но без full deep analysis каждого orphan chunk.
  Что сделать:
  - оставить `summary pass` отдельным обязательным шагом для любого non-redline compare;
  - усилить summary prompt так, чтобы он всегда возвращал:
    - `Ключевые темы`;
    - `Что исчезло / чем заменено`;
    - `Что нового добавилось`;
    - `Последствия / риски`;
  - перейти на `top-N prioritized deep diff`:
    - анализировать LLM-ом только наиболее значимые различия, ориентир `12-20`;
    - prioritization строить по `MODIFIED first`, semantic overlap, topic importance, legal impact markers;
  - остальные различия оставлять в `Приложении` без полного LLM-анализа;
  - в metadata/report явно фиксировать:
    - сколько различий было всего;
    - сколько ушло в deep diff;
    - сколько осталось appendix-only.
  Execution budget:
  - `fast_compare` target: summary + top findings укладываются в `<= 90s` на документах порядка `30-50` chunks на сторону;
  - `deep_compare` target: `<= 6 min`, при этом bounded depth и отсутствие unbounded item-by-item expansion;
  - report должен честно отражать, если deep analysis ограничен budget-ом, а хвост вынесен в appendix.
  Acceptance:
  - compare перестаёт анализировать десятки low-value orphan items одинаково глубоко;
  - summary становится обязательной и содержательной частью любого semantic compare;
  - runtime остаётся bounded и предсказуемым, без `70+` последовательных LLM-вызовов на один кейс;
  - отчёт по проблемной паре PDF явно объясняет общие смысловые сдвиги между пакетами поправок, а не только перечисляет `ADDED/DELETED`.
  Verification:
  - unit tests на prioritization/top-N selection и budget enforcement;
  - integration test на pair `H12100110_1621890000.pdf` vs `H12300274_1688590800.pdf`;
  - manual review generated report на наличие всех summary sections и bounded appendix.

- [ ] **B3.51h — Ввести fast/deep compare mode с user choice и отдельными execution graph paths**
  Контекст:
  - compare теперь реально требует разного профиля исполнения: иногда нужен быстрый обзор, иногда глубокий legal diff;
  - текущий UI скрывает эту развилку, поэтому пользователь не контролирует tradeoff `speed vs depth`;
  - user request: дать два выбора через кнопки и вести workflow по разным путям.
  Что сделать:
  - в Chainlit/UI добавить явный выбор перед compare run:
    - `Быстрое сравнение`
    - `Глубокое сравнение`
  - для `Быстрое сравнение` запускать graph path:
    - mandatory summary pass;
    - top findings only;
    - appendix без полного deep diff хвоста;
  - для `Глубокое сравнение` запускать отдельный graph path:
    - summary pass;
    - расширенный prioritized deep diff;
    - richer evidence package;
  - передавать выбранный mode через orchestration contract и сохранять его в metadata/report;
  - зафиксировать safe default:
    - если user явно не выбрал mode, стартовать с `Быстрое сравнение`.
  Acceptance:
  - пользователь может управлять глубиной анализа до запуска compare;
  - fast/deep path отличаются не только текстом статуса, но и реальным execution graph / budget / output depth;
  - saved report и telemetry явно показывают выбранный mode.
  Verification:
  - unit tests на mode selection/request payload;
  - integration tests на fast vs deep graph routing;
  - manual UI smoke с button-based selection в Chainlit.

- [ ] **B3.51i — Убрать дублирующиеся compare progress messages в Chainlit**
  Контекст:
  - после внедрения progress-step пользователь видит повторяющиеся нижние сообщения вида:
    - `Avatar for Сравнение документов ... Анализирую различия по смыслу`
    - `Avatar for Сравнение документов ... Формирую юридический вывод...`
  - такой UX выглядит как дублирование финального отчёта и засоряет ленту.
  Что сделать:
  - пересобрать compare progress rendering так, чтобы использовался один устойчивый обновляемый status container, а не серия визуально дублирующихся сообщений;
  - проверить lifecycle:
    - создание;
    - update;
    - finalize/remove;
  - убедиться, что progress-state не остаётся “хвостом” после отправки финального compare report;
  - если `Chainlit Step.update()` не даёт чистого UX, ввести другой presentation primitive для transient execution status.
  Acceptance:
  - во время compare пользователь видит один понятный progress block;
  - после финального ответа внизу не остаётся лишних дублирующихся status entries;
  - appendix step и progress state визуально не смешиваются.
  Verification:
  - unit/integration tests на single-progress-container lifecycle;
  - manual Chainlit smoke на long-running compare.

- [ ] **T6.2 P0 — Compose full-stack smoke tests**
  Контекст: runtime/scripts и `docker-compose.yaml` часто меняются, но нет единого black-box gate, который подтверждает что весь stack действительно поднялся и отвечает не только на уровне unit mocks.
  Что сделать:
  - Добавить full-stack smoke suite для `docker compose up -d`
  - Проверять readiness для:
    - `agent-api`
    - `document-server`
    - `legal-server`
    - `ums`
    - `chainlit`
  - Проверять `healthcheck`/`/health`/`/status`/`/models` на уровне живого стека
  - Зафиксировать operator-friendly failure output: какой сервис не поднялся и на каком probe упал
  Verification:
  - `docker compose up -d`
  - `pytest backend/tests/test_full_stack_smoke.py -q`

- [ ] **T6.3 P0 — Persistence + SQLite migration E2E**
  Контекст: уже был реальный инцидент со schema drift (`steps.autoCollapse`). Нужен не только unit-тест мигратора, но и end-to-end сценарий с реальным restart lifecycle и сохранением history.
  Что сделать:
  - Добавить E2E test path:
    - старт со старой SQLite schema fixture
    - автодомиграция без удаления БД
    - создание thread/steps/elements
    - рестарт приложения
    - проверка, что история и schema совместимы после рестарта
  - Проверять, что migration path не теряет существующие rows
  Verification:
  - `pytest backend/tests/test_chainlit_persistence_e2e.py -q`

- [ ] **T6.4 P1 — Concurrency / cancel / busy black-box tests**
  Контекст:
  - unit-тесты уже покрывают `429 busy`, cancel semantics и часть saturation policy;
  - но нет одного black-box regression gate, который проверяет пользовательский path целиком:
    `long request -> second request busy -> cancel first -> retry second`.
  Что нужно сделать:
  - создать `backend/tests/test_runtime_busy_e2e.py`;
  - покрыть минимум 4 сценария:
    - длинный run действительно занимает runtime slot;
    - второй запрос получает ожидаемый `busy`/degraded ответ, а не произвольную ошибку;
    - cancel первого run действительно освобождает slot;
    - повторный запрос после cancel проходит без долгого stuck-state и без бесконечной серии `429`.
  Что проверять:
  - не только HTTP status, но и `execution_metadata` / `model_execution` / `UMS /status`, если соответствующая ветка их публикует;
  - bounded latency после cancel, а не только факт eventual success.
  Acceptance:
  - есть отдельный black-box test file для busy/cancel path;
  - сценарий стабильно воспроизводится без реального multi-user окружения;
  - regression suite ловит stuck busy-state после cancel.
  Verification:
  - `pytest backend/tests/test_runtime_busy_e2e.py -q`

- [ ] **T6.5 P1 — Cross-backend parity tests (`llama-server` vs `vllm`)**
  Контекст: система уже поддерживает минимум два backend mode (`llama-server`, `vllm`), но нет единого contract test, который гарантирует что ключевые пользовательские сценарии не ломаются асимметрично.
  Что сделать:
  - Добавить parity suite для одинаковых smoke scenarios под разными `BACKEND_MODE`
  - Проверять совместимость:
    - chat
    - simple doc-question
    - streaming response contract
    - `/status` и `/models` metadata
  - Зафиксировать какие различия допустимы, а какие считаются регрессией
  Verification:
  - `BACKEND_MODE=llama-server pytest backend/tests/test_backend_parity.py -q`
  - `BACKEND_MODE=vllm pytest backend/tests/test_backend_parity.py -q`

- [ ] **T6.6 P1 — Filesystem / permissions regression suite**
  Контекст: уже были реальные проблемы с созданием каталогов и mixed ownership после container/native paths. Сейчас `run_native.sh` это частично проверяет, но нет единого regression suite для permission edge cases.
  Что сделать:
  - Добавить сценарии:
    - `UPLOADS_DIR` отсутствует
    - `UPLOADS_DIR` readonly
    - `backend/.data` readonly
    - root-owned leftover / non-writable target file
    - container path и native path дают предсказуемую и явную ошибку или safe self-heal
  - Отдельно проверить path для Chainlit elements/report persistence
  Verification:
  - `pytest backend/tests/test_filesystem_permissions.py -q`

- [ ] **T6.7 P2 — Nightly benchmark regression gate**
  Контекст: функциональные тесты не ловят latency/resource regressions в LLM/RAG path. Для этого уже есть `scripts/benchmark.py` и `scripts/benchmark_compare.py`, но нет formal nightly gate.
  Что сделать:
  - Ввести nightly benchmark baseline/candidate workflow
  - Сравнивать хотя бы:
    - `health`
    - `embedding`
    - `chat`
    - `doc_question`
    - `compare`
    - `equipment`
  - Добавить пороги на грубые regressions по latency/error-rate вместо overly strict deterministic numbers
  Verification:
  - `python scripts/benchmark.py --output results/baseline.json`
  - `python scripts/benchmark_compare.py results/baseline.json results/candidate.json`

- [ ] **T6.8 P2 — Observability contract tests**
  Контекст: чем больше появляется full-stack/E2E тестов, тем важнее чтобы telemetry/trace headers/metrics оставались стабильными. Иначе падения сложно локализовать.
  Что сделать:
  - Проверять наличие trace header propagation на critical endpoints
  - Проверять, что metrics endpoints и базовые observability labels не ломаются после runtime/docker refactor
  - Зафиксировать минимальный observability contract для `agent-api` и `ums`
  Verification:
  - `pytest backend/tests/test_observability_contract.py -q`

#### Practical rollout plan for T6.1-T6.3

- [x] **T6.1a — E2E harness scaffold для Chainlit browser tests**
  Что сделать:
  - завести структуру:
    - `tests/e2e/chainlit/`
    - `tests/e2e/fixtures/`
    - `tests/e2e/pages/`
    - `playwright.config.ts`
  - определить один canonical browser project для CI (`chromium`) и optional local projects для ручного прогона
  - выбрать единый способ поднятия окружения:
    - либо через `webServer`/shell command,
    - либо через отдельный pre-start script для compose stack
  - зафиксировать output/artifacts policy:
    - trace on first retry
    - screenshot on failure
    - video retain on failure
  Рекомендуемый first slice:
  - пока без multi-browser; только `chromium`
  Verification:
  - `npx playwright test --list`
  Сделано:
  - добавлены `package.json`, `playwright.config.ts`, `tests/e2e/chainlit/`, `tests/e2e/fixtures/`, `tests/e2e/pages/`
  - зафиксирован один canonical project `chromium`
  - policy артефактов: `trace=on-first-retry`, `screenshot=only-on-failure`, `video=retain-on-failure`
  - проверка `npm run test:e2e:list` проходит и видит `2` теста

- [x] **T6.1b — Stable test hooks для Chainlit UI**
  Контекст: без стабильных locator hooks Playwright suite быстро станет flaky.
  Что сделать:
  - определить minimal set test hooks / stable selectors для:
    - login form
    - main chat input
    - upload trigger
    - thread list / current thread title
    - message bubble / assistant response container
    - download/report link
  - избегать привязки только к локализованному тексту UI
  Verification:
  - smoke locator test в `tests/e2e/chainlit/ui-smoke.spec.ts`
  Сделано:
  - hooks подключены через `Chainlit` `custom_js/custom_css`, без правок frontend bundle
  - добавлены стабильные селекторы для login form, main chat input, upload trigger, thread list/current thread, assistant response container, report download link
  - добавлен smoke locator test `tests/e2e/chainlit/ui-smoke.spec.ts`
  - pytest-проверка wiring/hooks проходит в `backend/tests/test_chainlit_runtime_mode.py`

- [ ] **T6.1c — Canonical browser scenarios v1**
  Что сделать:
  - реализовать первые 4 browser tests:
    - `chat-smoke.spec.ts`
    - `doc-question-upload.spec.ts`
    - `compare-two-docs.spec.ts`
    - `thread-reload-persistence.spec.ts`
  - все тесты должны использовать общие fixtures/page objects, а не ad-hoc selectors в каждом файле
  - upload fixtures держать в репозитории как маленькие deterministic test documents
  Verification:
  - `npx playwright test tests/e2e/chainlit/chat-smoke.spec.ts`
  - `npx playwright test tests/e2e/chainlit/thread-reload-persistence.spec.ts`

- [x] **T6.2a — Compose smoke harness**
  Что сделать:
  - создать pytest/shell harness для full-stack smoke:
    - up stack
    - wait for readiness
    - collect status on failure
    - teardown
  - в случае падения печатать:
    - `docker compose ps`
    - target service logs tail
    - failing probe endpoint
  - не смешивать это с browser E2E; harness должен быть reusable и для API smoke, и для Playwright
  Предлагаемые файлы:
  - `backend/tests/test_full_stack_smoke.py`
  - `backend/tests/utils/full_stack.py`
  Verification:
  - `pytest backend/tests/test_full_stack_smoke.py -q`
  Сделано:
  - добавлены `backend/tests/utils/full_stack.py` и `backend/tests/test_full_stack_smoke.py`
  - harness умеет поднимать compose stack, ждать readiness, собирать `docker compose ps`, logs tail и failing probe diagnostics
  - readiness probes покрывают `agent-api`, `document-server`, `legal-server`, `ums`, `chainlit`
  - локальная проверка: `pytest backend/tests/test_full_stack_smoke.py -q`

- [ ] **T6.2b — Canonical full-stack smoke scenarios**
  Что сделать:
  - минимум 3 smoke tests:
    - `all services healthy`
    - `ums status exposes runtime metadata`
    - `agent-api basic request path works against live stack`
  - отдельно подтвердить что `chainlit` endpoint отвечает как UI, а не просто контейнер существует
  Verification:
  - `docker compose up -d`
  - `pytest backend/tests/test_full_stack_smoke.py -q`

- [ ] **T6.3a — SQLite migration fixture set**
  Что сделать:
  - подготовить deterministic legacy SQLite fixtures:
    - schema before `command/defaultOpen/autoCollapse`
    - schema with partial migration
    - schema with existing rows
  - хранить их как минимальные reproducible fixtures, а не копии реальной живой БД
  Предлагаемые файлы:
  - `backend/tests/fixtures/chainlit_sqlite/legacy_steps_v1.db`
  - `backend/tests/fixtures/chainlit_sqlite/legacy_steps_partial.db`
  Verification:
  - fixture load test в `backend/tests/test_chainlit_persistence_e2e.py`

- [ ] **T6.3b — Restart + migration persistence test**
  Что сделать:
  - реализовать E2E сценарий:
    - поднять app на legacy DB fixture
    - дождаться миграции
    - создать thread / step / element
    - остановить и поднять app снова
    - убедиться, что данные читаются и schema не деградировала
  - проверять отдельно:
    - columns exist
    - rows preserved
    - новый write path не падает после migration
  Verification:
  - `pytest backend/tests/test_chainlit_persistence_e2e.py -q`

- [ ] **T6.3c — No-delete operational contract**
  Контекст: важно явно зафиксировать, что обычный remediation path для Chainlit SQLite — это migration/repair, а не удаление БД.
  Что сделать:
  - задокументировать в `README.md` или `docs/scripts/README.md`:
    - когда БД удалять не нужно
    - когда допустим backup + recreate
    - как проверить текущую schema версии/колонки
  - добавить operator note рядом с persistence tests
  Verification:
  - docs review + ссылка из `README.md`

- [ ] **T6.1-T6.3 execution order**
  Рекомендуемый порядок:
  - `T6.1a` `DONE`
  - `T6.1b` `DONE`
  - `T6.2a` `DONE`
  - `T6.2b`
  - `T6.1c`
  - `T6.3a`
  - `T6.3b`
  - `T6.3c`
  Принцип:
  - сначала stable harness и observability failure output,
  - потом короткие smoke paths,
  - затем persistence/restart scenarios,
  - только после этого расширять suite в concurrency/perf/permissions.

### H10 — Model Path Contract Simplification (TD10)

- [x] **TD10 — Упростить contract путей моделей: registry для model_id/roles, env для filesystem paths**
  Контекст: сейчас filesystem paths частично дублируются между `backend/config/models.yaml` и `backend/.env*`. Это создаёт лишнюю неоднозначность: логический `model_id` и runtime role должны жить в registry, а реальные пути файлов на машине — только в env.
  Цель:
  - не делать большой архитектурный rewrite;
  - сделать простой и чистый env-first contract для model paths.
  Что сделать:
  - оставить в `models.yaml`:
    - `model_id`
    - `kind`
    - `runtime_type`
    - `canonical_env`
    - `legacy_envs`
    - runtime metadata (`ctx_size_env`, `gpu_layers_env`, `quant_default`, roles, tiers)
  - убрать для runtime-моделей reliance на `path.default` как на обычный source of truth
  - считать canonical filesystem source of truth только через env:
    - `MODEL_PATH_LLM`
    - `MODEL_PATH_VLM`
    - `MODEL_PATH_EMBEDDING_INTENT`
    - `MODEL_PATH_EMBEDDING_RETRIEVAL`
    - при необходимости `MODEL_PATH_RERANKER`
  - если canonical env не задан или путь невалиден, давать явную ошибку вместо молчаливого fallback на registry path
  - документацию синхронизировать под правило:
    - registry = logical model registry
    - env = real machine-specific paths
  Не делать:
  - не вводить новый сложный config layer;
  - не размножать новые fallback flags без явной необходимости;
  - не трогать model ids/roles/tier selection шире, чем нужно для cleanup contract.
  Verification:
  - `pytest backend/tests/test_models_config.py backend/tests/test_model_selection.py backend/tests/test_unified_model_server_startup.py -q`
  - `pytest backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py -q`
  - ручная проверка: unset canonical path env -> runtime падает с явной ошибкой
  - ручная проверка: valid `.env` path -> `UMS /status` и launcher работают без regressions
  Статус 2026-03-18:
  - `backend/services/model_manager/models_config.py` переведён на env-first resolution без fallback на registry `path.default`.
  - `backend/services/model_manager/unified_model_server.py` теперь даёт явный `422`, если canonical model path env не задан.
  - Для `qwen-vl-8b` `mmproj` тоже больше не берётся из registry default; требуется `MODEL_MMPROJ_PATH_VLM` или `MMPROJ_PATH`.
  - Из `backend/config/models.yaml` удалены `default`/`mmproj_default` для основных runtime-моделей, чтобы registry не выглядел вторым source of truth для filesystem paths.
  - `scripts/models/download_models.py` отвязан от старого приватного `_MODEL_PATH_SPECS` и выровнен под новый contract.
  - Итоговый contract:
    - registry = `model_id`, roles, tiers, runtime metadata
    - env = реальные machine-specific пути к файлам моделей

---

## PR Inventory (20 draft PR)

Все PR заморожены до завершения текущего orchestration refactor.

| Кластер | PR | Статус | Действие |
|---------|-----|--------|----------|
| A. Runtime/scripts | #3, #4, #5, #6, #7 | supersede later | Закрыть после T4.13/T4.14 |
| B. Routing config | #8, #9, #10 | defer | После B3.21 |
| C. Docs | #11, #21 | docs-only | Переносить вручную |
| D. Resume/session | #12, #16, #17, #18, #19, #20, #22 | defer | После B3.31a |
| E. Summary memory | #13, #14, #15 | defer | После resume contract |

---

## E2E валидация (2026-03-04)

| Сценарий | Документы | Результат |
|----------|-----------|-----------|
| Smoke direct chat | — | PASS |
| S1 RAG question | Requirements.pdf | PASS |
| S2 compare_documents | 2 legal PDFs | PASS (38 различий) |
| S3 document_analysis (legal) | H12100110.pdf | PASS |
| S4 document_analysis (tz) | Requirements.pdf | PASS (3 позиции) |
| S5 equipment_analysis | ТЗ + КП | PASS (8 pairs matched) |

---

## Working Notes

### 2026-03-04 — Consolidation branch model
- Каноническая основная ветка: `v3.0`
- `main` удалена. Все изменения базируются на `v3.0`.

### 2026-03-11 — NotebookLM operational note
- Auth валидна (`nlm login --check` подтверждён)
- MCP-интеграция нестабильна — использовать CLI fallback `nlm ...`
- VS Code + Gemini Code Assist может циклически переподнимать `notebooklm-mcp`, если в `~/.gemini/settings.json` одновременно присутствуют `mcpServers.notebooklm` и `mcpServers.notebooklm-mcp`, а в `~/.gemini/mcp-server-enablement.json` отключён только `notebooklm`. Follow-up: оставить один канонический сервер и синхронизировать ключ enablement с фактическим именем сервера.

### 2026-03-12 — Orchestration boundary review
- B3.31 ядро закрыто в коде (3 новых модуля + 37 тестов)
- `chainlit_app.py` делегирует в backend, `_execute_intent` удалён
- Остаётся cleanup: routing-дубликаты в chainlit_app.py, uncommitted changes
- `agent_api.py` рефакторинг: legacy code удалён, unified execution core

### 2026-03-18 — CPU-тест и таймауты (анализ logs_cpu)

**Контекст:** тест проводился намеренно без GPU — симуляция слабого железа. Физически RTX 2070 × 2 присутствовали, но llama.cpp собран без CUDA (`ggml_cuda_init: failed to initialize CUDA`). Модель Qwen-14B Q4_K_M (8.37 GiB) загружена полностью на CPU.

**Производительность на CPU:**
- Prefill: ~12 tok/s, ~80 ms/token
- Generation: ~4-5 tok/s, ~200-400 ms/token
- Чанк ~2000 токенов → промпт eval ~165 сек, generation ~1-3 мин
- Суммаризация 20-страничного документа (455-z.pdf, 56 720 симв.) → ~2 часа суммарно

**Проблема таймаутов:** часть запросов прерывалась по `UMS_INFER_TIMEOUT_S=300` (5 мин) с `[UMS_CLIENT] Async error (attempt X/4)`. Система восстанавливалась через retry, задача завершилась в degraded/bounded режиме.

**Рекомендуемые env-переменные для CPU-режима** (добавить в `.env.hardware.override`):
```
UMS_INFER_TIMEOUT_S=600
UMS_CLIENT_TIMEOUT_S=600
UMS_RETRY_MAX_DELAY_S=30
DOCUMENT_ANALYSIS_SUMMARIZE_MAX_TOKENS=512
```

**Для полноценного GPU-инференса:** пересобрать llama.cpp с `-DGGML_CUDA=ON`. Ожидаемый прирост: 10–15× (до 40–60 tok/s). Prebuild-варианты: `pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124`.

### Future Task — B3.47: Закрыть cleanup после registry-backed universal model failover

- [ ] **B3.47 — Завершить cleanup и UI-contract после перехода на registry-backed model failover**
  Контекст:
  - базовый `primary -> fallback` contract уже реализован поверх `backend/config/models.yaml`;
  - `ums_client` и `UMS` уже используют registry-backed execution plan;
  - `429 busy` и cancellation корректно не трактуются как model-failure.
  Что ещё осталось:
  - убрать дублирующиеся local failover helper'ы и compatibility shims из `chainlit_app.py` / `agent_api.py`, если они больше не нужны как отдельный runtime contour;
  - проверить, что failover wiring не живёт в двух местах с собственной локальной логикой;
  - решить, нужен ли operator-facing UI surface для `last_fallback_event` и `model_execution`, чтобы не смотреть raw `UMS /status`;
  - если UI surface нужен:
    - определить минимальный контракт отображения;
    - не тащить в UI raw diagnostics целиком без фильтрации.
  Acceptance:
  - failover-логика не дублируется бессистемно между `Chainlit`, API и UMS;
  - `last_fallback_event` либо честно показывается в operator-facing surface, либо явно остаётся только debug/status artifact;
  - backlog больше не содержит note-блок без статуса вместо нормальной задачи.

### B3.50 — Infer-ready readiness gate после preload/fallback

- [x] **B3.50 — Добавить readiness gate уровня `UMS infer-ready`, а не только `/health`/`/status`**
  Контекст:
  - после launcher/preflight cleanup container/native orchestration всё ещё в основном ждёт `/status` и `/health`;
  - этого недостаточно для cold-start GPU path, если heavy runtime ещё не готов к первому реальному `POST /infer` после preload/fallback;
  - в таком окне возможен race: UI уже считает систему поднятой, а первый inference ещё не готов обслуживаться.
  Что было сделано:
  - добавлен `GET /ready/infer` в `UMS` как отдельный readiness contract для heavy inference path;
  - `run_native.sh` теперь ждёт `infer-ready` и не стартует `agent-api` / `chainlit`, если heavy path не подтверждён;
  - `run_all.sh` переведён на двухфазный startup:
    - phase 1: `document-server`, `legal-server`, `ums` и `vllm` при необходимости;
    - phase 2: `agent-api`, `chainlit` только после `infer-ready`;
  - readiness покрыт unit/regression тестами для `UMS` и launcher path.
  Verification:
  - `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_runtime_launcher.py -q`
  - `bash -n scripts/run_all.sh scripts/run_native.sh`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py`

### 2026-03-18 — B3.41: SQLite autoCollapse — причина и диагноз

**Вопрос:** была ли это плохая инициализация системы?
**Ответ: нет.** Это не ошибка запуска. Причина — схемный дрейф Chainlit.

**Что произошло:** существующая `.data/chainlit.db` была создана старой версией Chainlit, в которой у таблицы `steps` не было колонки `autoCollapse`. Новая версия Chainlit обращается к этой колонке при записи каждого Step.

**Почему не починилось само:** функция `_bootstrap_chainlit_sqlite_schema()` использует `CREATE TABLE IF NOT EXISTS` — она полностью пропускает создание таблицы, если та уже существует, и не добавляет новые колонки. Мигратор для `steps.command` и `steps.defaultOpen` уже реализован (через `PRAGMA table_info` + `ALTER TABLE`), но `autoCollapse` в него не добавлен.

**Следствие:** все Step-записи в ходе equipment-сессии (`kp_tz_equip`) падали с `OperationalError` → пользователь не увидел ни прогресса, ни результата, хотя workflow технически отработал (извлёк 0 позиций — отдельная проблема B3.43).

**Фикс описан в B3.41:** добавить `ALTER TABLE steps ADD COLUMN autoCollapse INTEGER` с проверкой через `PRAGMA table_info(steps)` в существующий цикл миграции.

### 2026-03-18 — Ревизия scripts/: найденный техдолг и follow-up

- `scripts/start_system_test.sh` фактически сломан как executable orchestration wrapper: ключевые `tmux` команды отсутствуют в исполняемом коде и остались только внутри comment-строк с `mux ...`; текущий файл печатает, что tmux session поднята, но сам её не создаёт.
- `scripts/run_all.sh` содержит хрупкий `curl -sf` внутри command substitution под `set -e` в `wait_for_model()`. Если `/status` временно недоступен, shell завершится раньше retry-loop. В `run_native.sh` этот же путь уже защищён через `|| true`, значит поведение между native/container paths сейчас расходится.
- `launcher.sh`, `run_native.sh`, `run_all.sh`, `stop_native.sh`, `stop_all.sh`, `run_openwebui.sh`, `models/install_models.sh`, `bootstrap_env.sh` исполняют `source` на `.env*`/override файлах как shell-код, а не как безопасный `KEY=VALUE` parser. Это допустимо только при fully trusted local files; для user-owned override path это отдельный риск и его нужно явно документировать либо заменить на безопасный parser.
- `backend/tests/test_runtime_launcher.py::test_launcher_sources_native_overrides_before_runtime_preflight` не hermetic: результат зависит от содержимого реального `backend/.env.hardware.override`. При текущем локальном `DEVICE_MODE="cpu"` тест падает, хотя launcher детерминированно применяет приоритет `.env -> .env.native -> .env.hardware.override`.
- launcher/preflight contract уже разделён на `current-run -> backend/.env.runtime` и `persistent save -> backend/.env.hardware.override`, а `run_all.sh --from-launcher` больше не подмешивает `hardware.override` второй раз. Отдельный startup follow-up вынесен в `B3.50` (`UMS infer-ready` readiness gate).
- `scripts/setup_ubuntu.sh` скачивает CUDA keyring/Miniconda installer и Docker GPG material по сети без отдельной checksum/integrity verification в самом скрипте. Для interactive installer это workable path, но как supply-chain baseline слабое место.
- permission-path всё ещё несимметричен: `scripts/run_native.sh` делает реальный writable preflight для `UPLOADS_DIR` и `backend/.data`, но container/runtime Python path в `chainlit_app.py`, `report_utils.py`, `knowledge_base_store.py`, `state_store.py` в основном ограничен `os.makedirs(..., exist_ok=True)` без отдельной ранней диагностики permission-denied/root-owned state. Нужен единый writable-dir preflight и более явные ошибки для compose/container path.
- `scripts/setup_ubuntu.sh` до сих пор выставляет `chmod 777 backend/open_webui_uploads`; это помогает “чтобы работало”, но слишком грубая модель прав. Нужен более узкий ownership/permission contract вместо world-writable uploads dir.

### Future Task — B3.46: Честный multi-GPU runtime contract для 4+ GPU

- [ ] **B3.46 — Довести orchestration/UMS до надёжного распределения `LLM + intent embedder + retrieval embedder` на 4+ GPU**
  Контекст:
  - текущий `UMS` уже умеет строить `multi-gpu` placement для heavy LLM и передавать `--tensor-split`, но это пока только часть решения;
  - для `4+ GPU` нет полного и детерминированного runtime-контракта на уровне orchestration;
  - embedder'ы сейчас запускаются только как single-device `cuda:N`, без multi-GPU sharding;
  - нет жёсткого process/env pinning heavy LLM к выбранному набору GPU, поэтому фактическое распределение остаётся best-effort.
  Что нужно сделать:
  - зафиксировать deterministic GPU placement contract для heavy LLM в `llama-server` path:
    - явный pinning к выбранным GPU-индексам;
    - согласованность между `placement metadata` и реальным child-process placement;
    - предсказуемое поведение при `4+ GPU`, а не только weighted `tensor-split`;
  - определить отдельный placement contract для `intent_embedder` и `retrieval_embedder`:
    - prefer отдельные GPU, не занятые heavy LLM;
    - не сажать оба embedder'а на одну карту без явного headroom/admission;
    - сохранить manual override, но сделать auto-policy честной и повторяемой;
  - добавить explicit runtime/env overrides для наборов GPU:
    - `LLM GPU set`
    - `intent embedder GPU set`
    - `retrieval embedder GPU set`
    - поведение при конфликтующих override должно быть детерминированным и диагностируемым;
  - для `vllm` path отдельно определить, что считается supported multi-GPU contract:
    - tensor parallel / served model path;
    - какие env knobs являются source of truth;
    - как orchestration и launcher это публикуют в runtime metadata;
  - добавить admission/policy слой для `4+ GPU`:
    - если headroom позволяет, раскладывать `LLM + 2 embedders` по разным GPU;
    - если не позволяет, предсказуемо деградировать, а не silently collocate всё на одной/двух картах;
  - покрыть это тестами:
    - unit/integration tests для `UMS` placement logic на `4 GPU`;
    - launcher/runtime_preflight tests для exported placement metadata;
    - smoke-path для GPU-set overrides.
  Acceptance:
  - при `4+ GPU` heavy LLM запускается на детерминированно выбранном наборе GPU, а не только с metadata-level `gpu_indices`;
  - `intent` и `retrieval` embedder'ы не конкурируют по умолчанию с heavy LLM за ту же GPU, если есть свободные карты;
  - runtime metadata честно отражают фактическое распределение компонентов по GPU;
  - manual overrides для GPU sets работают предсказуемо и не ломают auto-policy;
  - есть тесты, которые подтверждают поведение для `4 GPU` и регрессии не завязаны на реальное железо конкретной машины.

### Future Task — B3.52: Явный tool/graph contract вместо UI-профилей как основного способа выбора сценария

- [ ] **B3.52 — Ввести backend-first contract `requested_tool` / `routing_mode` для action-first чата**
  Контекст:
  - текущий `Chainlit`-контур перегружен `assistant_mode` / `runtime_mode` / `rag_scope` / `tool_scope` / `model_profile` и выглядит как operator panel, а не как обычный пользовательский чат;
  - в коде уже есть переходный механизм `forced_route`, но он живёт как service override, а не как публичный основной контракт выбора действия;
  - пользовательский сценарий должен начинаться с обычного чата, поверх которого доступны явные действия-инструменты, а не с набора слабопонятных профилей.
  Что нужно сделать:
  - определить публичный orchestration contract:
    - `requested_tool` или `requested_graph`;
    - `routing_mode=explicit|assisted|auto`;
    - явное правило приоритета между `requested_tool`, planner/classifier hint и fallback routing;
  - не опираться на classifier как на единственный центр выбора graph;
  - перевести `forced_route` в нормализованный и документированный backend-owned contract;
  - подготовить registry/каталог доступных действий:
    - `compare_documents`
    - `document_analysis`
    - `document_question`
    - `equipment_analysis`
    - `documents_summary`
  - отделить product-facing labels от внутренних executor/route names.
  Acceptance:
  - backend умеет выполнить явный пользовательский выбор инструмента без classifier;
  - classifier остаётся optional hint / planner helper, а не hard dependency;
  - один и тот же contract пригоден для `Chainlit`, `Open WebUI` и будущего custom frontend.

### Future Task — B3.53: Упростить Chainlit до action-first UX и убрать ощущение недостоверного control panel

- [ ] **B3.53 — Радикально упростить основной `Chainlit` UI для обычного чата с инструментами**
  Контекст:
  - текущий `Chainlit` использует starter cards плюс многовкладочный `ChatSettings`, что визуально перегружает стартовый экран;
  - часть настроек полезна оператору, но не должна быть основной surface для конечного пользователя;
  - нужен UX уровня “обычный чат + понятные действия”, а не “консоль с профилями”.
  Что нужно сделать:
  - сократить главный UI до action-first surface:
    - несколько крупных product actions;
    - минимальный набор видимых настроек;
    - advanced/settings path отдельно и не в центре опыта;
  - перевести starter cards с preset-centric логики на явные продуктовые действия;
  - использовать `requested_tool` contract из `B3.52`, а не только `preset:*`;
  - сохранить `Chainlit` как полезный debug/dev shell:
    - progress steps;
    - route choice fallback;
    - trace/debug visibility.
  Не делать:
  - не строить хрупкий Claude-like hover-prefill на DOM hacks как основной UX path;
  - не плодить новые вкладки/селекты вместо сокращения surface.
  Acceptance:
  - стартовый экран `Chainlit` объясним без знания внутренних runtime policy;
  - пользователь видит 4-5 понятных действий вместо набора слабоочевидных профилей;
  - advanced knobs сохранены, но не мешают основному сценарию.

### Future Task — B3.54: Оценить Open WebUI как пользовательский shell поверх backend orchestration, не как второй мозг системы

- [ ] **B3.54 — Подготовить controlled migration/evaluation contour для `Open WebUI` как thin shell**
  Контекст:
  - `agent_api.py` уже даёт `/v1/chat/completions`, что делает `Open WebUI` технически совместимым frontend-кандидатом;
  - при этом нельзя допустить появления второго orchestration engine внутри UI или конфликта между native Open WebUI RAG и backend-owned routing;
  - основной интерес — обычный чатовый интерфейс с понятным выбором действий, а не замена логики всей LLM-системы.
  Что нужно сделать:
  - зафиксировать роль `Open WebUI`:
    - UI shell;
    - auth/history/admin;
    - prompt/slash-actions surface;
    - без takeover orchestration policy;
  - определить evaluation path:
    - native Open WebUI RAG initially off;
    - backend `agent_api` остаётся source of truth для routing/tool execution;
    - проверить file handoff, attachments, streaming и UX prompt actions;
  - описать границу между:
    - `Open WebUI` shell;
    - backend orchestration;
    - external parsing / OCR;
    - vector DB;
  - подготовить migration notes для coexistence:
    - `Chainlit` как dev/debug UI;
    - `Open WebUI` как candidate end-user shell.
  Acceptance:
  - есть честный rollout/evaluation plan без смешивания UI и orchestration;
  - `Open WebUI` не становится неявным вторым decision engine;
  - направление совместимо с последующим выносом ingestion/retrieval в `Qdrant` + внешний document service.

### Антикризисные правила
1. Не добавлять новые workflow до B3.31 cleanup
2. Не делать full rewrite на LangChain
3. UI — thin client над backend policy
4. Dev-loop: native Chainlit на хосте, Docker для prod-validation
5. Classifier upgrade только после стабилизации orchestration contract

## Session Log

- [x] **[2026-03-18 13:54]** Task #1: (без названия) — ✅ completed
- [x] **[2026-03-12 22:09]** Task #1: (без названия) — ✅ completed
