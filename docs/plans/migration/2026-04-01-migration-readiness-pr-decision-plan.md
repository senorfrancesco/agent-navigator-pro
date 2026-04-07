# Migration Readiness PR Decision Plan

**Дата:** 2026-04-01  
**Источник анализа:** `origin/copilot/check-pull-requests-for-v3-0` / исторический `PR #23`  
**Связанные документы:**  
- [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)  
- [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md)  
- [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md)  
- [MIGRATION_OVERIEW_WITH_LEAKS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_OVERIEW_WITH_LEAKS.md)

## 1. Назначение документа

- [ ] Зафиксировать, какие результаты из исторической ветки `origin/copilot/check-pull-requests-for-v3-0` имеют смысл для текущего `dev`.
- [ ] Разделить содержимое ветки на три класса:
  - [ ] брать в отдельные follow-up PR;
  - [ ] оставить только как reference/audit;
  - [ ] не переносить.
- [ ] Не допустить механического merge старой ветки в текущий `dev`.
- [ ] Привязать решение к текущей migration-стратегии, а не к старой базе `v3.0`.

**Контекст:** ветка `origin/copilot/check-pull-requests-for-v3-0` содержит одновременно:
- docs-аудит миграции;
- точечные OpenAI-compat фиксы;
- протоколизацию knowledge base storage layer;
- исторический triage старых PR против `v3.0`.

Это полезно как инженерный аудит, но уже не является готовым merge-кандидатом для текущей branch-модели.

---

## 2. Исходный состав ветки

### 2.1 Документы

- [ ] `docs/migration_readiness_report.md`
- [ ] `docs/pr_analysis_v3.0.md`

### 2.2 Кодовые изменения

- [ ] `backend/orchestrator/agent_api.py`
  - [ ] OpenAI-compatible SSE chunk contract
  - [ ] `POST /v1/files` upload endpoint
- [ ] `backend/orchestrator/knowledge_base_store.py`
  - [ ] `KnowledgeBaseStoreProtocol`
- [ ] `backend/orchestrator/knowledge_base_ingestion.py`
  - [ ] typing migration to protocol
- [ ] `backend/orchestrator/knowledge_base_retrieval.py`
  - [ ] typing migration to protocol

### 2.3 Общее решение по ветке

- [ ] Не рассматривать ветку как единый PR для merge.
- [ ] Использовать её как:
  - [ ] migration audit;
  - [ ] источник точных touch points;
  - [ ] источник кандидатов на отдельные targeted PR.

---

## 3. Общая оценка относительно текущего направления

### 3.1 Что в ветке качественно

- [ ] Хорошо декомпозированы migration areas:
  - [ ] OpenAI-compatible API
  - [ ] Knowledge Base abstraction
  - [ ] document parsing abstraction
  - [ ] Chainlit settings complexity
  - [ ] RAG/embedder dependency graph
- [ ] Есть инженерно полезный формат:
  - [ ] exact files
  - [ ] exact functions
  - [ ] line-level touch points
  - [ ] blocking vs non-blocking issues
  - [ ] adapter insertion points
- [ ] Это хорошо бьётся с текущим migration-подходом:
  - [ ] сначала картировать boundaries;
  - [ ] потом выделять контракты;
  - [ ] потом заменять реализации за адаптерами.

### 3.2 Где ветка уже не совпадает с текущей стратегией

- [ ] Ветка строилась относительно `v3.0`, а не текущего `dev`.
- [ ] Она смешивает:
  - [ ] audit/docs;
  - [ ] implementation fixes;
  - [ ] roadmap-level выводы.
- [ ] Это не соответствует текущему способу работы:
  - [ ] отдельный slice на одну инженерную цель;
  - [ ] отдельный PR на один архитектурный boundary;
  - [ ] отсутствие merge старых веток “целиком”.
- [ ] Ветка частично переоценивает приоритет `Open WebUI-first`, тогда как текущий проект живёт в модели:
  - [ ] `Chainlit` как основной текущий UI;
  - [ ] `Open WebUI` как migration/legacy-compatible направление;
  - [ ] manual-first tools migration как основной путь.

### 3.3 Нормативное решение

- [ ] Считать ветку **сильным audit/reference asset**, но **не готовым implementation PR**.
- [ ] Брать из неё только то, что усиливает:
  - [ ] tool boundaries;
  - [ ] storage abstraction;
  - [ ] migration readiness;
  - [ ] explicit compatibility layer.
- [ ] Не брать из неё то, что:
  - [ ] привязано к старой branch-модели;
  - [ ] не соответствует current `dev`;
  - [ ] расширяет legacy-контуры без прямой пользы для migration roadmap.

