# Flags Reference

Отдельный справочник по CLI-флагам и `env`-параметрам runtime-пути.

Главное различие:

- `native` path живёт от одного `backend/.env`
- `container` path всё ещё может материализовать `backend/.env.runtime`
- `scripts/evaluate_runtime.sh` ничего не применяет и только рекомендует значения для ручного переноса в `backend/.env`

## Источники Правды

| Где хранится | Для чего | Кто редактирует |
| --- | --- | --- |
| `backend/config/models.yaml` | registry моделей, role bindings, runtime defaults по моделям | разработчик |
| `backend/.env` | единственный user-owned конфиг для native path: пути, порты, URL, placement, budget, timeout, concurrency | пользователь / оператор |
| `backend/.env.runtime` | generated applied env для `container` path и legacy-совместимости | автоматически, руками не редактировать |
| `backend/.env.native` | deprecated | не использовать |
| `backend/.env.hardware.override` | deprecated | не использовать |

Короткое правило:

- если нужен постоянный native-конфиг, писать в `backend/.env`
- если нужно посмотреть рекомендации по железу, запускать `./scripts/evaluate_runtime.sh recommend`
- если нужен compose/container runtime, `launcher.sh` может дополнительно собрать `backend/.env.runtime`

## Скрипты И Флаги

### `./scripts/launcher.sh`

Канонический entrypoint для CLI-запуска.

Основные команды:

```bash
./scripts/launcher.sh --target native --profile adaptive
./scripts/launcher.sh --target native --profile adaptive --skip-chainlit
./scripts/launcher.sh --target native --profile adaptive --report-only
./scripts/launcher.sh --target container --profile default --no-attach
./scripts/launcher.sh --install --platform ubuntu
```

Ключевые флаги:

| Флаг | Для чего | Где работает |
| --- | --- | --- |
| `--target native|container` | выбор runtime path | оба |
| `--profile default|adaptive|manual` | профиль preflight | оба |
| `--asset-set core|all` | набор моделей для `ensure-present` | оба |
| `--models-root <path>` | корень layout моделей | оба |
| `--huggingface-cache <path>` | путь к кэшу Hugging Face | оба |
| `--gpu-layers-mode auto|max|manual` | стратегия GPU layers для preflight/evaluator | оба |
| `--gpu-layers <int>` | число GPU layers для `manual` | оба |
| `--device-mode cpu|gpu|hybrid` | общий fallback placement | оба |
| `--llm-device-mode ...` | placement только для LLM | оба |
| `--vlm-device-mode ...` | placement только для VLM | оба |
| `--intent-embedder-device-mode ...` | placement только для intent embedder | оба |
| `--retrieval-embedder-device-mode ...` | placement только для retrieval embedder | оба |
| `--skip-model-download` | пропустить model provisioning | оба |
| `--ensure-model-download` | явно включить model provisioning | оба |
| `--no-attach` | не подключаться к `tmux` | оба |
| `--skip-chainlit` | не запускать окно `Chainlit` | только `native` |
| `--report-only` | только вывести report/recommendation без запуска | оба |
| `--install` | guided install path | отдельно |
| `--platform auto|ubuntu|ubuntu-server|wsl|windows` | платформа installer path | c `--install` |

Важно для native path:

- `--review-runtime` больше не используется
- `--non-interactive` для native не нужен
- `--apply-runtime` и `--skip-runtime-apply` для native считаются ошибкой
- `--report-only` на native печатает рекомендации для `backend/.env`, а не пишет `backend/.env.runtime`

Важно для container path:

- `--review-runtime`, `--non-interactive`, `--apply-runtime`, `--skip-runtime-apply` относятся именно к generated `backend/.env.runtime`

### `./scripts/evaluate_runtime.sh`

User-facing рекомендатель для native path.

Примеры:

```bash
./scripts/evaluate_runtime.sh
./scripts/evaluate_runtime.sh recommend
./scripts/evaluate_runtime.sh plan --profile adaptive
./scripts/evaluate_runtime.sh detect
./scripts/evaluate_runtime.sh report
```

Что делает:

- читает только `backend/.env`
- показывает hardware snapshot и placement plan
- печатает рекомендуемый блок для ручного переноса в `backend/.env`
- ничего не записывает автоматически

Что не делает:

- не запускает сервисы
- не пишет `backend/.env.runtime`
- не меняет `backend/.env`

### `./scripts/run_native.sh`

Прямой native runner.

Примеры:

```bash
./scripts/run_native.sh
./scripts/run_native.sh --no-attach
./scripts/run_native.sh --skip-chainlit --no-attach
```

Поведение по умолчанию:

- читает `backend/.env`
- не применяет `backend/.env.runtime`
- поднимает `tmux`, `document_server`, `legal_server`, `UMS`, `agent_api`, `Chainlit`

Compatibility-флаги:

- `--apply-runtime`
- `--skip-runtime-apply`

Использовать их стоит только осознанно, если нужен legacy/applied runtime-файл. Для обычного native-пути они не нужны.

