# Checklist: инструменты и `deep-job` для любой выбранной модели

Связанные документы:

- архитектурный план: [2026-04-16-openwebui-any-model-tools-architecture-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/2026-04-16-openwebui-any-model-tools-architecture-plan.md)
- `deep-job` план: [2026-04-16-openwebui-deep-job-clean-architecture-tz.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/2026-04-16-openwebui-deep-job-clean-architecture-tz.md)
- unified gateway plan: [2026-04-08-unified-model-catalog-gateway-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-unified-model-catalog-gateway-plan.md)

## 1. Нормативные решения

- [ ] Явно зафиксировать, что `llm-tools-platform` не является целевой пользовательской моделью.
- [ ] Явно зафиксировать, что инструменты и `deep-job` принадлежат общему инструментальному слою, а не отдельному `model id`.
- [ ] Не считать compatibility alias целевым архитектурным решением.
- [ ] Явно зафиксировать, что процессный UX `deep-job` не зависит от выбранной модели.
- [x] Явно зафиксировать, что пользовательский long-running UX показывает общий класс `Инструмент долгого выполнения` и отдельно имя конкретного инструмента.
- [x] Явно зафиксировать, что long-running панель опирается на нормализованный execution-контракт поверх `tool_jobs`, а не напрямую на `LangGraph` или `LangChain`.

## 2. Контракт выбора модели

- [ ] Описать канонический контракт передачи выбранной модели из `Open WebUI` в backend.
- [ ] Зафиксировать, где именно хранится выбранный `model id` для сообщения и чата.
- [ ] Убедиться, что panel-path `deep-job` не требует ручного переключения на отдельную специальную модель.

## 3. Отдельный inference-контракт

- [ ] Явно зафиксировать, что в каждый момент времени у продукта есть одна активная пользовательская генеративная модель.
- [ ] Явно зафиксировать, что baseline по умолчанию остаётся `Qwen2.5 Instruct 14B` / `qwen-14b-llm`, пока не появится новый подтверждённый default.
- [ ] Явно зафиксировать, что `DEFAULT_ACTIVE_MODEL_ID` является необязательным стартовым fallback, а не обязательной частью runtime-контракта.
- [ ] Зафиксировать порядок выбора активной генеративной модели:
  - [ ] сначала сохранённый runtime `active_model_id`;
  - [ ] затем `DEFAULT_ACTIVE_MODEL_ID`, если он задан;
  - [ ] затем явный запрос выбора модели пользователем.
- [ ] Явно зафиксировать, что текущая активная генеративная модель хранится в runtime state `UMS`, а не в `.env`.
- [ ] Явно зафиксировать, что служебные модели не смешиваются с пользовательским выбором chat-модели.
- [ ] Зафиксировать, что workflow и инструменты описывают требования к возможностям модели, а не требуют отдельный тяжёлый `LLM`-профиль на каждый сценарий.
- [ ] Зафиксировать, что каноническим идентификатором активной модели является `model_id`, а не путь к файлу.
- [ ] Зафиксировать разделение конфигурации:
  - [ ] `env` хранит machine-specific пути `MODEL_PATH_*` и служебные model bindings;
  - [ ] registry хранит `model_id`, `runtime_type`, `kind`, capabilities и defaults;
  - [ ] runtime state хранит текущий `active_model_id`.
- [ ] Зафиксировать layering model registry:
  - [ ] статический operator-owned registry в `backend/config/models.yaml`;
  - [ ] отдельный dynamic registry для административных регистраций в `UMS`;
  - [ ] при folder-browser потоке отдельно хранить scan-folders как служебное административное состояние, не смешивая их с model entries;
  - [ ] единый нормализованный каталог `static + dynamic` для UI и orchestration;
  - [ ] единые статусы готовности `ready` / `incomplete` / `ambiguous` / `unsupported`.
