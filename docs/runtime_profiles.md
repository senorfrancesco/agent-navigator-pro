# Runtime Profiles

`llm-tools-platform` использует backend-owned runtime profiles для подстройки под железо и budget-контракт `UMS`.

Этот документ описывает semantics runtime profiles. Канонический ответ на вопрос “куда писать какой флаг” вынесен в [docs/flags-reference.md](./flags-reference.md).

## Backend modes

Отдельно от runtime profiles `UMS` поддерживает backend mode switch через `BACKEND_MODE`:

- `llama-cpp-python`
- `llama-server`
- `vllm`

`vllm` в текущем safe slice означает только remote text-generation adapter для heavy `gguf`-LLM. Это не меняет embedding path и не переносит `gguf-vl` / vision на upstream runtime.

Для operator rollout отдельного upstream runtime используется `docker compose --profile vllm up -d vllm`; это отдельный deployment path поверх backend mode switch, а не замена runtime profiles.

Для локального `llama-server` prompt-cache policy управляется отдельно через `UMS_LLAMA_CACHE_PROMPT=true|false`. Effective policy публикуется в `UMS /status -> prompt_cache_policy` и используется benchmark probe `prompt_cache_probe`.

## Model registry contract

Канонический source of truth для model registry, role bindings и preload policy теперь находится в:

- `backend/config/models.yaml`

Env больше не считается основным местом выбора model id для workflow/runtime. Он используется для:
- пути к registry (`MODEL_REGISTRY_CONFIG_PATH`, если нужен override);
- путей к model artifacts;
- operator/runtime overrides;
- секретов и service URLs.

## Universal failover contract

Universal model failover теперь backend-owned и registry-backed:

- для каждой runtime-critical role в `models.yaml` задаются `primary` и `fallback`;
- `ums_client` и `UMS` используют один и тот же execution plan;
- failover выполняется один раз по схеме `primary -> fallback`;
- failover разрешён только для model-failure веток (`startup/load/probe/infer`);
- `429 busy` и cancellation не переводят execution на fallback модель;
- diagnostics публикуются как:
  - `model_execution` в client/workflow responses;
  - `last_fallback_event` в `UMS /status`;
  - `llm_tools_platform_fallback_events_total` в metrics.

## Model path contract

Для путей к артефактам моделей канонический env contract такой:

- `MODEL_PATH_LLM`
- `MODEL_PATH_VLM`
- `MODEL_PATH_EMBEDDING_INTENT`
- `MODEL_PATH_EMBEDDING_RETRIEVAL`

Legacy aliases остаются допустимыми для compatibility rollout:

- `MODEL_PATH_QWEN14B` -> `MODEL_PATH_LLM`
- `MODEL_PATH_QWENVL` -> `MODEL_PATH_VLM`
- `MODEL_PATH_QWEN3_EMBEDDING_06B` -> `MODEL_PATH_EMBEDDING_INTENT`
- `MODEL_PATH_LABSE` -> `MODEL_PATH_EMBEDDING_RETRIEVAL`

Runtime profiles не меняют этот contract. Они управляют budget, placement и routing policy поверх уже заданных model paths.

Практическое правило:

- `backend/.env` — user-owned paths, service env и runtime intent;
- `backend/.env.runtime` — generated applied plan.

## Профили

- `default`
  Безопасный дефолтный профиль. Использует conservative context budget без дополнительных hardware-derived усилений.

- `adaptive`
  Основной рекомендуемый профиль. Использует hardware/tier hints и строит `effective_context_tokens` от текущего runtime context size.

- `manual`
  Явно задаваемый профиль для операторского контроля. Позволяет передать собственные overrides, например `UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS`.

## Chainlit Model Profiles

Отдельно от runtime profiles в `UMS` пользовательский `Chainlit` control-plane использует логические model profiles:

- `default-chat`
  - `device_mode=auto`
  - `context_budget_profile=standard`
- `long-context`
  - `device_mode=prefer-gpu`
  - `context_budget_profile=long-context`
- `legal-compare`
  - `device_mode=prefer-gpu`
  - `context_budget_profile=legal-compare`
- `low-vram`
  - `device_mode=low-vram`
  - `context_budget_profile=compact`

