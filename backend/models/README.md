# Директория Моделей (models)

Эта директория предназначена для хранения всех локальных моделей, используемых бэкендом.

## Структура

Для удобства и соответствия вашему локальному хранилищу, директория разделена на две основные подпапки:

| Папка | Назначение | Пример содержимого |
| :--- | :--- | :--- |
| `gguf/` | Модели в формате GGUF для LLM (через `llama-cpp-python`) | `gguf/qwen-14b/Qwen3-14B.Q4_K_M.gguf` |
| `st/` | Модели Sentence Transformers для эмбеддингов | `st/LaBSE/` (полная папка модели) |

## Использование

### 1. Размещение файлов
Разместите ваши файлы моделей в соответствующих подпапках.

### 2. Конфигурация
Настройте переменные окружения, чтобы указать путь к каноническому registry и пути к model artifacts. Role bindings и fallback policy теперь живут в `backend/config/models.yaml`. Env contract использует универсальные имена для путей, а старые переменные сохранены как legacy aliases. Пути должны указывать на содержимое внутри `models/gguf/` или `models/st/`.

Model failover тоже описывается не кодом, а registry: для каждой роли в `models.yaml` задаются `primary` и `fallback`. Runtime делает только один retry `primary -> fallback`, а `busy/cancel` не переводятся на другую модель.

```bash
# Канонический registry моделей и ролей
export MODEL_REGISTRY_CONFIG_PATH="./config/models.yaml"

# Канонические переменные для GGUF моделей
export MODEL_PATH_LLM="./models/gguf/qwen-14b/Qwen3-14B.Q4_K_M.gguf"
export MODEL_PATH_VLM="./models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
export MMPROJ_PATH="./models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"

# Канонические переменные для embedding моделей
export MODEL_PATH_EMBEDDING_INTENT="./models/st/Qwen3-Embedding-0.6B"
export MODEL_PATH_EMBEDDING_RETRIEVAL="./models/st/LaBSE" # Путь к папке, а не к GGUF-файлу
```

Если модели лежат на другом диске, используйте абсолютные пути. Для `WSL` это обычно `/mnt/d/...`, например:

```bash
export MODEL_REGISTRY_CONFIG_PATH="/mnt/d/agent-navigator-pro/backend/config/models.yaml"
export MODEL_PATH_LLM="/mnt/d/agent-models/gguf/qwen-14b/Qwen3-14B.Q4_K_M.gguf"
export MODEL_PATH_VLM="/mnt/d/agent-models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
export MMPROJ_PATH="/mnt/d/agent-models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"
export MODEL_PATH_EMBEDDING_INTENT="/mnt/d/agent-models/st/Qwen3-Embedding-0.6B"
export MODEL_PATH_EMBEDDING_RETRIEVAL="/mnt/d/agent-models/st/LaBSE"
```

Для автоматической дозагрузки отсутствующих моделей используйте:

```bash
./scripts/models/install_models.sh --ensure-present
./scripts/models/install_models.sh --ensure-present --models-root=/mnt/d/agent-models
```

Legacy aliases для обратной совместимости:

- `MODEL_PATH_QWEN14B` -> `MODEL_PATH_LLM`
- `MODEL_PATH_QWENVL` -> `MODEL_PATH_VLM`
- `MODEL_PATH_QWEN3_EMBEDDING_06B` -> `MODEL_PATH_EMBEDDING_INTENT`
- `MODEL_PATH_LABSE` -> `MODEL_PATH_EMBEDDING_RETRIEVAL`

## Важное Примечание

Все файлы моделей **игнорируются** Git (см. `.gitignore`), за исключением файлов `README.md` внутри подпапок. Скачивайте модели отдельно из источников:
- [MaziyarPanahi/Qwen3-14B-GGUF](https://huggingface.co/MaziyarPanahi/Qwen3-14B-GGUF)
- [Qwen/Qwen3-VL-8B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF)
- [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [cointegrated/LaBSE-en-ru](https://huggingface.co/cointegrated/LaBSE-en-ru)