- [ ] Явно зафиксировать, что `json` в этом контуре нужен только как backend API/storage формат:
  - [ ] пользователь не импортирует `json`-файл модели;
  - [ ] `browse-folders` / `preview-model-path` / `register-model` обмениваются обычными `json`-payload;
  - [ ] persisted `json` допустим только для scan-folders и dynamic registry, а не как пользовательский формат выбора модели.
- [ ] Зафиксировать, что пользовательские локальные модели не кодируются набором `USER_MODEL_*` переменных в `.env`, а регистрируются в model registry.
- [ ] Зафиксировать backend-owned иерархию inference defaults по образцу `Unsloth`:
  - [ ] model-specific defaults;
  - [ ] family defaults;
  - [ ] global defaults.
- [ ] Зафиксировать backend-owned резолв параметров загрузки модели:
  - [ ] `4bit` / `quant`;
  - [ ] `trust_remote_code`;
  - [ ] `mmproj`;
  - [ ] `ctx` / другие runtime-параметры загрузки.
- [ ] Зафиксировать разделение параметров на два класса:
  - [ ] request-time параметры генерации, которые можно менять из UI без перезагрузки модели;
  - [ ] loader/runtime параметры, которые требуют `activate` / `reload`.
- [ ] Зафиксировать, что обычный выбор другой модели не требует отдельной кнопки `Reload`, потому что сам выбор уже инициирует её активацию в `UMS`.
- [ ] Зафиксировать, что `system prompt`, `temperature`, `top_p`, `top_k`, `min_p`, `max_tokens`, `stop`, `seed`, `reasoning_tags`, `function_calling` относятся к request-time параметрам.
- [ ] Зафиксировать, что `runtime_type`, `load_in_4bit`, `quant`, `trust_remote_code`, `mmproj`, `device`, `gpu split`, `num_ctx` и другие loader/runtime параметры не считаются обычными chat settings.
- [ ] Зафиксировать fallback policy:
  - [ ] если активная модель поддерживает нужные возможности, используется она;
  - [ ] если не поддерживает, backend либо возвращает явную несовместимость, либо использует отдельно объявленный fallback.

## 4. Что нужно сделать в `agent-navigator-pro`

- [ ] Ввести backend-owned `ModelConfig` / `ModelDescriptor` слой для пользовательских и локальных моделей.
- [ ] Добавить backend-сканирование пользовательских папок и безопасный allowlist директорий.
- [ ] Связать `selected model` из UI с `active_model_id` в `UMS`.
- [ ] Вынести текущее активное состояние модели из `.env` в runtime-хранилище `UMS`.
- [ ] Сделать `DEFAULT_ACTIVE_MODEL_ID` необязательным startup fallback, а не обязательным конфигом.
- [ ] Развести:
  - [ ] активную пользовательскую генеративную модель;
  - [ ] служебные модели `embedder` / `classifier` / `reranker` / `vision`.
- [ ] Оставить конфигурацию служебных моделей в backend на первом этапе через `env`.
- [ ] Оставить в `env` только machine-specific пути и служебные привязки:
  - [ ] `MODEL_PATH_*`;
  - [ ] `*_EMBEDDER_MODEL`;
  - [ ] `RERANKER_MODEL`;
  - [ ] `VISION_MODEL_ID`;
  - [ ] другие backend-owned service bindings.
- [x] Зафиксировать фактический baseline инвентарь в `backend/models` и опираться дальше на него, а не на абстрактный `raw`-каталог:
  - [x] `qwen-14b-llm` -> `backend/models/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf`;
  - [x] `qwen-vl-8b` -> `backend/models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf` + `mmproj`;
  - [x] `qwen3-embedding-0.6b` -> `backend/models/st/Qwen3-Embedding-0.6B`;
  - [x] `labse-embedding` -> `backend/models/st/LaBSE`.
