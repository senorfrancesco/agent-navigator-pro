- **Статус:** Финальная синхронизация с планом выполнена.

### 18. Синхронизация с Report_Update_Plan.md [2026-01-08]
- **Анализ:** Сравнение текущих текстов с глобальным планом выявило отсутствие ссылок и некоторых деталей оптимизации.
- **Внедрение в Отчет №1:**
    - Добавлено описание флага `-cmoe` (CPU offloading for Experts) — критичная фича для запуска MoE моделей на RTX 3060.
- **Внедрение в Отчет №2:**
    - Добавлен раздел "Список использованных источников" (References) с ссылками на Habr и GitHub, указанными в плане.
- **Итог:** Отчеты полностью соответствуют заявленной структуре и содержанию.
### Исследование Model Zoo [2026-01-08]
- Выполнен поиск по темам: SLM vs LLM vs Micro-LM, Edge AI models 2025.
- Создан файл отчета: @for_cli/research_model_zoo_2025.md
- Обновлен основной отчет (@for_cli/main.tex): добавлен раздел с классификацией моделей и обоснованием выбора Qwen/Phi.

### Шаг: Исследование Зоопарка Моделей [2026-01-08]
- Выполнен веб-поиск по темам: SLM vs LLM, Micro-LM, Open Source Tools (Habr).
- Создан отчет: for_cli/research_model_zoo_2025.md
- Обновлен for_cli/main.tex: добавлен раздел классификации моделей и обоснование выбора Qwen 2.5 14B.

### Обновление Highload отчета [2026-01-08]
- В for_cli/report_highload.tex добавлен подраздел 'Векторные представления' (BGE-M3, e5-mistral).
- Добавлено сравнение с Edge/SLM подходом для связности двух отчетов.

### Полное обновление Отчета №1 (main.tex) [2026-01-08]
- **Действие:** Файл `main.tex` полностью перезаписан. Объединена структура из `old_main.tex` с новыми данными из `User_Report_Comments.txt`.
- **Ключевые изменения:**
  - Восстановлен объем (700+ строк) и глубина теоретического описания.
  - Добавлен честный статус микросервисной архитектуры ("Нестабильна").
  - Подробно расписаны проблемы `llama-cpp-python` и преимущество `llama-server`.
  - Добавлена классификация моделей (Micro/Small/Large) и раздел про зоопарк Hugging Face.
  - Добавлен сравнительный анализ UI (Ollama, LM Studio, Open WebUI).
  - Интегрирована информация о последовательной обработке (Serial Processing) для слабых GPU.
- **Статус:** Отчет №1 готов.

### Верификация Отчета №2 (report_highload.tex) [2026-01-08]
- **Анализ:** Файл проверен на соответствие требованиям по теме Highload/Cluster.
- **Содержание:**
  - Инфраструктура: H100, Bare Metal vs K8s.
  - Стек: vLLM, SGLang.
  - Модели: Qwen 72B, DeepSeek, Whisper, Flux.
  - Агенты: LangGraph, MCP, Supervisor pattern.
  - Кибербезопасность: PoC Database, Fine-tuning.
  - Бизнес-кейс: Document Factory.

- **Статус:** Отчет №2 готов и полностью соответствует требованиям.
\n### [2026-01-09 01:36:03] Update Agent API v2.2.0 & UMS v1.3.2\n**Changes:**\n- **Agent API:**\n  - Implemented 'Soft ReAct Router' for smart workflow selection (Compare/Equipment/Analyze).\n  - Added 'Map-Reduce' for deep analysis of large documents (>8k chars).\n  - Added 'Simple Extraction' for estimate analysis (single file).\n  - Implemented 'Dynamic Model Zoo' (scans 'models/gguf' for .gguf files).\n  - Added 'Direct Model Access' mode (bypasses agent logic for raw models).\n  - Fixed multimodal request parsing (text + image).\n- **UMS (Unified Model Server):**\n  - Implemented 'Selective Swapping': 'st' models (LaBSE) are kept in memory, only 'gguf' models are swapped.\n  - Implemented 'Dynamic Model Loading': can run any .gguf file from disk without hardcoded config.\n  - Increased timeouts (start: 120s, infer: 300s) to handle large docs.\n- **Fixes:**\n  - Fixed '500 Internal Server Error' during high load (model thrashing).\n  - Fixed 'Manual analysis required' by improving JSON extraction.\n
### Запуск системы [2026-02-19]
- **Действие:** Запуск backend/run_all.sh по запросу пользователя.
- **Статус:** Выполняется...

### 19. Стабилизация v3.0 и рефакторинг техдолга [2026-03-03]
- **Выполнено:** Рефакторинг TD-1-6, 10-13. Переход на пакетную структуру, общие утилиты и семантический роутинг.
- **Тесты:** Playwright подтвердил чистоту отчетов (8/8 позиций) и правильность метаданных (12 стр).
- **План:** Зафиксированы шаги по устранению падений LLM в STABILIZATION_PLAN.md.
### [2026-03-03 16:50] Fix & Verify Document Analysis Workflow\n**Changes:**\n- Fixed 'NameError: name asyncio is not defined' in 'summarize_node'.\n- Verified Map-Reduce performance on 12-page PDF (18k chars).\n- **Result:** High-quality 10KB report generated with deep technical specs extraction.\n- **Observation:** Map-Reduce is essential for detail preservation. No simplification needed, only optimization of Reduce prompt and UI table limits.
