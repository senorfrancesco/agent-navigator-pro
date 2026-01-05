#!/bin/bash

# Скрипт для запуска всех компонентов системы на одной машине
# Использует tmux для управления несколькими процессами

set -e

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$BACKEND_DIR/venv"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Запуск системы Agent Navigator Pro ===${NC}"

# Проверяем наличие виртуального окружения
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Создание виртуального окружения...${NC}"
    python3.11 -m venv "$VENV_DIR"
fi

# Активируем виртуальное окружение
source "$VENV_DIR/bin/activate"

# Проверяем наличие tmux
if ! command -v tmux &> /dev/null; then
    echo -e "${YELLOW}tmux не найден. Установка...${NC}"
    sudo apt-get update && sudo apt-get install -y tmux
fi

# Создаем новую tmux сессию
SESSION_NAME="agent-navigator"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo -e "${YELLOW}Завершение существующей сессии...${NC}"
    tmux kill-session -t "$SESSION_NAME"
fi

tmux new-session -d -s "$SESSION_NAME" -x 200 -y 50

# Окно 1: Agent API (Main)
echo -e "${GREEN}Запуск Agent API на порту 8000...${NC}"
tmux new-window -t "$SESSION_NAME" -n "agent-api"
tmux send-keys -t "$SESSION_NAME:agent-api" "cd $BACKEND_DIR/orchestrator && source $VENV_DIR/bin/activate && uvicorn agent_api:app --host 0.0.0.0 --port 8000" Enter
sleep 2

# Окно 2: Document Server
echo -e "${GREEN}Запуск Document Server на порту 8001...${NC}"
tmux new-window -t "$SESSION_NAME" -n "doc-server"
tmux send-keys -t "$SESSION_NAME:doc-server" "cd $BACKEND_DIR/services/document_server && source $VENV_DIR/bin/activate && uvicorn mcp_document_server:app --host 0.0.0.0 --port 8001" Enter
sleep 2

# Окно 3: Legal Server
echo -e "${GREEN}Запуск Legal Server на порту 8002...${NC}"
tmux new-window -t "$SESSION_NAME" -n "legal-server"
tmux send-keys -t "$SESSION_NAME:legal-server" "cd $BACKEND_DIR/services/legal_server && source $VENV_DIR/bin/activate && uvicorn mcp_legal_server:app --host 0.0.0.0 --port 8002" Enter
sleep 2

# Окно 4: UMS (Optional)
echo -e "${GREEN}Запуск Unified Model Server на порту 8090...${NC}"
tmux new-window -t "$SESSION_NAME" -n "ums"
tmux send-keys -t "$SESSION_NAME:ums" "cd $BACKEND_DIR/services/model_manager && source $VENV_DIR/bin/activate && python3.11 unified_model_server.py" Enter

# Окно 5: Monitor/Logs
echo -e "${GREEN}Открытие окна мониторинга...${NC}"
tmux new-window -t "$SESSION_NAME" -n "monitor"
tmux send-keys -t "$SESSION_NAME:monitor" "cd $BACKEND_DIR && echo 'Система запущена. Используйте Ctrl+C для остановки.' && sleep infinity" Enter

# Выводим информацию
echo -e "${GREEN}=== Система Запущена ===${NC}"
echo -e "Имя сессии: ${YELLOW}$SESSION_NAME${NC}"
echo -e "Agent API: ${YELLOW}http://localhost:8000${NC}"
echo -e "Document Server: ${YELLOW}http://localhost:8001${NC}"
echo -e "Legal Server: ${YELLOW}http://localhost:8002${NC}"
echo -e "UMS: ${YELLOW}http://localhost:8090${NC}"
echo ""
echo -e "Доступные окна tmux:"
echo -e "  - ${YELLOW}agent-api${NC}: Главный API"
echo -e "  - ${YELLOW}doc-server${NC}: Сервер документов"
echo -e "  - ${YELLOW}legal-server${NC}: Юридический сервер"
echo -e "  - ${YELLOW}ums${NC}: Менеджер моделей"
echo -e "  - ${YELLOW}monitor${NC}: Мониторинг/Логи"
echo ""
echo -e "Для подключения к сессии: ${YELLOW}tmux attach-session -t $SESSION_NAME${NC}"
echo -e "Для завершения сессии: ${YELLOW}tmux kill-session -t $SESSION_NAME${NC}"
echo ""

# Attach к сессии
tmux attach-session -t "$SESSION_NAME"
