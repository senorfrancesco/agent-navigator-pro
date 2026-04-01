# Техническое задание: плавный переход с Chainlit на Open WebUI без обязательного оркестратора

См. также:
- [MIGRATION_OVERIEW_WITH_LEAKS.md](./MIGRATION_OVERIEW_WITH_LEAKS.md)
- [MIGRATION_RAG_OPENWEBUI.md](./MIGRATION_RAG_OPENWEBUI.md)

## 1. Цель

Перевести текущую систему с `Chainlit` на `Open WebUI` так, чтобы:

- основным пользовательским интерфейсом стал **Open WebUI**;
- текущий backend на **FastAPI** остался основным исполняющим слоем;
- сценарии `document_question`, `document_analysis`, `compare_documents`, `equipment_analysis` остались рабочими;
- переход был **плавным**, без одномоментного удаления текущего кода;
- **автоматический роутинг и классификатор были убраны из критического пути**;
- основным режимом стал **ручной выбор инструмента**, потому что для текущей инфраструктуры время ответа и экономия ресурсов важнее, чем “умный” автовыбор. Open WebUI умеет подключать внешние инструменты через **OpenAPI Tool Servers** и через **MCP**, поэтому такая архитектура ему подходит. :contentReference[oaicite:0]{index=0}

---

## 2. Контекст текущего состояния

Сейчас у вас уже есть хорошая основа для миграции:

- `chainlit_app.py` используется как UI-оболочка, в нём есть хранение сессии, шаги прогресса `cl.Step`, локальная persistence-логика и восстановление состояния/документов; это значит, что Chainlit сейчас выполняет прежде всего **UI- и session-роль**, а не является центром доменной логики. :contentReference[oaicite:1]{index=1} :contentReference[oaicite:2]{index=2} :contentReference[oaicite:3]{index=3}
- `agent_api.py` уже оформлен как отдельный FastAPI-слой с `OrchestrationRequest`, OpenAI-совместимым API, `/orchestrate`, `/execute_orchestration`, `/v1/chat/completions`, а также дедупликацией параллельных запросов от Open WebUI; значит, backend уже близок к роли внешнего tool server. :contentReference[oaicite:4]{index=4} :contentReference[oaicite:5]{index=5} :contentReference[oaicite:6]{index=6}
- `execution_runtime.py` всё ещё тянет в себя intent-классификацию (`EmbeddingIntentClassifier`, `LLMIntentClassifier`, `select_classifier_result`) и runtime-политику; это как раз тот слой, который нужно перестать считать обязательным перед каждым вызовом. :contentReference[oaicite:7]{index=7} :contentReference[oaicite:8]{index=8}
- `document_analysis.py` уже построен как многошаговый workflow с map-reduce-суммаризацией и budget-aware логикой; `summary_reduce_policy.py` отдельно держит логику `fast_final_merge` и правила деградации. Это хорошая база для fast/deep-исполнения без второго “умного оркестратора”. :contentReference[oaicite:9]{index=9} :contentReference[oaicite:10]{index=10} :contentReference[oaicite:11]{index=11}
- `compare.py` уже фактически разделяет структурное сравнение и более глубокий смысловой fallback; значит, тут нужно не придумывать новый алгоритм, а сделать явные tool-контракты. :contentReference[oaicite:12]{index=12} :contentReference[oaicite:13]{index=13}
- `equipment.py` уже является отдельным специализированным workflow: двухпроходная экстракция, short-circuit на таблицах, LLM-оценка пар, отдельный граф `extract → match → evaluate → report`; его надо переносить как отдельный инструмент, а не как подвид compare. :contentReference[oaicite:14]{index=14} :contentReference[oaicite:15]{index=15} :contentReference[oaicite:16]{index=16}

---

## 3. Целевое архитектурное решение

### Базовый принцип

