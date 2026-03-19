# Руководство по Conda

Conda — менеджер окружений и пакетов. В текущем runtime-контуре проекта достаточно `conda base`: отдельное окружение `diploma_llm` больше не является обязательным.

---

## Установка

```bash
# Скачать Miniconda (x86_64)
wget -O miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh

# Установить без интерактивного режима
bash miniconda.sh -b -p ~/miniconda3

# Инициализировать для bash
~/miniconda3/bin/conda init bash
source ~/.bashrc
```

Проверка:
```bash
conda --version
```

---

## Конфигурация проекта (`.condarc`)

Конфиг хранится в `config/conda.condarc`. Применить:

```bash
cp config/conda.condarc ~/.condarc
```

Содержимое — каналы `conda-forge` (приоритет) и `defaults`:

```yaml
channels:
  - conda-forge
  - defaults
```

`conda-forge` содержит более свежие пакеты и лучше поддерживает ML-библиотеки.

---

## Рабочее окружение

### Базовый путь

```bash
conda activate base
pip install -r backend/requirements.txt
```

### Активация

```bash
conda activate base
```

Или через скрипт (создаётся `setup_ubuntu.sh`):
```bash
source activate_env.sh
```

### Деактивация

```bash
conda deactivate
```

---

## Основные команды

### Окружения

```bash
# Список всех окружений
conda env list
conda info --envs

# Создать окружение
conda create -n my-env python=3.11 -y

# Активировать
conda activate my-env

# Деактивировать
conda deactivate

# Удалить окружение
conda env remove -n my-env -y

# Клонировать окружение
conda create -n new-env --clone diploma_llm

# Экспортировать в yml (для воспроизведения)
conda env export > environment.yml

# Восстановить из yml
conda env create -f environment.yml
```

### Пакеты

```bash
# Установить пакет
conda install numpy

# Установить конкретную версию
conda install numpy=1.24

# Установить через pip (когда нет в conda)
pip install langchain

# Список установленных пакетов
conda list

# Поиск пакета
conda search numpy

# Обновить пакет
conda update numpy

# Удалить пакет
conda remove numpy
```

### Обновление самой conda

```bash
conda update -n base -c defaults conda
```

---

## Зависимости проекта

Устанавливаются через pip из `backend/requirements.txt`. Ключевые:

| Пакет | Роль |
|-------|------|
| `fastapi` + `uvicorn` | HTTP сервисы (Agent API, UMS, доки) |
| `langchain` + `langgraph` | Orchestration + LangGraph workflows |
| `chainlit` | UI фреймворк |
| `llama-cpp-python[server]` | LLM inference через llama-server |
| `onnxruntime` | LaBSE ONNX embeddings (CPU) |
| `sentence-transformers` | Fallback embeddings + Qwen3-Embedding |
| `torch` | PyTorch (для некоторых embedder-путей) |
| `httpx` | Async HTTP клиент между сервисами |
| `pydantic` | Валидация данных |
| `pymupdf` / `python-docx` | Парсинг PDF и DOCX |

### Установка с CUDA-поддержкой для llama-cpp-python

```bash
# Если CUDA доступна — пересобрать с GPU-поддержкой
CMAKE_ARGS="-DLLAMA_CUBLAS=on" pip install llama-cpp-python[server] --force-reinstall --no-cache-dir
```

---

## Настройка `.condarc`

Файл `~/.condarc` управляет глобальным поведением conda:

```yaml
# Каналы поиска пакетов (приоритет сверху вниз)
channels:
  - conda-forge
  - defaults
```

Полезные параметры которые можно добавить:

```yaml
# Всегда говорить "да" на вопросы (осторожно!)
# always_yes: true

# Не показывать progress bar
# quiet: true

# Хранить кеш пакетов в другом месте
# pkgs_dirs:
#   - /data/conda/pkgs

# Хранить окружения в другом месте
# envs_dirs:
#   - /data/conda/envs
```

---

## Типичный рабочий процесс

```bash
# 1. Активировать окружение
conda activate base

# 2. Запустить систему
./scripts/launcher.sh --target native

# 3. Запустить тесты
cd backend && pytest tests/ -v -m "not integration"

# 4. Добавить новый пакет
pip install some-package
# Зафиксировать в requirements.txt:
pip freeze | grep some-package >> backend/requirements.txt
```

---

## Частые проблемы

**ToSNonInteractiveError / Terms of Service:**

```bash
conda tos accept
```

или точечно по каналам:

```bash
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
```

**conda: command not found после установки:**
```bash
source ~/.bashrc
# или явно:
source ~/miniconda3/etc/profile.d/conda.sh
```

**Медленная установка пакетов через conda:**
Использовать pip для ML-пакетов — они быстрее обновляются на PyPI:
```bash
pip install -r backend/requirements.txt
```

**Конфликт версий пакетов:**
```bash
# Создать чистое окружение заново
conda env remove -n diploma_llm -y
conda create -n diploma_llm python=3.11 -y
conda activate diploma_llm
pip install -r backend/requirements.txt
```

**Проверить что активировано нужное окружение:**
```bash
conda info --envs   # звёздочка * = активное
which python        # должно вести в ~/miniconda3/envs/diploma_llm/
python --version    # должно быть Python 3.11.x
```
