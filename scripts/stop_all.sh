#!/bin/bash

# ===========================================
# Скрипт для остановки всех компонентов системы
# Agent Navigator Pro
# ===========================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SESSION_NAMES=("agent-navigator" "agent-navigator-native")

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<EOF
stop_all.sh

Останавливает compose/container runtime path и выполняет глобальную зачистку
tmux-сессий и сервисных процессов Agent Navigator Pro.

Использование:
  ./scripts/stop_all.sh

Флаги:
  У скрипта нет пользовательских CLI-флагов.
  -h, --help
      Показать эту справку.
EOF
    exit 0
fi

load_runtime_env() {
    local env_file
    set -a
    for env_file in \
        "$BACKEND_DIR/.env" \
        "$BACKEND_DIR/.env.runtime"
    do
        if [ -f "$env_file" ]; then
            # shellcheck disable=SC1090
            source "$env_file"
        fi
    done
    set +a
}

kill_pid_list() {
    local label="$1"
    local pids="$2"
    if [ -z "$pids" ]; then
        return
    fi
    echo -e "${YELLOW}Завершение $label (PID: $pids)...${NC}"
    echo "$pids" | xargs kill 2>/dev/null || true
    sleep 1
    local survivors=""
    for pid in $pids; do
        if kill -0 "$pid" 2>/dev/null; then
            survivors="${survivors}${pid} "
        fi
    done
    if [ -n "$survivors" ]; then
        echo "$survivors" | xargs kill -9 2>/dev/null || true
    fi
}

kill_matching_processes() {
    local label="$1"
    local pattern="$2"
    local pids
    pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    kill_pid_list "$label" "$pids"
}

load_runtime_env

CHAINLIT_PORT="${CHAINLIT_PORT:-3000}"
AGENT_API_PORT="${AGENT_API_PORT:-8000}"
DOC_PORT="${DOC_PORT:-8001}"
LEGAL_PORT="${LEGAL_PORT:-8002}"
UMS_PORT="${UMS_PORT:-8090}"

echo -e "${RED}=== Остановка системы Agent Navigator Pro ===${NC}"
echo ""

# -------------------------------------------
# 1. Остановка tmux сессии (все backend-сервисы)
# -------------------------------------------
for SESSION_NAME in "${SESSION_NAMES[@]}"; do
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo -e "${YELLOW}Завершение tmux сессии '$SESSION_NAME'...${NC}"
        tmux kill-session -t "$SESSION_NAME"
        echo -e "${GREEN}  tmux сессия '$SESSION_NAME' завершена${NC}"
    else
        echo -e "${BLUE}  tmux сессия '$SESSION_NAME' не найдена (уже остановлена)${NC}"
    fi
done

# -------------------------------------------
# 2. Остановка Docker контейнеров (Open WebUI)
# -------------------------------------------
echo -e "${YELLOW}Остановка Docker контейнеров...${NC}"
if command -v docker &> /dev/null && [ -f "$PROJECT_ROOT/docker-compose.yaml" ]; then
    cd "$PROJECT_ROOT"
    if docker compose down >/dev/null 2>&1; then
        echo -e "${GREEN}  Docker контейнеры остановлены${NC}"
    else
        echo -e "${YELLOW}  Docker compose down завершился с ошибкой, проверьте docker compose ps${NC}"
    fi
else
    echo -e "${BLUE}  Docker compose не найден или не настроен${NC}"
fi

# -------------------------------------------
# 3. Явное завершение runtime-процессов UMS
# -------------------------------------------
kill_matching_processes "UMS" "services/model_manager/unified_model_server.py"
kill_matching_processes "UMS" "$PROJECT_ROOT/backend/services/model_manager/unified_model_server.py"
kill_matching_processes "llama-server runtime" "llama-server"
kill_matching_processes "llama-server runtime" "llama-server.*$PROJECT_ROOT/backend/models/"
kill_matching_processes "embedding runtime" "services/model_manager/st_server.py"
kill_matching_processes "embedding runtime" "$PROJECT_ROOT/backend/services/model_manager/st_server.py"
kill_matching_processes "Document Server" "uvicorn mcp_document_server:app --host 0.0.0.0 --port $DOC_PORT"
kill_matching_processes "Legal Server" "uvicorn mcp_legal_server:app --host 0.0.0.0 --port $LEGAL_PORT"
kill_matching_processes "Agent API" "python agent_api.py"
kill_matching_processes "Chainlit" "chainlit run chainlit_app.py --host 0.0.0.0 --port $CHAINLIT_PORT"

# -------------------------------------------
# 4. Подчистка зависших процессов (если tmux не убил)
# -------------------------------------------
PORTS=("$CHAINLIT_PORT" "$AGENT_API_PORT" "$DOC_PORT" "$LEGAL_PORT" "$UMS_PORT")
PORT_NAMES=("Chainlit" "Agent API" "Document Server" "Legal Server" "UMS")
KILLED=0

for i in "${!PORTS[@]}"; do
    PORT=${PORTS[$i]}
    NAME=${PORT_NAMES[$i]}
    PIDS=$(lsof -ti ":$PORT" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        kill_pid_list "$NAME (порт $PORT)" "$PIDS"
        KILLED=1
    fi
done

if [ "$KILLED" -eq 0 ]; then
    echo -e "${BLUE}  Зависших процессов на сервисных портах не найдено${NC}"
fi

# -------------------------------------------
# Итог
# -------------------------------------------
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Agent Navigator Pro - Остановлен             ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════╝${NC}"
