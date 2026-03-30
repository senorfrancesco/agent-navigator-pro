# Анализ пулл-реквестов относительно ветки v3.0

> Дата анализа: 2026-03-30  
> База сравнения: `v3.0` (SHA `1bffd5fc`)  
> Всего открытых PR → `v3.0`: **20**

---

## Методология

Для каждой ветки проверено:
1. **Количество уникальных коммитов** (ahead/behind v3.0 через `git rev-list`)
2. **Конфликты слияния** (dry-run `git merge-tree`)
3. **Наличие файлов** в v3.0, которые PR вводит впервые
4. **Соответствие функций** в v3.0 с тем, что PR реализует
5. **Статус задач** в `TASKS.md` v3.0 (закрытые TD/B-пункты)

Все 20 веток отстают от `v3.0` на **118 коммитов** — ветка значительно эволюционировала
после ответвления PR-веток.

---

## ✅ Актуальные ветки (требуют rebase → merge)

### PR #16 — `codex/add-tests-for-chat-resume-context`
**Заголовок:** `test(chainlit): cover chat resume context restoration`  
**Конфликты:** ✅ НЕТ — **чистое слияние без конфликтов**  
**Что добавляет:**
- `backend/tests/test_chat_resume_context.py` — новый файл (отсутствует в v3.0)
- `backend/tests/test_chainlit_streaming.py` — расширение класса `TestResumeStreamingIntegration`

Файлы не существуют в v3.0 и не пересекаются с текущим кодом → **готов к мержу напрямую**.

---

### PR #10 — `codex/add-document-type-keywords-section`
**Заголовок:** `feat(agent): load document type keywords from YAML config`  
**Конфликты:** ⚠️ 3 маркера (в `TASKS.md`)  
**Что добавляет:**
- Секция `document_type_keywords` в `backend/orchestrator/data/parsers_config.yaml`:
  ```yaml
  document_type_keywords:
    tz:    [...]
    smeta: [...]
    kp:    [...]
    legal: [...]
  ```
- Соответствующая загрузка в `workflows/document_analysis.py`
- Тест в `tests/test_document_analysis.py`

В v3.0 секция `document_type_keywords` в `parsers_config.yaml` **отсутствует** — ключевой тип-классификатор не вынесен в конфиг. Конфликты только в `TASKS.md` → **нужен простой rebase**.

---

### PR #15 — `codex/implement-session-state-object-for-conversation-summary`
**Заголовок:** `feat(agent): инкрементальная summary-память диалога в Chainlit`  
**Конфликты:** ⚠️ 5 маркеров (в `chainlit_app.py`)  
**Что добавляет:**
- Новые функции в `chainlit_app.py`:
  - `_utc_now_iso()`
  - `_get_conversation_summary()`
  - `_summary_to_text()`
  - `_extract_critical_entities()`
  - `async _update_conversation_summary()`
- Сессионный ключ `conversation_summary` с полями `facts`, `decisions`, `open_questions`, `critical_entities`, `updated_at`

В v3.0 **нет функций summary-памяти** в `chainlit_app.py` (0 совпадений по `summary_mem`).
Требуется rebase из-за того, что v3.0 сильно переработал промптовую логику.

---

### PR #14 — `codex/implement-summary-thresholds-and-metrics`
**Заголовок:** `feat(agent): пороги пересуммаризации и метрики chat summary memory`  
**Конфликты:** ⚠️ 5 маркеров  
**Что добавляет:**
- `_generate_summary_from_messages()`, `_ensure_summary_consistency()`, `_prepare_messages_with_summary_memory()` в `agent_api.py`
- Флаг `ENABLE_CHAT_SUMMARY_MEMORY` для постепенного rollout
- Инъекция summary в ChatML через расширение `_build_multiturn_prompt(..., summary_memory=...)`
- Пороги: `SUMMARY_TRIGGER_TOKENS`, `SUMMARY_MAX_CHARS`

Функции summary-памяти в `agent_api.py` в v3.0 **не реализованы**. Нужен rebase с учётом
переработанного `run_workflow_stream` в v3.0.

---

