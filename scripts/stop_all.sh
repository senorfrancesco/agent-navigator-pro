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

SESSION_NAME="agent-navigator"

echo -e "${RED}=== Остановка системы Agent Navigator Pro ===${NC}"
echo ""

# -------------------------------------------
# 1. Остановка tmux сессии (все backend-сервисы)
# -------------------------------------------
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo -e "${YELLOW}Завершение tmux сессии '$SESSION_NAME'...${NC}"
    tmux kill-session -t "$SESSION_NAME"
    echo -e "${GREEN}  tmux сессия завершена${NC}"
else
    echo -e "${BLUE}  tmux сессия '$SESSION_NAME' не найдена (уже остановлена)${NC}"
fi

# -------------------------------------------
# 2. Остановка Docker контейнеров (Open WebUI)
# -------------------------------------------
echo -e "${YELLOW}Остановка Docker контейнеров...${NC}"
if command -v docker &> /dev/null && [ -f "$PROJECT_ROOT/docker-compose.yaml" ]; then
    cd "$PROJECT_ROOT"
    docker compose down 2>/dev/null
    echo -e "${GREEN}  Docker контейнеры остановлены${NC}"
else
    echo -e "${BLUE}  Docker compose не найден или не настроен${NC}"
fi

# -------------------------------------------
# 3. Явное завершение llama-server (остаётся в памяти после закрытия tmux)
# -------------------------------------------
LLAMA_PIDS=$(pgrep -f "llama-server" 2>/dev/null)
if [ -n "$LLAMA_PIDS" ]; then
    echo -e "${YELLOW}Завершение llama-server (PID: $LLAMA_PIDS)...${NC}"
    echo "$LLAMA_PIDS" | xargs kill 2>/dev/null
    sleep 1
    # SIGKILL если не завершился
    LLAMA_PIDS=$(pgrep -f "llama-server" 2>/dev/null)
    if [ -n "$LLAMA_PIDS" ]; then
        echo "$LLAMA_PIDS" | xargs kill -9 2>/dev/null
    fi
    echo -e "${GREEN}  llama-server завершён${NC}"
else
    echo -e "${BLUE}  llama-server не запущен${NC}"
fi

# -------------------------------------------
# 4. Подчистка зависших процессов (если tmux не убил)
# -------------------------------------------
PORTS=(8000 8001 8002 8090)
PORT_NAMES=("Agent API" "Document Server" "Legal Server" "UMS")
KILLED=0

for i in "${!PORTS[@]}"; do
    PORT=${PORTS[$i]}
    NAME=${PORT_NAMES[$i]}
    PIDS=$(lsof -ti ":$PORT" 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo -e "${YELLOW}Завершение $NAME (порт $PORT, PID: $PIDS)...${NC}"
        echo "$PIDS" | xargs kill 2>/dev/null
        KILLED=1
    fi
done

if [ "$KILLED" -eq 0 ]; then
    echo -e "${BLUE}  Зависших процессов на портах 8000-8090 не найдено${NC}"
fi

# -------------------------------------------
# Итог
# -------------------------------------------
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Agent Navigator Pro - Остановлен             ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════╝${NC}"