**Не переносить Chainlit-логику “как есть” в Open WebUI.**  
Нужно вынести наружу **явные backend-инструменты**, а Open WebUI использовать как интерфейс, который умеет вызывать такие инструменты. Open WebUI для этого поддерживает OpenAPI Tool Servers и MCP, а Tools и Functions у него разведены по разным разделам платформы. :contentReference[oaicite:17]{index=17}

### Целевая схема

`Open WebUI -> внешний tool server (FastAPI backend) -> workflow`

### Ключевое решение по роутингу

**Автоматический классификатор и обязательный оркестратор убрать из основного сценария.**  
Основной сценарий:

`пользователь / UI выбирает конкретный tool -> backend сразу исполняет его`

Это согласуется и с MCP: tools — это отдельно именованные операции со схемой входа, а клиент или модель уже выбирают, какой вызвать. MCP не требует, чтобы перед каждым tool-вызовом работал отдельный внутренний классификатор. :contentReference[oaicite:18]{index=18}

---

## 4. Что должно стать отдельными инструментами

### Обязательный минимальный набор tools

- [ ] `ask_document`
- [ ] `analyze_document_fast`
- [ ] `analyze_document_deep`
- [ ] `compare_documents_fast`
- [ ] `compare_documents_deep`
- [ ] `analyze_equipment_fast`
- [ ] `analyze_equipment_deep`

### Контекст

Это лучше, чем один “универсальный” endpoint с внутренним скрытым выбором, потому что:

- Open WebUI лучше работает с явно подключёнными инструментами; :contentReference[oaicite:19]{index=19}
- MCP именно так и моделирует инструменты — отдельно именованные действия с предсказуемой схемой; :contentReference[oaicite:20]{index=20}
- ваша текущая кодовая база уже логически разделена на самостоятельные workflow по этим сценариям. :contentReference[oaicite:21]{index=21} :contentReference[oaicite:22]{index=22}

---

## 5. Что делать с оркестратором

### Целевое решение

- [ ] Убрать оркестратор из обязательного пути исполнения.
- [ ] Оставить его только как **временный compatibility-layer** на переходный период.
- [ ] После миграции на Open WebUI перевести оркестратор в статус **опционального legacy-модуля**.
- [ ] Не запускать `EmbeddingIntentClassifier` / `LLMIntentClassifier` перед каждым пользовательским запросом.
- [ ] Исключить обязательный шаг `decide_orchestration(...)` из manual-first сценария.

### Контекст

Сейчас оркестратор и execution-слой всё ещё завязаны на классификатор, `forced_route`, `runtime_mode`, pending action и control-plane-логику. Это удобно для Chainlit, но создаёт лишнюю задержку и сложность, если пользователь и так может выбрать правильный инструмент руками. :contentReference[oaicite:23]{index=23} :contentReference[oaicite:24]{index=24} :contentReference[oaicite:25]{index=25} :contentReference[oaicite:26]{index=26}

---

## 6. Принцип миграции: manual-first

### Решение

- [ ] Зафиксировать **manual-first** как основной режим на текущей инфраструктуре.
- [ ] Не вводить отдельные “профили” в первой фазе.
- [ ] Не добавлять agent mode в обязательный MVP перехода.
- [ ] Автоматический выбор tools оставить только как **будущую опцию для более мощных серверов**.

### Контекст

Это соответствует вашим ограничениям: сейчас время ответа и экономия ресурсов важнее, чем агентный комфорт. Open WebUI умеет работать как оболочка для инструментов и не требует обязательно строить поверх них сложную внутреннюю логику выбора. :contentReference[oaicite:27]{index=27}

---

## 7. План реализации backend-слоя

## Этап A. Зафиксировать целевой API-контракт

- [ ] Создать отдельный backend-spec документ `docs/openwebui_tools_contract.md`.
- [ ] Для каждого инструмента описать:
  - [ ] имя tool;
  - [ ] входные аргументы;
  - [ ] обязательность файлов;
  - [ ] формат результата;
  - [ ] ошибки валидации;
  - [ ] требования к цитированию / groundedness;
  - [ ] возможность стриминга;
  - [ ] формат execution metadata.