---

## 4. Что брать в отдельные follow-up PR

## 4.1 Candidate A: `KnowledgeBaseStoreProtocol`

- [ ] Создать отдельный PR на `dev` только под KB abstraction contract.

### Почему это брать

- [ ] Это напрямую бьётся с [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md):
  - [ ] отделение storage backend от retrieval/orchestration слоя;
  - [ ] подготовка к `SQLiteKnowledgeBaseStore -> Qdrant`;
  - [ ] уменьшение жёсткой привязки к SQLite-реализации.
- [ ] Это хорошо ложится на [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md):
  - [ ] explicit contract;
  - [ ] replaceable backend boundary.
- [ ] Это не привязано к `Chainlit` и не тянет UI-долг.

### Что именно брать

- [ ] `KnowledgeBaseStoreProtocol` из `knowledge_base_store.py`
- [ ] замена type annotations в:
  - [ ] `knowledge_base_ingestion.py`
  - [ ] `knowledge_base_retrieval.py`
- [ ] отдельные tests на protocol contract и текущую SQLite implementation compatibility

### Что не тащить вместе с этим PR

- [ ] OpenAI compat fixes
- [ ] docs про `v3.0` PR triage
- [ ] общий migration report целиком

### Условия принятия

- [ ] protocol не ломает текущий SQLite path;
- [ ] `get_knowledge_base_store()` остаётся канонической factory boundary;
- [ ] follow-up на Qdrant можно делать без переписывания retrieval call sites.

---

## 4.2 Candidate B: Open WebUI compatibility fixes в `agent_api.py`

- [ ] Не merge-ить автоматически.
- [ ] Завести отдельный decision PR только если сохраняется реальный roadmap на Open WebUI compatibility path.

### Что здесь потенциально полезно

- [ ] OpenAI-compatible SSE chunks с полями:
  - [ ] `id`
  - [ ] `object`
  - [ ] `created`
  - [ ] `model`
- [ ] `POST /v1/files` для Open WebUI file upload compatibility

### Почему это не брать автоматически

- [ ] Текущий основной UI не Open WebUI, а `Chainlit`.
- [ ] Эти фиксы полезны только если:
  - [ ] Open WebUI path реально будет поддерживаться не номинально, а operationally;
  - [ ] есть ближайший migration milestone на OpenAI-compatible uploads / chat path.
- [ ] Ветка смешивает compatibility fixes с audit-документами и устаревшим контекстом `v3.0`.

### Решение

- [ ] Оставить это как **conditional follow-up**.
- [ ] Возвращаться только при одном из условий:
  - [ ] запланирован новый Open WebUI integration slice;
  - [ ] нужен working `/v1/files` path для реального клиента;
  - [ ] нужен strict OpenAI-compatible streaming contract как product requirement.

### Условия принятия

- [ ] фиксы должны приходить отдельным PR;
- [ ] tests на `/v1/chat/completions`, SSE и `/v1/files` обязательны;
- [ ] нельзя смешивать это с KB abstraction или docs migration audit.

---

## 4.3 Candidate C: `migration_readiness_report.md` как архивный reference

- [ ] Не считать это активным roadmap-документом.
- [ ] При необходимости сохранить только как архивный audit snapshot.

### Почему это может быть полезно

- [ ] В документе есть точные touch points для:
  - [ ] OpenAI compat gaps
  - [ ] KB abstraction touch points
  - [ ] document parsing adapter points
  - [ ] Chainlit settings inventory
  - [ ] RAG/embedder dependency graph
- [ ] Это хороший materialized audit, который можно использовать в future planning.

### Почему это не должно становиться каноническим документом

- [ ] Он привязан к ветке `v3.0`
- [ ] Он не отражает уже сделанные изменения в текущем `dev`
- [ ] Он не согласован по статусам и приоритетам с текущими:
  - [ ] [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)
  - [ ] [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md)
  - [ ] [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md)

### Решение

- [ ] Если нужен reference в репо:
  - [ ] перенести как архивный документ с пометкой `historical audit against v3.0`
- [ ] Если reference не нужен:
  - [ ] ветку можно удалить без переноса этого файла

---

## 5. Что оставить только как reference, без внедрения

## 5.1 `docs/pr_analysis_v3.0.md`

- [ ] Не переносить в `dev`.

### Почему

- [ ] Документ жёстко привязан к старому сравнению с `v3.0`
- [ ] Его роль уже отыграна:
  - [ ] мы использовали его для triage старых PR;
  - [ ] решения по тем PR уже приняты;
  - [ ] часть полезных PR уже выборочно портирована;
  - [ ] stale PR закрыты и прокомментированы.

