# Единый генеральный план миграции на Open WebUI

**Дата:** 2026-04-02
**Статус:** рабочий черновик / живой документ
**Назначение:** единый отчёт-план по controlled migration/evaluation contour `Chainlit -> Open WebUI`, переводу инструментов в явный контракт инструментов (`tools`) и серверов инструментов (`OpenAPI` / `MCP`), внедрению `Qdrant`, разбору незавершённых веток и порядку выполнения работ.

> **Current truth / target direction:** на момент этого документа canonical UI проекта остаётся `Chainlit`, а `Open WebUI` рассматривается как `candidate shell` и controlled evaluation contour до functional parity. Этот документ не объявляет migration завершённой и не отменяет текущий `Chainlit-first` runtime contract в `AGENTS.md` и `README.md`.

## 1. Связанные документы

- [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)
- [MOTIVATION.md](/home/seral/HDD/proj/agent-navigator-pro/MOTIVATION.md)
- [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md)
- [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md)
- [MIGRATION_OVERIEW_WITH_LEAKS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_OVERIEW_WITH_LEAKS.md)
- [docs/plans/2026-04-01-knowledge-base-store-protocol-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-knowledge-base-store-protocol-plan.md)
- [docs/plans/2026-04-01-qdrant-knowledge-base-store-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-qdrant-knowledge-base-store-plan.md)
- [docs/plans/2026-04-01-openwebui-tool-server-integration-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-openwebui-tool-server-integration-plan.md)
- [docs/plans/2026-04-01-openwebui-migration-sprint-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-openwebui-migration-sprint-plan.md)
- [docs/plans/2026-04-01-migration-readiness-pr-decision-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-migration-readiness-pr-decision-plan.md)
- [conversation_about_rag.pdf](/home/seral/HDD/proj/agent-navigator-pro/conversation_about_rag.pdf)
- [conversation_about_tools.pdf](/home/seral/HDD/proj/agent-navigator-pro/conversation_about_tools.pdf)

---

## 2. Краткое резюме

### 2.1 Главные решения

- [ ] Ближайшая цель — не немедленный `main UI switch`, а controlled re-entry `Open WebUI` как candidate shell поверх существующего backend.
- [ ] До достижения functional parity `Chainlit` остаётся canonical UI и не считается уже снятым с основной роли.
- [ ] После достижения parity `Chainlit` переводим в роль отладочной и переходной оболочки.
- [ ] `Chainlit`-профили не считаются стратегическим направлением; новые вложения в перегруженный профилями UX не делаем.
- [ ] Основной backend остаётся нашим `FastAPI`-слоем, а не переносится в Open WebUI.
- [ ] Основной путь интеграции с Open WebUI делаем через **сервер инструментов OpenAPI** (`OpenAPI Tool Server`).
- [ ] `MCP` поддерживаем как второй, совместимый слой, но не как основной производственный путь интеграции.
- [ ] `backend/open_webui_uploads` считаем текущим shared storage contract; ранний rename откладываем до отдельного approved slice после стабилизации upload/document binding.
- [ ] Не переносим бизнес-логику, orchestration policy и document lifecycle внутрь `Open WebUI`.
- [ ] На baseline-этапе оркестратором остаётся сам пользователь: он явно выбирает tool и режим работы, а UI не принимает скрытые backend-решения вместо него.
- [ ] Инструменты становятся **явными** и вызываются по именованным контрактам:
  - [ ] `ask_document`
  - [ ] `analyze_document_fast`
  - [ ] `analyze_document_deep`
  - [ ] `compare_documents_fast`
  - [ ] `compare_documents_deep`
  - [ ] `analyze_equipment_fast`
  - [ ] `analyze_equipment_deep`
- [ ] `document_question` остаётся доказательным путём ответа по документу (`grounded RAG`), а не смешивается с полным анализом документа.
- [ ] `document_analysis` и `compare_documents` разделяем на `fast/deep` как режимы инструмента, а не как профили интерфейса.
- [ ] `Qdrant` принимаем как целевой production-ready vector backend.
- [ ] До `Qdrant` обязательно вводим `KnowledgeBaseStoreProtocol`.
- [ ] Session RAG и Knowledge Base RAG разводим как разные слои, но даём им общий merge/policy layer.
- [ ] Файлы, документы, привязки к базе знаний (`knowledge bindings`) и состояние поиска (`retrieval state`) должны жить в backend-контракте, а не в памяти сеанса интерфейса.
- [ ] Для operator UI заранее закладываем отдельную панель под:
  - [ ] upload/indexing в `Qdrant`
  - [ ] статус ingestion
  - [ ] пересборку embeddings
  - [ ] reindex / delete / verify flows

### 2.1.1 Future directions, не входящие в текущий baseline

- [ ] В будущем `Open WebUI` может использоваться как внешний orchestration shell через native workflows / pipelines, но только поверх уже стабилизированного backend tool contract.
- [ ] Это не отменяет baseline-модель `user as orchestrator`: до появления отдельного approved режима пользователь остаётся явным источником выбора `requested_tool`.
- [ ] Будущий assisted-layer должен быть classifier-based, а не embedding-based:
  - [ ] classifier выбирает `requested_tool`
  - [ ] classifier задаёт `routing_mode`
  - [ ] classifier не исполняет domain logic сам по себе
- [ ] Даже при добавлении classifier-assisted routing backend остаётся source-of-truth для tool execution, job state, document lifecycle и retrieval policy.

### 2.2 Что точно не делаем

- [ ] Не делаем `big-bang rewrite`.
- [ ] Не переносим orchestration-логику в Open WebUI.
- [ ] Не завязываем критически важный запуск (`runtime`) на сохранение сеанса (`session persistence`) внутри `Chainlit`.
- [ ] Не привязываем миграцию к одному большому PR.
- [ ] Не merge-им старые ветки целиком только потому, что в них есть полезные идеи.

### 2.3 Главный порядок работ

- [ ] Этап 0: зафиксировать controlled evaluation contour, единый контракт запуска (`single-runtime contract`), контракт явных инструментов (`tool-first`) и снять наследованный долг, мешающий миграции.
- [ ] Этап 1: внедрить `KnowledgeBaseStoreProtocol`.
- [ ] Этап 2: внедрить `QdrantKnowledgeBaseStore`.
- [ ] Этап 3: перевести документные инструменты на явные backend-endpoint'ы.
- [ ] Этап 4: сделать backend-модель загрузки файлов и привязки документов (`file upload` + `document binding`).
- [ ] Этап 5: подключить Open WebUI как основной интерфейс через сервер инструментов OpenAPI.
- [ ] Этап 6: добавить совместимый слой `MCP`, шаблоны команд (`slash`) и операционное укрепление.
- [ ] Этап 7: перевести `Chainlit` в наследуемый отладочный путь (`legacy/debug path`).

---

## 3. Что уже считаем установленными предпосылками

- [ ] Manual-first interaction остаётся главным продуктовым принципом.
- [ ] Пользователь должен явно выбирать инструмент, а не доверять обязательному classifier-first маршруту.
- [ ] `Open WebUI` рассматривается как оболочка (`shell`) для чата, истории, аутентификации и вызова инструментов (`tool-calling`), а не как место, где живёт основная бизнес-логика.
- [ ] Возможный будущий orchestration shell в `Open WebUI` допускается только как внешний слой над stable backend contracts, а не как перенос core orchestration внутрь UI.
- [ ] Retrieval, storage, parsing, orchestration и UI должны эволюционировать независимо.
- [ ] По итогам `conversation_about_rag.pdf` и `conversation_about_tools.pdf` правильное разделение уже зафиксировано:
  - [ ] `document_question` = вопрос по документу с retrieval и цитатами
  - [ ] `document_analysis` = анализ одного документа
  - [ ] `compare_documents` = сравнение двух документов
  - [ ] `fast/deep` = режим глубины и бюджета внутри конкретного инструмента, а не отдельный UI-профиль

---

## 4. Итоговая целевая архитектура

## 4.1 Каноническая схема

