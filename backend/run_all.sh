#!/bin/bash

# ===========================================
# Скрипт для запуска всех компонентов системы
# Agent Navigator Pro
# ===========================================
# Использует tmux для управления несколькими процессами

set -e

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# PROJECT_ROOT - это родительская директория backend
PROJECT_ROOT="$(dirname "$BACKEND_DIR")"
ENV_FILE="$BACKEND_DIR/.env"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Запуск системы Agent Navigator Pro ===${NC}"

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

# -------------------------------------------
# Определение Conda окружения
# -------------------------------------------
CONDA_ENV="${CONDA_ENV:-diploma_llm}"
CONDA_SH_PATH=""

# Функция для поиска и активации conda
find_conda() {
    # Попытка 1: Стандартные пути
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
    
    # Попытка 2: Через which conda
    if command -v conda &> /dev/null; then
        # Если conda в PATH, попробуем найти conda.sh через него
        local conda_bin=$(which conda)
        local conda_root=$(dirname $(dirname "$conda_bin"))
        if [ -f "$conda_root/etc/profile.d/conda.sh" ]; then
            CONDA_SH_PATH="$conda_root/etc/profile.d/conda.sh"
            source "$CONDA_SH_PATH"
            conda activate "$CONDA_ENV" 2>/dev/null && return 0
        fi
        
        # Fallback: eval hook (менее надежно в tmux send-keys)
        eval "$(conda shell.bash hook)"
        conda activate "$CONDA_ENV" 2>/dev/null && return 0
    fi
    
    return 1
}

echo -e "${YELLOW}Активация Conda окружения: $CONDA_ENV...${NC}"
if ! find_conda; then
    echo -e "${RED}Ошибка: Не удалось активировать conda окружение $CONDA_ENV${NC}"
    echo -e "${YELLOW}Попробуйте активировать вручную: conda activate $CONDA_ENV${NC}"
    exit 1
fi

echo -e "${GREEN}Conda окружение активировано: $CONDA_ENV${NC}"
if [ -n "$CONDA_SH_PATH" ]; then
    echo -e "${BLUE}Используется conda.sh: $CONDA_SH_PATH${NC}"
fi

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

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo -e "${YELLOW}Завершение существующей сессии...${NC}"
    tmux kill-session -t "$SESSION_NAME"
fi

tmux new-session -d -s "$SESSION_NAME" -x 200 -y 50

# Команда активации для tmux окон
if [ -n "$CONDA_SH_PATH" ]; then
    # Самый надежный способ: source conda.sh
    ACTIVATE_CMD="source $CONDA_SH_PATH && conda activate $CONDA_ENV"
else
    # Fallback, но это может вызвать проблемы с кавычками в tmux send-keys
    ACTIVATE_CMD="eval \"\\$(conda shell.bash hook)\" && conda activate $CONDA_ENV"
fi

# Порты из .env или значения по умолчанию
AGENT_PORT="${AGENT_API_PORT:-8000}"
DOC_PORT=8001
LEGAL_PORT=8002
UMS_PORT="${UMS_PORT:-8090}"
WEBUI_PORT=3000

# -------------------------------------------
# Запуск сервисов
# -------------------------------------------

# Окно 0: webui (Docker)
tmux rename-window -t "$SESSION_NAME:0" "webui"
echo -e "${GREEN}Запуск Open WebUI (Docker) на порту $WEBUI_PORT...${NC}"
# Используем -d и --force-recreate для чистого запуска, затем стримим логи
tmux send-keys -t "$SESSION_NAME:webui" "cd $PROJECT_ROOT && docker compose up -d --force-recreate && docker compose logs -f" Enter

# Окно 1: Agent API (Main)
echo -e "${GREEN}Запуск Agent API на порту $AGENT_PORT...${NC}"
tmux new-window -t "$SESSION_NAME" -n "agent-api"
tmux send-keys -t "$SESSION_NAME:agent-api" "cd $BACKEND_DIR/orchestrator && $ACTIVATE_CMD && python agent_api.py 2>&1 | tee agent-api.log" Enter
sleep 2