- [ ] Зафиксировать, что `ask_document` — это **строго grounded QA по документу**, а не “полный анализ”.
- [ ] Зафиксировать, что `equipment` — отдельная предметная ветка, не подветка compare.

### Минимальная целевая схема аргументов

- `ask_document`
  - `message`
  - `active_doc_ids[]`
  - `session_docs`
  - `history`
- `analyze_document_fast|deep`
  - `document_id` или `path`
  - `history` (опционально)
- `compare_documents_fast|deep`
  - `doc_id_1`
  - `doc_id_2`
  - `history` (опционально)
- `analyze_equipment_fast|deep`
  - `doc_id_1`
  - `doc_id_2`
  - `mode` (опционально, если не вычисляется автоматически)
  - `history` (опционально)

### Контекст

Сейчас `agent_api.py` уже имеет общий `OrchestrationRequest`, но он перегружен control-plane и legacy-полями вроде `runtime_mode`, `assistant_mode`, `tool_scope`, `classifier_result`, `forced_route`. Для Open WebUI лучше иметь более узкие и явные контракты на каждый инструмент. :contentReference[oaicite:28]{index=28}

---

## Этап B. Отделить execution от orchestration

- [ ] Вынести вызовы workflow в отдельный модуль, например `tool_executor.py`.
- [ ] Сделать в этом модуле **прямые функции вызова**:
  - [ ] `run_ask_document(...)`
  - [ ] `run_analyze_document_fast(...)`
  - [ ] `run_analyze_document_deep(...)`
  - [ ] `run_compare_documents_fast(...)`
  - [ ] `run_compare_documents_deep(...)`
  - [ ] `run_equipment_fast(...)`
  - [ ] `run_equipment_deep(...)`
- [ ] Убедиться, что эти функции не требуют предварительного запуска intent-классификатора.
- [ ] Разделить:
  - [ ] валидацию входа;
  - [ ] подготовку runtime_context;
  - [ ] исполнение workflow;
  - [ ] рендер результата.

### Контекст

Сейчас `execute_orchestration` и связанные с ним слои смешивают runtime-политику, классификацию и исполнение. Для ручных tools это нужно упростить: UI уже выбрал инструмент, backend должен только проверить вход и выполнить его. :contentReference[oaicite:29]{index=29} :contentReference[oaicite:30]{index=30}

---

## Этап C. Упростить `agent_api.py`

- [ ] Не удалять `agent_api.py`, а сделать его главным внешним server entrypoint.
- [ ] Добавить отдельные endpoints под tools.
- [ ] Сохранить существующие `/health`, `/metrics`, OpenAI-compatible routes.
- [ ] Перенести `/orchestrate` в legacy-раздел.
- [ ] Явно пометить `/orchestrate` как deprecated для UI-маршрута.
- [ ] Оставить `/execute_orchestration` только на переходный период.
- [ ] Ввести новые endpoints, например:
  - [ ] `POST /tools/ask_document`
  - [ ] `POST /tools/analyze_document_fast`
  - [ ] `POST /tools/analyze_document_deep`
  - [ ] `POST /tools/compare_documents_fast`
  - [ ] `POST /tools/compare_documents_deep`
  - [ ] `POST /tools/analyze_equipment_fast`
  - [ ] `POST /tools/analyze_equipment_deep`

### Контекст

Open WebUI v0.6+ поддерживает подключение внешних OpenAPI servers как tool servers, поэтому этот слой лучше сделать стабильным и явным. :contentReference[oaicite:31]{index=31}

---

## Этап D. Оставить OpenAI-compatible API как совместимый слой