## Что Настраивать В `backend/.env`

### Базовый native/runtime контракт

- `MODEL_PATH_LLM`
- `MODEL_PATH_VLM`
- `MMPROJ_PATH`
- `MODEL_PATH_EMBEDDING_INTENT`
- `MODEL_PATH_EMBEDDING_RETRIEVAL`
- `CONDA_ENV`
- `UPLOADS_DIR`
- `HOST_UPLOADS_DIR`
- `AGENT_API_PORT`
- `DOC_PORT`
- `LEGAL_PORT`
- `UMS_PORT`
- `CHAINLIT_PORT`
- `UMS_URL`
- `MCP_DOCUMENT_SERVER_URL`
- `MCP_LEGAL_SERVER_URL`
- `CHAINLIT_DB_URL`
- `CHAINLIT_ENABLE_DATA_LAYER`

### Placement и multi-GPU split

Это реальные рабочие параметры для `UMS`:

- `UMS_RUNTIME_PROFILE`
- `DEVICE_MODE`
- `LLM_DEVICE_MODE`
- `VLM_DEVICE_MODE`
- `INTENT_EMBEDDER_DEVICE_MODE`
- `RETRIEVAL_EMBEDDER_DEVICE_MODE`
- `UMS_LLM_GPU_INDICES`
- `UMS_EMBEDDING_GPU_INDEX`
- `UMS_LLM_MIN_FREE_VRAM_GB`
- `UMS_LLM_MIN_BALANCE_RATIO`
- `UMS_ALLOW_HEAVY_CPU_DEGRADE_AFTER_GPU_FAILURE`

Короткая логика:

- `UMS_LLM_GPU_INDICES` ограничивает список GPU для LLM
- `UMS_LLM_MIN_FREE_VRAM_GB` отбрасывает карты с недостатком свободной VRAM
- `UMS_LLM_MIN_BALANCE_RATIO` убирает слишком несбалансированные карты из multi-GPU split
- `UMS_EMBEDDING_GPU_INDEX` позволяет закрепить embedding-модели на конкретной карте

### Budget и runtime knobs

- `UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS`
- `UMS_RETRIEVED_CONTEXT_RATIO`
- `UMS_GENERATION_TOKENS_RESERVE`
- `UMS_LLM_MAX_CONCURRENCY`
- `UMS_EMBED_MAX_CONCURRENCY`
- `UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S`
- `UMS_FAIL_FAST_ON_SATURATION`
- `UMS_INFER_TIMEOUT_S`
- `UMS_CLIENT_TIMEOUT_S`

### GPU layers и per-model offload

Нужно разделять два уровня:

- `GPU_LAYERS_MODE` и `N_GPU_LAYERS_OVERRIDE` нужны для preflight/evaluator и launcher-совместимости
- реальный per-model offload для `gguf`-моделей по-прежнему задаётся через:
  - `N_GPU_LAYERS_QWEN14B`
  - `N_GPU_LAYERS_QWENVL`
  - `N_GPU_LAYERS_LABSE`
  - `N_GPU_LAYERS_QWEN3_EMBEDDING_06B`

Если нужно просто управлять размещением и split-ом, обычно достаточно `*_DEVICE_MODE` и `UMS_*GPU*` параметров.

## Практические Профили

### Стабильный профиль для 2x8GB

```env
UMS_RUNTIME_PROFILE="adaptive"
DEVICE_MODE="hybrid"
LLM_DEVICE_MODE="gpu"
VLM_DEVICE_MODE="gpu"
INTENT_EMBEDDER_DEVICE_MODE="cpu"
RETRIEVAL_EMBEDDER_DEVICE_MODE="cpu"
UMS_LLM_GPU_INDICES=0,1
UMS_EMBEDDING_GPU_INDEX=1
UMS_LLM_MIN_FREE_VRAM_GB=0
UMS_LLM_MIN_BALANCE_RATIO=0.5
```

Идея простая:

- LLM/VLM идут на GPU
- embeddings не конкурируют с ними за VRAM
- split для LLM разрешён только на нужных картах

### Полный CPU-safe режим

```env
DEVICE_MODE="cpu"
LLM_DEVICE_MODE="cpu"
VLM_DEVICE_MODE="cpu"
INTENT_EMBEDDER_DEVICE_MODE="cpu"
RETRIEVAL_EMBEDDER_DEVICE_MODE="cpu"
```

### Как безопасно подобрать параметры

1. Запустить `./scripts/evaluate_runtime.sh recommend`
2. Перенести только нужные значения в `backend/.env`
3. Запустить `./scripts/run_native.sh`
4. Если нужен только backend без UI, добавить `--skip-chainlit`

## Что Считать Устаревшим

- `backend/.env.native`
- `backend/.env.hardware.override`
- попытки настраивать native path через `launcher.sh --review-runtime`
- ручное редактирование `backend/.env.runtime` для обычного native запуска

Термин `deprecated` здесь означает: файл или сценарий оставлен только ради совместимости и не должен использоваться как новый канонический контракт.
