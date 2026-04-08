# Flags Reference

Канонический справочник operator-facing флагов, env-переменных и runtime override-файлов проекта.

Этот документ отвечает на три вопроса:

1. Куда писать конкретный флаг.
2. Что влияет на текущий запуск, а что сохраняется постоянно.
3. Какие флаги влияют на скорость, качество, VRAM и стабильность старта.

## Source Of Truth

| Где хранится | За что отвечает | Кто редактирует |
| --- | --- | --- |
| `backend/config/models.yaml` | registry моделей, role bindings `primary/fallback`, preload policy, canonical env names для model artifacts | разработчик / оператор только если осознанно меняется registry |
| `backend/.env` | основной shared config: model paths, URLs, auth, backend mode, classifier/RAG defaults, timeout/concurrency defaults | пользователь / оператор |
| `backend/.env.runtime` | applied runtime plan для текущего запуска | пишет `launcher.sh` / `runtime_preflight.py`; руками не редактировать |
| `./scripts/launcher.sh ...` | current-run overrides и выбор target/install path | пользователь / оператор |

Короткое правило:

- registry и role bindings: `models.yaml`
- постоянные пути, URL, auth, backend mode и runtime intent: `backend/.env`
- разовый override на один запуск: флаги `launcher.sh`
- generated applied file: `backend/.env.runtime`

Legacy note:

- `backend/.env.native` и `backend/.env.hardware.override` считаются deprecated для native/operator path
- новый код и operator UI не должны использовать их как canonical source of truth

## Куда Писать Что

### Постоянные настройки проекта

Писать в `backend/.env`:

- `MODEL_REGISTRY_CONFIG_PATH`
- `MODEL_PATH_LLM`
- `MODEL_PATH_VLM`
- `MMPROJ_PATH`
- `MODEL_PATH_EMBEDDING_INTENT`
- `MODEL_PATH_EMBEDDING_RETRIEVAL`
- `BACKEND_MODE`
- `UMS_URL`, `DOC_SERVER_URL`, `LEGAL_SERVER_URL`
- `CHAINLIT_*`, `GF_SECURITY_*`
- `INTENT_CLASSIFIER_*`
- `LEGAL_EMBEDDER_MODEL`
- `RAG_MODE_OVERRIDE`
- `UMS_LLM_MAX_CONCURRENCY`, `UMS_EMBED_MAX_CONCURRENCY`
- `UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S`
- `UMS_FAIL_FAST_ON_SATURATION`
- `UMS_INFER_TIMEOUT_S`
- `UMS_CLIENT_TIMEOUT_S`
- `VLLM_*`
- `CONDA_ENV`
- `UPLOADS_DIR`
- `UMS_RUNTIME_PROFILE`
- `DEVICE_MODE`
- `LLM_DEVICE_MODE`
- `VLM_DEVICE_MODE`
- `INTENT_EMBEDDER_DEVICE_MODE`
- `RETRIEVAL_EMBEDDER_DEVICE_MODE`
- `GPU_LAYERS_MODE`
- `N_GPU_LAYERS_OVERRIDE`

### Разовый override только на текущий запуск

Задавать через `launcher.sh`:

```bash
./scripts/launcher.sh --target native --profile adaptive
./scripts/launcher.sh --target native --llm-device-mode gpu --intent-embedder-device-mode cpu
./scripts/launcher.sh --target native --gpu-layers-mode manual --gpu-layers 24
./scripts/launcher.sh --target native --skip-chainlit --no-attach
./scripts/launcher.sh --target container --profile default --no-attach
```

Такие флаги влияют только на текущий запуск и материализуются в `backend/.env.runtime`.

### Что не редактировать руками

Не использовать как user-owned source of truth:

- `backend/.env.runtime`

Это generated applied file. Launcher перегенерирует его на каждом запуске.

## Launcher Flags

Основной entrypoint:

```bash
./scripts/launcher.sh --target native --profile adaptive
```

Ключевые флаги:

| Флаг | Значение |
| --- | --- |
| `--target native|container` | выбор runtime path |
| `--profile default|adaptive|manual` | runtime profile для preflight |
| `--models-root <path>` | корень для auto-derived model paths |
| `--asset-set core|all` | какие модели проверять/докачивать |
| `--gpu-layers-mode auto|max|manual` | стратегия GPU layers |
| `--gpu-layers <int>` | число GPU layers для `manual` |
| `--device-mode cpu|gpu|hybrid` | общий fallback device mode |
| `--llm-device-mode ...` | placement для LLM |
| `--vlm-device-mode ...` | placement для VLM |
| `--intent-embedder-device-mode ...` | placement для intent/classifier embedder |
| `--retrieval-embedder-device-mode ...` | placement для retrieval/legal embedder |
| `--review-runtime` | interactive review current-run plan |
| `--non-interactive` | без interactive review |
| `--skip-model-download` | не запускать downloader |
| `--skip-chainlit` | только для `--target native`: не запускать окно Chainlit |
| `--report-only` | только plan/report без запуска |
| `--install --platform <platform>` | guided install path |

## Backend Mode И vLLM

Писать в `backend/.env`:

- `BACKEND_MODE=llama-cpp-python|llama-server|vllm`
- `UMS_LLAMA_CACHE_PROMPT=true|false`
- `VLLM_BASE_URL`
- `VLLM_API_KEY`
- `VLLM_PORT`
- `VLLM_MODEL_ID_QWEN_14B_LLM`
- `VLLM_MODEL_SOURCE_QWEN_14B_LLM`
- `VLLM_TENSOR_PARALLEL_SIZE`
- `VLLM_GPU_MEMORY_UTILIZATION`
- `VLLM_MAX_MODEL_LEN`