- [ ] `Open WebUI`
  -> [ ] `OpenAPI Tool Server`
  -> [ ] `FastAPI backend`
  -> [ ] `tool executor / execution runtime`
  -> [ ] `workflow modules`
  -> [ ] `document ingestion + parsing`
  -> [ ] `Qdrant`
  -> [ ] `metadata store`
  -> [ ] `UMS / legal server / document server`

## 4.2 Слои

### Слой A. Интерфейс и оболочка

- [ ] `Open WebUI` как основной chat shell.
- [ ] `Chainlit` только как отладочная и разработческая оболочка.
- [ ] operator UI остаётся отдельной рабочей панелью управления.

### Слой B. Слой контрактов инструментов

- [ ] Явные backend tools.
- [ ] Узкие request/response schemas.
- [ ] Отдельный контракт метаданных выполнения.

### Слой C. Слой оркестрации и правил

- [ ] `execution_runtime.py`
- [ ] `orchestration_runtime.py`
- [ ] `knowledge_base_retrieval.py`
- [ ] `doc_question_heuristics.py`

Этот слой:
- [ ] решает, как собирать контекст;
- [ ] делает merge session docs + KB docs;
- [ ] формирует grounded response;
- [ ] не зависит от конкретного UI.

### Слой D. Слой загрузки и разбора документов

- [ ] upload handling
- [ ] document registration
- [ ] parsing
- [ ] chunking
- [ ] embedding
- [ ] indexing

### Слой E. Слой хранения и поиска

- [ ] `KnowledgeBaseStoreProtocol`
- [ ] `SQLiteKnowledgeBaseStore` как текущая реализация
- [ ] `QdrantKnowledgeBaseStore` как целевая реализация

### Слой F. Слой метаданных

- [ ] binding `chat/thread <-> documents`
- [ ] ingestion jobs
- [ ] document versions
- [ ] tenant/workspace/thread scoping

---

## 5. Open WebUI: как именно будет работать интеграция

## 5.1 Что рекомендует сам Open WebUI

Судя по актуальной документации Open WebUI:

- [ ] **OpenAPI** считается предпочтительным производственным путём интеграции.
- [ ] `MCP` в Open WebUI поддерживается нативно c `v0.6.31+`, но документация прямо указывает: для большинства сценариев развёртывания OpenAPI предпочтительнее.
- [ ] Причины в docs Open WebUI:
  - [ ] готовность к корпоративному использованию (`enterprise readiness`)
  - [ ] шлюзы API, единый вход и квоты (`API gateways / SSO / quotas`)
  - [ ] стандартная HTTP-семантика
  - [ ] наблюдаемость (`observability`)
  - [ ] более предсказуемые рабочие контракты

### Нормативное решение

- [ ] Основной путь интеграции делаем через `OpenAPI`.
- [ ] `MCP` добавляем как совместимый слой для будущих команд (`slash`), сценариев работы (`workflows`) и шаблонов (`prompts`).
- [ ] Внутренний backend не строим как MCP-only.

## 5.1.1 Практические ограничения пути через OpenAPI

- [ ] Путь через OpenAPI не должен становиться заменой backend-модели заданий (`jobs`) и телеметрии.
- [ ] Долгие задачи, загрузку в индекс (`ingestion`) и переиндексацию (`reindexing`) не следует проектировать как "магический стриминг" через вызов инструмента в Open WebUI.
- [ ] Для длинных операций backend должен оставаться source-of-truth для:
  - [ ] status polling
  - [ ] job state
  - [ ] telemetry
  - [ ] operator actions
- [ ] Интеграция с Open WebUI должна получать компактный результат инструмента и при необходимости ссылку на backend-задание или отчёт, а не сырой внутренний прогресс конвейера.

### Почему это важно

- [ ] Иначе мы заново затянем orchestration и progress-логику в UI-shell.
- [ ] Это сломает границу между продуктовым chat UI и operator/control plane.
- [ ] Это особенно критично для:
  - [ ] `document_analysis_deep`
  - [ ] `compare_documents_deep`
  - [ ] ingestion/reindex flows
  - [ ] OCR / Docling pipelines

## 5.2 Как будут жить инструменты (`tools`)

- [ ] Инструменты не прячем за одним `/orchestrate`.
- [ ] Вводим явные endpoints:
  - [ ] `POST /tools/ask_document`
  - [ ] `POST /tools/analyze_document_fast`
  - [ ] `POST /tools/analyze_document_deep`
  - [ ] `POST /tools/compare_documents_fast`
  - [ ] `POST /tools/compare_documents_deep`
  - [ ] `POST /tools/analyze_equipment_fast`
  - [ ] `POST /tools/analyze_equipment_deep`

### Почему это лучше

- [ ] Open WebUI лучше работает с явно описанными tools.
- [ ] Это полностью бьётся с manual-first подходом из [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md).
- [ ] Это не заставляет прогонять каждый запрос через intent-классификатор.

## 5.3 Как будут работать slash-команды

### Важная развилка

По `MCP`-спецификации модель команд (`slash`) естественнее ложится не на `tools`, а на `prompts`:

- [ ] `MCP prompts` предназначены для повторно используемых пользовательских сценариев (`user-controlled reusable workflows`).
- [ ] В документации MCP они описываются как интерфейсная поверхность (`UI-surface`), которая может выступать аналогом slash-команд.

### Практическое решение

- [ ] Не привязывать базовую диспетчеризацию инструментов (`tool dispatch`) к slash-командам.
- [ ] Канонический путь:
  - [ ] пользователь прикрепляет файл(ы)
  - [ ] пользователь выбирает tool в Open WebUI
  - [ ] backend исполняет tool
- [ ] Слой slash-команд можно добавить позже как удобную оболочку:
  - [ ] `/ask-document`
  - [ ] `/analyze-document-fast`
  - [ ] `/compare-fast`
  - [ ] `/compare-deep`
- [ ] Но slash не должен быть единственным способом запуска инструмента.

## 5.4 Как будут работать файлы и изображения

### Текущее целевое правило

- [ ] Пользователь загружает файл/изображение в чат Open WebUI.
- [ ] Open WebUI передаёт загрузку (`upload`) в backend-контур.
- [ ] Backend присваивает:
  - [ ] `file_id`
  - [ ] `document_id`
  - [ ] `version_id`
  - [ ] `thread_id`
  - [ ] `tenant_id/workspace_id`
- [ ] Затем документ либо:
  - [ ] остаётся как наложение сеанса (`session overlay`)
  - [ ] отправляется в индексирующую обработку (`ingestion`) для базы знаний (`KB`)
  - [ ] и то, и другое

### Ответ на пользовательский вопрос

> Возможно ли сразу, загрузив фотографию, вызвать slash/tool и обработать документ?

- [ ] Да, но технически правильнее строить это как:
  - [ ] загрузка файла
  - [ ] регистрация документа в backend
  - [ ] явный вызов инструмента (`explicit tool invocation`)
- [ ] Для MVP не стоит завязывать это на “магическую” семантику slash-команд.
- [ ] Правильный UX:
  - [ ] файл прикреплён
  - [ ] пользователь выбирает `ask_document` или `analyze_document_fast`
  - [ ] backend знает, какие загруженные документы активны в этой ветке диалога (`thread`)

### Фото / OCR

- [ ] Фото и PDF с большим числом изображений не должны жить отдельной логикой внутри интерфейса.
- [ ] OCR-путь и путь обработки изображений должны быть частью слоя разбора и загрузки (`parsing/ingestion layer`).
- [ ] В дальнейшем это лучше совмещать с `Docling` и OCR-резервом (`fallback`), а не с разрозненными ветками в чатовом слое.

## 5.5 Как будет работать контекст и сессионность

### Что нельзя сохранять

- [ ] Нельзя хранить состояние поиска (`retrieval state`) только в сеансе интерфейса (`UI session`).
- [ ] Нельзя строить главный индекс в памяти UI-процесса.

### Что нужно хранить отдельно

