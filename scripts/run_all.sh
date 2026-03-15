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
RUNTIME_ENV_FILE="${AGENT_NAVIGATOR_RUNTIME_ENV_FILE:-$BACKEND_DIR/.env.runtime}"
ATTACH_TMUX=true
FROM_LAUNCHER=false

EXTERNAL_BACKEND_MODE="${BACKEND_MODE:-}"
EXTERNAL_VLLM_BASE_URL="${VLLM_BASE_URL:-}"
EXTERNAL_VLLM_PORT="${VLLM_PORT:-}"
EXTERNAL_VLLM_MODEL_ID_QWEN_14B_LLM="${VLLM_MODEL_ID_QWEN_14B_LLM:-}"

for arg in "$@"; do
    case "$arg" in
        --from-launcher)
            FROM_LAUNCHER=true
            ;;
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

if [ "$FROM_LAUNCHER" = false ]; then
    exec bash "$SCRIPT_DIR/launcher.sh" --target container $([ "$ATTACH_TMUX" = false ] && echo "--no-attach")
fi

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

if [ -f "$RUNTIME_ENV_FILE" ]; then
    echo -e "${BLUE}Загрузка runtime overrides $RUNTIME_ENV_FILE${NC}"
    set -a
    source "$RUNTIME_ENV_FILE"
    set +a
fi

if [ -n "$EXTERNAL_BACKEND_MODE" ]; then
    BACKEND_MODE="$EXTERNAL_BACKEND_MODE"
fi
if [ -n "$EXTERNAL_VLLM_BASE_URL" ]; then
    VLLM_BASE_URL="$EXTERNAL_VLLM_BASE_URL"
fi
if [ -n "$EXTERNAL_VLLM_PORT" ]; then
    VLLM_PORT="$EXTERNAL_VLLM_PORT"
fi
if [ -n "$EXTERNAL_VLLM_MODEL_ID_QWEN_14B_LLM" ]; then
    VLLM_MODEL_ID_QWEN_14B_LLM="$EXTERNAL_VLLM_MODEL_ID_QWEN_14B_LLM"
fi

BACKEND_MODE_RESOLVED="${BACKEND_MODE:-llama-cpp-python}"
VLLM_ENABLED=false
COMPOSE_PROFILE_ARGS=("--profile" "backend")
COMPOSE_LOG_TARGETS=("agent-api" "document-server" "legal-server" "ums" "chainlit")
COMPOSE_PROFILE_TEXT="--profile backend"
COMPOSE_SERVICES_TEXT="agent-api document-server legal-server ums chainlit"
VLLM_PORT="${VLLM_PORT:-8101}"
VLLM_SERVED_MODEL_ID="${VLLM_MODEL_ID_QWEN_14B_LLM:-qwen-14b-llm}"

if [ "$BACKEND_MODE_RESOLVED" = "vllm" ]; then
    VLLM_ENABLED=true
    COMPOSE_PROFILE_ARGS=("--profile" "backend" "--profile" "vllm")
    COMPOSE_LOG_TARGETS=("agent-api" "document-server" "legal-server" "ums" "chainlit" "vllm")
    COMPOSE_PROFILE_TEXT="--profile backend --profile vllm"
    COMPOSE_SERVICES_TEXT="agent-api document-server legal-server ums chainlit vllm"
fi

if [ "${AGENT_NAVIGATOR_TEST_MODE:-0}" = "1" ]; then
    echo "run_all:test-mode backend_mode=$BACKEND_MODE_RESOLVED compose_profiles=$COMPOSE_PROFILE_TEXT compose_services=$COMPOSE_SERVICES_TEXT attach_tmux=$ATTACH_TMUX"
    exit 0
fi

CONDA_ENV="container-compose"

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
echo -e "${YELLOW}Завершение существующих tmux/runtime/docker процессов...${NC}"
"$SCRIPT_DIR/stop_all.sh" >/dev/null 2>&1 || true

tmux new-session -d -s "$SESSION_NAME" -x 200 -y 50

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

    if [ "$VLLM_ENABLED" = true ]; then
        printf "  %-20s " "vLLM runtime"
        while [ $elapsed -lt $timeout ]; do
            local models=$(curl -sf "http://localhost:$VLLM_PORT/v1/models" 2>/dev/null || true)
            if [ -n "$models" ] && echo "$models" | grep -q "\"$VLLM_SERVED_MODEL_ID\""; then
                echo -e "${GREEN}✓ upstream готов${NC}"
                return 0
            fi
            sleep 3
            elapsed=$((elapsed + 3))
        done
        echo -e "${YELLOW}⚠ upstream не подтвердил $VLLM_SERVED_MODEL_ID за ${timeout}с${NC}"
        return 0
    fi

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

echo -e "${GREEN}Запуск backend services через Docker Compose...${NC}"
COMPOSE_ENV_PREFIX="CHAINLIT_UMS_URL=http://ums:$UMS_PORT CHAINLIT_DOC_SERVER_URL=http://document-server:$DOC_PORT CHAINLIT_LEGAL_SERVER_URL=http://legal-server:$LEGAL_PORT CHAINLIT_MCP_DOCUMENT_SERVER_URL=http://document-server:$DOC_PORT CHAINLIT_MCP_LEGAL_SERVER_URL=http://legal-server:$LEGAL_PORT"
CHAINLIT_COMPOSE_CMD="cd $PROJECT_ROOT && env $COMPOSE_ENV_PREFIX docker compose ${COMPOSE_PROFILE_ARGS[*]} up -d ${COMPOSE_LOG_TARGETS[*]} && env $COMPOSE_ENV_PREFIX docker compose ${COMPOSE_PROFILE_ARGS[*]} logs -f ${COMPOSE_LOG_TARGETS[*]}"
start_tmux_window "backend-compose" "$CHAINLIT_COMPOSE_CMD"

# 1) Document Server
wait_for_service "Document Server" "$DOC_PORT" "/health" 60 || SERVICES_OK=false

# 2) Legal Server
wait_for_service "Legal Server" "$LEGAL_PORT" "/health" 60 || SERVICES_OK=false

# 3) UMS
wait_for_service "UMS" "$UMS_PORT" "/health" 120 || SERVICES_OK=false

echo ""
echo -e "${YELLOW}Ожидание загрузки модели Qwen LLM (до 3 мин)...${NC}"
wait_for_model 180 || SERVICES_OK=false

# 4) Agent API
wait_for_service "Agent API" "$AGENT_PORT" "/health" 30 || SERVICES_OK=false

# 5) Chainlit UI (Docker)
if [ "$VLLM_ENABLED" = true ]; then
    wait_for_service "vLLM" "$VLLM_PORT" "/health" 180 || SERVICES_OK=false
fi
wait_for_service "Chainlit UI" "$CHAINLIT_PORT" "/" 60 || SERVICES_OK=false

# 6) Monitor/Logs
echo -e "${GREEN}Открытие окна мониторинга...${NC}"
start_tmux_window "monitor" "cd $PROJECT_ROOT && echo -e '${GREEN}Compose backend stack запущен.${NC}\nДля остановки используйте ${YELLOW}docker compose down${NC} или завершите tmux сессию.' && (docker compose ps || true) && (htop 2>/dev/null || top)"

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
echo -e "${GREEN}║${NC} Runtime target:   ${YELLOW}container-compose${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Chainlit UI:     ${YELLOW}http://localhost:$CHAINLIT_PORT${NC}  (login: ${CHAINLIT_ADMIN_USER:-admin}, password from env)"
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