Практика:

- shared deploy/server config: `backend/.env`
- native runtime intent: `backend/.env`

## Placement И GPU Layers

Канонический public contract:

- `DEVICE_MODE=cpu|gpu|hybrid`
- `LLM_DEVICE_MODE=cpu|gpu|hybrid`
- `VLM_DEVICE_MODE=cpu|gpu|hybrid`
- `INTENT_EMBEDDER_DEVICE_MODE=cpu|gpu|hybrid`
- `RETRIEVAL_EMBEDDER_DEVICE_MODE=cpu|gpu|hybrid`
- `GPU_LAYERS_MODE=auto|max|manual`
- `N_GPU_LAYERS_OVERRIDE=<int>`

Где писать:

- постоянно: `backend/.env`
- разово: `launcher.sh` flags

Рекомендации:

### CPU-only safe mode

```env
DEVICE_MODE="cpu"
LLM_DEVICE_MODE="cpu"
VLM_DEVICE_MODE="cpu"
INTENT_EMBEDDER_DEVICE_MODE="cpu"
RETRIEVAL_EMBEDDER_DEVICE_MODE="cpu"
GPU_LAYERS_MODE="manual"
N_GPU_LAYERS_OVERRIDE="0"
```

### Mixed mode для 2x8GB / слабых GPU

```env
DEVICE_MODE="gpu"
LLM_DEVICE_MODE="gpu"
VLM_DEVICE_MODE="gpu"
INTENT_EMBEDDER_DEVICE_MODE="cpu"
RETRIEVAL_EMBEDDER_DEVICE_MODE="cpu"
GPU_LAYERS_MODE="max"
N_GPU_LAYERS_OVERRIDE="-1"
```

### Full GPU

```env
DEVICE_MODE="gpu"
LLM_DEVICE_MODE="gpu"
VLM_DEVICE_MODE="gpu"
INTENT_EMBEDDER_DEVICE_MODE="gpu"
RETRIEVAL_EMBEDDER_DEVICE_MODE="gpu"
GPU_LAYERS_MODE="max"
N_GPU_LAYERS_OVERRIDE="-1"
```

Использовать только если VRAM реально хватает и startup стабилен.

## Timeout И Concurrency

Эти флаги обычно писать в `backend/.env`.

### Inference timeouts

- `UMS_INFER_TIMEOUT_S`
  - timeout для upstream inference внутри `UMS`
- `UMS_CLIENT_TIMEOUT_S`
  - timeout для `ums_client`

Практика:

- если пользователь хочет дольше ждать тяжёлый ответ, увеличивать оба значения вместе
- для разовых экспериментов лучше сначала менять `.env`, а не код

### Concurrency / saturation

- `UMS_LLM_MAX_CONCURRENCY`
- `UMS_EMBED_MAX_CONCURRENCY`
- `UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S`
- `UMS_FAIL_FAST_ON_SATURATION`

Практика:

- low-VRAM / single heavy model: держать `UMS_LLM_MAX_CONCURRENCY=1`
- embeddings обычно можно держать выше, чем LLM
- если нужна предсказуемая деградация вместо длинного ожидания, включать `UMS_FAIL_FAST_ON_SATURATION=true`

## Registry, Roles И Model Paths

Канонические пути к артефактам:

- `MODEL_PATH_LLM`
- `MODEL_PATH_VLM`
- `MMPROJ_PATH`
- `MODEL_PATH_EMBEDDING_INTENT`
- `MODEL_PATH_EMBEDDING_RETRIEVAL`

Role bindings `primary/fallback` задаются не в env, а в:

- `backend/config/models.yaml`

Env нужен для:

- путей к артефактам моделей
- override пути к registry через `MODEL_REGISTRY_CONFIG_PATH`
- secrets / URLs / runtime knobs

## Practical Recipes

### Если нужно ускорить систему

1. Начать с `UMS_RUNTIME_PROFILE=adaptive`
2. Перевести LLM на GPU или `hybrid`
3. Поднять `GPU_LAYERS_MODE=max`
4. Не уводить embeddings на GPU, если это вызывает OOM
5. При необходимости уменьшить `MAX_TOKENS`/пользовательские generation defaults, а не ломать registry

### Если нужен более стабильный старт

1. Оставить `LLM_DEVICE_MODE=gpu|hybrid`
2. Увести `INTENT_EMBEDDER_DEVICE_MODE` и `RETRIEVAL_EMBEDDER_DEVICE_MODE` на `cpu`
3. Проверить `GET /ready/infer` после запуска
4. Не считать `UMS /health` достаточным признаком готовности heavy path

### Если `UMS` долго отвечает

Проверить и при необходимости поднять:

- `UMS_INFER_TIMEOUT_S`
- `UMS_CLIENT_TIMEOUT_S`

Если проблема не в timeout, а в перегрузке:

- `UMS_LLM_MAX_CONCURRENCY`
- `UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S`
- `UMS_FAIL_FAST_ON_SATURATION`

### Если нужен только временный override

Использовать `launcher.sh` flags или `--review-runtime`, а не править persistent `.env.hardware.override`.

## Antipatterns

- не редактировать `backend/.env.runtime` руками
- не писать role bindings в workflow-код вместо `models.yaml`
- не использовать legacy `N_GPU_LAYERS_QWEN14B` как основной public contract
- не смешивать shared `.env` и host-only `.env.native` без причины
- не считать `run_native.sh` и `run_all.sh` основными entrypoints для новых сценариев

## Related Docs

- [README.md](../README.md)
- [docs/scripts/README.md](./scripts/README.md)
- [docs/runtime_profiles.md](./runtime_profiles.md)
- [docs/deploy-guide.md](./deploy-guide.md)
