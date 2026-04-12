#!/bin/bash

# ===========================================
# Stop native dev session (no Docker actions)
# ===========================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_ENV_FILE="${AGENT_NAVIGATOR_BACKEND_ENV_FILE:-$PROJECT_ROOT/backend/.env}"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/utils/env_loader.sh"
ENV_LOADER_PYTHON="$(resolve_env_loader_python || true)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SESSION_NAME="agent-navigator-native"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<EOF
stop_native.sh

Останавливает native tmux/runtime path без воздействия на Docker-контейнеры.

Использование:
  ./scripts/stop_native.sh

Флаги:
  У скрипта нет пользовательских CLI-флагов.
  -h, --help
      Показать эту справку.
EOF
  exit 0
fi

load_runtime_env() {
  load_env_file "$BACKEND_ENV_FILE" "backend env" || return 1
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

load_runtime_env || exit 1

CHAINLIT_PORT="${CHAINLIT_PORT:-3000}"
AGENT_API_PORT="${AGENT_API_PORT:-8000}"
DOC_PORT="${DOC_PORT:-8001}"
LEGAL_PORT="${LEGAL_PORT:-8002}"
UMS_PORT="${UMS_PORT:-8090}"

echo -e "${RED}=== Остановка native сессии Agent Navigator ===${NC}"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo -e "${YELLOW}Завершение tmux сессии '$SESSION_NAME'...${NC}"
  tmux kill-session -t "$SESSION_NAME"
  echo -e "${GREEN}  tmux сессия завершена${NC}"
else
  echo -e "${BLUE}  tmux сессия '$SESSION_NAME' не найдена${NC}"
fi

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

PORTS=("$CHAINLIT_PORT" "$AGENT_API_PORT" "$DOC_PORT" "$LEGAL_PORT" "$UMS_PORT")
NAMES=("Chainlit" "Agent API" "Document Server" "Legal Server" "UMS")
KILLED=0

for i in "${!PORTS[@]}"; do
  port="${PORTS[$i]}"
  name="${NAMES[$i]}"
  pids=$(lsof -ti ":$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    kill_pid_list "$name (порт $port)" "$pids"
    KILLED=1
  fi
done

if [ "$KILLED" -eq 0 ]; then
  echo -e "${BLUE}  Активных native-процессов на сервисных портах не найдено${NC}"
fi

echo ""
echo -e "${GREEN}Native сессия остановлена. Docker контейнеры не затрагивались.${NC}"