# Окно 2: Document Server
echo -e "${GREEN}Запуск Document Server на порту $DOC_PORT...${NC}"
tmux new-window -t "$SESSION_NAME" -n "doc-server"
tmux send-keys -t "$SESSION_NAME:doc-server" "cd $BACKEND_DIR/services/document_server && $ACTIVATE_CMD && uvicorn mcp_document_server:app --host 0.0.0.0 --port $DOC_PORT 2>&1 | tee doc-server.log" Enter
sleep 2

# Окно 3: Legal Server
echo -e "${GREEN}Запуск Legal Server на порту $LEGAL_PORT...${NC}"
tmux new-window -t "$SESSION_NAME" -n "legal-server"
tmux send-keys -t "$SESSION_NAME:legal-server" "cd $BACKEND_DIR/services/legal_server && $ACTIVATE_CMD && uvicorn mcp_legal_server:app --host 0.0.0.0 --port $LEGAL_PORT 2>&1 | tee legal-server.log" Enter
sleep 2

# Окно 4: UMS (Unified Model Server)
echo -e "${GREEN}Запуск Unified Model Server на порту $UMS_PORT...${NC}"
tmux new-window -t "$SESSION_NAME" -n "ums"
tmux send-keys -t "$SESSION_NAME:ums" "cd $BACKEND_DIR/services/model_manager && $ACTIVATE_CMD && python unified_model_server.py 2>&1 | tee ums.log" Enter

# Окно 5: Monitor/Logs
echo -e "${GREEN}Открытие окна мониторинга...${NC}"
tmux new-window -t "$SESSION_NAME" -n "monitor"
tmux send-keys -t "$SESSION_NAME:monitor" "cd $BACKEND_DIR && echo -e '${GREEN}Система запущена.${NC}\nДля выхода нажмите ${YELLOW}Ctrl+B${NC} затем ${YELLOW}:kill-session${NC} (это остановит все сервисы, включая Docker).'
" Enter
tmux send-keys -t "$SESSION_NAME:monitor" "htop 2>/dev/null || top" Enter

# -------------------------------------------
# Выводим информацию
# -------------------------------------------
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║            Agent Navigator Pro - Запущен                   ║${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Сессия tmux: ${YELLOW}$SESSION_NAME${NC}"
echo -e "${GREEN}║${NC} Conda окружение: ${YELLOW}$CONDA_ENV${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Open WebUI:      ${YELLOW}http://localhost:$WEBUI_PORT${NC}"
echo -e "${GREEN}║${NC} Agent API:       ${YELLOW}http://localhost:$AGENT_PORT${NC}"
echo -e "${GREEN}║${NC} Document Server: ${YELLOW}http://localhost:$DOC_PORT${NC}"
echo -e "${GREEN}║${NC} Legal Server:    ${YELLOW}http://localhost:$LEGAL_PORT${NC}"
echo -e "${GREEN}║${NC} UMS:             ${YELLOW}http://localhost:$UMS_PORT${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Окна tmux:"
echo -e "${GREEN}║${NC}   - ${YELLOW}webui${NC}:       Docker compose logs"
echo -e "${GREEN}║${NC}   - ${YELLOW}agent-api${NC}:   Главный API агента"
echo -e "${GREEN}║${NC}   - ${YELLOW}doc-server${NC}:  Сервер документов"
echo -e "${GREEN}║${NC}   - ${YELLOW}legal-server${NC}: Юридический сервер"
echo -e "${GREEN}║${NC}   - ${YELLOW}ums${NC}:         Менеджер моделей"
echo -e "${GREEN}║${NC}   - ${YELLOW}monitor${NC}:     Мониторинг системы"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Подключение: ${BLUE}tmux attach-session -t $SESSION_NAME${NC}"
echo -e "${GREEN}║${NC} Завершение:  ${BLUE}tmux kill-session -t $SESSION_NAME${NC}"
echo -e "${GREEN}║${NC} (Это остановит и Docker контейнеры)${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Attach к сессии
tmux attach-session -t "$SESSION_NAME"
