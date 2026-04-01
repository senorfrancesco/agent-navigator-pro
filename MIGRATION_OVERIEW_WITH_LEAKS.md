# ТЗ: обзор миграции с использованием reference/leaks-кодовых баз

## 1. Назначение документа

- [ ] Зафиксировать, **что именно можно брать как референс** из локального набора кодовых баз в `/home/seral/HDD/clone_claude`.
- [ ] Зафиксировать, **что нельзя переносить напрямую** и что допустимо использовать только как архитектурный ориентир.
- [ ] Связать этот обзор с:
  - [MIGRATION_TOOLS.md](./MIGRATION_TOOLS.md)
  - [MIGRATION_RAG_OPENWEBUI.md](./MIGRATION_RAG_OPENWEBUI.md)
- [ ] Сформировать **единый архитектурный паттерн миграции**, чтобы последующие изменения не выглядели как случайный набор borrow-идей.

**Контекст:** в `/home/seral/HDD/clone_claude` лежит несколько референсных кодовых баз и спеков, включая рабочие форки, исходники, документацию и spec-проекты. Их допустимо использовать как **donor/reference architecture**, но не как источник механического копирования в наш продукт.

---

## 2. Базовый принцип использования reference/leaks-кодовых баз

- [ ] Использовать кодовые базы из `/home/seral/HDD/clone_claude` только как:
  - [ ] архитектурный референс;
  - [ ] источник паттернов;
  - [ ] базу для compare-анализа;
  - [ ] источник идей по boundary-моделям, orchestration, streaming, permissions, skills, context/memory.
- [ ] Не использовать их как основание для прямого копирования больших кусков кода.
- [ ] Не переносить в проект их продуктовые assumptions без адаптации под нашу архитектуру `v3.0`.
- [ ] Не смешивать их naming, runtime assumptions и UX-модель с нашей системой без явной проектной причины.

**Ключевое правило:**  
Берём **паттерны и архитектурные решения**, но не переносим код “как есть”.

---

## 3. Какие reference-кодовые базы считать основными

### 3.1 Основной архитектурный референс

- [ ] Считать главным референсом:
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working`

**Почему:**
- там есть наиболее полезный набор docs по архитектуре;
- там хорошо разложены слои системы;
- там описаны loop, streaming, skills, permissions и state-handling;
- этот набор ближе всего к задачам нашего orchestration/migration слоя.

### 3.2 Вторичный референс

- [ ] Использовать как дополнительный source-of-truth:
  - [ ] `/home/seral/HDD/clone_claude/claude-code-source-code-full`
  - [ ] `/home/seral/HDD/clone_claude/claude-code`

**Почему:**
- это полезно для просмотра реальной кодовой структуры;
- это даёт вторую опору, если в `claude-code-working` какая-то часть только документирована, но неочевидна по реализации.

### 3.3 Spec/reference-only базы

- [ ] Использовать только как вспомогательный spec-материал:
  - [ ] `/home/seral/HDD/clone_claude/Kuberwastaken-claurst-9e8fccd/Kuberwastaken-claurst-9e8fccd`
  - [ ] `/home/seral/HDD/clone_claude/open-multi-agent`

**Почему:**
- они полезны как дополнительный взгляд на multi-agent/systems design;
- но их нельзя считать каноническим источником паттернов для нашего production path.

---

## 4. Кодовая структура reference-архитектуры

### 4.1 Полезные docs-узлы

- [ ] Использовать как основные документы:
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/docs/introduction/architecture-overview.mdx`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/docs/conversation/the-loop.mdx`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/docs/conversation/streaming.mdx`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/docs/extensibility/skills.mdx`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/docs/safety/permission-model.mdx`

### 4.2 Полезные исходные каталоги

- [ ] Использовать как карту реальной кодовой структуры:
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/src/commands`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/src/components`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/src/coordinator`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/src/context`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/src/entrypoints`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/src/hooks`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/src/jobs`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/src/components/permissions`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/src/components/skills`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/src/components/messages`