UI отправляет только `model_profile`, а backend резолвит effective config через `models.yaml`.

Legacy env overrides для совместимости ещё допустимы, но считаются deprecated:

- `CHAINLIT_MODEL_PROFILE_DEFAULT_CHAT_MODEL`
- `CHAINLIT_MODEL_PROFILE_LONG_CONTEXT_MODEL`
- `CHAINLIT_MODEL_PROFILE_LEGAL_COMPARE_MODEL`
- `CHAINLIT_MODEL_PROFILE_LOW_VRAM_MODEL`

Embedder routing тоже backend-owned и сейчас резолвится без raw selector в UI:

- intent embedder:
  - `CHAINLIT_INTENT_EMBEDDER_PROFILE_DEFAULT_MODEL`
  - canonical primary/fallback берутся из `models.yaml`
- retrieval/legal embedder:
  - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LEGAL_DEFAULT_MODEL`
  - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LOW_VRAM_MODEL`
  - canonical primary/fallback берутся из `models.yaml`

Итоговый effective config публикует:
- `resolved_model_id`
- `resolved_intent_embedder_model_id`
- `resolved_retrieval_embedder_model_id`

При необходимости можно задать profile-specific generation defaults через:

- `CHAINLIT_MODEL_PROFILE_<PROFILE>_TEMPERATURE`
- `CHAINLIT_MODEL_PROFILE_<PROFILE>_TOP_P`
- `CHAINLIT_MODEL_PROFILE_<PROFILE>_MAX_TOKENS`

`Chainlit` не даёт пользователю raw `model_id` selector и не принимает route/policy decisions локально. Он только рендерит effective values, которые резолвит backend control-plane.

## Multi-GPU placement policy

Placement policy для LLM и embeddings тоже backend-owned и применяется внутри `UMS`, а не в UI.

- GGUF/LLM startup:
  - `UMS` выбирает GPU по free VRAM
  - multi-GPU admission фильтруется по `UMS_LLM_MIN_FREE_VRAM_GB`
  - плохо сбалансированные GPU отбрасываются через `UMS_LLM_MIN_BALANCE_RATIO`
  - если остаётся несколько GPU, `--tensor-split` считается как weighted split от free VRAM, а не как равные доли
  - если корректный multi-GPU набор не собран, `UMS` падает в best single GPU

- SentenceTransformer/embedding startup:
  - можно явно задать `UMS_EMBEDDING_GPU_INDEX`
  - tier-level `embedding_device=cpu` принудительно оставляет embeddings на CPU
  - если уже есть активное тяжёлое LLM placement, embeddings стараются уйти на другой GPU
  - если свободного GPU без конфликта нет, embedding server падает в CPU, а не в implicit `cuda:0`

Operational status публикуется в `UMS /status` через `placements`, но raw placement не поднимается в `Chainlit` effective settings и не даётся пользователю как selector.

## Applied output

Launcher и preflight не должны быть вторым decision engine.  
Поэтому runtime plan применяется в `backend/.env.runtime`, а source of truth после старта остаётся `UMS /status`.

Примеры переменных:

- `UMS_RUNTIME_PROFILE`
- `UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS`
- `UMS_RETRIEVED_CONTEXT_RATIO`
- `UMS_GENERATION_TOKENS_RESERVE`
- `DEVICE_MODE`
- `UMS_LLM_GPU_INDICES`
- `UMS_LLM_MIN_FREE_VRAM_GB`
- `UMS_LLM_MIN_BALANCE_RATIO`
- `UMS_EMBEDDING_GPU_INDEX`

## Canonical flow

```text
scripts/launcher.sh
  -> scripts/bootstrap_env.sh (optional)
  -> scripts/runtime_preflight.py apply|report
  -> source backend/.env + backend/.env.runtime
  -> launch target native|container
  -> read UMS /status and print runtime summary
```

## Примеры

Только report:

```bash
./scripts/launcher.sh --target native --profile adaptive --report-only
```

Native запуск:

```bash
./scripts/launcher.sh --target native --profile adaptive
```

Container-oriented запуск:

```bash
./scripts/launcher.sh --target container --profile default
```