- [x] Зафиксировать, что `qwen3-reranker-0.6b` уже есть в static registry, но пока не подтверждён локальным готовым runtime-артефактом в `backend/models`.
- [ ] Добавить в registry следующие реальные кандидаты из уже существующего инвентаря:
  - [ ] `qwen-32b-llm` на базе `backend/models/gguf/qwen2.5-32b-q4/qwen2.5-32b-instruct-q4-merged.gguf`;
  - [ ] `llama-3.1-8b`;
  - [ ] `mistral-7b`;
  - [ ] `phi-3-mini-q4` и определить судьбу `phi-3-mini-fp16` как отдельного entry или внутреннего runtime-варианта;
  - [ ] отдельно оценить `saiga-yandexgpt-8b` и `yandexgpt-lite-8b`;
  - [ ] отдельно решить служебные кандидаты `E5-legal`, `Rubert`, `bge-m3`, `multilingual-e5-large-instruct`, `xlm-roberta-large-xnli`.
- [ ] Перестать использовать `.env` как механизм динамического пользовательского переключения модели.
- [ ] В registry хранить отдельно:
  - [ ] `model_id`;
  - [ ] `display_name`;
  - [ ] `runtime_type`;
  - [ ] `kind`;
  - [ ] `source`;
  - [ ] capabilities;
  - [ ] `generation_defaults`.
- [ ] Добавить в model entry отдельный блок `load_defaults`.
- [ ] Сделать подготовленный backend-реестр моделей основным источником моделей для UI.
- [ ] Удалить `raw`-логику как отдельный системный и пользовательский режим выбора моделей.
- [ ] Оставить unsloth-подобный resolver только как backend-механику нормализации модели по явно указанному пути.
- [ ] Добавить явную backend-регистрацию модели по пути:
  - [ ] администратор указывает путь;
  - [ ] backend распознаёт `GGUF` / `LoRA` / vision / другие признаки;
  - [ ] backend строит нормализованную запись registry;
  - [ ] только после этого модель попадает в пользовательский список выбора.
- [ ] Развести служебные backend-сущности:
  - [ ] scan-folder как разрешённый или ранее добавленный корень для навигации и повторного сканирования;
  - [ ] model entry как нормализованную selectable-модель;
  - [ ] не превращать сам факт добавления папки в автоматическую публикацию новой модели.
- [ ] Нормализовать `gguf-vl` наборы файлов в одну модельную запись:
  - [ ] основной `GGUF` файл считается моделью;
  - [ ] `mmproj` считается связанным runtime-артефактом, а не отдельной моделью;
  - [ ] в registry сохраняется `mmproj_path`, а не отдельный selectable entry;
  - [ ] при неоднозначном или отсутствующем `mmproj` backend не публикует модель как готовую без явного решения администратора.
- [ ] Нормализовать split-`GGUF` наборы в одну модельную запись:
  - [ ] шард-файлы `00001-of-00004` и далее не считаются отдельными selectable entries;
  - [ ] в registry сохраняется шаблон набора и полный список `shards`;
  - [ ] модель не публикуется как `ready`, если набор shard-файлов неполный.
- [x] Зафиксировать для текущего инвентаря, что `qwen2.5-32b-q4` уже лежит и как `merged.gguf`, и как полный split-набор; первым пользовательским entry должен быть `merged.gguf`, а split-набор остаётся backend-нормализуемым источником.
- [ ] Нормализовать адаптеры `LoRA` / `QLoRA` в отдельные логические model entries:
  - [ ] хранить `adapter_path`, `adapter_format`, `base_model_ref`;
  - [ ] merged-экспорт адаптера считать обычной моделью, а не adapter-entry;
  - [ ] не публиковать каталог `PEFT` как готовую `llama.cpp`-модель без конвертации или другого runtime.