- [ ] `chat/session state`
  - [ ] история сообщений (`history`)
  - [ ] активное намерение по инструменту (`active tool intent`)
  - [ ] active_doc_ids
- [ ] `document state`
  - [ ] метаданные файла (`file metadata`)
  - [ ] версии
  - [ ] статус разбора (`parser status`)
- [ ] `retrieval state`
  - [ ] векторный индекс (`vector index`)
  - [ ] фильтры полезной нагрузки (`payload filters`)
  - [ ] пространства имён и tenant-привязки

### Практическая модель

- [ ] История чата хранится в слое чата интерфейса и backend.
- [ ] Активные вложения хранятся как привязки (`bindings`).
- [ ] Поиск идёт по фильтрам:
  - [ ] `tenant_id`
  - [ ] `thread_id`
  - [ ] `active_doc_ids`
  - [ ] `collection_id`

### Итог

- [ ] После рестарта системный контекст не теряется.
- [ ] Сценарий с несколькими воркерами (`multi-worker`) становится реалистичным.
- [ ] Open WebUI можно масштабировать без привязки к одному процессу.

---

## 6. Qdrant: как именно строим RAG

## 6.1 Общий принцип

- [ ] `Qdrant` используем как основное постоянное векторное хранилище (`persistent vector backend`).
- [ ] `knowledge_base_retrieval.py` не превращаем в слой прямых Qdrant-деталей.
- [ ] `knowledge_base_retrieval.py` остаётся:
  - [ ] слоем правил (`policy layer`)
  - [ ] слоем объединения (`merge layer`)
  - [ ] слоем происхождения источников (`provenance layer`)
  - [ ] слоем подключения повторного ранжирования (`rerank hook layer`)

## 6.2 До Qdrant нужна граница протокола (`protocol boundary`)

- [ ] Сначала внедряем `KnowledgeBaseStoreProtocol`.
- [ ] Только после этого внедряем `QdrantKnowledgeBaseStore`.
- [ ] Это уже зафиксировано в:
  - [ ] [docs/plans/2026-04-01-knowledge-base-store-protocol-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-knowledge-base-store-protocol-plan.md)
  - [ ] [docs/plans/2026-04-01-qdrant-knowledge-base-store-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-qdrant-knowledge-base-store-plan.md)

## 6.3 Сессионный RAG и RAG базы знаний

### Целевое разделение

- [ ] `session_rag`
  - [ ] только что загруженные документы
  - [ ] ограничение по ветке диалога (`thread-scoped`)
  - [ ] может жить как путь наложения (`overlay path`)
- [ ] `knowledge_base_rag`
  - [ ] постоянные векторы в `Qdrant`
  - [ ] ограничение по коллекции, рабочему пространству и арендатору (`collection/workspace/tenant scoped`)
- [ ] `knowledge_base + session overlay`
  - [ ] короткий список кандидатов из KB (`shortlist`)
  - [ ] короткий список кандидатов из документов сеанса
  - [ ] объединение, дедупликация и повторное ранжирование (`merge + dedup + rerank`)

### Можно ли Qdrant использовать для session RAG

- [ ] Да, но не обязательно в первой фазе.
- [ ] Лучшая практика:
  - [ ] в V1 хранить базу знаний в Qdrant
  - [ ] наложение сеанса (`session overlay`) держать отдельно
  - [ ] объединять на слое правил (`policy layer`)
- [ ] В V2 можно вынести и документы сеанса в Qdrant с короткоживущими областями действия (`short-lived scopes`):
  - [ ] `thread_id`
  - [ ] `ttl`
  - [ ] `source_scope=session`

### Почему не надо делать “только Qdrant сразу для всего”

- [ ] Сессионные документы требуют более осторожной модели жизненного цикла (`life-cycle`).
- [ ] Нужно заранее решить:
  - [ ] TTL
  - [ ] re-upload semantics
  - [ ] cleanup
  - [ ] privacy / isolation

## 6.4 Рекомендуемый порядок внедрения Qdrant

- [ ] сначала плотный поиск (`dense-first`)
- [ ] затем фильтры полезной нагрузки (`payload filters`)
- [ ] затем подключение повторного ранжирования (`rerank hook`)
- [ ] затем гибридный поиск (`hybrid search`)

### Практический смысл

- [ ] Не начинать с `ColBERT`, мультивекторного режима и разрежённой сложной схемы (`sparse complexity`).
- [ ] Сначала получить производственно устойчивую основу (`production-stable`):
  - [ ] стратегию коллекций
  - [ ] схему полезной нагрузки (`payload schema`)
  - [ ] детерминированные идентификаторы
  - [ ] плотный поиск с фильтрами

## 6.5 Qdrant payload schema

В V1 обязательно нужны:

- [ ] `tenant_id`
- [ ] `collection_id`
- [ ] `source_id`
- [ ] `document_id`
- [ ] `version_id`
- [ ] `display_name`
- [ ] `chunk_id`
- [ ] `chunk_index`
- [ ] `text`
- [ ] `doc_type`
- [ ] `language`
- [ ] `section_title`
- [ ] `thread_id`
- [ ] `workspace_id`
- [ ] `source_scope`
- [ ] `content_hash`
- [ ] `embedding_model_id`
- [ ] `index_version`

### Правила

- [ ] Один chunk = один point.
- [ ] `point_id` должен быть детерминированным.
- [ ] Не создавать collection на каждый документ.
- [ ] Предпочитать payload-based partitioning.

## 6.6 Стратегия фильтрации

- [ ] Фильтры должны работать минимум по:
  - [ ] `tenant_id`
  - [ ] `workspace_id`
  - [ ] `thread_id`
  - [ ] `document_id`
  - [ ] `collection_id`
  - [ ] `source_scope`
  - [ ] `doc_type`
  - [ ] `language`

### Практический вывод

- [ ] Для вопросов “по этому документу” поиск не должен идти по всей базе знаний.
- [ ] Для наложения сеанса (`session overlay`) должен быть жёсткий контракт фильтрации.

## 6.7 Гибридный поиск и повторное ранжирование

На основании Qdrant docs и текущего проекта:

- [ ] V1:
  - [ ] плотный поиск (`dense retrieval`)
  - [ ] фильтры полезной нагрузки
  - [ ] опциональный локальный `BM25` по короткому списку кандидатов
- [ ] V2:
  - [ ] гибридный поиск `dense+sparse`
  - [ ] опциональное повторное ранжирование (`reranking`)
- [ ] V3:
  - [ ] позднее взаимодействие (`late interaction`) и повторное ранжирование в стиле `ColBERT`, только если это действительно нужно

### Нормативное решение

- [ ] Не начинать миграцию с полной гибридной сложности.
- [ ] Сначала получить качественный плотный поиск, фильтры и цитаты.

---

## 7. Разбор документов и обработка файлов

## 7.1 Целевая позиция

- [ ] Текущий разбор документов (`document parsing`) не должен навсегда оставаться смесью разрозненных резервных путей (`ad-hoc fallback paths`).
- [ ] Целевой путь разбора (`parser path`) должен принадлежать сервисному слою.
- [ ] `Docling` остаётся целевым кандидатом на следующий производственно пригодный слой разбора.

## 7.2 Что это значит на практике

- [ ] `document_server` должен эволюционировать к модели адаптеров (`adapter model`):
  - [ ] текущие парсеры
  - [ ] адаптер `Docling`
  - [ ] OCR-резерв (`fallback`)
- [ ] Вызывающие модули не должны знать, какой парсер сработал.

## 7.3 Для миграции в Open WebUI

- [ ] Upload/document binding и parsing migration не надо делать в одном PR с Open WebUI shell.
- [ ] Но нужно уже сейчас проектировать единый backend file contract так, чтобы потом без боли заменить parser backend.

---

## 8. Разбор незавершённых веток и предложений

## 8.1 `origin/claude/compare-workflow-llm-discrepancy`

### Что в ветке ценно

- [ ] Это самый практичный и срочный candidate.
- [ ] Ветка адресует уже существующие задачи в [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md):
  - [ ] `R1.0.10`
  - [ ] `R1.0.11`
  - [ ] `R1.0.12`
