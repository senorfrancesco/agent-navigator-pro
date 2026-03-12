# Agent Navigator Pro — Инструкция по сборке и деплою на сервер

> Инструкция для переноса проекта с dev-машины на production/staging сервер.
> Актуальна для ветки `codex/orchestration-control-plane-snapshot` (база `v3.0`).

---

## 1. Требования к серверу

### Hardware (минимум)
- **GPU:** NVIDIA с ≥12 GB VRAM (для Qwen-14B Q4_K_M с `-1` gpu layers)
- **RAM:** ≥32 GB
- **Диск:** ≥200 GB (модели ~130 GB + система)
- **CUDA:** 12.x + nvidia-driver

### Без GPU (CPU-only fallback)
- RAM ≥64 GB
- Установить `N_GPU_LAYERS_QWEN14B=0` в `.env`
- Работать будет медленно, но функционально

### Software
- **OS:** Ubuntu 22.04+ / Debian 12+
- **Docker:** 24+ с Docker Compose v2
- **Python:** 3.11 (через conda или system)
- **NVIDIA Container Toolkit** (если GPU)
- **tmux** (для управления backend-сервисами)
- **conda** (Miniconda/Anaconda)

---

## 2. Подготовка сервера

```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# NVIDIA Container Toolkit (если GPU)
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# tmux
sudo apt-get install -y tmux

# Конфигурация tmux (Oh My Tmux) из репозитория
mkdir -p ~/.config/tmux
cp config/tmux/tmux.conf ~/.config/tmux/tmux.conf
cp config/tmux/tmux.conf.local ~/.config/tmux/tmux.conf.local

# Miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b
~/miniconda3/bin/conda init bash
source ~/.bashrc
```

---

## 3. Перенос проекта

### Вариант A: Git clone (рекомендуется)

```bash
cd /opt  # или ваш target dir
git clone <repo-url> agent-navigator-pro
cd agent-navigator-pro
git checkout codex/orchestration-control-plane-snapshot
```

### Вариант B: rsync с dev-машины

```bash
# На dev-машине:
rsync -avz --progress \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude 'backend/models/' \
  --exclude 'backend/open_webui_uploads/' \
  --exclude '.claude/' \
  --exclude '.gemini/' \
  --exclude 'node_modules' \
  /home/seral/HDD/proj/agent-navigator-pro/ \
  user@server:/opt/agent-navigator-pro/
```

**Модели переносить отдельно** (они большие):

```bash
rsync -avz --progress \
  /home/seral/HDD/proj/agent-navigator-pro/backend/models/ \
  user@server:/opt/agent-navigator-pro/backend/models/
```

---

## 4. Структура проекта на сервере

```
/opt/agent-navigator-pro/
├── backend/
│   ├── .env                          # ← создать из .env.example
│   ├── .env.example
│   ├── requirements.txt              # Python deps (backend services)
│   ├── models/
│   │   ├── gguf/
│   │   │   └── qwen-14b/
│   │   │       └── Qwen2.5-14B-Instruct-Q4_K_M.gguf   # ~9 GB
│   │   └── st/
│   │       ├── LaBSE/                # ONNX FP32
│   │       └── Qwen3-Embedding-0.6B/ # sentence-transformers
│   ├── orchestrator/
│   │   ├── agent_api.py              # FastAPI orchestrator (порт 8000)
│   │   ├── chainlit_app.py           # Chainlit UI app
│   │   ├── execution_runtime.py      # Execution layer
│   │   ├── orchestration_runtime.py  # Decision layer
│   │   ├── ui_control_plane.py       # Config resolution
│   │   ├── .chainlit/                # Chainlit config (translations)
│   │   ├── rag/                      # RAG pipeline
│   │   └── workflows/                # LangGraph workflows
│   ├── services/
│   │   ├── document_server/          # MCP Document Server (порт 8001)
│   │   ├── legal_server/             # MCP Legal Server (порт 8002)
│   │   └── model_manager/            # UMS (порт 8090)
│   ├── open_webui_uploads/           # Shared uploads dir
│   └── tests/
├── docker-compose.yaml               # Chainlit Docker
├── Dockerfile.chainlit
├── requirements.chainlit.txt
├── scripts/
│   ├── run_all.sh                    # Запуск всего (tmux + Docker)
│   ├── run_native.sh                 # Запуск без Docker (dev)
│   ├── stop_all.sh                   # Остановка
│   └── run_container.sh
├── TASKS.md
└── docs/plans/
```

---

## 5. Настройка окружения

### 5.1. Conda environment

```bash
cd /opt/agent-navigator-pro
conda create -n diploma_llm python=3.11 -y
conda activate diploma_llm
pip install -r backend/requirements.txt
```

### 5.2. Конфигурация `.env`

```bash
cp backend/.env.example backend/.env
```

Отредактировать `backend/.env`:

