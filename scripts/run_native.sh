#!/bin/bash

# ===========================================
# Native launch (host-only, no Docker)
# Agent Navigator Pro v3.0 (Chainlit + services)
# ===========================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
ENV_FILE="$BACKEND_DIR/.env"
NATIVE_ENV_FILE="$BACKEND_DIR/.env.native"
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

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}=== Native запуск Agent Navigator Pro (без Docker) ===${NC}"

if [ -f "$ENV_FILE" ]; then
  echo -e "${BLUE}Загрузка $ENV_FILE${NC}"
  set -a
  source "$ENV_FILE"
  set +a
fi

if [ -f "$NATIVE_ENV_FILE" ]; then
  echo -e "${BLUE}Загрузка override $NATIVE_ENV_FILE${NC}"
  set -a
  source "$NATIVE_ENV_FILE"
  set +a
else
  echo -e "${YELLOW}Файл $NATIVE_ENV_FILE не найден. Можно создать из .env.native.example${NC}"
fi

CONDA_ENV="${CONDA_ENV:-diploma_llm}"
CONDA_SH_PATH=""

find_conda() {
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

  if command -v conda &> /dev/null; then
    local conda_bin
    conda_bin=$(which conda)
    local conda_root
    conda_root=$(dirname "$(dirname "$conda_bin")")
    if [ -f "$conda_root/etc/profile.d/conda.sh" ]; then
      CONDA_SH_PATH="$conda_root/etc/profile.d/conda.sh"
      source "$CONDA_SH_PATH"
      conda activate "$CONDA_ENV" 2>/dev/null && return 0
    fi
    eval "$(conda shell.bash hook)"
    conda activate "$CONDA_ENV" 2>/dev/null && return 0
  fi

  return 1
}

echo -e "${YELLOW}Активация conda окружения: $CONDA_ENV${NC}"
if ! find_conda; then
  echo -e "${RED}Не удалось активировать conda env: $CONDA_ENV${NC}"
  exit 1
fi

if ! command -v tmux &> /dev/null; then
  echo -e "${RED}tmux не найден. Установите tmux и повторите запуск.${NC}"
  exit 1
fi

SESSION_NAME="agent-navigator-native"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo -e "${YELLOW}Завершение существующей native tmux сессии...${NC}"
  tmux kill-session -t "$SESSION_NAME"
fi

LLAMA_PIDS=$(pgrep -f "llama-server" 2>/dev/null || true)
if [ -n "$LLAMA_PIDS" ]; then
  echo -e "${YELLOW}Завершение llama-server перед запуском (PID: $LLAMA_PIDS)...${NC}"
  echo "$LLAMA_PIDS" | xargs kill 2>/dev/null || true
  sleep 1
fi

tmux new-session -d -s "$SESSION_NAME" -x 220 -y 60

if [ -n "$CONDA_SH_PATH" ]; then
  ACTIVATE_CMD="source $CONDA_SH_PATH && conda activate $CONDA_ENV"
else
  ACTIVATE_CMD="eval \"\$(conda shell.bash hook)\" && conda activate $CONDA_ENV"
fi

AGENT_PORT="${AGENT_API_PORT:-8000}"
DOC_PORT="${DOC_PORT:-8001}"
LEGAL_PORT="${LEGAL_PORT:-8002}"
UMS_PORT="${UMS_PORT:-8090}"
CHAINLIT_PORT="${CHAINLIT_PORT:-3000}"

MCP_DOCUMENT_SERVER_URL="${MCP_DOCUMENT_SERVER_URL:-http://localhost:8001}"
MCP_LEGAL_SERVER_URL="${MCP_LEGAL_SERVER_URL:-http://localhost:8002}"
UMS_URL="${UMS_URL:-http://localhost:8090}"
UPLOADS_DIR="${UPLOADS_DIR:-$BACKEND_DIR/open_webui_uploads}"
HOST_UPLOADS_DIR="${HOST_UPLOADS_DIR:-}"
CHAINLIT_DB_URL="${CHAINLIT_DB_URL:-sqlite+aiosqlite:///$BACKEND_DIR/.data/chainlit.db}"
CHAINLIT_ENABLE_DATA_LAYER="${CHAINLIT_ENABLE_DATA_LAYER:-true}"

mkdir -p "$UPLOADS_DIR"
mkdir -p "$BACKEND_DIR/.data"