- [ ] Ключевая полезная часть:
  - [ ] structural items (`ADDED/DELETED`) убираются из LLM queue
  - [ ] single-item JSON parsing стабилизируется
  - [ ] offline-mode budget for compare gets constrained
  - [ ] добавлены хорошие regression tests

### Что не надо делать

- [ ] Не merge-ить ветку целиком against current `dev`.
- [ ] Не брать из неё побочные удаления/устаревшие docs pieces.

### Решение

- [ ] Брать **почти целиком по смыслу**, но переносить вручную как targeted compare-fix slice.
- [ ] Приоритет: `P0`.

## 8.2 `origin/claude/redesign-compare-bounded-latency`

### Что в ветке ценно

- [ ] Ветка хорошо попадает в:
  - [ ] bounded latency
  - [ ] fast/deep compare
  - [ ] summary-first path
- [ ] Ветка даёт:
  - [ ] `compare_depth_mode`
  - [ ] top-N bounded analysis
  - [ ] summary-first structure
  - [ ] tests under `test_compare_bounded_latency.py`

### Что не совпадает с текущим направлением

- [ ] Ветка включает `Chainlit`-specific UI choice buttons.
- [ ] Это не совпадает с принятым направлением:
  - [ ] не инвестировать в Chainlit profiles / mode UX
  - [ ] выносить fast/deep на уровень tool contracts

### Решение

- [ ] Брать **частично**:
  - [ ] bounded latency policy
  - [ ] compare fast/deep semantics
  - [ ] tests
- [ ] Не брать:
  - [ ] Chainlit-specific interaction layer
- [ ] Перепроектировать это как tool-level `compare_documents_fast/deep`.
- [ ] Приоритет: `P1`.

## 8.3 `origin/copilot/fix-model-download-scripts`

### Что в ветке ценно

- [ ] Ветка затрагивает реальную боль: актуальность model download paths.
- [ ] Она переводит baseline LLM на `Qwen3-14B` и retrieval model source на другой LaBSE repo.

### Почему нельзя брать напрямую

- [ ] Текущий проект осознанно держит `Qwen2.5-14B` как working baseline для LLM.
- [ ] По retrieval baseline уже зафиксировано, что `LaBSE` остаётся baseline до отдельного решения.
- [ ] Raw merge этой ветки изменит рабочий runtime contract без достаточной проверки.

### Решение

- [ ] Брать **частично**.
- [ ] Правильный follow-up:
  - [ ] не заменять `Qwen2.5` на `Qwen3` насильно
  - [ ] добавить dual-source support:
    - [ ] `Qwen2.5` stays baseline
    - [ ] `Qwen3` becomes optional install target
  - [ ] не менять retrieval baseline автоматически
  - [ ] отдельно оценить `cointegrated/LaBSE-en-ru` только через eval
- [ ] Приоритет: `P2`.

## 8.4 `origin/copilot/update-package-scripts-and-dependencies`

### Что в ветке ценно

- [ ] Есть полезный intent: осовременить package floor versions и wheelhouse pins.

### Почему raw merge вреден

- [ ] Ветка собрана против старого base state и тащит огромный мусорный diff.
- [ ] Она обновляет Python ecosystem без единого conda-first контракта.
- [ ] У пользователя уже есть явное требование:
  - [ ] выстроить зависимости через `conda/conda-forge`
  - [ ] потом пересобрать host bundle APT + wheelhouse + miniconda + dev/runtime packages

### Решение

- [ ] Не брать ветку как merge candidate.
- [ ] Завести отдельный workstream:
  - [ ] `conda-first dependency baseline`
  - [ ] `offline bundle rebuild`
  - [ ] `apt + wheelhouse + miniconda packaging review`
- [ ] Использовать ветку только как reference input по версиям.
- [ ] Приоритет: `P2`, но отдельным треком.

---

## 9. Что берём из `clone_claude`

Из локальной reference-кодовой базы в `/home/seral/HDD/clone_claude` для нас полезны не куски кода, а паттерны.

## 9.1 Что берём

- [ ] five-layer architecture
- [ ] explicit core loop
- [ ] streaming-first event model
- [ ] tool as capability
- [ ] permission as boundary
- [ ] separation of context / memory / runtime state

## 9.2 Что это значит у нас

- [ ] `Open WebUI` и `Chainlit` должны быть только интерфейсным слоем (`interaction layer`).
- [ ] `execution_runtime` должен быть явным ядром выполнения.
- [ ] `tools` должны быть отдельными единицами возможностей (`capability units`) с ясными схемами.
- [ ] Разрешения и опасные действия должны жить в явном каталоге действий и инструментов.
- [ ] Состояние поиска не должно растворяться в памяти сеанса интерфейса.

## 9.3 Что не переносим

- [ ] допущения вида `CLI-first`
- [ ] их продуктовые названия
- [ ] удалённую модель разрешений один в один
- [ ] их UX с командами и инструментами без адаптации к Open WebUI

---

## 10. Последовательность работ по этапам

### Этап 0. Очистка области работ, унификация окружения и архитектурная фиксация

**Смысл этапа:** прежде чем трогать `Open WebUI`, нужно убрать всё, что размывает контракт системы уже сейчас: многофайловые `env`, неочевидные профили в `Chainlit`, размытый список инструментов и незафиксированные обязательные решения.

- [ ] Зафиксировать в документации и backlog, что текущий canonical UI — `Chainlit`, а `Open WebUI` возвращается как controlled evaluation contour и candidate shell.
- [ ] Зафиксировать, что `Chainlit`-профили больше не развиваем.
- [ ] Зафиксировать phase-0 decision record:
  - [ ] `OpenAPI-first`
  - [ ] `MCP-secondary`
  - [ ] `backend/open_webui_uploads` rename postponed
  - [ ] no business logic inside `Open WebUI`
- [ ] Зафиксировать, что `backend/.env` становится единственным каноническим источником конфигурации и запуска для native-пути.
- [ ] Вывести `backend/.env.runtime` и `backend/.env.hardware.override` из канонического контракта.
- [ ] Перевести `runtime_preflight.py` в режим:
  - [ ] советчика (`advisor`)
  - [ ] опционального применителя изменений в `backend/.env`
- [ ] Подготовить в operator UI отдельную кнопку `Подобрать рекомендуемые параметры`, которая вызывает preflight и предлагает или применяет рекомендации без второго env-файла.
- [ ] Зафиксировать матрицу инструментов:
  - [ ] `ask_document`
  - [ ] `analyze_document_fast/deep`
  - [ ] `compare_documents_fast/deep`
  - [ ] `analyze_equipment_fast/deep`
- [ ] Закрыть review по `compare-workflow-llm-discrepancy`.
- [ ] Подготовить исправления compare как отдельный рабочий срез (`implementation slice`).

### Этап 1. Немедленные стабилизаторы

**Смысл этапа:** убрать баги и поведение, которые уже сейчас делают будущую миграцию дороже и опаснее. Это не этап смены UI, а этап снятия ложной сложности.

- [ ] Внедрить `compare-workflow-llm-discrepancy`.
- [ ] Перенести ограничения по времени ответа (`bounded latency semantics`) из `redesign-compare-bounded-latency`, но без `Chainlit`-специфичного UX.
- [ ] Не трогать Open WebUI на этом шаге.

### Этап 2. Абстракция хранилища

**Смысл этапа:** разорвать прямую зависимость от `SQLiteKnowledgeBaseStore`, чтобы последующий переход на `Qdrant` был заменой реализации, а не переписыванием вызывающих мест.

- [ ] Внедрить `KnowledgeBaseStoreProtocol`.
- [ ] Убрать типовую зависимость от `SQLiteKnowledgeBaseStore`.
- [ ] Прогнать регрессионные тесты на базу знаний и поиск (`KB/retrieval regression tests`).

### Этап 3. Backend на Qdrant

**Смысл этапа:** перевести постоянную базу знаний на производственное векторное хранилище, не ломая текущие правила поиска и не растворяя сессионные документы в той же логике раньше времени.

