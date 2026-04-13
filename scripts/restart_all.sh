#!/bin/bash

# ===========================================
# Скрипт для перезапуска всех компонентов системы
# llm-tools-platform
# ===========================================
# Останавливает все сервисы, ждёт освобождения портов, запускает заново.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"

# Цвета
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<EOF
restart_all.sh

Перезапускает compose/runtime стек: сначала вызывает stop_all.sh, затем ждёт
освобождения сервисных портов и запускает run_all.sh.

Использование:
  ./scripts/restart_all.sh

Флаги:
  У скрипта нет пользовательских флагов.
  -h, --help
      Показать эту справку.
EOF
    exit 0
fi

echo -e "${GREEN}=== Перезапуск llm-tools-platform ===${NC}"
echo ""

# 1. Остановка
echo -e "${YELLOW}--- Фаза 1: Остановка ---${NC}"
"$SCRIPT_DIR/stop_all.sh"

# 2. Ожидание освобождения портов
echo ""
echo -e "${YELLOW}--- Фаза 2: Ожидание освобождения портов ---${NC}"
PORTS=(8000 8001 8002 8090)
MAX_WAIT=10
for PORT in "${PORTS[@]}"; do
    WAITED=0
    while lsof -ti ":$PORT" &>/dev/null && [ "$WAITED" -lt "$MAX_WAIT" ]; do
        sleep 1
        WAITED=$((WAITED + 1))
    done
    if [ "$WAITED" -gt 0 ]; then
        echo -e "  Порт $PORT освободился за ${WAITED}с"
    fi
done
echo -e "${GREEN}  Порты свободны${NC}"

# 3. Запуск
echo ""
echo -e "${YELLOW}--- Фаза 3: Запуск ---${NC}"
"$SCRIPT_DIR/run_all.sh"