### PR #13 — `codex/add-tests-for-chat-summary-scenarios`
**Заголовок:** `feat(agent): добавить summary-memory для длинного чата и покрыть инварианты тестами`  
**Конфликты:** ⚠️ 2 маркера (только в `TASKS.md`)  
**Что добавляет:**
- `backend/tests/test_chat_summary_memory.py` — новый тест-файл (отсутствует в v3.0)
- Константы `SUMMARY_TRIGGER_MESSAGES`, `SUMMARY_KEEP_RECENT`, `MAX_SUMMARY_CHARS`
- Функции `_extract_critical_facts()`, `_generate_history_summary()`, `_refresh_history_summary()`

Тестовый файл и вся summary-логика **отсутствуют в v3.0**. Реализация в `chainlit_app.py` требует rebase, тест-файл — чистый.

---

## ❌ Неактуальные ветки (устарели / superseded)

### PR #22 — `codex/enhance-session-metadata-file-handling`
**Заголовок:** `fix(chainlit): handle missing resume files and RAG degradation`  
**Конфликты:** 13 маркеров  
**Причина устаревания:** В v3.0 реализованы `_ensure_session_state()`, `_build_backend_resume_snapshot()`,
`_restore_backend_resume_snapshot()` — надёжное восстановление сессии через backend snapshot.
Подход PR (file-based resume guard) вытеснен более зрелой архитектурой.

---

### PR #21 — `codex/create-ux-section-in-tasks.md`
**Заголовок:** `docs(tasks): add UX — Chat Navigation & History tasks and session log entry`  
**Конфликты:** 2 маркера  
**Причина устаревания:** `TASKS.md` в v3.0 полностью переработан — добавлены секции
`Release/v1.0 Transition`, обширный `Session Log`, backlog B3.52/B3.53/B3.54.
UX-секция из PR поглощена более актуальными задачами.

---

### PR #20 — `codex/add-custom-resume-step-in-chainlit_app.py`
**Заголовок:** `feat(chainlit): add resume document-context restore flow and smoke test`  
**Конфликты:** 7 маркеров  
**Причина устаревания:** `on_chat_resume` в v3.0 реализует полноценный backend-snapshot:
`_restore_backend_resume_snapshot()` + Chainlit data layer интеграция. PR's smoke test
(`test_chainlit_resume_restore_smoke.py`) не существует в v3.0, но сам flow заменён.

---

### PR #19 — `codex/fix-target-ux-scenario-for-chainlit`
**Заголовок:** `feat(agent): унифицировать UX восстановления чатов в Chainlit`  
**Конфликты:** 5 маркеров  
**Причина устаревания:** Восстановление UX в v3.0 реализовано через `_build_resume_markdown()`
и `restored_via` parameter. Файлы `chainlit_ru-RU.md`, `ru-RU.json` существуют в v3.0
в актуальном состоянии.

---

### PR #18 — `codex/document-database-path-and-defaults`
**Заголовок:** `fix(chainlit): unify DB path and add Chainlit history troubleshooting guide`  
**Конфликты:** 2 маркера  
**Причина устаревания:** `README.md`, `backend/.env.example`, `docker-compose.yaml` в v3.0
уже обновлены под Chainlit 2.9.6 с SQLite WAL mode и unified DB path. Гайд по troubleshooting
актуально включён в `TASKS.md` и INSTALLATION.md.

---

### PR #17 — `codex/add-context-validation-and-logging`
**Заголовок:** `fix(chainlit): guard restored context before doc intents`  
**Конфликты:** 16 маркеров  
**Причина устаревания:** Guard логика реализована иначе в v3.0 — через
`_apply_session_state_patch()` и `_build_backend_resume_snapshot()`. Архитектура
`chainlit_app.py` переработана настолько сильно, что патч неприменим.

---

### PR #16 — `codex/add-tests-for-chat-resume-context`
> ℹ️ Перенесён в раздел **Актуальные** — единственный PR без конфликтов.

---