```bash
# Пути к моделям — АБСОЛЮТНЫЕ на сервере
MODEL_PATH_QWEN14B="/opt/agent-navigator-pro/backend/models/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf"
MODEL_PATH_LABSE="/opt/agent-navigator-pro/backend/models/st/LaBSE"
MODEL_PATH_QWEN3_EMBEDDING_06B="/opt/agent-navigator-pro/backend/models/st/Qwen3-Embedding-0.6B"

# GPU layers (-1 = все на GPU, 0 = CPU only)
N_GPU_LAYERS_QWEN14B=-1

# Контексты
CONTEXT_SIZE_QWEN14B=16384

# API URLs
UMS_URL="http://localhost:8090"
DOC_SERVER_URL="http://localhost:8001"
LEGAL_SERVER_URL="http://localhost:8002"

# Chainlit auth (СМЕНИТЬ для production!)
CHAINLIT_AUTH_SECRET="your-production-secret-key-here"
CHAINLIT_ADMIN_USER="admin"
CHAINLIT_ADMIN_PASSWORD="your-secure-password"

# Intent classifier
INTENT_CLASSIFIER_MODE="embedder"
INTENT_CLASSIFIER_EMBEDDER_MODEL="qwen3-embedding-0.6b"
INTENT_CLASSIFIER_EMBEDDER_CONFIDENCE_THRESHOLD="0.60"
INTENT_CLASSIFIER_EMBEDDER_MARGIN_THRESHOLD="0.10"
```

### 5.3. Директории

```bash
mkdir -p backend/open_webui_uploads
mkdir -p backend/.data
```

---

## 6. Сборка Docker-образа Chainlit

Chainlit UI работает в Docker-контейнере. Backend-сервисы (UMS, Document Server, Legal Server, Agent API) работают на хосте.

```bash
cd /opt/agent-navigator-pro

# Сборка образа
docker compose build chainlit
```

**Что попадает в образ:**
- `backend/orchestrator/` — весь код оркестратора
- `backend/services/model_manager/ums_client.py` — HTTP-клиент к UMS
- `requirements.chainlit.txt` — минимальные deps (без torch/onnx/llama-cpp)

**Что НЕ попадает** (через `.dockerignore`):
- `backend/models/` — модели остаются на хосте
- `backend/open_webui_uploads/` — монтируется как volume
- `.git/`, `docs/`, `*.md`

### Верификация образа

```bash
docker compose run --rm chainlit python -c "from orchestrator.execution_runtime import execute_orchestration; print('ok')"
```

---

## 7. Запуск системы

### Вариант A: Полный запуск (рекомендуется для production)

```bash
cd /opt/agent-navigator-pro
./scripts/run_all.sh --no-attach
```

Скрипт:
1. Активирует conda env `diploma_llm`
2. Создаёт tmux-сессию `agent-navigator` с окнами:
   - `doc-server` — Document Server (:8001)
   - `legal-server` — Legal Server (:8002)
   - `ums` — Unified Model Server (:8090)
   - `agent-api` — Agent API (:8000)
   - `chainlit` — Docker Chainlit UI (:3000)
   - `monitor` — htop/top
3. Ждёт health-check каждого сервиса
4. Ждёт загрузку модели Qwen-14B (до 3 мин)

### Вариант B: Native запуск (без Docker, для dev/debug)

```bash
./scripts/run_native.sh --no-attach
```

Chainlit запускается напрямую на хосте (не в Docker). Полезно для отладки.

### Подключение к tmux

```bash
tmux attach-session -t agent-navigator
# Переключение между окнами: Ctrl+B, затем номер окна (0-5)
```

---

## 8. Проверка работоспособности

### 8.1. Health checks

```bash
# Все сервисы
curl -s http://localhost:8000/health && echo " Agent API OK"
curl -s http://localhost:8001/health && echo " Doc Server OK"
curl -s http://localhost:8002/health && echo " Legal Server OK"
curl -s http://localhost:8090/health && echo " UMS OK"
curl -s http://localhost:3000/ -o /dev/null -w "%{http_code}" && echo " Chainlit OK"
```

### 8.2. Проверка загрузки модели

```bash
curl -s http://localhost:8090/status | python3 -m json.tool
# Должен показать "qwen-14b-llm" в loaded models
```

### 8.3. Тест API

```bash
# OpenAI-compatible endpoint
curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"agent-navigator","messages":[{"role":"user","content":"Привет"}],"stream":false}' \
  | python3 -m json.tool

# Orchestration endpoint
curl -s http://localhost:8000/orchestrate \
  -H "Content-Type: application/json" \
  -d '{"message":"Привет","runtime_mode":"auto"}' \
  | python3 -m json.tool
```

### 8.4. Chainlit UI

Открыть в браузере: `http://<server-ip>:3000`

