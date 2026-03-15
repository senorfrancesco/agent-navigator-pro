# Директория Моделей (models)

Эта директория предназначена для хранения всех локальных моделей, используемых бэкендом.

## Структура

Для удобства и соответствия вашему локальному хранилищу, директория разделена на две основные подпапки:

| Папка | Назначение | Пример содержимого |
| :--- | :--- | :--- |
| `gguf/` | Модели в формате GGUF для LLM (через `llama-cpp-python`) | `gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf` |
| `st/` | Модели Sentence Transformers для эмбеддингов | `st/LaBSE/` (полная папка модели) |

## Использование

### 1. Размещение файлов
Разместите ваши файлы моделей в соответствующих подпапках.

### 2. Конфигурация
Настройте переменные окружения, чтобы указать пути к вашим моделям. Канонический env contract использует универсальные имена, а старые переменные сохранены как legacy aliases. Пути должны указывать на содержимое внутри `models/gguf/` или `models/st/`.

```bash
# Канонические переменные для GGUF моделей
export MODEL_PATH_LLM="./models/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf"
export MODEL_PATH_VLM="./models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
export MMPROJ_PATH="./models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"

# Канонические переменные для embedding моделей
export MODEL_PATH_EMBEDDING_INTENT="./models/st/Qwen3-Embedding-0.6B"
export MODEL_PATH_EMBEDDING_RETRIEVAL="./models/st/LaBSE" # Путь к папке, а не к GGUF-файлу
```

Legacy aliases для обратной совместимости:

- `MODEL_PATH_QWEN14B` -> `MODEL_PATH_LLM`
- `MODEL_PATH_QWENVL` -> `MODEL_PATH_VLM`
- `MODEL_PATH_QWEN3_EMBEDDING_06B` -> `MODEL_PATH_EMBEDDING_INTENT`
- `MODEL_PATH_LABSE` -> `MODEL_PATH_EMBEDDING_RETRIEVAL`

## Важное Примечание

Все файлы моделей **игнорируются** Git (см. `.gitignore`), за исключением файлов `README.md` внутри подпапок. Скачивайте модели отдельно из источников, таких как:
- [Hugging Face](https://huggingface.co/)
- [GGUF модели TheBloke](https://huggingface.co/TheBloke)
