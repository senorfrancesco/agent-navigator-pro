# Руководство по Установке

Это руководство содержит пошаговые инструкции по настройке проекта Agent Navigator Pro.

## Предварительные Требования

Перед началом убедитесь, что у вас установлено следующее:

- **Python 3.11+**: Требуется для сервисов бэкенда
- **Node.js 18+**: Требуется для фронтенда
- **NVIDIA GPU с CUDA**: Рекомендуется для оптимальной производительности (RTX 3060 12GB или выше)
- **Git**: Для клонирования репозитория

## Шаг 1: Клонирование Репозитория

```bash
git clone https://github.com/senorfrancesco/agent-navigator-pro.git
cd agent-navigator-pro
```

## Шаг 2: Настройка Бэкенда (Backend)

### Создание Виртуального Окружения

```bash
cd backend
python3.11 -m venv venv
```

### Активация Виртуального Окружения

**На Linux/macOS:**
```bash
source venv/bin/activate
```

**На Windows:**
```bash
venv\Scripts\activate
```

### Установка Python Зависимостей

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Опционально: Поддержка GPU

Для мониторинга NVIDIA GPU и поддержки CUDA:

```bash
pip install nvidia-ml-py
```

### Загрузка Моделей

Разместите файлы GGUF моделей в директории `backend/models/`. Вы можете скачать модели с:

- [Hugging Face](https://huggingface.co/)
- [GGUF модели TheBloke](https://huggingface.co/TheBloke)

Примеры моделей:
- `Qwen2.5-14B-Instruct-Q4_K_M.gguf`
- `Qwen3-VL-8B-Instruct-Q4_K_M.gguf`
- `LaBSE-Q4_K_M.gguf`

### Настройка Конфигурации (.env)

1. **Создайте файл `.env`** в директории `backend`, скопировав содержимое из `.env.example`:

```bash
cd backend
cp .env.example .env
```

2. **Отредактируйте файл `.env`**, указав актуальные пути к вашим моделям в подпапках `models/gguf/` и `models/st/`.

**Пример содержимого `.env`:**
```
# GGUF Модели (LLM)
MODEL_PATH_QWEN14B="./models/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf"
MODEL_PATH_QWENVL="./models/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
MMPROJ_PATH="./models/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"

# Sentence Transformer Модели (Эмбеддинги)
MODEL_PATH_LABSE="./models/st/LaBSE"
MODEL_PATH_E5_LEGAL="./models/st/E5-legal"
MODEL_PATH_RUBERT="./models/st/Rubert"
```
Все сервисы бэкенда автоматически загружают эти переменные при старте.

## Шаг 3: Настройка Фронтенда (Frontend)

Перейдите в директорию фронтенда и установите Node.js зависимости:

```bash
cd ../frontend
npm install
```

## Шаг 4: Запуск Приложения

### Вариант 1: Ручной Запуск

Запустите каждый сервис в отдельном окне терминала.

**Терминал 1: Agent API**
```bash
cd backend/orchestrator
uvicorn agent_api:app --host 0.0.0.0 --port 8000
```

**Терминал 2: Сервер Документов**
```bash
cd backend/services/document_server
uvicorn mcp_document_server:app --host 0.0.0.0 --port 8001
```

**Терминал 3: Юридический Сервер**
```bash
cd backend/services/legal_server
uvicorn mcp_legal_server:app --host 0.0.0.0 --port 8002
```

**Терминал 4: Unified Model Server (Опционально)**
```bash
cd backend/services/model_manager
python unified_model_server.py
```

**Терминал 5: Фронтенд**
```bash
cd frontend
npm run dev
```

### Вариант 2: Автоматический Запуск (Linux/macOS)

Используйте предоставленный shell-скрипт для запуска всех сервисов бэкенда:

```bash
cd backend
chmod +x run_all.sh
./run_all.sh
```

Затем запустите фронтенд в отдельном терминале:

```bash
cd frontend
npm run dev
```

## Шаг 5: Проверка Установки

Откройте браузер и перейдите по адресам:

- **Фронтенд**: `http://localhost:5173` (или порт, указанный Vite)
- **Agent API**: `http://localhost:8000/health`

Вы должны увидеть статус здоровья всех подключенных сервисов.

## Устранение Неполадок

### Проблемы с Версией Python

Убедитесь, что вы используете Python 3.11 или выше:

```bash
python --version
```

### Отсутствующие Зависимости

Если вы столкнулись с ошибками импорта, переустановите зависимости:

```bash
pip install -r requirements.txt --force-reinstall
```

### Порт Уже Используется

Если порт уже используется, вы можете изменить его в командах запуска или завершить процесс, использующий этот порт:

```bash
# Найти процесс, использующий порт 8000
lsof -i :8000

# Завершить процесс
kill -9 <PID>
```

### CUDA Не Обнаружена

Убедитесь, что у вас установлены драйверы NVIDIA и инструментарий CUDA. Проверьте доступность CUDA:

```bash
nvidia-smi
```

## Следующие Шаги

После завершения установки обратитесь к основному [README.md](README.md) для получения инструкций по использованию и документации API.