### 4.3 Быстрый mapping на наш проект

- [ ] Сопоставлять reference-слои с нашими зонами:
  - [ ] reference `interaction layer` -> [prototype/operator-ui](./prototype/operator-ui)
  - [ ] reference `orchestration/core loop` -> [backend/orchestrator](./backend/orchestrator)
  - [ ] reference `tool layer` -> [backend/orchestrator](./backend/orchestrator) + [backend/services](./backend/services)
  - [ ] reference `communication/provider layer` -> [backend/services](./backend/services) и provider adapters
  - [ ] reference `permissions/sandbox boundary` -> [backend/orchestrator/operator_ui_actions.py](./backend/orchestrator/operator_ui_actions.py), [backend/orchestrator/operator_ui_api.py](./backend/orchestrator/operator_ui_api.py)

---

## 5. Что конкретно можно брать как паттерн

### 5.1 Слоистая архитектура

- [ ] Взять как ориентир **жёсткое разделение слоёв**:
  - [ ] interaction/UI;
  - [ ] orchestration/session;
  - [ ] core loop/state machine;
  - [ ] tool/capability layer;
  - [ ] provider/communication layer.

**Что это значит у нас:**
- [ ] не смешивать UI-state и workflow-state;
- [ ] не смешивать orchestration и конкретные service-calls;
- [ ] не тащить provider/runtime-specific логику в верхний API-слой;
- [ ] держать `backend/orchestrator` как слой orchestration и policy, а `backend/services` как слой конкретных системных вызовов.

### 5.2 Явный agentic/core loop

- [ ] Взять как паттерн **явный цикл исполнения**, а не “скрытую магию”:
  - [ ] preprocessing;
  - [ ] model/service call;
  - [ ] tool execution;
  - [ ] decision continue/stop;
  - [ ] recovery path.

**Что это значит у нас:**
- [ ] делать workflow-переходы в [backend/orchestrator/execution_runtime.py](./backend/orchestrator/execution_runtime.py) более явными;
- [ ] фиксировать причины `continue/stop/fallback`;
- [ ] не прятать retry/fallback/classification в разрозненные условные ветки без единой модели.

### 5.3 Streaming-first response model

- [ ] Взять как ориентир **событийную модель стриминга**:
  - [ ] start;
  - [ ] content delta;
  - [ ] tool progress;
  - [ ] stop;
  - [ ] stall/error/retry.

**Что это значит у нас:**
- [ ] развивать текущий telemetry/footer путь не только как post-factum summary, но и как event-stream contract;
- [ ] отдельно различать:
  - [ ] TTFT;
  - [ ] service stall;
  - [ ] tool progress;
  - [ ] LLM phase;
  - [ ] report phase.

### 5.4 Skill/extensibility system

- [ ] Взять как паттерн **декларативные capability packs / skills**, а не только hardcoded routes.

**Что это значит у нас:**
- [ ] использовать эту идею для дальнейшего развития migration-инструментов;
- [ ] проектировать `tools`, `migration modes`, `operator workflows` как явно описанные capability units;
- [ ] отделять:
  - [ ] atomic tool;
  - [ ] workflow pack / skill;
  - [ ] orchestration policy.

**Где это особенно полезно:**
- [ ] [MIGRATION_TOOLS.md](./MIGRATION_TOOLS.md)
- [ ] будущий tool contract для Open WebUI/MCP/OpenAPI Tool Servers
- [ ] operator control plane presets / action groups

### 5.5 Permission boundary

- [ ] Взять как паттерн **permission as boundary**, а не как вторичную проверку в конце.

**Что это значит у нас:**
- [ ] все runtime actions должны проходить через единый action catalog;
- [ ] старт/стоп/deploy/path browser не должны иметь неявных обходов;
- [ ] опасные действия должны быть типизированы, ограничены и обозримы.