- [x] Зафиксировать, что в текущем `backend/models` нет `LoRA` / `QLoRA`-артефактов (`adapter_config.json`, `adapter_model.safetensors`, `*lora*.gguf`), поэтому adapter-runtime остаётся follow-up, а не текущим обязательным slice.
- [x] Зафиксировать, что `WizardLM-30B` на текущем узле представлен только `.lock` / `.incomplete`-артефактами и не должен появляться в каталоге готовых моделей.
- [ ] Зафиксировать отдельный backend-контракт для `llama.cpp`-адаптеров:
  - [ ] запуск через `--lora` / `--lora-scaled`;
  - [ ] для `llama-server` допускается backend-owned управление через `/lora-adapters`;
  - [ ] пользовательский UI не показывает сырые adapter-файлы как selectable models.
- [ ] Вынести capability-флаги модели в backend source-of-truth:
  - [ ] поддержка инструментов;
  - [ ] поддержка `vision`;
  - [ ] пригодность для структурированного вывода;
  - [ ] достаточный контекст;
  - [ ] пригодность для long-running planning / execution.
- [ ] Перенести из подхода `Unsloth` backend-owned логику:
  - [ ] резолв модели по локальному пути / `GGUF` / `LoRA` / vision;
  - [ ] inference defaults по модели / семейству / глобальному fallback;
  - [ ] автоподстановку параметров загрузки перед `activate` / `preload`.
- [ ] Проверять capability-флаги до запуска `LangGraph`, `LangChain` и explicit tools.
- [ ] Не позволять orchestration слою молча подменять пользовательскую модель другой тяжёлой `LLM`, кроме отдельно объявленного fallback-контракта.

Слои и файлы:

- [ ] `UMS`: [backend/services/model_manager/unified_model_server.py](/home/seral/HDD/proj/agent-navigator-pro/backend/services/model_manager/unified_model_server.py)
- [ ] модельная конфигурация: [backend/services/model_manager/models_config.py](/home/seral/HDD/proj/agent-navigator-pro/backend/services/model_manager/models_config.py)
- [ ] orchestration entrypoint: [backend/orchestrator/agent_api.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/agent_api.py)

## 5. Что нужно сделать во форке `Open WebUI`

- [x] Сделать `UMS`-интеграцию опциональным адаптером, а не обязательной зависимостью самостоятельного форка.
  - Done: добавлен флаг `ENABLE_AGENT_NAVIGATOR_RUNTIME_MODELS`; без него runtime-каталог пустой, runtime-меню скрыто, а стандартный `Open WebUI` не ходит в `UMS`.
- [ ] Показывать пользователю только пользовательские chat-модели, а не скрытую специальную tool-модель как обязательный путь.
- [ ] Сохранять выбранный пользователем `model id` и передавать его в backend без скрытой подмены.
- [ ] Показывать статус переключения модели и загрузки, но не владеть реальной lifecycle-логикой модели.
- [ ] Показать ограничения по возможностям, если backend сообщает, что активная модель не подходит для сценария.
- [ ] Не хранить пути к служебным моделям и не настраивать их в UI как пользовательский выбор.
- [ ] Не пытаться выбирать активную модель по сырому файловому пути; UI работает только с `model_id`.
- [ ] Не писать пользовательский runtime-switch обратно в `.env`.
- [ ] Использовать уже существующую панель параметров вместо отдельного нового экрана:
  - [ ] `System Prompt`;
  - [ ] `Advanced Params`;
  - [ ] текущие chat settings и model params.
- [ ] Собрать единый встроенный settings sidebar по образцу `Unsloth`, а не разносить выбор модели и параметры по несвязанным экранам.
- [ ] Подвязать панель параметров к backend defaults:
  - [ ] показывать backend-recommended defaults для выбранной модели;
  - [ ] позволять пользователю делать override для текущего чата;
  - [ ] не переносить в UI логику выбора `4bit`, `mmproj`, `trust_remote_code` и других loader-параметров.
