#!/bin/bash

# Скрипт для запуска всех компонентов системы на одной машине
# Использует tmux для управления несколькими процессами

set -e

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# VENV_DIR="$BACKEND_DIR/venv"
CONDA_ENV="diploma_llm"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Запуск системы Agent Navigator Pro ===${NC}"

# --- Virtual Environment (Закомментировано по просьбе пользователя) ---
# if [ ! -d "$VENV_DIR" ]; then
#     echo -e "${YELLOW}Создание виртуального окружения...${NC}"
#     python3.11 -m venv "$VENV_DIR"
# fi
# source "$VENV_DIR/bin/activate"
# -----------------------------------------------------------------------

# Активация Conda окружения
# Примечание: алиасы из .bashrc не работают в скриптах, используем прямую активацию
echo -e "${YELLOW}Активация Conda окружения: $CONDA_ENV...${NC}"
# Пытаемся найти conda и активировать окружение
CONDA_BASE=$(conda info --base 2>/dev/null || echo "$HOME/anaconda3")
if [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
    source "$CONDA_BASE/etc/profile.d/conda.sh"
    conda activate "$CONDA_ENV"
else
    # Если conda.sh не найден, пробуем просто вызвать conda activate
    # (может не сработать в некоторых оболочках без инициализации)
    conda activate "$CONDA_ENV" || echo -e "${RED}Ошибка: Не удалось активировать conda окружение $CONDA_ENV${NC}"
fi

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

# Команда активации для tmux окон
# Используем bash -i для загрузки .bashrc и алиасов, либо прямую активацию
ACTIVATE_CMD="source $CONDA_BASE/etc/profile.d/conda.sh && conda activate $CONDA_ENV"

# Окно 1: Agent API (Main)
echo -e "${GREEN}Запуск Agent API на порту 8000...${NC}"
tmux new-window -t "$SESSION_NAME" -n "agent-api"
tmux send-keys -t "$SESSION_NAME:agent-api" "cd $BACKEND_DIR/orchestrator && $ACTIVATE_CMD && uvicorn agent_api:app --host 0.0.0.0 --port 8000" Enter
sleep 2

# Окно 2: Document Server
echo -e "${GREEN}Запуск Document Server на порту 8001...${NC}"
tmux new-window -t "$SESSION_NAME" -n "doc-server"
tmux send-keys -t "$SESSION_NAME:doc-server" "cd $BACKEND_DIR/services/document_server && $ACTIVATE_CMD && uvicorn mcp_document_server:app --host 0.0.0.0 --port 8001" Enter
sleep 2

# Окно 3: Legal Server
echo -e "${GREEN}Запуск Legal Server на порту 8002...${NC}"
tmux new-window -t "$SESSION_NAME" -n "legal-server"
tmux send-keys -t "$SESSION_NAME:legal-server" "cd $BACKEND_DIR/services/legal_server && $ACTIVATE_CMD && uvicorn mcp_legal_server:app --host 0.0.0.0 --port 8002" Enter
sleep 2

# Окно 4: UMS (Optional)
echo -e "${GREEN}Запуск Unified Model Server на порту 8090...${NC}"
tmux new-window -t "$SESSION_NAME" -n "ums"
tmux send-keys -t "$SESSION_NAME:ums" "cd $BACKEND_DIR/services/model_manager && $ACTIVATE_CMD && python3.11 unified_model_server.py" Enter

# Окно 5: Monitor/Logs
echo -e "${GREEN}Открытие окна мониторинга...${NC}"
tmux new-window -t "$SESSION_NAME" -n "monitor"
tmux send-keys -t "$SESSION_NAME:monitor" "cd $BACKEND_DIR && echo 'Система запущена. Используйте Ctrl+C для остановки.' && sleep infinity" Enter

# Выводим информацию
echo -e "${GREEN}=== Система Запущена ===${NC}"
echo -e "Имя сессии: ${YELLOW}$SESSION_NAME${NC}"
echo -e "Conda окружение: ${YELLOW}$CONDA_ENV${NC}"
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