- [ ] Не ломать `/v1/chat/completions`.
- [ ] Оставить OpenAI-compatible API для внешних клиентов и отладки.
- [ ] Сохранить дедупликацию параллельных запросов.
- [ ] Сохранить trace-id и observability middleware.
- [ ] Сохранить возможность стриминга ответов.

### Контекст

У вас уже есть OpenAI-compatible слой, плюс дедупликация запросов от Open WebUI. Это полезно оставить как совместимость и для безопасного перехода. :contentReference[oaicite:32]{index=32} :contentReference[oaicite:33]{index=33}

---

## 8. План реализации по workflow

## Этап E. `ask_document` оставить отдельным grounded-RAG инструментом

- [ ] Не смешивать `ask_document` с `document_analysis`.
- [ ] Оставить его как режим: “найти релевантные фрагменты -> собрать ответ -> проверить цитаты / доказательства”.
- [ ] Вынести его в отдельный endpoint/tool.
- [ ] Сохранить deterministic fallback, если источников недостаточно.
- [ ] Возвращать:
  - [ ] `assistant_message`
  - [ ] `sources`
  - [ ] `quality_signals`
  - [ ] `execution_metadata`

### Контекст

В текущем `execution_runtime.py` уже есть grounded path с fallback при отсутствии источников и retrieval adapter. Это правильная модель для `ask_document`, её не нужно превращать в “анализ”. :contentReference[oaicite:34]{index=34} :contentReference[oaicite:35]{index=35}

---

## Этап F. `document_analysis` разделить на fast и deep

### `analyze_document_fast`

- [ ] Использовать текущий workflow как основу.
- [ ] Задать более жёсткие лимиты summary policy:
  - [ ] меньше `chunk_input_chars`
  - [ ] меньше `group_input_chars`
  - [ ] меньше `final_input_chars`
  - [ ] меньше `final_max_tokens`
  - [ ] разрешён `fast_final_merge`
- [ ] Делать короткий итоговый отчёт.
- [ ] Не форсировать расширенный финальный синтез, если хватает частичной/короткой сводки.
- [ ] Возвращать execution metadata с указанием реально выбранной reduce-стратегии.

### `analyze_document_deep`

- [ ] Использовать тот же workflow, но с более “дорогой” policy.
- [ ] Увеличить допустимый объём финальной сборки.
- [ ] Разрешить больше уровней reduce.
- [ ] Делать полный аналитический отчёт.
- [ ] В итог включать больше секций и метаданных.

### Контекст

`document_analysis.py` уже имеет map-reduce-суммаризацию и адаптивную summary policy, а `summary_reduce_policy.py` уже держит логику fast path и деградации. Здесь нужен не новый алгоритм, а явная параметризация под два режима. :contentReference[oaicite:36]{index=36} :contentReference[oaicite:37]{index=37} :contentReference[oaicite:38]{index=38}

---

## Этап G. `compare_documents` разделить на fast и deep

### `compare_documents_fast`

- [ ] Сделать основой **структурное сравнение**.
- [ ] LLM-анализ выполнять только для модификаций, реально требующих расшифровки.
- [ ] Semantic fallback не запускать автоматически в большинстве случаев.
- [ ] Делать короткий redline-подобный отчёт.

### `compare_documents_deep`

- [ ] Поверх fast-пути включить semantic path.
- [ ] Разрешить heterogeneous / semantic fallback.
- [ ] Включать:
  - [ ] relation summary
  - [ ] key findings
  - [ ] coverage gaps
  - [ ] conflicts
  - [ ] recommended actions

### Контекст

Сейчас `compare.py` уже сначала отделяет structural vs LLM-needed differences, а затем при определённых условиях включает semantic fallback. Это почти готовая основа для двух инструментов. :contentReference[oaicite:39]{index=39} :contentReference[oaicite:40]{index=40} :contentReference[oaicite:41]{index=41}

---

## Этап H. `equipment` разделить на fast и deep