- [ ] Добавить `QdrantKnowledgeBaseStore`.
- [ ] Ввести схему полезной нагрузки (`payload schema`).
- [ ] Ввести детерминированные идентификаторы точек.
- [ ] Подключить плотный поиск с фильтрами.
- [ ] Сохранить наложение сеанса (`session overlay`) в слое объединения.

### Этап 4. Переработка контрактов инструментов

**Смысл этапа:** уйти от одного перегруженного маршрута оркестрации к явным backend-инструментам с понятными схемами входа и результата. Это и есть реальная основа для Open WebUI.

- [ ] Отвязать выполнение от наследованного перегруженного контракта `orchestrate`.
- [ ] Добавить явные endpoint'ы инструментов.
- [ ] Вынести общий исполнительный слой (`shared executor layer`).
- [ ] Сделать режимы `fast/deep` частью контракта инструмента.

### Этап 5. Загрузка файлов и привязка документов

**Смысл этапа:** сделать так, чтобы файлы, изображения и документы стали backend-сущностями с идентификаторами, версиями и привязкой к чату, а не просто временными путями на файловой системе.

- [ ] Ввести backend-контракт загрузки файлов.
- [ ] Ввести регистрацию документов и управление версиями.
- [ ] Ввести привязки `thread/workspace/document`.
- [ ] Подготовить путь расширения для OCR и замены парсера документов.

### Этап 6. Основное подключение Open WebUI

**Смысл этапа:** только после стабилизации backend-границ подключить Open WebUI как основной пользовательский интерфейс, чтобы он опирался на уже готовую backend-модель, а не диктовал её.

- [ ] Подключить backend как сервер инструментов OpenAPI.
- [ ] Настроить видимость инструментов, аутентификацию и обработку файлов.
- [ ] Реализовать рабочий пользовательский путь:
  - [ ] загрузить файл
  - [ ] выбрать инструмент
  - [ ] выполнить инструмент
  - [ ] получить доказательный ответ или отчёт

### Этап 7. Сопутствующий слой MCP

**Смысл этапа:** после стабилизации пути через OpenAPI можно добавить совместимый слой `MCP` для будущих команд, шаблонов и повторно используемых сценариев, не делая его обязательным ядром системы.

- [ ] Поднять совместимую оболочку `MCP` поверх инструментов.
- [ ] Добавить повторно используемые шаблоны (`prompts`) и сценарии, если понадобится UX с командами (`slash UX`).
- [ ] Не заменять этим `OpenAPI` как основной путь.

### Этап 8. Operator UI и поверхности индексации

**Смысл этапа:** оператор должен видеть не только запуск системы, но и жизненный цикл базы знаний: загрузку, индексацию, переиндексацию, очистку и состояние `Qdrant`.

- [ ] Добавить в operator UI панель:
  - [ ] загрузка и индексирование документов в `Qdrant`
  - [ ] статус очереди индексации
  - [ ] выбор модели эмбеддингов
  - [ ] переиндексация, пересборка и удаление
- [ ] Отдельно показать:
  - [ ] статистику коллекций
  - [ ] версию схемы полезной нагрузки
  - [ ] состояние индекса

### Этап 9. Перевод Chainlit в вторичную роль

**Смысл этапа:** только когда пользовательский путь через Open WebUI действительно работает, можно официально понизить роль `Chainlit` до отладочного и переходного контура.

- [ ] Убрать `Chainlit` из основного продуктового пути.
- [ ] Оставить только:
  - [ ] отладку и разработку
  - [ ] быстрые дымовые проверки (`smoke tests`)
  - [ ] просмотр трассировки выполнения

---

## 11. Практика реализации по лучшим практикам

### 11.1 Как реализовывать контракты инструментов

**Рекомендуемая реализация**

- [ ] Делать каждый инструмент отдельным backend-endpoint'ом с узкой схемой входа и ответа.
- [ ] Не делать один универсальный `POST /orchestrate` для всех сценариев Open WebUI.
- [ ] В схеме каждого инструмента явно фиксировать:
  - [ ] обязательные поля
  - [ ] необязательные поля
  - [ ] идентификаторы документов
  - [ ] режим `fast/deep`
  - [ ] структуру результата
  - [ ] поля телеметрии и качества
- [ ] Разделять:
  - [ ] пользовательский текст ответа
  - [ ] структурированный результат
  - [ ] метаданные выполнения
  - [ ] список источников
- [ ] Держать инструменты атомарными; выбор сценария должен идти через выбор инструмента, а не через скрытый роутинг внутри одного общего endpoint'а.

**Почему так**

- [ ] Open WebUI сам продвигает OpenAPI Tool Servers как production-friendly слой интеграции.
- [ ] MCP-спецификация рассматривает инструменты как атомарные единицы возможностей, а не как скрытый внутренний оркестратор.
- [ ] Такой контракт проще тестировать, версионировать и подключать к разным оболочкам.

**Источники**

- [ ] Open WebUI OpenAPI Tool Servers:
  - [ ] <https://docs.openwebui.com/features/plugin/tools/openapi-servers/>
- [ ] MCP tools specification:
  - [ ] <https://modelcontextprotocol.io/specification/2025-03-26/server/tools>

### 11.2 Как реализовывать длинные задачи, опрос состояния и модель заданий

**Рекомендуемая реализация**

- [ ] Для тяжёлых операций не пытаться удерживать один долгий HTTP-ответ до конца выполнения.
- [ ] Делать старт задачи через:
  - [ ] `POST /jobs/...` или `POST /tools/...`
  - [ ] ответ `202 Accepted`
  - [ ] возврат `job_id`, `status_url`, `submitted_at`, `kind`
- [ ] Дальше вести опрос состояния через отдельный endpoint:
  - [ ] `GET /jobs/{job_id}`
- [ ] Для завершённой задачи хранить:
  - [ ] `status`
  - [ ] `started_at`
  - [ ] `completed_at`
  - [ ] `current_stage`
  - [ ] `logs`
  - [ ] `result_ref`
  - [ ] `error_summary`
- [ ] Для лёгких фоновых операций внутри одного процесса можно использовать `FastAPI BackgroundTasks`.
- [ ] Для тяжёлых операций индексации, OCR, переиндексации и больших отчётов лучше закладывать отдельную очередь или worker, а не полагаться только на in-process background tasks.

**Почему так**

- [ ] RFC 9110 для `202 Accepted` прямо говорит, что сервер должен дать представление о текущем статусе и указать на монитор состояния.
- [ ] FastAPI рекомендует `BackgroundTasks` только для небольших фоновых действий; тяжёлые задачи лучше выносить в отдельную очередь.
- [ ] Это естественно отделяет пользовательский чат от operator/control plane.

**Источники**

- [ ] RFC 9110, `202 Accepted`:
  - [ ] <https://www.rfc-editor.org/rfc/rfc9110>
- [ ] FastAPI Background Tasks:
  - [ ] <https://fastapi.tiangolo.com/tutorial/background-tasks/>
  - [ ] <https://fastapi.tiangolo.com/reference/background/>

### 11.3 Как реализовывать загрузку файлов и привязку документов

**Рекомендуемая реализация**

- [ ] Принимать файлы как `multipart/form-data`.
- [ ] В FastAPI использовать `UploadFile`, а не загружать всё в `bytes`, если файлы могут быть крупными.
- [ ] После приёма файла сразу создавать backend-сущности:
  - [ ] `file_id`
  - [ ] `document_id`
  - [ ] `version_id`
  - [ ] `thread_id`
  - [ ] `tenant_id`
- [ ] Хранить отдельно:
  - [ ] исходный файл
  - [ ] запись о документе
  - [ ] запись о версии
  - [ ] привязку документа к чату или рабочему пространству
- [ ] Не передавать дальше по системе голые локальные пути из интерфейса.
- [ ] Для нескольких файлов использовать единый upload-endpoint с поддержкой списка `UploadFile`.
- [ ] Для изображения и PDF с картинками не делать отдельную ветку в UI; backend сам должен решить, идёт ли файл в OCR/Docling/document parser path.