wait_for_service() {
  local name="$1"
  local port="$2"
  local endpoint="${3:-/health}"
  local timeout="${4:-60}"
  local elapsed=0

  printf "  %-20s " "$name (:$port)"
  while [ $elapsed -lt $timeout ]; do
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port$endpoint" 2>/dev/null || true)
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

wait_for_model() {
  local timeout="${1:-180}"
  local elapsed=0
  printf "  %-20s " "Qwen-14B LLM"
  while [ $elapsed -lt $timeout ]; do
    local status
    status=$(curl -sf "http://localhost:$UMS_PORT/status" 2>/dev/null || true)
    if [ -n "$status" ] && echo "$status" | grep -q '"qwen-14b-llm"'; then
      echo -e "${GREEN}✓ загружена${NC}"
      return 0
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
echo -e "${GREEN}Запуск UMS на порту $UMS_PORT...${NC}"
start_tmux_window "ums" "cd $BACKEND_DIR/services/model_manager && $ACTIVATE_CMD && python unified_model_server.py 2>&1 | tee ums.log"
wait_for_service "UMS" "$UMS_PORT" "/health" 90 || SERVICES_OK=false

echo ""
echo -e "${YELLOW}Ожидание загрузки модели Qwen LLM (до 3 мин)...${NC}"
wait_for_model 180 || SERVICES_OK=false

# 4) Agent API
echo -e "${GREEN}Запуск Agent API на порту $AGENT_PORT...${NC}"
start_tmux_window "agent-api" "cd $BACKEND_DIR/orchestrator && $ACTIVATE_CMD && export MCP_DOCUMENT_SERVER_URL='$MCP_DOCUMENT_SERVER_URL' MCP_LEGAL_SERVER_URL='$MCP_LEGAL_SERVER_URL' UMS_URL='$UMS_URL' UPLOADS_DIR='$UPLOADS_DIR' HOST_UPLOADS_DIR='$HOST_UPLOADS_DIR' CHAINLIT_DB_URL='$CHAINLIT_DB_URL' && python agent_api.py 2>&1 | tee agent-api.log"
wait_for_service "Agent API" "$AGENT_PORT" "/health" 30 || SERVICES_OK=false

# 5) Chainlit (native)
echo -e "${GREEN}Запуск Chainlit (native) на порту $CHAINLIT_PORT...${NC}"
start_tmux_window "chainlit" "cd $BACKEND_DIR/orchestrator && $ACTIVATE_CMD && export MCP_DOCUMENT_SERVER_URL='$MCP_DOCUMENT_SERVER_URL' MCP_LEGAL_SERVER_URL='$MCP_LEGAL_SERVER_URL' UMS_URL='$UMS_URL' UPLOADS_DIR='$UPLOADS_DIR' HOST_UPLOADS_DIR='$HOST_UPLOADS_DIR' CHAINLIT_DB_URL='$CHAINLIT_DB_URL' CHAINLIT_ENABLE_DATA_LAYER='$CHAINLIT_ENABLE_DATA_LAYER' && chainlit run chainlit_app.py --host 0.0.0.0 --port $CHAINLIT_PORT 2>&1 | tee chainlit.log"
wait_for_service "Chainlit UI" "$CHAINLIT_PORT" "/" 60 || SERVICES_OK=false

# 6) Monitor
start_tmux_window "monitor" "cd $BACKEND_DIR && echo 'Native mode: logs in tmux windows' && (htop 2>/dev/null || top)"

if [ "$SERVICES_OK" = true ]; then
  SYSTEM_STATUS="${GREEN}ГОТОВА К РАБОТЕ${NC}"
else
  SYSTEM_STATUS="${YELLOW}ЗАПУЩЕНА (частично)${NC}"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║      Agent Navigator Pro v3.0 (Native Dev)               ║${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Статус:          $SYSTEM_STATUS"
echo -e "${GREEN}║${NC} tmux сессия:     ${YELLOW}$SESSION_NAME${NC}"
echo -e "${GREEN}║${NC} Chainlit UI:     ${YELLOW}http://localhost:$CHAINLIT_PORT${NC}"
echo -e "${GREEN}║${NC} Agent API:       ${YELLOW}http://localhost:$AGENT_PORT${NC}"
echo -e "${GREEN}║${NC} Document Server: ${YELLOW}http://localhost:$DOC_PORT${NC}"
echo -e "${GREEN}║${NC} Legal Server:    ${YELLOW}http://localhost:$LEGAL_PORT${NC}"
echo -e "${GREEN}║${NC} UMS:             ${YELLOW}http://localhost:$UMS_PORT${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Подключение: ${BLUE}tmux attach-session -t $SESSION_NAME${NC}"
echo -e "${GREEN}║${NC} Остановка:    ${BLUE}./scripts/stop_native.sh${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"

if [ "$ATTACH_TMUX" = true ]; then
  tmux attach-session -t "$SESSION_NAME"
else
  echo -e "${YELLOW}tmux attach пропущен (--no-attach).${NC}"
fi
