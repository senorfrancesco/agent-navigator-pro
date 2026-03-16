# TASKS_NIGHT — Safe Autonomous Night Shift

Цель: дать ночному агенту узкий, безопасный и проверяемый контур работы, чтобы он мог автономно двигать проект по скриптам установки и запуска, документации и launcher/installer path без поломки backend/runtime ядра.

Основная идея: агент работает только по одному логическому блоку за раз, держится в рамках `scripts/`, `docs/`, `README.md` и связанных task-документов, после каждого подблока делает verification и оставляет понятный checkpoint.

---

# TASK GROUP 1 — Night Guardrails and Scope Control

## Goal
Зафиксировать правила ночной автономной работы и ограничить область изменений.

## Deliverables

- Обновлённый `AGENTS.md` с отдельным разделом про ночной автономный режим.
- `workflow.yaml` с явными правилами последовательности, stop conditions и verification gate.
- Ясно определённый allowlist файлов и областей работы на ночь.

## Completion Criteria

- [x] В `AGENTS.md` есть правила ночной автономной работы.
- [x] В `workflow.yaml` есть stop conditions и commit policy.
- [x] Ночной агент не должен уходить в `backend/orchestrator` wide-refactor без отдельной записи в `TASKS_NIGHT.md`.

---

# TASK 1.1 — Update AGENTS Guardrails

Создать или обновить раздел:

```md
## Night Autonomous Mode
```

Требования:

- [x] читать `AGENTS.md`, `TASKS_NIGHT.md`, `workflow.yaml` перед циклом работы
- [x] работать только по первому незавершённому блоку из `TASKS_NIGHT.md`
- [x] не делать push без явного запроса
- [x] не делать destructive git actions
- [x] писать workaround/blocker/progress только в главу `ЛОГИ` в конце `TASKS_NIGHT.md`
- [x] не закрывать задачу без verification
- [x] останавливаться на safe checkpoint при blocker'е

---

# TASK 1.2 — Create Workflow Policy File

Создать:

```yaml
workflow.yaml
```

Файл должен описывать:

- [x] context files
- [x] allowed scope
- [x] forbidden actions
- [x] execution order
- [x] verification requirements
- [x] commit policy
- [x] stop conditions

---

# TASK GROUP 2 — Script Inventory and Launcher Review

## Goal
Понять текущее состояние скриптов запуска/остановки/подготовки и зафиксировать каноническую карту скриптов.

## Deliverables

- Документ с обзором скриптов в `scripts/`.
- Таблица: скрипт → назначение → режим запуска → риски → текущий статус.
- Отдельное решение по `launcher`: оставляем, дорабатываем или сужаем роль.

## Completion Criteria

- [x] Все ключевые скрипты перечислены и классифицированы.
- [x] Для каждого скрипта ясно, он `canonical`, `compatibility`, `legacy` или `internal`.
- [x] Есть письменное решение по `launcher` и оно синхронизировано с `README.md`.

---

# TASK 2.1 — Build Script Inventory

Проверить каталог:

```bash
scripts/
```

Нужно описать минимум:

- [x] `run_native.sh`
- [x] `run_all.sh`
- [x] `run_container.sh`
- [x] `launcher.sh`
- [x] `bootstrap_env.sh`
- [x] `stop_native.sh`
- [x] `stop_all.sh`
- [x] `run_openwebui.sh`
- [x] installer-скрипты в `scripts/install/`, если уже существуют

Для каждого скрипта зафиксировать:

- [x] цель
- [x] кто его должен использовать
- [x] основные флаги
- [x] входные env vars
- [x] safe / risky side effects

---

# TASK 2.2 — Launcher Decision

Подготовить решение по `launcher`.

Нужно ответить:

- [x] зачем проекту нужен `launcher`
- [x] чем он отличается от `run_native.sh` и `run_all.sh`
- [x] является ли он canonical entrypoint
- [x] если да — где это отражено в `README.md`
- [x] если нет — как ограничить его роль и описать это в документации

Записать результат в:

```md
docs/scripts/README.md
```

и при необходимости в:

```md
README.md
```

---

# TASK GROUP 3 — Installer Framework Plan and Safe Scaffolding

## Goal
Подготовить модульную основу installer path, не ломая текущий native runtime.

## Deliverables