**Почему так**

- [ ] `UploadFile` в FastAPI использует `SpooledTemporaryFile`, что лучше для больших файлов и не держит всё в памяти.
- [ ] Open WebUI умеет работать с файлами и знаниями как UX-моделью, но жизненный цикл документов и привязок к чату нам всё равно нужно держать у себя.

**Источники**

- [ ] FastAPI Request Files:
  - [ ] <https://fastapi.tiangolo.com/tutorial/request-files/>
- [ ] FastAPI Request Forms and Files:
  - [ ] <https://fastapi.tiangolo.com/tutorial/request-forms-and-files/>
- [ ] Open WebUI RAG / files:
  - [ ] <https://docs.openwebui.com/features/rag/>

### 11.4 Как реализовывать контекст, сессионность и память

**Рекомендуемая реализация**

- [ ] Отделить три сущности:
  - [ ] история чата
  - [ ] активные документы
  - [ ] состояние поиска
- [ ] Историю чата хранить как историю сообщений и метаданные вызовов инструментов.
- [ ] Активные документы хранить как привязки `thread -> document_version`.
- [ ] Состояние поиска хранить в `Qdrant` и metadata-слое, а не в памяти процесса интерфейса.
- [ ] Для сессионных документов на первой фазе держать отдельное наложение сеанса (`session overlay`) и объединять его с KB-результатами в backend.
- [ ] На второй фазе можно рассмотреть хранение session-чанков в `Qdrant` с коротким сроком жизни и фильтрами по `thread_id`.

**Почему так**

- [ ] Это позволяет переживать рестарт, несколько воркеров и смену оболочки.
- [ ] Это соответствует и лучшим практикам vector DB, и разделению слоёв из reference architecture.

**Источники**

- [ ] Qdrant multitenancy:
  - [ ] <https://qdrant.tech/documentation/guides/multitenancy/>
- [ ] Qdrant filtering:
  - [ ] <https://qdrant.tech/documentation/concepts/filtering/>

### 11.5 Как реализовывать Qdrant для RAG

**Рекомендуемая реализация**

- [ ] На первой фазе делать `dense-first` поиск.
- [ ] Не создавать отдельную коллекцию на каждый документ.
- [ ] Держать одну коллекцию на модель эмбеддингов или ограниченный набор коллекций, а изоляцию делать через payload-фильтры.
- [ ] В payload хранить:
  - [ ] tenant/workspace/thread scope
  - [ ] document/version identity
  - [ ] source_scope
  - [ ] chunk metadata
  - [ ] embedding/index version
- [ ] `point_id` делать детерминированным.
- [ ] На первой фазе ограничиться:
  - [ ] dense retrieval
  - [ ] payload filtering
  - [ ] optional rerank on shortlist
- [ ] Гибридный поиск `dense+sparse` и позднее взаимодействие (`late interaction`) добавлять только после стабильной базовой схемы.

**Почему так**

- [ ] Сама документация `Qdrant` рекомендует payload-based partitioning вместо большого числа коллекций.
- [ ] Гибридный поиск и повторное ранжирование реально полезны, но добавляют задержку и сложность; их лучше включать поэтапно.

**Источники**

- [ ] Qdrant filtering:
  - [ ] <https://qdrant.tech/documentation/concepts/filtering/>
- [ ] Qdrant multitenancy:
  - [ ] <https://qdrant.tech/documentation/guides/multitenancy/>
- [ ] Qdrant hybrid search with reranking:
  - [ ] <https://qdrant.tech/documentation/advanced-tutorials/reranking-hybrid-search/>

### 11.6 Как реализовывать путь через Open WebUI

**Рекомендуемая реализация**

- [ ] Сначала подключать backend как OpenAPI Tool Server.
- [ ] Разделять пользовательские (`user`) и общие (`global`) серверы инструментов.
- [ ] Не рассчитывать на то, что Open WebUI сам станет источником истины для состояния задачи, документов или привязок.
- [ ] Для native function calling учитывать, что выбранная модель действительно должна хорошо поддерживать вызов инструментов.
- [ ] Для Open WebUI file/knowledge UX использовать его как оболочку, но не отдавать ему владение жизненным циклом документа.

**Почему так**

- [ ] Open WebUI прямо рекомендует OpenAPI как предпочтительный путь интеграции.
- [ ] Для `MCP` Open WebUI сам показывает путь через `mcpo` как мост к OpenAPI.
- [ ] Документация Open WebUI предупреждает, что implicit RAG/tool behavior нельзя считать стабильной бизнес-логикой.

**Источники**

- [ ] Open WebUI OpenAPI servers:
  - [ ] <https://docs.openwebui.com/openapi-servers/>
- [ ] Open WebUI OpenAPI integration:
  - [ ] <https://docs.openwebui.com/features/plugin/tools/openapi-servers/open-webui/>
- [ ] Open WebUI MCP:
  - [ ] <https://docs.openwebui.com/features/mcp>
  - [ ] <https://docs.openwebui.com/openapi-servers/mcp/>

### 11.7 Как реализовывать путь через MCP

**Рекомендуемая реализация**

- [ ] Не начинать систему с MCP-only режима.
- [ ] Рассматривать `MCP` как второй интеграционный слой поверх уже готовых backend-инструментов.
- [ ] Через `MCP` хорошо выносить:
  - [ ] повторно используемые команды
  - [ ] шаблоны действий
  - [ ] будущие slash-сценарии
- [ ] Если нужен быстрый production path, сначала поднимать OpenAPI и только потом оборачивать его в `mcpo` или параллельный `MCP`-контур.

**Почему так**

- [ ] И документация Open WebUI, и их собственный мост `mcpo` фактически подтверждают подход `OpenAPI first`.
- [ ] Это уменьшает риски по аутентификации, квотам, проксированию и наблюдаемости.

**Источники**

- [ ] Open WebUI MCP:
  - [ ] <https://docs.openwebui.com/features/mcp>
- [ ] Open WebUI MCP to OpenAPI proxy:
  - [ ] <https://docs.openwebui.com/openapi-servers/mcp/>
- [ ] MCP prompts:
  - [ ] <https://modelcontextprotocol.io/legacy/concepts/prompts>
- [ ] MCP tools:
  - [ ] <https://modelcontextprotocol.io/specification/2025-03-26/server/tools>

### 11.8 Как реализовывать замену document parsing на Docling

**Рекомендуемая реализация**

- [ ] Не менять сразу все вызовы `document_server` на прямые вызовы `Docling`.
- [ ] Вынести parser adapter layer внутри document service.
- [ ] Сохранять единый ответ для вызывающих модулей:
  - [ ] текст
  - [ ] страницы
  - [ ] таблицы
  - [ ] структурные блоки
- [ ] `Docling` сначала вводить как один из parser backend-ов:
  - [ ] current parser path
  - [ ] Docling adapter
  - [ ] OCR fallback
- [ ] Только после этого переводить конкретные workflow на новый parser path.

**Почему так**

- [ ] Это позволяет не ломать `compare`, `document_analysis` и `equipment` одним большим diff.
- [ ] Такой путь бьётся с общим принципом adapter-first migration.

**Источники**

- [ ] Docling documentation:
  - [ ] <https://docling-project.github.io/docling/>
- [ ] Docling GitHub:
  - [ ] <https://github.com/docling-project/docling>

---

## 12. Backlog-эпики для агентов
### Эпик A. Укрепление compare-сценария

**Зачем нужен:** без этого сравнение документов останется главным источником ложной нагрузки на `LLM`, деградации времени ответа и некорректного поведения в offline-режиме.

- [ ] Перенести `origin/claude/compare-workflow-llm-discrepancy`
- [ ] Перенести полезную часть из `origin/claude/redesign-compare-bounded-latency`
- [ ] Перевести `fast/deep` из уровня интерфейса на уровень контракта инструмента