### `analyze_equipment_fast`

- [ ] Оставить двухпроходную экстракцию.
- [ ] Сохранить short-circuit при хорошем table extraction.
- [ ] Ограничить число пар, уходящих в LLM evaluate.
- [ ] Делать короткий итог: подходит / не подходит / спорно.
- [ ] Минимизировать batch LLM usage.

### `analyze_equipment_deep`

- [ ] Разрешить больше пар на этап `evaluate`.
- [ ] Добавить более подробные причины соответствия/несоответствия.
- [ ] Выводить спорные и рискованные позиции отдельно.
- [ ] Сохранять полный отчёт по позициям.

### Контекст

`equipment.py` уже построен как отдельный граф `extract -> match -> evaluate -> report`, с условной маршрутизацией после extract и отдельным LLM evaluation batching. Это надо не прятать в compare, а переносить как самостоятельные tools. :contentReference[oaicite:42]{index=42} :contentReference[oaicite:43]{index=43} :contentReference[oaicite:44]{index=44}

---

## 9. План интеграции с Open WebUI

## Этап I. Первый шаг — OpenAPI Tool Servers

- [ ] Подготовить OpenAPI-спецификацию для новых `/tools/*` endpoints.
- [ ] Подключить backend как OpenAPI Tool Server в Open WebUI.
- [ ] Проверить доступность каждого tool из интерфейса Open WebUI.
- [ ] Для MVP использовать именно OpenAPI, а не сразу MCP.
- [ ] Не переносить бизнес-логику в Open WebUI Functions.

### Контекст

Open WebUI прямо поддерживает OpenAPI servers как внешний способ интеграции tools; это самый практичный первый шаг. Tools и Functions у Open WebUI — разные сущности, причём Functions меняют поведение платформы и исполняют код на сервере, поэтому их лучше не использовать как место для вашей предметной логики. :contentReference[oaicite:45]{index=45}

---

## Этап J. Второй шаг — MCP как целевой слой

- [ ] После стабилизации OpenAPI-интеграции добавить MCP server поверх тех же tool-контрактов.
- [ ] Делать MCP только через **HTTP / Streamable HTTP**, если возможно.
- [ ] Не смешивать OpenAPI-конфиг и MCP-конфиг в Open WebUI.
- [ ] При необходимости использовать MCPO только для нестандартных транспортов.

### Контекст

Open WebUI поддерживает нативный MCP Streamable HTTP и отдельно предупреждает, что MCP нужно добавлять как MCP connection, а не как OpenAPI connection. Для не-HTTP MCP-серверов предлагается MCPO. :contentReference[oaicite:46]{index=46}

---

## Этап K. Как именно это должно выглядеть в Open WebUI

- [ ] Не реализовывать в Open WebUI “режимы” вроде `manual / compat / agent`.
- [ ] В Open WebUI просто зарегистрировать набор tools.
- [ ] Пользовательский выбор делать через сам вызов нужного tool.
- [ ] Файлы загружать через стандартный механизм Open WebUI + ваш backend document path handling.
- [ ] Для админской настройки использовать разделы Tools / External Tools.
- [ ] Не переносить session-state-логику Chainlit внутрь Open WebUI.

### Контекст

В Open WebUI нет готовых встроенных “вкладок” под ваши кастомные режимы маршрутизации; есть инструменты, внешние tool servers, functions и модельные настройки. Значит, ваши режимы — это не UI-фича Open WebUI, а ваша backend-политика. :contentReference[oaicite:47]{index=47}

---

## 10. Что именно переносить из Chainlit, а что нет

## Переносить

- [ ] логику загрузки и регистрации документов;
- [ ] логику восстановления активного набора документов;
- [ ] trace-id / request correlation;
- [ ] progress / status сообщения как backend metadata;
- [ ] RAG indexing lifecycle, если он нужен для `ask_document`;
- [ ] cancellation hooks на уровне backend execution.

