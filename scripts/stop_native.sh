#!/bin/bash

# ===========================================
# Stop native dev session (no Docker actions)
# ===========================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SESSION_NAME="agent-navigator-native"

echo -e "${RED}=== Остановка native сессии Agent Navigator ===${NC}"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo -e "${YELLOW}Завершение tmux сессии '$SESSION_NAME'...${NC}"
  tmux kill-session -t "$SESSION_NAME"
  echo -e "${GREEN}  tmux сессия завершена${NC}"
else
  echo -e "${BLUE}  tmux сессия '$SESSION_NAME' не найдена${NC}"
fi

LLAMA_PIDS=$(pgrep -f "llama-server" 2>/dev/null || true)
if [ -n "$LLAMA_PIDS" ]; then
  echo -e "${YELLOW}Завершение llama-server (PID: $LLAMA_PIDS)...${NC}"
  echo "$LLAMA_PIDS" | xargs kill 2>/dev/null || true
fi

PORTS=(3000 8000 8001 8002 8090)
NAMES=("Chainlit" "Agent API" "Document Server" "Legal Server" "UMS")
KILLED=0

for i in "${!PORTS[@]}"; do
  port="${PORTS[$i]}"
  name="${NAMES[$i]}"
  pids=$(lsof -ti ":$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo -e "${YELLOW}Завершение $name (порт $port, PID: $pids)...${NC}"
    echo "$pids" | xargs kill 2>/dev/null || true
    KILLED=1
  fi
done

if [ "$KILLED" -eq 0 ]; then
  echo -e "${BLUE}  Активных native-процессов на портах 3000/8000/8001/8002/8090 не найдено${NC}"
fi

echo ""
echo -e "${GREEN}Native сессия остановлена. Docker контейнеры не затрагивались.${NC}"