### Нормативное решение

- [ ] Оставить только в исторической ветке / истории PR
- [ ] В текущий `dev` не переносить

---

## 5.2 Open WebUI migration touch points как ideas-only reference

- [ ] Сохранить на уровне идеи:
  - [ ] `/v1/files`
  - [ ] SSE chunk compliance
  - [ ] `/v1/models` completeness
- [ ] Не считать их текущими обязательными задачами.

### Почему

- [ ] Они полезны для будущего Open WebUI path
- [ ] Но сейчас не являются самым важным engineering bottleneck
- [ ] Их нельзя приоритизировать выше текущих runtime/retrieval/compare задач без отдельного решения

---

## 6. Что не брать вообще

## 6.1 Не брать ветку как единый merge

- [ ] Не создавать PR `origin/copilot/check-pull-requests-for-v3-0 -> dev`
- [ ] Не merge-ить её целиком

### Почему

- [ ] Ветка смешивает:
  - [ ] исторический triage PR;
  - [ ] docs-аудит;
  - [ ] compatibility fixes;
  - [ ] KB abstraction;
  - [ ] старый branch context.
- [ ] Такой merge создаст шум и ухудшит trackability изменений.

---

## 6.2 Не переносить старый `v3.0`-центричный framing

- [ ] Не использовать `docs/pr_analysis_v3.0.md` как основание для текущих merge decisions
- [ ] Не использовать `migration_readiness_report.md` как текущий “канонический migration plan”

### Почему

- [ ] Канонический planning stack уже другой:
  - [ ] [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)
  - [ ] [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md)
  - [ ] [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md)
  - [ ] [MIGRATION_OVERIEW_WITH_LEAKS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_OVERIEW_WITH_LEAKS.md)

---

## 7. Решение по ветке как по артефакту

## 7.1 `origin/copilot/check-pull-requests-for-v3-0`

- [ ] Не считать рабочей веткой для merge.
- [ ] После фиксации всех нужных решений допустимо удалить remote-ветку.

### Перед удалением

- [ ] Убедиться, что:
  - [ ] решения по старым PR уже зафиксированы комментариями;
  - [ ] нужные candidate slices перенесены в backlog/plans;
  - [ ] при необходимости архивный `migration_readiness_report.md` сохранён отдельно.

---

## 7.2 `origin/codex/orchestration-control-plane-snapshot`

- [ ] Считать ветку полностью исторической.
- [ ] Удалять без дополнительных действий.

### Почему

- [ ] Она уже поглощена текущим `dev`
- [ ] Уникальной инженерной ценности относительно `dev` не осталось

---

## 8. План действий

### Phase 1: Decision capture

- [ ] Принять этот документ как decision-plan по исторической ветке `check-pull-requests-for-v3-0`
- [ ] Не открывать новый PR из этой ветки на `dev`

### Phase 2: Follow-up decomposition

- [ ] Завести отдельный plan/issue на `KnowledgeBaseStoreProtocol`
- [ ] Отдельно решить, нужен ли:
  - [ ] Open WebUI compat PR
  - [ ] архивный перенос `migration_readiness_report.md`

### Phase 3: Branch cleanup

- [ ] Удалить `origin/codex/orchestration-control-plane-snapshot`
- [ ] `origin/copilot/check-pull-requests-for-v3-0` удалить после того, как reference/value окончательно перенесён в backlog/docs или признан ненужным

---

## 9. Итоговое нормативное решение

- [ ] **Берём в отдельный follow-up PR:** `KnowledgeBaseStoreProtocol` и связанный typing boundary.
- [ ] **Берём условно, только при явном Open WebUI milestone:** `/v1/files` и SSE/OpenAI compat fixes.
- [ ] **Оставляем только как reference:** `migration_readiness_report.md`.
- [ ] **Не переносим:** `docs/pr_analysis_v3.0.md` и всю ветку целиком как единый merge.
- [ ] **Удаляем как полностью историческую:** `origin/codex/orchestration-control-plane-snapshot`.
- [ ] **Удаляем после decision capture:** `origin/copilot/check-pull-requests-for-v3-0`.

---

## 10. Критерии успешности

- [ ] Нет попытки merge-ить старую audit-ветку целиком.
- [ ] Полезная инженерная ценность отделена от исторического шума.
- [ ] Migration roadmap продолжает жить в current docs stack, а не в `v3.0`-центричных артефактах.
- [ ] Для каждой части ветки есть явный статус:
  - [ ] follow-up PR
  - [ ] reference only
  - [ ] do not port
