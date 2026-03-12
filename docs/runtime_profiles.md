# Runtime Profiles

`Agent Navigator Pro` использует backend-owned runtime profiles для подстройки под железо и budget-контракт `UMS`.

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

UI отправляет только `model_profile`, а backend резолвит effective config через env-backed mapping:

- `CHAINLIT_MODEL_PROFILE_DEFAULT_CHAT_MODEL`
- `CHAINLIT_MODEL_PROFILE_LONG_CONTEXT_MODEL`
- `CHAINLIT_MODEL_PROFILE_LEGAL_COMPARE_MODEL`
- `CHAINLIT_MODEL_PROFILE_LOW_VRAM_MODEL`

Embedder routing тоже backend-owned и сейчас резолвится без raw selector в UI:

- intent embedder:
  - `CHAINLIT_INTENT_EMBEDDER_PROFILE_DEFAULT_MODEL`
  - default -> `qwen3-embedding-0.6b`
- retrieval/legal embedder:
  - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LEGAL_DEFAULT_MODEL`
  - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LOW_VRAM_MODEL`
  - current baseline stays `labse-embedding` until retrieval verdict changes

Итоговый effective config публикует:
- `resolved_model_id`
- `resolved_intent_embedder_model_id`
- `resolved_retrieval_embedder_model_id`

При необходимости можно задать profile-specific generation defaults через:

- `CHAINLIT_MODEL_PROFILE_<PROFILE>_TEMPERATURE`
- `CHAINLIT_MODEL_PROFILE_<PROFILE>_TOP_P`
- `CHAINLIT_MODEL_PROFILE_<PROFILE>_MAX_TOKENS`

`Chainlit` не даёт пользователю raw `model_id` selector и не принимает route/policy decisions локально. Он только рендерит effective values, которые резолвит backend control-plane.

## Applied output

Launcher и preflight не должны быть вторым decision engine.  
Поэтому runtime plan применяется в `backend/.env.runtime`, а source of truth после старта остаётся `UMS /status`.

Примеры переменных:

- `UMS_RUNTIME_PROFILE`
- `UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS`
- `UMS_RETRIEVED_CONTEXT_RATIO`
- `UMS_GENERATION_TOKENS_RESERVE`
- `DEVICE_MODE`

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
