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

- [ ] В `AGENTS.md` есть правила ночной автономной работы.
- [ ] В `workflow.yaml` есть stop conditions и commit policy.
- [ ] Ночной агент не должен уходить в `backend/orchestrator` wide-refactor без отдельной записи в `TASKS_NIGHT.md`.

---

# TASK 1.1 — Update AGENTS Guardrails

Создать или обновить раздел:

```md
## Night Autonomous Mode
```

Требования:

- [ ] читать `AGENTS.md`, `TASKS.md`, `TASKS_NIGHT.md`, `workflow.yaml` перед циклом работы
- [ ] работать только по первому незавершённому блоку из `TASKS_NIGHT.md`
- [ ] не делать push без явного запроса
- [ ] не делать destructive git actions
- [ ] обновлять `TASKS.md` при workaround/blocker
- [ ] не закрывать задачу без verification
- [ ] останавливаться на safe checkpoint при blocker'е

---

# TASK 1.2 — Create Workflow Policy File

Создать:

```yaml
workflow.yaml
```

Файл должен описывать:

- [ ] context files
- [ ] allowed scope
- [ ] forbidden actions
- [ ] execution order
- [ ] verification requirements
- [ ] commit policy
- [ ] stop conditions

---

# TASK GROUP 2 — Script Inventory and Launcher Review

## Goal
Понять текущее состояние скриптов запуска/остановки/подготовки и зафиксировать каноническую карту скриптов.

## Deliverables

- Документ с обзором скриптов в `scripts/`.
- Таблица: скрипт → назначение → режим запуска → риски → текущий статус.
- Отдельное решение по `launcher`: оставляем, дорабатываем или сужаем роль.

## Completion Criteria

- [ ] Все ключевые скрипты перечислены и классифицированы.
- [ ] Для каждого скрипта ясно, он `canonical`, `compatibility`, `legacy` или `internal`.
- [ ] Есть письменное решение по `launcher` и оно синхронизировано с `README.md`.

---

# TASK 2.1 — Build Script Inventory

Проверить каталог:

```bash
scripts/
```

Нужно описать минимум:

- [ ] `run_native.sh`
- [ ] `run_all.sh`
- [ ] `run_container.sh`
- [ ] `launcher.sh`
- [ ] `bootstrap_env.sh`
- [ ] `stop_native.sh`
- [ ] `stop_all.sh`
- [ ] `run_openwebui.sh`
- [ ] installer-скрипты в `scripts/install/`, если уже существуют

Для каждого скрипта зафиксировать:

- [ ] цель
- [ ] кто его должен использовать
- [ ] основные флаги
- [ ] входные env vars
- [ ] safe / risky side effects

---

# TASK 2.2 — Launcher Decision

Подготовить решение по `launcher`.

Нужно ответить:

- [ ] зачем проекту нужен `launcher`
- [ ] чем он отличается от `run_native.sh` и `run_all.sh`
- [ ] является ли он canonical entrypoint
- [ ] если да — где это отражено в `README.md`
- [ ] если нет — как ограничить его роль и описать это в документации

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

- [ ] Структура installer path зафиксирована.
- [ ] Нет полного rewrite существующих runtime scripts.
- [ ] Installer path описан как additive слой, а не замена текущего orchestration/runtime.

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

- [ ] проверить, какие файлы уже есть
- [ ] не дублировать существующую рабочую логику без причины
- [ ] создать только безопасные scaffold'ы или plan-notes, если реализация не помещается в ночной slice

---

# TASK 3.2 — Define Responsibilities Per Installer Script

Для каждого будущего installer-скрипта описать:

- [ ] что он делает
- [ ] что он не делает
- [ ] какие зависимости нужны
- [ ] какие проверки обязательны перед успехом
- [ ] какие official docs нужно использовать для реализации и проверки

Минимальные блоки:

- [ ] `detect_os.sh`
- [ ] `install_dependencies.sh`
- [ ] `install_cuda.sh`
- [ ] `build_llamacpp.sh`
- [ ] `verify_system.sh`
- [ ] `install_models.sh`

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

- [ ] Пользователь может понять, какой скрипт запускать для native/container/install сценария.
- [ ] У каждого канонического скрипта есть краткое описание и пример запуска.
- [ ] README ссылается на документацию скриптов.

---

# TASK 4.1 — Write Script Manual

В `docs/scripts/README.md` описать:

- [ ] `run_native.sh`
- [ ] `run_all.sh`
- [ ] `run_container.sh`
- [ ] `launcher.sh`
- [ ] `bootstrap_env.sh`
- [ ] `stop_native.sh`
- [ ] `stop_all.sh`

Для каждого:

- [ ] назначение
- [ ] когда использовать
- [ ] пример запуска
- [ ] основные флаги
- [ ] ограничения

---

# TASK 4.2 — Link Script Docs from README

Обновить:

```md
README.md
```

Нужно:

- [ ] добавить ссылку на `docs/scripts/README.md`
- [ ] убрать двусмысленность между launcher/native/container путями
- [ ] явно отметить canonical path для разработки

---

# TASK GROUP 5 — Verification, Logging, and Safe Checkpoint

## Goal
Сделать так, чтобы ночной агент останавливался в понятной точке и не оставлял “полусделанный успех”.

## Deliverables

- Обновлённый `TASKS.md` с follow-up'ами и найденным техдолгом.
- Проверки для затронутых shell-скриптов и docs.
- Локальный checkpoint summary в конце ночного цикла.

## Completion Criteria

- [ ] После каждого shell-change есть `bash -n` для затронутых скриптов.
- [ ] После документационных правок проверены ссылки и согласованность с `README.md`.
- [ ] Все временные допущения записаны в `TASKS.md`.
- [ ] Если slice не закрыт, это явно отмечено как `partial` в `TASKS_NIGHT.md`.

---

# TASK 5.1 — Minimum Verification Rules

Для shell/docs slices обязательно:

- [ ] `bash -n` для затронутых `scripts/*.sh`
- [ ] targeted smoke-команды только если они безопасны и не требуют ручного ввода
- [ ] `git diff --check`
- [ ] обновление `TASKS.md`
- [ ] web research по официальным источникам, если задача зависит от внешних install/runtime docs

---

# TASK 5.2 — Night Stop Conditions

Ночной агент должен остановиться и записать blocker, если:

- [ ] нужен доступ к внешней сети или реальным credentials
- [ ] требуются destructive operations
- [ ] verification нестабильна и причина не локализована
- [ ] задача расползается за пределы `scripts/`, `docs/`, `README.md`, `TASKS*.md`, `workflow.yaml`
- [ ] требуется рефакторинг backend/runtime beyond script integration

---

# TASK 5.3 — Night Checkpoint Output

В конце каждого завершённого slice агент обязан записать:

- [ ] что сделано
- [ ] что проверено
- [ ] что осталось
- [ ] какие follow-up'ы добавлены в `TASKS.md`
- [ ] был ли сделан commit