### Контекст

В `chainlit_app.py` уже есть работа с активными документами, кэшем RAG, восстановлением документов, прерыванием активного исполнения и синхронизацией состояния. Это полезная логика, но её надо вынести из Chainlit-специфичного слоя в backend-сервисы. :contentReference[oaicite:48]{index=48} :contentReference[oaicite:49]{index=49} :contentReference[oaicite:50]{index=50}

## Не переносить буквально

- [ ] `cl.Step`
- [ ] `cl.user_session`
- [ ] Chainlit Data Layer как основное хранилище
- [ ] pending route choice UI
- [ ] control-plane UI-переключатели Chainlit
- [ ] прямую зависимость frontend-логики от `_backend_decide_orchestration`

### Контекст

Это специфично для Chainlit и не должно становиться архитектурной зависимостью будущей системы. :contentReference[oaicite:51]{index=51} :contentReference[oaicite:52]{index=52}

---

## 11. План по состоянию и данным

## Этап L. Вынести состояние из Chainlit-формата

- [ ] Ввести backend-структуру `SessionExecutionState`.
- [ ] Хранить отдельно:
  - [ ] `session_id`
  - [ ] `thread_id`
  - [ ] `active_doc_ids`
  - [ ] `session_docs`
  - [ ] `last_tool_used`
  - [ ] `last_execution_metadata`
  - [ ] `pending_cancel_flag`
- [ ] Не использовать `cl.user_session` как источник истины.
- [ ] Сделать backend state store независимым от конкретного UI.

### Контекст

Сейчас Chainlit хранит значительную часть состояния внутри своей user-session модели. Для Open WebUI это надо заменить backend-слоем состояния. :contentReference[oaicite:53]{index=53} :contentReference[oaicite:54]{index=54}

---

## Этап M. Вынести progress/update в backend response model

- [ ] Добавить в tool responses поле `progress_events` или отдельный стрим статусов.
- [ ] Не полагаться на `cl.Step`.
- [ ] Унифицировать:
  - [ ] `stage`
  - [ ] `message`
  - [ ] `percent` или `position/total`
  - [ ] `started_at`
  - [ ] `elapsed`
- [ ] Сделать так, чтобы Open WebUI видел обычный ответ, даже если подробный progress UI позже не будет отрисован.

### Контекст

Chainlit даёт красивую модель шагов, но Open WebUI не обязан повторять её один в один. Поэтому прогресс нужно сначала сделать данными backend, а не UI-конструкцией. :contentReference[oaicite:55]{index=55}

---

## 12. План по конфигурации и feature flags

## Этап N. Ввести явные флаги миграции

- [ ] Добавить `ENABLE_LEGACY_ORCHESTRATOR=true|false`
- [ ] Добавить `ENABLE_OPENWEBUI_TOOL_ENDPOINTS=true|false`
- [ ] Добавить `ENABLE_MCP_SERVER=true|false`
- [ ] Добавить `DEFAULT_INVOCATION_MODE=manual`
- [ ] Добавить `ALLOW_AGENT_TOOL_SELECTION=false` по умолчанию
- [ ] Добавить `ENABLE_CHAINLIT_UI=true|false`
- [ ] Добавить `CHAINLIT_LEGACY_COMPAT_MODE=true|false`

### Контекст

Переход должен быть плавным, а не одномоментным. Поэтому всё критичное нужно включать по флагам, чтобы можно было катить поэтапно. Это важнее, чем попытка сразу “вырезать” старую схему. Основа для runtime- и effective-settings-подхода у вас уже есть. :contentReference[oaicite:56]{index=56} :contentReference[oaicite:57]{index=57}

---

## 13. План по безопасности и надёжности

## Этап O. Безопасность интеграции с Open WebUI