- План структуры installer scripts.
- Безопасные scaffold-файлы в `scripts/install/` и `scripts/models/`, если их ещё нет.
- Документация по назначению каждого installer-модуля.

## Completion Criteria

- [x] Структура installer path зафиксирована.
- [x] Нет полного rewrite существующих runtime scripts.
- [x] Installer path описан как additive слой, а не замена текущего orchestration/runtime.

---

# TASK 3.1 — Define Installer Layout

Целевая структура:

```text
scripts/
  install/
    install.sh
    detect_os.sh
    install_dependencies.sh
    install_cuda.sh
    build_llamacpp.sh
    verify_system.sh
  models/
    install_models.sh
  utils/
    system_check.sh
```

Нужно:

- [x] проверить, какие файлы уже есть
- [x] не дублировать существующую рабочую логику без причины
- [x] создать только безопасные scaffold'ы или plan-notes, если реализация не помещается в ночной slice

---

# TASK 3.2 — Define Responsibilities Per Installer Script

Для каждого будущего installer-скрипта описать:

- [x] что он делает
- [x] что он не делает
- [x] какие зависимости нужны
- [x] какие проверки обязательны перед успехом
- [x] какие official docs нужно использовать для реализации и проверки

Минимальные блоки:

- [x] `detect_os.sh`
- [x] `install_dependencies.sh`
- [x] `install_cuda.sh`
- [x] `build_llamacpp.sh`
- [x] `verify_system.sh`
- [x] `install_models.sh`

---

# TASK GROUP 4 — Script Documentation and README Links

## Goal
Сделать короткую и каноническую документацию по запуску скриптов и привязать её из `README.md`.

## Deliverables

- Новый документ:

```md
docs/scripts/README.md
```

- Раздел в `README.md` со ссылкой на документацию скриптов.
- Краткий review по каждому каноническому скрипту.

## Completion Criteria

- [x] Пользователь может понять, какой скрипт запускать для native/container/install сценария.
- [x] У каждого канонического скрипта есть краткое описание и пример запуска.
- [x] README ссылается на документацию скриптов.

---

# TASK 4.1 — Write Script Manual

В `docs/scripts/README.md` описать:

- [x] `run_native.sh`
- [x] `run_all.sh`
- [x] `run_container.sh`
- [x] `launcher.sh`
- [x] `bootstrap_env.sh`
- [x] `stop_native.sh`
- [x] `stop_all.sh`

Для каждого:

- [x] назначение
- [x] когда использовать
- [x] пример запуска
- [x] основные флаги
- [x] ограничения

---

# TASK 4.2 — Link Script Docs from README

Обновить:

```md
README.md
```

Нужно:

- [x] добавить ссылку на `docs/scripts/README.md`
- [x] убрать двусмысленность между launcher/native/container путями
- [x] явно отметить canonical path для разработки

---

# TASK GROUP 5 — Verification, Logging, and Safe Checkpoint

## Goal
Сделать так, чтобы ночной агент останавливался в понятной точке и не оставлял “полусделанный успех”.

## Deliverables

- Обновлённая глава `ЛОГИ` в `TASKS_NIGHT.md` с follow-up'ами и найденным техдолгом.
- Проверки для затронутых shell-скриптов и docs.
- Локальный checkpoint summary в конце ночного цикла.

## Completion Criteria

- [x] После каждого shell-change есть `bash -n` для затронутых скриптов.
- [x] После документационных правок проверены ссылки и согласованность с `README.md`.
- [x] Все временные допущения записаны в главе `ЛОГИ` в `TASKS_NIGHT.md`.
- [x] Если slice не закрыт, это явно отмечено как `partial` в `TASKS_NIGHT.md`.

---

# TASK 5.1 — Minimum Verification Rules

Для shell/docs slices обязательно:

- [x] `bash -n` для затронутых `scripts/*.sh`
- [x] targeted smoke-команды только если они безопасны и не требуют ручного ввода
- [x] `git diff --check`
- [x] обновление главы `ЛОГИ` в `TASKS_NIGHT.md`
- [x] web research по официальным источникам, если задача зависит от внешних install/runtime docs

---

# TASK 5.2 — Night Stop Conditions

Ночной агент должен остановиться и записать blocker, если:

- [x] нужен доступ к внешней сети или реальным credentials
- [x] требуются destructive operations
- [x] verification нестабильна и причина не локализована
- [x] задача расползается за пределы `scripts/`, `docs/`, `README.md`, `TASKS*.md`, `workflow.yaml`
- [x] требуется рефакторинг backend/runtime beyond script integration

---

# TASK 5.3 — Night Checkpoint Output

В конце каждого завершённого slice агент обязан записать:

- [x] что сделано
- [x] что проверено
- [x] что осталось
- [x] какие follow-up'ы добавлены в главу `ЛОГИ`
- [x] был ли сделан commit

---

# ЛОГИ

Добавлять новые записи только в конец этого раздела.

Формат записи:

```md
## YYYY-MM-DD HH:MM — Night checkpoint
- Task group: ...
- Что сделано: ...
- Что проверено: ...
- Блокеры / риски: ...
- Follow-up: ...
- Commit status: ...
```

## 2026-03-16 01:35 +03 — Night checkpoint
- Task group: pre-flight / safe checkpoint before TASK GROUP 1
- Что сделано: прочитаны `AGENTS.md`, `TASKS_NIGHT.md`, `workflow.yaml`; выполнен pre-flight check рабочего дерева перед выбором первого незавершённого night slice.
- Что проверено: `git status --short`; подтверждено наличие diff вне night allowlist и вне текущего safe scope.
- Блокеры / риски: сработал `stop_conditions.unexpected_dirty_diff_outside_scope`; обнаружены изменения в `backend/tests/test_runtime_launcher.py`, `docker-compose.yaml`, `Dockerfile.backend`, `docs/plans/2026-03-15-t317-backend-services-dockerization.md`; night slice помечен как `partial` и не начат, чтобы не смешивать unrelated diff и не нарушить policy.
- Follow-up: продолжать ночной цикл только после явного решения по текущему внешнему diff: либо пользователь подтверждает, что он ожидаемый и его можно оставить вне scope, либо рабочее дерево приводится к безопасному checkpoint без destructive действий.
- Commit status: no commit

## 2026-03-16 01:36 +03 — Night checkpoint
- Task group: TASK GROUP 1 — Night Guardrails and Scope Control
- Что сделано: подтверждено, что требования блока уже реализованы в `AGENTS.md` и `workflow.yaml`; статусы `TASK GROUP 1`, `TASK 1.1` и `TASK 1.2` синхронизированы в `TASKS_NIGHT.md`; внешний diff вне night scope отдельно подтверждён пользователем как ожидаемый и не блокирующий этот slice.
- Что проверено: ручная сверка `AGENTS.md` на наличие `Night Autonomous Mode`; ручная сверка `workflow.yaml` на `allowed_paths`, `forbidden_work_types`, `execution`, `verification`, `stop_conditions`, `commit_policy`.
- Блокеры / риски: новых blocker'ов в рамках `TASK GROUP 1` нет; в рабочем дереве остаётся ожидаемый внешний diff вне текущего night slice, его по-прежнему нельзя смешивать с последующими изменениями без аккуратного scope control.
- Follow-up: перейти к `TASK GROUP 2` как к следующему первому незавершённому блоку; при работе держать текущий внешний diff вне модифицируемого набора файлов.
- Commit status: no commit

## 2026-03-16 01:40 +03 — Night checkpoint
- Task group: TASK GROUP 2 — Script Inventory and Launcher Review
- Что сделано: создан [docs/scripts/README.md](docs/scripts/README.md) с картой runtime-скриптов, таблицей классификации, launcher decision и notes по `run_native.sh`, `run_all.sh`, `run_container.sh`, `launcher.sh`, `bootstrap_env.sh`, `stop_native.sh`, `stop_all.sh`, `run_openwebui.sh`; в `README.md` добавлена ссылка на script manual и убрана двусмысленность о wrapper scripts и legacy `run_openwebui.sh`; зафиксировано, что `scripts/install/` в текущем дереве отсутствует.
- Что проверено: `bash -n scripts/launcher.sh scripts/bootstrap_env.sh scripts/run_container.sh scripts/run_native.sh scripts/run_all.sh scripts/stop_native.sh scripts/stop_all.sh scripts/run_openwebui.sh`; `git diff --check -- README.md docs/scripts/README.md TASKS_NIGHT.md`; ручная link/sync review между `README.md` и `docs/scripts/README.md`.
- Блокеры / риски: каталог `scripts/install/` пока не существует, поэтому installer-path зафиксирован как absent/planned, а не как реализованный; в рабочем дереве по-прежнему есть ожидаемый внешний diff вне текущего night slice.
- Follow-up: следующий незавершённый блок — `TASK GROUP 3`; при его старте важно не дублировать уже существующий install path через `setup_ubuntu.sh` и `launcher.sh --install`.
- Commit status: no commit