### PR #12 — `codex/implement-session-recovery-contract-and-improvements`
**Заголовок:** `feat(agent): контракт восстановления сессии и сериализация состояния (resume v1)`  
**Конфликты:** 12 маркеров  
**Причина устаревания:** TD-4 (on_chat_resume не восстанавливает документы) **закрыт** в v3.0.
v3.0 реализует более зрелый контракт: `_build_backend_resume_snapshot()`,
`_restore_backend_resume_snapshot()`, `_extract_state_from_thread()`.
Функции из PR (#_get_route_mode, #_build_session_state_payload) вытеснены.

---

### PR #11 — `codex/update-tasks.md-with-partial-completion-notes`
**Заголовок:** `docs(tasks): зафиксировать частичное выполнение TD-2/TD-10 и добавить TD-15..TD-17`  
**Конфликты:** 2 маркера  
**Причина устаревания:** TD-2 и TD-10 в v3.0 **полностью закрыты** (не частично).
`TASKS.md` v3.0 содержит расширенный backlog, секцию Release/v1.0, Session Log —
документационные правки PR устарели.

---

### PR #9 — `codex/update-classifier-example-loading-and-validation`
**Заголовок:** `fix(agent): строгая валидация intent_examples.yaml и fail-fast загрузка`  
**Конфликты:** 5 маркеров  
**Причина устаревания:** TD-2 (INTENT_EXAMPLES hardcoded → YAML) **закрыт** в v3.0.
`rag/classifier.py` в v3.0 уже содержит `_load_examples()`, `select_classifier_result()`,
`_passes_embedder_thresholds()`. Добавленный PR классом `IntentExamplesConfigError` поглощён.

---

### PR #8 — `codex/implement-routing-config-and-loader`
**Заголовок:** `refactor(agent): externalize chainlit routing keywords to yaml`  
**Конфликты:** 7 маркеров  
**Причина устаревания:** TD-10 (Keyword routing как fallback) **закрыт** в v3.0.
v3.0 перешёл на `orchestrator.doc_question_heuristics` модуль вместо `routing_keywords.yaml`.
B3.31-cleanup удалил routing-дубликаты из `chainlit_app.py`. Подход через YAML-файл
`routing_keywords.yaml` вытеснен.

---

### PR #7 — `codex/create-preflight-configuration-script`
**Заголовок:** `Add preflight runtime profile, effective-context budgeting, and RAG runtime integration`  
**Конфликты:** 17 маркеров  
**Причина устаревания:** v3.0 имеет `scripts/runtime_preflight.py` с runtime plan/report
и `rag_mode_label` публикацией. `docs/preflight_runtime_profile.md` отсутствует в v3.0,
но концепция реализована иначе. 17 конфликтов — патч неприменим.

---

### PR #6 — `codex/fix-typos-and-refactor-start_system_test.sh`
**Заголовок:** `refactor(scripts): simplify start_system_test via launcher and fail-fast health checks`  
**Конфликты:** 2 маркера  
**Причина устаревания:** `scripts/start_system_test.sh` в v3.0 переработан в рамках
унификации launcher-архитектуры (`launcher.sh`). Тайпо-фиксы и refactoring из PR
поглощены общим рефактором скриптов.

---

### PR #5 — `codex/add-preflight-script-with-phases`
**Заголовок:** `feat(scripts): add preflight and .env.runtime support in run_all`  
**Конфликты:** 4 маркера  
**Причина устаревания:** v3.0 реализует:
- `scripts/runtime_preflight.py` (вместо `scripts/preflight.py`)
- `AGENT_NAVIGATOR_RUNTIME_ENV_FILE` / `.env.runtime` поддержку в `run_all.sh`
- `scripts/bootstrap_env.sh` для auto-heal `.env`  

Предложенный `scripts/preflight.py` вытеснен более зрелым `runtime_preflight.py`.

---

### PR #4 — `codex/add-effective-context-calculation`
**Заголовок:** `feat(model-manager): effective context budgeting in UMS and Chainlit RAG`  
**Конфликты:** 9 маркеров  
**Причина устаревания:** v3.0 реализует `resolve_runtime_budget()` в UMS (полноценный расчёт
`effective_context_tokens`, `context_budget_ratio`, `retrieved_context_tokens_budget`),
а `_get_runtime_budget_metadata()` в `chainlit_app.py` уже покрывает получение
effective context из UMS `/status`. Подход PR (`_get_rag_runtime_settings()`,
`_resolve_context_guardrail_cap()`) вытеснен.

---

### PR #3 — `codex/create-unified-bootstrap-script`
**Заголовок:** `refactor(scripts): unify launcher entrypoint by ui profile`  
**Конфликты:** 3 маркера  
**Причина устаревания:** v3.0 имеет унифицированный launcher-слой:
- `scripts/launcher.sh` — unified entrypoint по профилям (chainlit/openwebui/native)
- `scripts/bootstrap_env.sh` — auto-heal `.env` + `CHAINLIT_AUTH_SECRET`
- `scripts/runtime_preflight.py` — preflight валидация
- `scripts/run_all.sh`, `scripts/run_openwebui.sh` — актуальные враппиры

PR предлагал `scripts/bootstrap.sh` + `scripts/lib/common.sh`, которые заменены
другой архитектурой.

---

## Сводная таблица

| PR# | Ветка | Статус | Конфликты | Причина |
|-----|-------|--------|-----------|---------|
| #16 | add-tests-for-chat-resume-context | ✅ **Актуален** (clean merge) | 0 | Тест-файлы отсутствуют в v3.0 |
| #10 | add-document-type-keywords-section | ✅ **Актуален** (нужен rebase) | 3 | `document_type_keywords` отсутствует |
| #15 | implement-session-state-object-for-conversation-summary | ✅ **Актуален** (нужен rebase) | 5 | Summary-память не реализована в v3.0 |
| #14 | implement-summary-thresholds-and-metrics | ✅ **Актуален** (нужен rebase) | 5 | Chat summary в agent_api отсутствует |
| #13 | add-tests-for-chat-summary-scenarios | ✅ **Актуален** (нужен rebase) | 2 | test_chat_summary_memory.py отсутствует |
| #22 | enhance-session-metadata-file-handling | ❌ Устарел | 13 | TD-4 закрыт, resume иначе реализован |
| #21 | create-ux-section-in-tasks.md | ❌ Устарел | 2 | TASKS.md эволюционировал |
| #20 | add-custom-resume-step-in-chainlit_app.py | ❌ Устарел | 7 | Resume flow реализован иначе |
| #19 | fix-target-ux-scenario-for-chainlit | ❌ Устарел | 5 | UX resume реализован иначе |
| #18 | document-database-path-and-defaults | ❌ Устарел | 2 | README/.env уже обновлены |
| #17 | add-context-validation-and-logging | ❌ Устарел | 16 | Guard логика реализована иначе |
| #12 | implement-session-recovery-contract-and-improvements | ❌ Устарел | 12 | TD-4 закрыт |
| #11 | update-tasks.md-with-partial-completion-notes | ❌ Устарел | 2 | TD-2/TD-10 полностью закрыты |
| #9  | update-classifier-example-loading-and-validation | ❌ Устарел | 5 | TD-2 закрыт |
| #8  | implement-routing-config-and-loader | ❌ Устарел | 7 | TD-10 закрыт, routing_keywords.yaml вытеснен |
| #7  | create-preflight-configuration-script | ❌ Устарел | 17 | runtime_preflight.py уже в v3.0 |
| #6  | fix-typos-and-refactor-start_system_test.sh | ❌ Устарел | 2 | start_system_test.sh переработан |
| #5  | add-preflight-script-with-phases | ❌ Устарел | 4 | runtime_preflight.py уже в v3.0 |
| #4  | add-effective-context-calculation | ❌ Устарел | 9 | resolve_runtime_budget() уже в v3.0 |
| #3  | create-unified-bootstrap-script | ❌ Устарел | 3 | launcher.sh + bootstrap_env.sh уже в v3.0 |

---

## Рекомендуемые действия

### Немедленно
1. **Слить PR #16** (`add-tests-for-chat-resume-context`) — чистый merge без конфликтов, добавляет ценные тесты.

### После rebase
2. **PR #10** → rebase on v3.0, разрешить конфликты только в `TASKS.md` → merge  
3. **PR #13** → rebase on v3.0, разрешить конфликты в `TASKS.md`, объединить с PR #14/#15  
4. **PR #14** + **PR #15** → рассмотреть как единый feature: chat summary memory в `chainlit_app.py` + `agent_api.py` → rebase → merge

### Закрыть (stale)
Закрыть PR: **#3, #4, #5, #6, #7, #8, #9, #11, #12, #17, #18, #19, #20, #21, #22**  
Все они superseded эволюцией v3.0 за 118 коммитов.