- [ ] Не переносить доменную логику в Open WebUI Workspace Functions.
- [ ] Подключать backend как внешний tool server.
- [ ] Ограничить CORS до реальных origin, когда фронт переедет на Open WebUI.
- [ ] Добавить auth/token между Open WebUI и backend tool server.
- [ ] Проверить path mapping между контейнером, Open WebUI и document server.
- [ ] Сохранить дедупликацию запросов от Open WebUI.

### Контекст

Open WebUI Functions и некоторые tool-механизмы исполняют код на сервере и должны рассматриваться как чувствительный механизм. Для вашей архитектуры безопаснее держать логику во внешнем backend. :contentReference[oaicite:58]{index=58}  
У вас уже есть дедупликация параллельных запросов от Open WebUI — это полезно сохранить. :contentReference[oaicite:59]{index=59} :contentReference[oaicite:60]{index=60}

---

## 14. План по тестированию

## Этап P. Контрактные тесты backend tools

- [ ] Написать tests на каждый новый `/tools/*` endpoint.
- [ ] Проверить:
  - [ ] корректную валидацию входа;
  - [ ] ошибки при отсутствии файлов;
  - [ ] ошибки при неправильном числе документов;
  - [ ] корректность grounded answer в `ask_document`;
  - [ ] корректность формата отчёта в `document_analysis`;
  - [ ] корректность парного сравнения в `compare`;
  - [ ] корректность equipment-режима.

## Этап Q. Интеграционные тесты Open WebUI

- [ ] Проверить подключение OpenAPI tool server к Open WebUI.
- [ ] Проверить, что каждый tool доступен в интерфейсе.
- [ ] Проверить загрузку файлов и передачу attachment metadata.
- [ ] Проверить многодокументный сценарий.
- [ ] Проверить, что Open WebUI не дублирует запуск одного и того же тяжёлого workflow.

## Этап R. Регрессионные тесты Chainlit

- [ ] На переходный период сохранить минимальный smoke-suite для Chainlit.
- [ ] Проверить:
  - [ ] загрузку документов;
  - [ ] восстановление истории;
  - [ ] pending-choice сценарии;
  - [ ] прерывание long-running обработки.
- [ ] После стабилизации Open WebUI перевести Chainlit в статус legacy/dev-only.

---

## 15. План по телеметрии

## Этап S. Метрики по каждому tool

- [ ] Добавить отдельные latency-метрики по всем 7 tools.
- [ ] Логировать:
  - [ ] общее время;
  - [ ] число LLM-вызовов;
  - [ ] число вызовов document server;
  - [ ] факт деградации / fallback;
  - [ ] долю ответов без достаточных источников;
  - [ ] размер входных данных;
  - [ ] число обработанных чанков;
  - [ ] число пар в compare/equipment.
- [ ] Вывести в метаданные ответа:
  - [ ] `tool_name`
  - [ ] `execution_mode=manual`
  - [ ] `degraded=true|false`
  - [ ] `fallbacks[]`
  - [ ] `duration_sec`

### Контекст

У вас уже есть наблюдаемость, metrics endpoint и telemetry/runtime hooks. Их нужно не переписывать, а перевести с “маршрута оркестратора” на “маршрут конкретного инструмента”. :contentReference[oaicite:61]{index=61} :contentReference[oaicite:62]{index=62} :contentReference[oaicite:63]{index=63}

---

## 16. План вывода Chainlit из эксплуатации

## Этап T. Переходный период

- [ ] Сначала держать Chainlit и Open WebUI параллельно.
- [ ] Новые tool endpoints делать общими для обоих интерфейсов.
- [ ] Chainlit перевести на вызов тех же новых backend tool endpoints, если это возможно.
- [ ] Не развивать новые фичи в Chainlit, кроме критических багфиксов.
- [ ] Зафиксировать дату прекращения активной продуктовой разработки в Chainlit.

## Этап U. Финальная консервация Chainlit