## 2026-03-16 01:43 +03 — Night checkpoint
- Task group: TASK GROUP 3 — Installer Framework Plan and Safe Scaffolding
- Что сделано: добавлены scaffold-only каталоги и файлы `scripts/install/`, `scripts/models/`, `scripts/utils/`; создан [docs/scripts/installers.md](docs/scripts/installers.md) с target layout, responsibilities, dependencies, verification criteria и official-source references для `detect_os.sh`, `install_dependencies.sh`, `install_cuda.sh`, `build_llamacpp.sh`, `verify_system.sh`, `install_models.sh`; `docs/scripts/README.md` дополнен ссылкой на новый installer plan.
- Что проверено: `bash -n scripts/install/install.sh scripts/install/detect_os.sh scripts/install/install_dependencies.sh scripts/install/install_cuda.sh scripts/install/build_llamacpp.sh scripts/install/verify_system.sh scripts/models/install_models.sh scripts/utils/system_check.sh`; `git diff --check -- docs/scripts/README.md docs/scripts/installers.md scripts/install/install.sh scripts/install/detect_os.sh scripts/install/install_dependencies.sh scripts/install/install_cuda.sh scripts/install/build_llamacpp.sh scripts/install/verify_system.sh scripts/models/install_models.sh scripts/utils/system_check.sh TASKS_NIGHT.md README.md`; ручная sync-проверка с `scripts/setup_ubuntu.sh`, `launcher.sh --install` и script docs.
- Блокеры / риски: installer scripts пока intentionally scaffold-only и завершаются сообщением `scaffold only`; для реальной реализации следующих шагов нужны отдельные controlled slices и vendor-specific verification на supported host.
- Follow-up: следующий незавершённый блок — `TASK GROUP 4`; если продолжать, достаточно доформализовать script manual как canonical quick-start и синхронизировать `README.md` уже без изменения shell-логики.
- Commit status: no commit

## 2026-03-16 01:44 +03 — Night checkpoint
- Task group: TASK GROUP 4 — Script Documentation and README Links
- Что сделано: формально закрыт manual/runtime-doc slice на базе уже внесённых правок; `docs/scripts/README.md` содержит canonical quick-start по launcher/native/container/install сценариям, а `README.md` ссылается на manual и явно разводит canonical launcher path, compatibility wrappers и legacy `run_openwebui.sh`.
- Что проверено: повторная ручная сверка `docs/scripts/README.md` и `README.md`; использованы уже пройденные checks `git diff --check` и link/sync review из предыдущих slices без новых shell/runtime изменений.
- Блокеры / риски: новых blocker'ов нет; runtime behavior не менялся, slice документационный.
- Follow-up: следующий незавершённый блок — `TASK GROUP 5`; он уже почти закрыт текущими verification/logging practices и требует только синхронизации статусов.
- Commit status: no commit

## 2026-03-16 01:44 +03 — Night checkpoint
- Task group: TASK GROUP 5 — Verification, Logging, and Safe Checkpoint
- Что сделано: формально синхронизированы verification/logging requirements с фактической ночной работой этой сессии; в `ЛОГИ` уже есть safe checkpoint по внешнему diff, verification summaries по документационным и scaffold slices, а также follow-up/commit status для каждого завершённого блока.
- Что проверено: `git diff --check` на затронутых файлах; `bash -n` на затронутых shell-скриптах; web research по официальным источникам для installer-contract slice; наличие `partial` checkpoint для pre-flight blocker и явных `no commit` записей.
- Блокеры / риски: в рабочем дереве остаётся ожидаемый внешний diff вне night scope; installer-path остаётся scaffold-only до отдельной реализации.
- Follow-up: ночной backlog этого файла закрыт; следующий отдельный шаг уже вне текущего `TASKS_NIGHT.md` и должен исходить из нового задания или нового night backlog.
- Commit status: no commit
