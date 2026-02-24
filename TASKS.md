# TASKS - Agent Navigator Pro

## v3.0 — Интеграция Open WebUI + RAG + Роутинг

### В работе

- [ ] **T3.1 — Фикс роутинга follow-up запросов**
  Файлы, уже загруженные в сессию, не должны считаться новыми для роутинга.
  Сейчас Open WebUI пересылает все attachments на каждый запрос → роутер повторно запускает workflow.
  Решение: сравнивать attachments с `session["documents"]`, считать `file_count` только для новых файлов.

- [ ] **T3.2 — Передача истории диалога (messages[]) в промпт LLM**
  Open WebUI отправляет полный массив `messages[]`, но agent_api берёт только последний `user` message.
  Нужно формировать multi-turn промпт из всех messages → LLM понимает контекст ("приведи любую" после обсуждения статей).

- [ ] **T3.3 — Подключить LaBSE как движок эмбеддингов Open WebUI**
  Добавить OpenAI-compatible `/v1/embeddings` эндпоинт в UMS/agent_api.
  Настроить docker-compose: `RAG_EMBEDDING_ENGINE=openai`, `RAG_OPENAI_API_BASE_URL=http://host.docker.internal:8090`.
  LaBSE лучше для русского текста, чем дефолтный MiniLM.

- [ ] **T3.4 — Убрать дублирование RAG-логики из agent_api**
  Удалить самодельный context stuffing (`_should_include_doc_context`, `_build_doc_context`, `DOC_CONTEXT_KEYWORDS`).
  Open WebUI RAG сам подставляет релевантные чанки в промпт до отправки в agent_api.
  В agent_api оставить только роутинг на workflows (compare/equipment).

- [ ] **T3.5 — Верификация: тестирование полной интеграции Open WebUI + LaBSE RAG**
  Проверить: загрузка файлов → RAG с LaBSE → цитирование → follow-up вопросы → workflows.

### Backlog

- [ ] Vision-анализ изображений (Qwen-VL интеграция)
- [ ] Мониторинг и алерты для микросервисов
- [ ] Пул портов для динамических моделей в UMS

## v2.3.1 — Багфиксы (2026-02-25)

### Выполнено

- [x] **Фикс стриминга UMS** — httpx клиент закрывался до начала генерации (`async with` scope bug) (2026-02-25)
- [x] **Скрипт run_all.sh** — убран auto-attach к tmux, работает в фоне (2026-02-25)
- [x] **Диагностика CUDA** — `nvidia_uvm` модуль зависал после sleep/hibernate, `rmmod + modprobe` решает (2026-02-25)

## v2.3.0 — Refactoring (2026-02-24)

### Выполнено

- [x] Определение порядка документов (старый/новый) — 4 уровня: query → filename → content date → mtime
- [x] Исправить дублирование отчётов — dedup 30s, хеш файлов, report-level dedup 60s + quality compare
- [x] Real-time стриминг ответов LLM — `async_infer_stream()` + прямой чат
- [x] Сессионный менеджер документов — on-demand контекст, intent keywords
- [x] Обновление CLAUDE.md — секция Recent Refactoring v2.3.0
- [x] Async LLM inference, Hungarian matching, batch analysis, dedup middleware
- [x] Создание скриптов: `run_all.sh`, `stop_all.sh`, `restart_all.sh`, `setup_ubuntu.sh`, `start_system_test.sh`
- [x] Фикс dedup key lifecycle — `try/finally` в async generator
- [x] Report quality dedup — замена отчёта если новый содержит больше изменений