**Как реализовывать:**
- [ ] Сначала убрать структурные изменения (`ADDED/DELETED`) из очереди на `LLM`, чтобы не тратить модель на детерминированные случаи.
- [ ] Для `batch=1` принимать и один JSON-объект, и список с одним объектом; это уменьшит ломкость parsing-пути.
- [ ] Режимы `fast/deep` описывать как backend-параметр инструмента, а не как UI-ветку.
- [ ] Все изменения сопровождать регрессионными тестами на честный размер очереди, разбор single-item JSON и bounded latency.

**Источники:**
- [ ] [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)
- [ ] `origin/claude/compare-workflow-llm-discrepancy`
- [ ] `origin/claude/redesign-compare-bounded-latency`

### Эпик B. Граница хранилища базы знаний

**Зачем нужен:** это обязательный мост между текущим `SQLite` и будущим `Qdrant`; без него миграция на production-ready поиск будет хаотичной.

- [ ] Реализовать `KnowledgeBaseStoreProtocol`
- [ ] Перетипизировать ingestion и retrieval
- [ ] Сохранить текущее поведение `SQLite`

**Как реализовывать:**
- [ ] Вводить `Protocol` как чистый контракт методов, а не новую параллельную реализацию.
- [ ] Оставить `get_knowledge_base_store()` канонической фабрикой (`factory`), чтобы UI и orchestration не знали про конкретный backend.
- [ ] Перетипизировать сначала ingestion и retrieval, и только потом добавлять новый backend.
- [ ] Не менять retrieval-semantics в том же diff, где появляется `Protocol`.

**Источники:**
- [ ] [2026-04-01-knowledge-base-store-protocol-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-knowledge-base-store-protocol-plan.md)
- [ ] [2026-04-01-migration-readiness-pr-decision-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-migration-readiness-pr-decision-plan.md)

### Эпик C. Векторное хранилище Qdrant

**Зачем нужен:** он переводит базу знаний с локального пересоздания индекса на постоянный векторный поиск с фильтрами и рабочей схемой данных.

- [ ] Добавить `QdrantKnowledgeBaseStore`
- [ ] Добавить фильтры и схему полезной нагрузки
- [ ] Встроить выдачу короткого списка кандидатов (`shortlist retrieval`) в `knowledge_base_retrieval.py`

**Как реализовывать:**
- [ ] Идти по схеме `dense-first`: сначала плотный поиск и фильтры, затем гибридный поиск и повторное ранжирование.
- [ ] Один чанк = одна точка (`point`) с детерминированным `point_id`.
- [ ] Не создавать коллекцию на каждый документ; использовать фильтрацию по полезной нагрузке (`payload filters`) для `tenant/workspace/thread/document`.
- [ ] Сессионные документы сначала держать как отдельное наложение (`session overlay`), а не смешивать их сразу с постоянной базой знаний.
- [ ] Переиндексацию делать через версионирование `document_version` и `index_version`, а не через silent overwrite.

**Источники:**
- [ ] [2026-04-01-qdrant-knowledge-base-store-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-qdrant-knowledge-base-store-plan.md)
- [ ] <https://qdrant.tech/documentation/concepts/filtering/>
- [ ] <https://qdrant.tech/documentation/guides/multitenancy/>
- [ ] <https://qdrant.tech/documentation/advanced-tutorials/reranking-hybrid-search/>

### Эпик D. Программный интерфейс инструментов

**Зачем нужен:** это реальный производственный контракт системы. Пока его нет, ни Open WebUI, ни MCP, ни operator surface не могут опираться на стабильные backend-инструменты.

- [ ] Описать схемы инструментов
- [ ] Построить endpoint'ы инструментов
- [ ] Собрать общий исполнительный путь
- [ ] Добавить backend-модель загрузки файлов и привязки документов

**Как реализовывать:**
- [ ] Для каждого инструмента фиксировать входную схему, обязательность файлов, формат ответа, ошибки валидации и метаданные выполнения.
- [ ] Не использовать один перегруженный endpoint с внутренним классификатором; делать отдельные маршруты `POST /tools/...`.
- [ ] Общий исполнительный путь выносить в один backend-слой, чтобы и `Chainlit`, и `Open WebUI`, и будущий `MCP` вызывали одну и ту же доменную логику.
- [ ] В ответах длинных инструментов возвращать не только текст, но и структурированные поля: `assistant_message`, `sources`, `quality_signals`, `execution_metadata`, при необходимости `job_id`.

**Источники:**
- [ ] [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md)
- [ ] <https://docs.openwebui.com/features/plugin/tools/openapi-servers/>
- [ ] <https://modelcontextprotocol.io/specification/2025-03-26/server/tools>

### Эпик E. Подключение Open WebUI

**Зачем нужен:** именно он превращает уже подготовленный backend в реальную пользовательскую оболочку, но только после того, как backend-границы будут готовы.

- [ ] Настроить сервер инструментов OpenAPI
- [ ] Проверить путь загрузки файлов
- [ ] Проверить явный выбор инструмента
- [ ] Проверить поведение базы знаний и локальных документов

**Как реализовывать:**
- [ ] Начинать с OpenAPI server, а не с `MCP`, потому что это стабильнее для продакшн-развёртывания и операционного контроля.
- [ ] Учитывать ограничение OpenAPI-пути: длинные задания не надо пытаться "стримить" как внутренний прогресс; для них нужен backend `job_id` и последующий polling.
- [ ] Проверять 3 базовых пути отдельно:
  - [ ] чат без файлов
  - [ ] чат с загруженными локальными документами
  - [ ] чат с базой знаний (`KB`)
- [ ] Инструмент должен выбираться явно; автоматический выбор допустим только как дополнительный режим на более поздней фазе.

**Источники:**
- [ ] <https://docs.openwebui.com/features/plugin/tools/openapi-servers/>
- [ ] <https://docs.openwebui.com/features/plugin/tools/>
- [ ] <https://docs.openwebui.com/troubleshooting/rag/>

### Эпик F. Зависимости и упаковка

**Зачем нужен:** ветка `origin/copilot/update-package-scripts-and-dependencies` полезна как источник идей, но нам нужен отдельный, аккуратный трек под `conda/conda-forge`, offline bundle и согласованные зависимости.

- [ ] Провести review базовой линии `conda/conda-forge`
- [ ] Проверить разделение окружений runtime/dev
- [ ] Подготовить план по `apt + wheelhouse + miniconda`
- [ ] Выборочно взять полезное из `origin/copilot/update-package-scripts-and-dependencies`

**Как реализовывать:**
- [ ] Не merge-ить ветку с зависимостями как есть; сначала собрать канонический список runtime и dev-зависимостей для `conda/conda-forge`.
- [ ] Затем отдельно фиксировать, какие Python-пакеты должны идти в wheelhouse, а какие лучше держать на стороне `conda`.
- [ ] После выравнивания Python-зависимостей пересобирать offline bundle уже на согласованной базе, а не параллельно менять код и упаковку.
- [ ] Отдельно проверять совместимость `CUDA`, `NVIDIA`-драйверов, `Miniconda`, `apt`-пакетов и wheelhouse.

**Источники:**
- [ ] `origin/copilot/update-package-scripts-and-dependencies`
- [ ] [MOTIVATION.md](/home/seral/HDD/proj/agent-navigator-pro/MOTIVATION.md)

### Эпик G. Модели и установщики

**Зачем нужен:** обновление скриптов скачивания моделей нельзя делать как молчаливую смену baseline. Нужен аккуратный путь, в котором `Qwen2.5` остаётся основой, а `Qwen3` добавляется как опциональный вариант.

- [ ] Подготовить план установщика с двумя источниками моделей
- [ ] Сохранить `Qwen2.5` как базовый вариант
- [ ] Добавить `Qwen3` как опциональный путь
- [ ] Не менять baseline без отдельной оценки качества (`eval`)