- [ ] После стабилизации Open WebUI:
  - [ ] отключить Chainlit по feature flag;
  - [ ] оставить только dev/debug запуск;
  - [ ] убрать его из production deployment;
  - [ ] сохранить smoke tests на случай экстренного rollback.

### Контекст

Сейчас Chainlit уже позиционируется в коде как “замена Open WebUI”, но по целевому вектору он должен стать временной оболочкой, а не главной точкой входа. :contentReference[oaicite:64]{index=64}

---

## 17. План релизов

## Release 1 — backend preparation

- [ ] Выделены новые `/tools/*` endpoints.
- [ ] Инструменты вызываются без обязательного оркестратора.
- [ ] Сохранён OpenAI-compatible API.
- [ ] Chainlit всё ещё работает.

## Release 2 — Open WebUI MVP

- [ ] Backend подключён к Open WebUI как OpenAPI Tool Server.
- [ ] Все 7 tools доступны из Open WebUI.
- [ ] Ручной выбор инструмента — основной сценарий.
- [ ] Chainlit остаётся fallback UI.

## Release 3 — stabilization

- [ ] Прогнаны интеграционные тесты.
- [ ] Сняты метрики latency/quality.
- [ ] Устранены расхождения между Chainlit и Open WebUI.
- [ ] Оркестратор больше не обязателен.

## Release 4 — optional MCP

- [ ] Те же инструменты подняты как MCP tools.
- [ ] Open WebUI умеет работать и через OpenAPI, и через MCP.
- [ ] MCP используется там, где это реально даёт пользу.

## Release 5 — legacy cleanup

- [ ] Chainlit выведен из production.
- [ ] `/orchestrate` помечен deprecated или выключен.
- [ ] Intent-classifier убран из основного пользовательского пути.
- [ ] Manual-first окончательно закреплён как дефолтный режим.

---

## 18. Явно не делать на первом этапе

- [ ] Не внедрять сложные “профили”.
- [ ] Не строить новый большой оркестратор.
- [ ] Не завязывать Open WebUI на custom Functions как на место основной логики.
- [ ] Не переносить `cl.Step` 1-в-1.
- [ ] Не начинать с MCP раньше, чем стабилизирован OpenAPI-контракт.
- [ ] Не обучать LoRA-роутер до того, как будет устойчивая manual-first схема.
- [ ] Не смешивать `ask_document` с полным анализом документа.
- [ ] Не сводить `equipment` к подтипу compare.

---

## 19. Итоговое архитектурное решение

### Финальное решение для реализации

- [ ] **UI:** Open WebUI
- [ ] **Основной внешний контракт:** OpenAPI Tool Server
- [ ] **Целевой дополнительный контракт:** MCP
- [ ] **Основной режим:** manual-first
- [ ] **Оркестратор:** только временная совместимость
- [ ] **Chainlit:** временный legacy UI
- [ ] **Backend:** FastAPI + существующие workflow
- [ ] **Инструменты:** 7 явных tools
- [ ] **Классификатор:** не используется в обязательном пути
- [ ] **Agent tool selection:** только будущий optional mode для более мощных серверов

---

## 20. Критерии готовности

- [ ] Open WebUI подключён к backend без Chainlit-зависимостей.
- [ ] Пользователь может вручную вызвать любой из 7 tools.
- [ ] `ask_document` работает как grounded QA с цитатами / fallback.
- [ ] `document_analysis_fast/deep` различаются policy, а не отдельным дублированным кодом.
- [ ] `compare_documents_fast/deep` различаются степенью semantic анализа.
- [ ] `equipment_fast/deep` вынесены как отдельные tools.
- [ ] Запуск tool не требует предварительного intent-классификатора.
- [ ] Chainlit больше не является обязательной частью пользовательского пути.
- [ ] Система даёт меньшую задержку, чем текущий сценарий с обязательным оркестратором.
- [ ] Переход можно включать по feature flags и откатывать без потери работоспособности.
