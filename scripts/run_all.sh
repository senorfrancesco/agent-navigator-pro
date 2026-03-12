#!/bin/bash

# ===========================================
# Скрипт для запуска всех компонентов системы
# Agent Navigator Pro v3.0 (Chainlit UI)
# ===========================================
# Использует tmux для управления несколькими процессами
# Chainlit запускается через Docker (порт 3000)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
ENV_FILE="$BACKEND_DIR/.env"
ATTACH_TMUX=true

for arg in "$@"; do
    case "$arg" in
        --no-attach)
            ATTACH_TMUX=false
            ;;
        *)
            echo "Неизвестный аргумент: $arg" >&2
            echo "Поддерживается: --no-attach" >&2
            exit 1
            ;;
    esac
done

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Запуск системы Agent Navigator Pro v3.0 (Chainlit) ===${NC}"

# -------------------------------------------
# Загрузка переменных окружения из .env
# -------------------------------------------
if [ -f "$ENV_FILE" ]; then
    echo -e "${BLUE}Загрузка переменных из .env...${NC}"
    set -a
    source "$ENV_FILE"
    set +a
else
    echo -e "${YELLOW}Предупреждение: .env файл не найден. Используются значения по умолчанию.${NC}"
    echo -e "${YELLOW}Создайте .env из .env.example: cp .env.example .env${NC}"
fi

# -------------------------------------------
# Определение Conda окружения
# -------------------------------------------
CONDA_ENV="${CONDA_ENV:-diploma_llm}"
CONDA_SH_PATH=""

# Функция для поиска и активации conda
find_conda() {
    # Попытка 1: Стандартные пути
    local conda_paths=(
        "$HOME/anaconda3"
        "$HOME/miniconda3"
        "/opt/conda"
        "/opt/anaconda3"
        "/usr/local/anaconda3"
    )

    for conda_path in "${conda_paths[@]}"; do
        if [ -f "$conda_path/etc/profile.d/conda.sh" ]; then
            CONDA_SH_PATH="$conda_path/etc/profile.d/conda.sh"
            source "$CONDA_SH_PATH"
            conda activate "$CONDA_ENV" 2>/dev/null && return 0
        fi
    done

    # Попытка 2: Через which conda
    if command -v conda &> /dev/null; then
        local conda_bin=$(which conda)
        local conda_root=$(dirname $(dirname "$conda_bin"))
        if [ -f "$conda_root/etc/profile.d/conda.sh" ]; then
            CONDA_SH_PATH="$conda_root/etc/profile.d/conda.sh"
            source "$CONDA_SH_PATH"
            conda activate "$CONDA_ENV" 2>/dev/null && return 0
        fi

        # Fallback: eval hook
        eval "$(conda shell.bash hook)"
        conda activate "$CONDA_ENV" 2>/dev/null && return 0
    fi

    return 1
}

echo -e "${YELLOW}Активация Conda окружения: $CONDA_ENV...${NC}"
if ! find_conda; then
    echo -e "${RED}Ошибка: Не удалось активировать conda окружение $CONDA_ENV${NC}"
    echo -e "${YELLOW}Попробуйте активировать вручную: conda activate $CONDA_ENV${NC}"
    exit 1
fi

echo -e "${GREEN}Conda окружение активировано: $CONDA_ENV${NC}"
if [ -n "$CONDA_SH_PATH" ]; then
    echo -e "${BLUE}Используется conda.sh: $CONDA_SH_PATH${NC}"
fi

# -------------------------------------------
# Проверяем наличие tmux
# -------------------------------------------
if ! command -v tmux &> /dev/null; then
    echo -e "${YELLOW}tmux не найден. Установка...${NC}"
    sudo apt-get update && sudo apt-get install -y tmux
fi

# -------------------------------------------
# Создаем новую tmux сессию
# -------------------------------------------
SESSION_NAME="agent-navigator"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo -e "${YELLOW}Завершение существующей сессии...${NC}"
    tmux kill-session -t "$SESSION_NAME"
fi

