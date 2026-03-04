#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LAUNCHER_SCRIPT="$SCRIPT_DIR/run_all.sh"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

if [ ! -x "$LAUNCHER_SCRIPT" ]; then
    echo -e "${RED}Ошибка: launcher-скрипт не найден или не исполняемый: $LAUNCHER_SCRIPT${NC}"
    exit 1
fi

echo -e "${GREEN}Запуск launcher/preflight в тестовом режиме (--mode default --check-only)...${NC}"
"$LAUNCHER_SCRIPT" --mode default --check-only

# -------------------------------------------
# Health-check блок (fail-fast)
# -------------------------------------------

echo ""
echo -e "${YELLOW}=== System Health Check ===${NC}"

echo "Docker Containers:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | rg -i "chainlit|open-webui" || {
    echo -e "${RED}Ошибка: UI контейнер (Chainlit/Open WebUI) не найден среди запущенных.${NC}"
    exit 1
}

check_http() {
    local name="$1"
    local url="$2"

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" "$url" || true)

    if [ "$http_code" -ge 200 ] 2>/dev/null && [ "$http_code" -lt 400 ] 2>/dev/null; then
        echo -e "${GREEN}${name}: HTTP ${http_code}${NC}"
    else
        echo -e "${RED}${name}: HTTP ${http_code} (FAIL)${NC}"
        exit 1
    fi
}

echo ""
echo "Backend Services Check:"
check_http "Agent API" "http://localhost:8000/health"
check_http "Doc Server" "http://localhost:8001/health"
check_http "Legal Server" "http://localhost:8002/health"
check_http "UMS" "http://localhost:8090/health"

echo ""
echo -e "${GREEN}=== Test Complete: all checks passed ===${NC}"
echo "Для просмотра логов: tmux attach -t agent-navigator"