Логин: значения `CHAINLIT_ADMIN_USER` / `CHAINLIT_ADMIN_PASSWORD` из `.env`.

### 8.5. Unit-тесты

```bash
cd /opt/agent-navigator-pro/backend
conda activate diploma_llm
pytest tests/ -v -m "not integration" --tb=short
# Ожидание: 326+ passed
```

---

## 9. Пересборка после изменений кода

Chainlit код копируется в Docker-образ при сборке. После любых изменений в `backend/orchestrator/`:

```bash
cd /opt/agent-navigator-pro
docker compose build chainlit && docker compose up -d chainlit
```

Backend-сервисы (Agent API, UMS, Doc/Legal Server) работают на хосте — изменения подхватываются при перезапуске:

```bash
./scripts/stop_all.sh && ./scripts/run_all.sh --no-attach
```

---

## 10. Остановка

```bash
./scripts/stop_all.sh
```

Скрипт:
1. Убивает tmux-сессию (все backend-процессы)
2. Останавливает Docker-контейнеры
3. Явно завершает llama-server
4. Чистит зависшие процессы на портах 8000-8090

---

## 11. Порты и сетевая модель

```
┌─────────────────────────────────────────────────────────┐
│                       СЕРВЕР                            │
│                                                         │
│  ┌──────────────┐  Docker                               │
│  │ Chainlit UI  │──────────────────────────────┐        │
│  │  :3000       │  host.docker.internal:8090   │        │
│  └──────────────┘  host.docker.internal:8001   │        │
│         │          host.docker.internal:8002   │        │
│         │                                      │        │
│  ┌──────┴───────────────────────────────────┐  │        │
│  │              ХОСТ                         │  │        │
│  │                                           │  │        │
│  │  Agent API (:8000)  ◄────────────────────┘  │        │
│  │       │                                     │        │
│  │       ├── UMS (:8090)                       │        │
│  │       │     └── llama-server (:8091)         │        │
│  │       │     └── LaBSE ONNX (:8093)           │        │
│  │       ├── Document Server (:8001)            │        │
│  │       └── Legal Server (:8002)               │        │
│  └──────────────────────────────────────────┘          │
│                                                         │
│  Shared volume: backend/open_webui_uploads/             │
│     Docker mount: /app/uploads                          │
└─────────────────────────────────────────────────────────┘
```

### Открытые порты (firewall)

| Порт | Сервис | Публичный? |
|------|--------|------------|
| 3000 | Chainlit UI | Да (пользователи) |
| 8000 | Agent API | Опционально (для внешних клиентов) |
| 8001 | Document Server | Нет (internal) |
| 8002 | Legal Server | Нет (internal) |
| 8090 | UMS | Нет (internal) |
| 8091-8093 | llama-server/embeddings | Нет (internal) |

---

## 12. Troubleshooting

### Модель не грузится

```bash
# Проверить пути
ls -la $(grep MODEL_PATH_QWEN14B backend/.env | cut -d= -f2 | tr -d '"')
# Проверить GPU
nvidia-smi
# Проверить логи UMS
tmux attach -t agent-navigator   # окно "ums"
```

### Chainlit не стартует в Docker

```bash
docker compose logs chainlit
# Частая причина: неверный PYTHONPATH или отсутствующий модуль
docker compose run --rm chainlit python -c "import orchestrator; print('ok')"
```

### Порт занят

```bash
lsof -i :8000
# kill процесс или поменять порт в .env
```

### Uploads не видны в Chainlit

```bash
# Проверить bind mount
docker inspect chainlit-ui | grep -A5 Mounts
# Должен быть: backend/open_webui_uploads → /app/uploads
ls -la backend/open_webui_uploads/
```

### GPU memory

```bash
nvidia-smi
# Если OOM — уменьшить N_GPU_LAYERS_QWEN14B или CONTEXT_SIZE_QWEN14B в .env
```

---

## 13. Чеклист деплоя

- [ ] Conda env `diploma_llm` создан с Python 3.11
- [ ] `pip install -r backend/requirements.txt` успешен
- [ ] Модели скопированы в `backend/models/`
- [ ] `backend/.env` создан и настроен (пути, пароли, GPU layers)
- [ ] `mkdir -p backend/open_webui_uploads backend/.data`
- [ ] `docker compose build chainlit` успешен
- [ ] `nvidia-smi` работает (если GPU)
- [ ] `./scripts/run_all.sh --no-attach` — все health-checks пройдены
- [ ] `curl localhost:8000/health` → `{"status":"ok"}`
- [ ] `curl localhost:3000/` → HTTP 200
- [ ] `curl localhost:8090/status` → показывает `qwen-14b-llm`
- [ ] Логин в Chainlit UI работает
- [ ] Тестовый запрос через API возвращает ответ
- [ ] `CHAINLIT_AUTH_SECRET` и `CHAINLIT_ADMIN_PASSWORD` изменены с дефолтных