# Явное завершение llama-server (остаётся в памяти после закрытия tmux)
LLAMA_PIDS=$(pgrep -f "llama-server" 2>/dev/null || true)
if [ -n "$LLAMA_PIDS" ]; then
    echo -e "${YELLOW}Завершение llama-server перед запуском (PID: $LLAMA_PIDS)...${NC}"
    echo "$LLAMA_PIDS" | xargs kill 2>/dev/null || true
    sleep 1
    LLAMA_PIDS=$(pgrep -f "llama-server" 2>/dev/null || true)
    if [ -n "$LLAMA_PIDS" ]; then
        echo "$LLAMA_PIDS" | xargs kill -9 2>/dev/null || true
    fi
    echo -e "${GREEN}  llama-server завершён${NC}"
fi

tmux new-session -d -s "$SESSION_NAME" -x 200 -y 50

# Команда активации для tmux окон
if [ -n "$CONDA_SH_PATH" ]; then
    ACTIVATE_CMD="source $CONDA_SH_PATH && conda activate $CONDA_ENV"
else
    ACTIVATE_CMD="eval \"\$(conda shell.bash hook)\" && conda activate $CONDA_ENV"
fi

# Порты из .env или значения по умолчанию
AGENT_PORT="${AGENT_API_PORT:-8000}"
DOC_PORT=8001
LEGAL_PORT=8002
UMS_PORT="${UMS_PORT:-8090}"
CHAINLIT_PORT=3000

# -------------------------------------------
# Функция ожидания готовности сервиса
# -------------------------------------------
wait_for_service() {
    local name="$1"
    local port="$2"
    local endpoint="${3:-/health}"
    local timeout="${4:-60}"
    local elapsed=0

    printf "  %-20s " "$name (:$port)"
    while [ $elapsed -lt $timeout ]; do
        local http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port$endpoint" 2>/dev/null)
        if [ "$http_code" -ge 200 ] 2>/dev/null && [ "$http_code" -lt 500 ] 2>/dev/null; then
            echo -e "${GREEN}✓ готов (HTTP $http_code)${NC}"
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    echo -e "${RED}✗ таймаут (${timeout}с)${NC}"
    return 1
}

# Проверка загрузки модели в UMS через /status
wait_for_model() {
    local timeout="${1:-180}"
    local elapsed=0

    printf "  %-20s " "Qwen-14B LLM"
    while [ $elapsed -lt $timeout ]; do
        local status=$(curl -sf "http://localhost:$UMS_PORT/status" 2>/dev/null)
        if [ -n "$status" ]; then
            if echo "$status" | grep -q '"qwen-14b-llm"'; then
                echo -e "${GREEN}✓ загружена${NC}"
                return 0
            fi
        fi
        sleep 3
        elapsed=$((elapsed + 3))
    done
    echo -e "${YELLOW}⚠ не загружена за ${timeout}с (загрузится при первом запросе)${NC}"
    return 0
}

start_tmux_window() {
    local window_name="$1"
    local command="$2"
    local first_window_target=""

    if tmux list-windows -t "$SESSION_NAME" | grep -q "${window_name}"; then
        tmux kill-window -t "$SESSION_NAME:$window_name"
    fi

    first_window_target=$(tmux list-windows -t "$SESSION_NAME" -F "#{session_name}:#{window_index}" | head -n 1)

    if [ "$(tmux list-windows -t "$SESSION_NAME" | wc -l)" -eq 1 ] && [ -n "$first_window_target" ] && tmux display-message -p -t "$first_window_target" '#W' | grep -q '^bash$'; then
        tmux rename-window -t "$first_window_target" "$window_name"
        tmux send-keys -t "$SESSION_NAME:$window_name" "$command" Enter
    else
        tmux new-window -t "$SESSION_NAME" -n "$window_name"
        tmux send-keys -t "$SESSION_NAME:$window_name" "$command" Enter
    fi
}

# -------------------------------------------
# Запуск сервисов
# -------------------------------------------

# -------------------------------------------
# Ожидание готовности сервисов
# -------------------------------------------
echo ""
echo -e "${YELLOW}Проверка и запуск сервисов...${NC}"

SERVICES_OK=true

# 1) Document Server
echo -e "${GREEN}Запуск Document Server на порту $DOC_PORT...${NC}"
start_tmux_window "doc-server" "cd $BACKEND_DIR/services/document_server && $ACTIVATE_CMD && uvicorn mcp_document_server:app --host 0.0.0.0 --port $DOC_PORT 2>&1 | tee doc-server.log"
wait_for_service "Document Server" "$DOC_PORT" "/health" 30 || SERVICES_OK=false

