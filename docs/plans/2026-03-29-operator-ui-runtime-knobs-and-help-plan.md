# План: расширить typed-config для runtime/container knobs и help-текста

## Summary

Цель: довести `Config` в operator UI до полноценной рабочей поверхности для:

- `Native Runtime`
- `Offline Bundle / Containers`

Пользователь должен иметь возможность:

- менять реальные runtime/GPU/device параметры там, где это поддерживается текущими env-контрактами;
- видеть, из какого файла берётся каждое значение;
- понимать, для чего нужен каждый параметр;
- переключать весь этот слой между `RU/EN` без смешанного copy.

Ключевое решение:

- не расширять `backend/.env.native` искусственно;
- для `Native Runtime` собирать один typed UI-contract поверх:
  - `backend/.env`
  - `backend/.env.runtime`
  - `backend/.env.hardware.override`
- для контейнерного пути расширять typed UI-contract поверх:
  - `deploy/offline_bundle/env.bundle`

## Scope

### 1. Native Runtime: расширить editable surface

Нужно добавить группы:

- `Runtime / Backend`
  - `BACKEND_MODE`
  - `UMS_RUNTIME_PROFILE`
  - `DEVICE_MODE`
- `GPU / Placement`
  - `LLM_DEVICE_MODE`
  - `VLM_DEVICE_MODE`
  - `INTENT_EMBEDDER_DEVICE_MODE`
  - `RETRIEVAL_EMBEDDER_DEVICE_MODE`
  - `GPU_LAYERS_MODE`
  - `N_GPU_LAYERS_OVERRIDE`
- `LLM / Context`
  - `UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS`
  - `UMS_RETRIEVED_CONTEXT_RATIO`
  - `UMS_GENERATION_TOKENS_RESERVE`
- `Model Runtime`
  - `CONTEXT_SIZE_QWEN14B`
  - `N_GPU_LAYERS_QWEN14B`
  - `CONTEXT_SIZE_QWENVL`
  - `CONTEXT_SIZE_LABSE`
  - `CONTEXT_SIZE_QWEN3_EMBEDDING_06B`

### 2. Offline Bundle / Containers: расширить editable surface

Нужно добавить группы:

- `Secrets / Access`
  - `CHAINLIT_AUTH_SECRET`
  - `CHAINLIT_ADMIN_USER`
  - `CHAINLIT_ADMIN_PASSWORD`
  - `GF_SECURITY_ADMIN_USER`
  - `GF_SECURITY_ADMIN_PASSWORD`
  - `VLLM_API_KEY`
- `Runtime / Backend`
  - `BACKEND_MODE`
  - `UMS_RUNTIME_PROFILE`
  - `DEVICE_MODE`
- `GPU / Placement`
  - `LLM_DEVICE_MODE`
  - `VLM_DEVICE_MODE`
  - `INTENT_EMBEDDER_DEVICE_MODE`
  - `RETRIEVAL_EMBEDDER_DEVICE_MODE`
  - `UMS_LLM_GPU_INDICES`
  - `GPU_LAYERS_MODE`
  - `N_GPU_LAYERS_OVERRIDE`
  - `N_GPU_LAYERS_QWEN14B`
  - `VLLM_TENSOR_PARALLEL_SIZE`
  - `VLLM_GPU_MEMORY_UTILIZATION`
- `Embedders / Models`
  - `INTENT_CLASSIFIER_MODE`
  - `INTENT_CLASSIFIER_EMBEDDER_MODEL`
  - `INTENT_CLASSIFIER_LLM_MODEL`
  - `LEGAL_EMBEDDER_MODEL`
  - `CHAINLIT_INTENT_EMBEDDER_PROFILE_DEFAULT_MODEL`
  - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LEGAL_DEFAULT_MODEL`
  - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LOW_VRAM_MODEL`
- `Chainlit Profiles`
  - `CHAINLIT_DEFAULT_TEMPERATURE`
  - `CHAINLIT_DEFAULT_TOP_P`
  - `CHAINLIT_DEFAULT_MAX_TOKENS`
  - `CHAINLIT_MODEL_PROFILE_*`

### 3. Типы контролов

Использовать:

- `select`
  - `BACKEND_MODE`
  - все `*_DEVICE_MODE`
  - `GPU_LAYERS_MODE`
  - `INTENT_CLASSIFIER_MODE`
  - булевы параметры
- `text` / numeric text
  - секреты и логины
  - GPU indices
  - GPU layers overrides
  - context/token/temperature thresholds
  - URL/path fields

### 4. Help-текст по каждому параметру

Каждое поле должно иметь:

- `description`
- `descriptionEn`
- `recommendedReason`
- `recommendedReasonEn`

Требование:

- описание объясняет назначение параметра;
- `recommendedReason` объясняет, почему текущее suggested значение полезно;
- literal env key, path и raw value не переводятся;
- UI-подсказки и help-тексты переводятся полностью.

### 5. Bilingual contract

Для всех новых групп, полей, пресетов и help-текста нужен парный RU/EN слой:

- `title` / `titleEn`
- `description` / `descriptionEn`
- `label` / `labelEn`
- `recommendedReason` / `recommendedReasonEn`

## Public Contract

`operator_config_service.py` должен вернуть для каждого поля:

- `key`
- `label`
- `labelEn`
- `description`
- `descriptionEn`
- `suggested`
- `applied`
- `source`
- `recommendedReason`
- `recommendedReasonEn`
- `editable`
- `control`
- `options`
- `validation`

Для secrets дополнительно:

- `secret: true`

Чтобы UI мог по умолчанию маскировать значение.

## UX Rules

- где выбор конечный, пользователь не вводит значение вручную;
- где значение реально свободное, остаётся `input`;
- рядом с полем всегда видно:
  - applied
  - source
  - purpose/help
  - recommendation
- native и bundle не смешиваются в один flat editor;
- `RU/EN` переключает labels и help-текст, но не env keys и не literal values.

## Verification

- unit tests для `OperatorConfigService`:
  - новые группы реально попадают в state
  - typed fields приходят как `select`
  - help-тексты и `...En` пары существуют
  - secrets помечены как `secret`
- frontend checks:
  - `node --check prototype/operator-ui/app.js`
  - `Config` корректно рендерит новые группы
  - `RU/EN` переключает help-текст и labels

## Recommendation

Реализовывать в 3 slice:

1. Native runtime knobs
2. Bundle runtime/secrets/Chainlit knobs
3. Help-text + secret masking + bilingual cleanup