- [ ] Добавить в settings sidebar полезные элементы UX по образцу `Unsloth`:
  - [ ] inline-редактор `System Prompt` и переход в расширенный редактор;
  - [ ] capability-индикаторы активной модели: инструменты, reasoning, vision, контекст;
  - [ ] статус активной модели и прогресс её загрузки;
  - [ ] отдельное действие остановки текущей загрузки модели;
  - [ ] гидрацию effective inference-параметров из backend runtime state после перезагрузки страницы;
  - [ ] гидрацию фактических runtime-лимитов активной модели: `context_length`, производный `max_tokens` и связанные ограничения;
  - [ ] явное подтверждаемое состояние для `trust_remote_code`, если backend его требует;
  - [ ] presets для request-time параметров, если они не ломают backend defaults и capability-policy.
- [ ] Развести в UI:
  - [ ] пользовательские параметры генерации `system`, `temperature`, `top_p`, `max_tokens` и подобные;
  - [ ] backend-owned параметры загрузки модели.
- [ ] Разрешить менять request-time параметры по ходу работы из UI без перезагрузки модели.
- [ ] При изменении loader/runtime параметров реализовать поведение по образцу `Unsloth`:
  - [ ] изменения помечаются как ожидающие применения;
  - [ ] интерфейс явно показывает, что для применения нужна перезагрузка модели;
  - [ ] применяется отдельное действие вида `Reload` / `Apply and Reload`, а не скрытая мгновенная подмена runtime.
- [ ] Не показывать отдельное действие `Reload` при обычном выборе другой модели; этот случай должен отрабатывать через стандартный механизм активации выбранной модели.
- [ ] Упростить выбор моделей в основном UX:
  - [ ] показывать пользователю только подготовленный список доступных моделей из backend-реестра;
  - [ ] вынести добавление модели по пути в отдельный администраторский поток;
  - [ ] после явной backend-регистрации добавленная модель появляется в общем списке выбора.
- [ ] Явно не использовать `Workspace -> Models` `.json`-импорт как канонический путь регистрации локальных runtime-моделей.
- [ ] Явно не использовать ручной ввод `Model ID` / `Model Name` в `Workspace -> Models` как замену backend-регистрации runtime-модели.
- [ ] Сделать административный поток выбора модели по образцу `Unsloth`:
  - [ ] backend `json`-браузер каталогов с allowlist разрешённых корней;
  - [ ] `browse-folders` для навигации по папкам;
  - [ ] `preview-model-path` для backend-нормализации выбранной папки;
  - [ ] `register-model` для явной регистрации нормализованной model entry;
  - [ ] после регистрации новый `model_id` появляется в обычном chat selector.
- [ ] Удалить из форка `Open WebUI` пользовательский `raw`-режим выбора моделей:
  - [ ] убрать отдельный `raw`-список;
  - [ ] убрать отдельный `raw`-selector;
  - [ ] убрать скрытую зависимость UI от `raw`-автодискавери.
- [ ] Не показывать `mmproj` как отдельную запись в пользовательском selector.
- [ ] Не показывать shard-файлы split-`GGUF` как отдельные записи в пользовательском selector.
- [ ] Не показывать `LoRA` / `QLoRA`-служебные файлы как отдельные selectable entries в пользовательском selector.

Слои и файлы:

- [ ] модельный store: [/home/seral/HDD/proj/open-webui/src/lib/stores/index.ts](/home/seral/HDD/proj/open-webui/src/lib/stores/index.ts)
- [ ] агрегатор моделей: [/home/seral/HDD/proj/open-webui/backend/open_webui/utils/models.py](/home/seral/HDD/proj/open-webui/backend/open_webui/utils/models.py)
- [ ] chat selector: [/home/seral/HDD/proj/open-webui/src/lib/components/chat/ModelSelector.svelte](/home/seral/HDD/proj/open-webui/src/lib/components/chat/ModelSelector.svelte)
- [ ] workspace models import contour, который не должен использоваться для runtime-регистрации: [/home/seral/HDD/proj/open-webui/src/lib/components/workspace/Models.svelte](/home/seral/HDD/proj/open-webui/src/lib/components/workspace/Models.svelte)
- [ ] chat settings: [/home/seral/HDD/proj/open-webui/src/lib/components/chat/Settings/General.svelte](/home/seral/HDD/proj/open-webui/src/lib/components/chat/Settings/General.svelte)
- [ ] advanced params: [/home/seral/HDD/proj/open-webui/src/lib/components/chat/Settings/Advanced/AdvancedParams.svelte](/home/seral/HDD/proj/open-webui/src/lib/components/chat/Settings/Advanced/AdvancedParams.svelte)
- [ ] model editor: [/home/seral/HDD/proj/open-webui/src/lib/components/workspace/Models/ModelEditor.svelte](/home/seral/HDD/proj/open-webui/src/lib/components/workspace/Models/ModelEditor.svelte)
- [ ] backend apply params path: [/home/seral/HDD/proj/open-webui/backend/open_webui/utils/middleware.py](/home/seral/HDD/proj/open-webui/backend/open_webui/utils/middleware.py)

## 6. Backend gateway

- [ ] Определить единый tool-aware gateway path для обычных моделей.
- [ ] Зафиксировать, как выбранная модель доходит до orchestration слоя.
- [ ] Убедиться, что выбранная модель используется как базовая модель генерации, а не silently подменяется специальной моделью.
- [ ] Развести compatibility path и целевой gateway path.
- [x] Зафиксировать состав нормализованного execution-контракта long-running инструмента:
  - `state`
  - `current_stage`
  - `status_text`
  - `status_history`
  - `progress`
  - `tool_label`
- [x] Зафиксировать, что `LangGraph` и `LangChain` допустимы только как внутренние источники событий для адаптера, а не как прямой UI wire-format.

## 7. `Open WebUI` и UX

- [ ] Убедиться, что в UI не закрепляется отдельная “обязательная tool-модель”.
- [ ] Проверить, что запуск `deep-job` не зависит от специального `model id` в пользовательском интерфейсе.
- [ ] Убедиться, что acceptance-сценарии формулируются через обычный выбор модели, а не через специальную модель.
- [ ] Убедиться, что промежуточный `deep-job` для любой модели показывается как процесс выполнения, а не как разговорная реплика `потом допишу`.
- [ ] Убедиться, что во время активного `deep-job` обычный новый turn для любой модели блокируется или уходит в очередь.
- [ ] Убедиться, что long-running панель для любой модели показывает:
  - общий класс `Инструмент долгого выполнения`
  - отдельное имя конкретного инструмента
  - этапы и статусы из нормализованного execution-контракта

## 8. Acceptance и smoke

- [ ] Добавить отдельный acceptance-критерий: инструменты доступны через общий gateway для выбранной модели.
- [ ] Добавить отдельный acceptance-критерий: `deep-job` не требует специальной пользовательской модели.
- [ ] Убрать из целевого acceptance зависимость от `llm-tools-platform` как обязательного маршрута.
- [ ] Оставить compatibility path только для переходной диагностики, если он ещё нужен.
- [ ] Добавить отдельный acceptance-критерий: процессный UX `deep-job` сохраняется для любой поддерживаемой модели.
- [ ] Добавить отдельный acceptance-критерий: одна активная пользовательская генеративная модель реально используется в `UMS` и orchestration.
- [ ] Добавить отдельный acceptance-критерий: служебные модели остаются backend-owned и не меняются от пользовательского выбора chat-модели.

## 9. Завершение перехода

- [ ] После готовности общего gateway перевести демонстрации на обычные модели.
- [ ] После готовности общего gateway перестать использовать специальную модель в основном пользовательском сценарии.
- [ ] Обновить документацию так, чтобы она не подсказывала специальную модель как обязательный способ работы с инструментами.
- [ ] Зафиксировать итоговый статус compatibility alias: оставить как временный fallback или удалить.