# 2) Legal Server
echo -e "${GREEN}Запуск Legal Server на порту $LEGAL_PORT...${NC}"
start_tmux_window "legal-server" "cd $BACKEND_DIR/services/legal_server && $ACTIVATE_CMD && uvicorn mcp_legal_server:app --host 0.0.0.0 --port $LEGAL_PORT 2>&1 | tee legal-server.log"
wait_for_service "Legal Server" "$LEGAL_PORT" "/health" 30 || SERVICES_OK=false

# 3) UMS
echo -e "${GREEN}Запуск Unified Model Server на порту $UMS_PORT...${NC}"
start_tmux_window "ums" "cd $BACKEND_DIR/services/model_manager && $ACTIVATE_CMD && python unified_model_server.py 2>&1 | tee ums.log"
wait_for_service "UMS" "$UMS_PORT" "/health" 90 || SERVICES_OK=false

echo ""
echo -e "${YELLOW}Ожидание загрузки модели Qwen LLM (до 3 мин)...${NC}"
wait_for_model 180 || SERVICES_OK=false

# 4) Agent API
echo -e "${GREEN}Запуск Agent API на порту $AGENT_PORT...${NC}"
start_tmux_window "agent-api" "cd $BACKEND_DIR/orchestrator && $ACTIVATE_CMD && python agent_api.py 2>&1 | tee agent-api.log"
wait_for_service "Agent API" "$AGENT_PORT" "/health" 30 || SERVICES_OK=false

# 5) Chainlit UI (Docker)
echo -e "${GREEN}Запуск Chainlit UI (Docker) на порту $CHAINLIT_PORT...${NC}"
start_tmux_window "chainlit" "cd $PROJECT_ROOT && docker compose up -d chainlit && docker compose logs -f chainlit"
wait_for_service "Chainlit UI" "$CHAINLIT_PORT" "/" 60 || SERVICES_OK=false

# 6) Monitor/Logs
echo -e "${GREEN}Открытие окна мониторинга...${NC}"
start_tmux_window "monitor" "cd $BACKEND_DIR && echo -e '${GREEN}Система запущена.${NC}\nДля выхода нажмите ${YELLOW}Ctrl+B${NC} затем ${YELLOW}:kill-session${NC} (это остановит все сервисы, включая Docker).' && (htop 2>/dev/null || top)"

if [ "$SERVICES_OK" = true ]; then
    SYSTEM_STATUS="${GREEN}ГОТОВА К РАБОТЕ${NC}"
else
    SYSTEM_STATUS="${YELLOW}ЗАПУЩЕНА (некоторые сервисы не ответили)${NC}"
fi

# -------------------------------------------
# Выводим информацию
# -------------------------------------------
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        Agent Navigator Pro v3.0                          ║${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Статус:          $SYSTEM_STATUS"
echo -e "${GREEN}║${NC} Сессия tmux:     ${YELLOW}$SESSION_NAME${NC}"
echo -e "${GREEN}║${NC} Conda окружение: ${YELLOW}$CONDA_ENV${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Chainlit UI:     ${YELLOW}http://localhost:$CHAINLIT_PORT${NC}  (login: admin/admin)"
echo -e "${GREEN}║${NC} Agent API:       ${YELLOW}http://localhost:$AGENT_PORT${NC}"
echo -e "${GREEN}║${NC} Document Server: ${YELLOW}http://localhost:$DOC_PORT${NC}"
echo -e "${GREEN}║${NC} Legal Server:    ${YELLOW}http://localhost:$LEGAL_PORT${NC}"
echo -e "${GREEN}║${NC} UMS:             ${YELLOW}http://localhost:$UMS_PORT${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Подключение: ${BLUE}tmux attach-session -t $SESSION_NAME${NC}"
echo -e "${GREEN}║${NC} Завершение:  ${BLUE}./scripts/stop_all.sh${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

if [ "$ATTACH_TMUX" = false ]; then
    echo -e "${YELLOW}tmux attach пропущен (--no-attach).${NC}"
fi