**Практическое применение у нас:**
- [ ] развивать [backend/orchestrator/operator_ui_actions.py](./backend/orchestrator/operator_ui_actions.py) как канонический action layer;
- [ ] не добавлять ad-hoc shell-вызовы мимо этого слоя;
- [ ] path-browser и runtime operations держать в read-only / explicit-action модели.

### 5.6 Context / memory / compaction

- [ ] Взять как паттерн **разделение памяти, контекста и активного runtime-state**.

**Что это значит у нас:**
- [ ] не хранить всё в session-only prompt/runtime state;
- [ ] различать:
  - [ ] chat/session history;
  - [ ] retrieval state;
  - [ ] persistent project memory;
  - [ ] migration memory;
  - [ ] execution-local scratch state.

**Это особенно важно для:**
- [ ] [MIGRATION_RAG_OPENWEBUI.md](./MIGRATION_RAG_OPENWEBUI.md)
- [ ] переноса retrieval из session-bound prototype в продуктовую архитектуру.

---

## 6. Что нельзя переносить напрямую

### 6.1 Нельзя копировать продуктовые assumptions

- [ ] Не переносить терминальную/CLI-first UX-модель.
- [ ] Не переносить их naming как есть.
- [ ] Не переносить provider-specific API assumptions без проверки совместимости.
- [ ] Не переносить их permission model дословно в наш UI/control plane.

### 6.2 Нельзя копировать большие куски кода

- [ ] Не копировать большие модули без адаптации.
- [ ] Не переносить их файловую структуру 1:1 только потому, что она “смотрится богаче”.
- [ ] Не смешивать наш текущий `FastAPI + Chainlit/operator-ui + services` стек с их внутренней CLI-архитектурой механически.

### 6.3 Нельзя ломать уже принятые решения проекта

- [ ] Не отменять текущую `v3.0` branch model.
- [ ] Не возвращать `Open WebUI-first` как уже принятое основное UI-решение без отдельного архитектурного решения.
- [ ] Не ломать текущий `operator control plane`, telemetry и runtime contract ради похожести на reference.

---

## 7. Как это связано с MIGRATION_TOOLS.md

- [ ] Использовать reference architecture для усиления следующих направлений из [MIGRATION_TOOLS.md](./MIGRATION_TOOLS.md):
  - [ ] явный tool contract;
  - [ ] отделение execution от orchestration;
  - [ ] совместимость через OpenAPI/MCP/tool server;
  - [ ] отказ от обязательного classifier-first пути;
  - [ ] skill-like packaging для сценариев `ask_document`, `analyze_document_fast/deep`, `compare_fast/deep`, `equipment_fast/deep`.

**Практическое требование:**
- [ ] migration на Open WebUI tools должна заимствовать **архитектуру capability boundary**, а не hidden-orchestrator модель.

---

## 8. Как это связано с MIGRATION_RAG_OPENWEBUI.md

- [ ] Использовать reference architecture для усиления следующих направлений из [MIGRATION_RAG_OPENWEBUI.md](./MIGRATION_RAG_OPENWEBUI.md):
  - [ ] разделение retrieval state и session state;
  - [ ] явное разделение ingestion / orchestration / vector storage / metadata storage;
  - [ ] event-driven и observable execution model;
  - [ ] устойчивый слой permissions/boundaries для tools и pipes;
  - [ ] memory/context discipline.

**Практическое требование:**
- [ ] migration в сторону полноценной RAG-архитектуры не должна оставаться session-bound prototype-моделью.

---

## 9. Целевой архитектурный паттерн для нашего проекта

### 9.1 Каноническая схема

- [ ] Принять следующую схему как ориентир:

`UI layer -> orchestration API -> execution/runtime loop -> tool/service adapters -> storage/providers`

### 9.2 Разделение ответственности

- [ ] `UI layer`
  - [ ] Chainlit / operator UI сейчас;
  - [ ] Open WebUI / operator UI в migration path;
  - [ ] только вход, состояние интерфейса, прогресс, отображение результата.

- [ ] `orchestration API`
  - [ ] FastAPI entrypoint;
  - [ ] request validation;
  - [ ] execution contract;
  - [ ] telemetry envelope;
  - [ ] permission boundary for actions/tools.