**Как реализовывать:**
- [ ] Скрипты установки должны поддерживать выбор модели через явный параметр, а не молча менять базовую модель.
- [ ] `Qwen2.5` нужно оставить значением по умолчанию, пока не проведены оценка качества и повторные замеры производительности.
- [ ] `Qwen3` добавлять как альтернативный путь установки, чтобы можно было сравнивать без изменения базовой логики.
- [ ] Источники для эмбеддингов и `LaBSE`-вариантов менять только через отдельный замер качества и скорости.

**Источники:**
- [ ] `origin/copilot/fix-model-download-scripts`
- [ ] [MOTIVATION.md](/home/seral/HDD/proj/agent-navigator-pro/MOTIVATION.md)

---

## 13. Критерии готовности миграции

- [ ] Пользователь может в Open WebUI загрузить документ и явно выбрать tool.
- [ ] Вопрос по документу работает как доказательный `RAG` с цитатами и подтверждениями.
- [ ] Анализ одного документа работает как `fast/deep`.
- [ ] Сравнение двух документов работает как `fast/deep`.
- [ ] `equipment_analysis` остаётся отдельным инструментом.
- [ ] Поиск по базе знаний работает через `Qdrant`.
- [ ] Наложение сеанса (`session overlay`) не ломается.
- [ ] После рестарта восстанавливаются привязки чата и документов.
- [ ] Backend не зависит от `Chainlit` как от единственного владельца сеанса (`session owner`).
- [ ] Путь через `MCP` существует, но `OpenAPI` остаётся каноническим.
- [ ] Operator UI умеет управлять ingestion в `Qdrant`.

---

## 14. Риски и запреты

- [ ] Не пытаться одновременно менять:
  - [ ] интерфейс
  - [ ] поиск
  - [ ] файловую модель
  - [ ] backend-разборщик документов
  - [ ] стек зависимостей
  в одном diff.
- [ ] Не внедрять `Qdrant` до protocol boundary.
- [ ] Не строить производственный путь вокруг настроек и профилей `Chainlit`.
- [ ] Не заменять рабочее поведение compare на новый UX без регрессионных тестов.
- [ ] Не переключать базовую загрузку моделей (`baseline model downloads`) автоматически на `Qwen3`.
- [ ] Не переносить Open WebUI orchestration внутрь UI.

---

## 15. Внешние источники и практические выводы

## 15.1 Open WebUI

- [ ] Open WebUI OpenAPI tool servers docs:
  - [ ] <https://docs.openwebui.com/features/plugin/tools/openapi-servers/>
  - [ ] Вывод: серверы инструментов OpenAPI встраиваются как явный производственно пригодный слой инструментов; это и должен быть наш первый путь интеграции.
- [ ] Open WebUI MCP docs:
  - [ ] <https://docs.openwebui.com/openapi-servers/mcp/>
  - [ ] Вывод: `MCP` поддерживается, но с операционной точки зрения удобнее держать его как дополнительный слой поверх уже стабильных backend-контрактов.
- [ ] Open WebUI tools docs:
  - [ ] <https://docs.openwebui.com/features/plugin/tools/>
  - [ ] Вывод: модель пользовательских и глобальных серверов инструментов подходит для разделения продуктового чата и чисто операторских поверхностей.
- [ ] Open WebUI files / RAG docs:
  - [ ] <https://docs.openwebui.com/features/rag/>
  - [ ] Вывод: встроенная модель знаний и файлов полезна как UX-ориентир, но backend-семантику документов и модель привязок нам всё равно нужно держать у себя.
- [ ] Open WebUI RAG troubleshooting:
  - [ ] <https://docs.openwebui.com/troubleshooting/rag/>
  - [ ] Вывод: неявное поведение `RAG` в интерфейсе нестабильно как источник истины; для пути `manual-first/tool-first` поведение базы знаний и инструментов надо проектировать сознательно.

## 15.2 Qdrant

- [ ] Qdrant hybrid/reranking docs:
  - [ ] <https://qdrant.tech/documentation/advanced-tutorials/reranking-hybrid-search/>
  - [ ] Вывод: `Qdrant` хорошо поддерживает плотный и разрежённый поиск, а также позднее взаимодействие (`late interaction`), но это лучше вводить поэтапно, начиная с плотного пути.
- [ ] Qdrant multitenancy docs:
  - [ ] <https://qdrant.tech/documentation/guides/multitenancy/>
  - [ ] Вывод: разбиение через полезную нагрузку (`payload-based partitioning`) и стратегия фильтрации важнее, чем взрывной рост числа коллекций.

## 15.3 MCP

- [ ] MCP prompts docs:
  - [ ] <https://modelcontextprotocol.io/legacy/concepts/prompts>
  - [ ] Вывод: `prompts` в `MCP` предназначены для повторно используемых пользовательских сценариев и естественно ложатся на UX с командами.
- [ ] MCP tools specification:
  - [ ] <https://modelcontextprotocol.io/specification/2025-03-26/server/tools>
  - [ ] Вывод: инструменты должны оставаться атомарными единицами возможностей (`capability units`) с явной схемой входа, а не скрытым маршрутизатором.

## 15.4 Qdrant: фильтрация и многоарендность

- [ ] Qdrant filtering docs:
  - [ ] <https://qdrant.tech/documentation/concepts/filtering/>
  - [ ] Вывод: payload filters достаточны для tenant/thread/document scoping и должны стать основой session/KB isolation.
- [ ] Qdrant multitenancy guide:
  - [ ] <https://qdrant.tech/documentation/guides/multitenancy/>
  - [ ] Вывод: payload-based partitioning предпочтительнее collection explosion.
- [ ] Qdrant hybrid / reranking tutorial:
  - [ ] <https://qdrant.tech/documentation/advanced-tutorials/reranking-hybrid-search/>
  - [ ] Вывод: hybrid и reranking лучше включать после stable dense-first rollout.

## 15.5 Docling

- [ ] Docling docs:
  - [ ] <https://docling-project.github.io/docling/>
  - [ ] Вывод: Docling подходит как следующий parser backend, но его надо встраивать через adapter layer, а не прямой заменой caller-кода.
- [ ] Docling GitHub:
  - [ ] <https://github.com/docling-project/docling>
  - [ ] Вывод: полезен как reference по формату conversion pipeline и deployment-модели.

## 15.6 Референсная архитектура

- [ ] Local reference docs in `clone_claude`:
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/docs/introduction/architecture-overview.mdx`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/docs/conversation/the-loop.mdx`
  - [ ] `/home/seral/HDD/clone_claude/claude-code-working/docs/extensibility/skills.mdx`
  - [ ] Вывод: правильный путь миграции для нас — слоистая архитектура, явный цикл выполнения, инструменты как отдельные единицы возможностей, явные границы разрешений и разделение контекста, памяти и состояния выполнения.

---

## 16. Ближайшие действия

- [ ] Зафиксировать этот документ как главный опорный документ миграции.
- [ ] Начать реализацию не с оболочки Open WebUI, а с:
  - [ ] `compare-workflow-llm-discrepancy`
  - [ ] `KnowledgeBaseStoreProtocol`
- [ ] После этого переходить к:
  - [ ] `QdrantKnowledgeBaseStore`
  - [ ] явным endpoint'ам инструментов
  - [ ] загрузке файлов и привязке документов
- [ ] Затем уже подключать Open WebUI как основной UI.
- [ ] Отдельно завести задачу на operator-панель для загрузки в `Qdrant`, индексации и управления индексом.

---

## 17. Короткая версия решений

- [ ] `Open WebUI` — да, это primary direction.
- [ ] Профили `Chainlit` — нет, не развиваем.
- [ ] `OpenAPI` — основной путь.
- [ ] `MCP` — дополнительный слой.
- [ ] `Qdrant` — целевое векторное хранилище.
- [ ] `KnowledgeBaseStoreProtocol` — обязательный мост до Qdrant.
- [ ] `compare-workflow-llm-discrepancy` — брать первым.
- [ ] `redesign-compare-bounded-latency` — брать частично, без Chainlit UX.
- [ ] `fix-model-download-scripts` — брать частично, без насильной смены baseline.
- [ ] `update-package-scripts-and-dependencies` — не merge, а отдельный conda-first packaging track.