- [ ] `execution/runtime loop`
  - [ ] выбор шагов workflow;
  - [ ] fallback/retry/recovery;
  - [ ] stage timing;
  - [ ] quality signals;
  - [ ] groundedness / report assembly.

- [ ] `tool/service adapters`
  - [ ] document server;
  - [ ] legal server;
  - [ ] UMS;
  - [ ] embeddings;
  - [ ] retrieval adapters;
  - [ ] runtime start/stop/deploy actions.

- [ ] `storage/providers`
  - [ ] uploads / documents;
  - [ ] vector DB;
  - [ ] metadata DB;
  - [ ] provider-specific model endpoints;
  - [ ] bundle/runtime artifacts.

### 9.3 Что это должно дать

- [ ] Меньше скрытой связности между UI и execution.
- [ ] Более чистый migration path в Open WebUI tools / RAG architecture.
- [ ] Более безопасный operator runtime control.
- [ ] Более прозрачный telemetry/debugging слой.
- [ ] Более явный контракт между workflow и tool execution.

---

## 10. Пакеты работ по миграции на основе reference architecture

### Этап A. Архитектурная нормализация

- [ ] Зафиксировать в docs, что `/home/seral/HDD/clone_claude/claude-code-working` используется как основной architectural reference.
- [ ] Зафиксировать список допустимых паттернов borrow.
- [ ] Зафиксировать список запрещённых direct-copy зон.

### Этап B. Tools / capability model

- [ ] Перевести migration-tools contract к модели явных capabilities.
- [ ] Разделить `tool`, `workflow`, `preset`, `operator action`.
- [ ] Не допускать скрытого classifier-first исполнения для tool-вызовов.

### Этап C. RAG / memory / storage separation

- [ ] Развести session state и retrieval state.
- [ ] Вынести ingestion/retrieval/storage в самостоятельные слои.
- [ ] Свести RAG migration к продуктовой архитектуре, а не к session-bound overlay.

### Этап D. Execution / streaming / telemetry model

- [ ] Развивать execution runtime как явный state machine.
- [ ] Развивать streaming contract как набор событий, а не только финальный текст.
- [ ] Держать telemetry и quality как first-class contract.

### Этап E. Permission / safety boundary

- [ ] Держать все runtime actions и опасные системные операции в явном action catalog.
- [ ] Не плодить ad-hoc shell paths в UI-маршрутах.
- [ ] Согласовать path-browser, runtime-control и deploy actions с общей permission-моделью.

---

## 11. Критерии готовности этого migration-overview

- [ ] Новый документ существует как единая обзорная спецификация.
- [ ] В нём явно указано:
  - [ ] что можно брать;
  - [ ] что нельзя переносить;
  - [ ] какие reference paths считать каноническими;
  - [ ] куда это ложится в нашей архитектуре.
- [ ] Документ связан с:
  - [ ] [MIGRATION_TOOLS.md](./MIGRATION_TOOLS.md)
  - [ ] [MIGRATION_RAG_OPENWEBUI.md](./MIGRATION_RAG_OPENWEBUI.md)
- [ ] Документ можно использовать как стартовую рамку для следующих архитектурных решений без повторного “ручного объяснения”, зачем нам нужен reference из `clone_claude`.

---

## 12. Краткая формула использования reference/leaks

- [ ] Брать:
  - [ ] layered architecture;
  - [ ] loop/state machine;
  - [ ] streaming events;
  - [ ] skills/extensibility;
  - [ ] permission boundaries;
  - [ ] context/memory discipline.

- [ ] Не брать напрямую:
  - [ ] код как есть;
  - [ ] CLI-first UX;
  - [ ] vendor-specific assumptions;
  - [ ] naming и layout без адаптации;
  - [ ] чужие product decisions вместо наших.

**Итоговое правило:**  
`clone_claude` для нас — это **reference architecture set**, а не шаблон для механического переписывания проекта.
