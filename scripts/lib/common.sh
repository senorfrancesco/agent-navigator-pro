#!/bin/bash

# Общие функции запуска Agent Navigator Pro

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
BACKEND_DIR="$PROJECT_ROOT/backend"
ENV_FILE="$BACKEND_DIR/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SESSION_NAME="${SESSION_NAME:-agent-navigator}"
CONDA_ENV="${CONDA_ENV:-diploma_llm}"
CONDA_SH_PATH=""

AGENT_PORT="${AGENT_API_PORT:-8000}"
DOC_PORT=8001
LEGAL_PORT=8002
UMS_PORT="${UMS_PORT:-8090}"
UI_PORT=3000

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
            # shellcheck source=/dev/null
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
            # shellcheck source=/dev/null
            source "$CONDA_SH_PATH"
            conda activate "$CONDA_ENV" 2>/dev/null && return 0
        fi

        eval "$(conda shell.bash hook)"
        conda activate "$CONDA_ENV" 2>/dev/null && return 0
    fi

    return 1
}

load_env_file() {
    if [ -f "$ENV_FILE" ]; then
        echo -e "${BLUE}Загрузка переменных из .env...${NC}"
        set -a
        # shellcheck source=/dev/null
        source "$ENV_FILE"
        set +a
    else
        echo -e "${YELLOW}Предупреждение: .env файл не найден. Используются значения по умолчанию.${NC}"
        echo -e "${YELLOW}Создайте .env из .env.example: cp .env.example .env${NC}"
    fi
}

ensure_tmux() {
    if ! command -v tmux &> /dev/null; then
        echo -e "${YELLOW}tmux не найден. Установка...${NC}"
        sudo apt-get update && sudo apt-get install -y tmux
    fi
}

prepare_runtime() {
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

    ensure_tmux
    recreate_tmux_session

    if [ -n "$CONDA_SH_PATH" ]; then
        ACTIVATE_CMD="source $CONDA_SH_PATH && conda activate $CONDA_ENV"
    else
        ACTIVATE_CMD="eval \"\$(conda shell.bash hook)\" && conda activate $CONDA_ENV"
    fi
}

recreate_tmux_session() {
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo -e "${YELLOW}Завершение существующей сессии...${NC}"
        tmux kill-session -t "$SESSION_NAME"
    fi

    local llama_pids
    llama_pids=$(pgrep -f "llama-server" 2>/dev/null || true)
    if [ -n "$llama_pids" ]; then
        echo -e "${YELLOW}Завершение llama-server перед запуском (PID: $llama_pids)...${NC}"
        echo "$llama_pids" | xargs kill 2>/dev/null || true
        sleep 1
        llama_pids=$(pgrep -f "llama-server" 2>/dev/null || true)
        if [ -n "$llama_pids" ]; then
            echo "$llama_pids" | xargs kill -9 2>/dev/null || true
        fi
        echo -e "${GREEN}  llama-server завершён${NC}"
    fi

    tmux new-session -d -s "$SESSION_NAME" -x 200 -y 50
}

start_ui_window() {
    local ui_profile="$1"

    case "$ui_profile" in
        chainlit)
            UI_PORT=3000
            tmux rename-window -t "$SESSION_NAME" "chainlit"
            echo -e "${GREEN}Запуск Chainlit UI (Docker) на порту $UI_PORT...${NC}"
            tmux send-keys -t "$SESSION_NAME:chainlit" "cd $PROJECT_ROOT && docker compose up -d chainlit && docker compose logs -f chainlit" Enter
            ;;
        openwebui)
            UI_PORT=3000
            tmux rename-window -t "$SESSION_NAME" "open-webui"
            echo -e "${GREEN}Запуск Open WebUI (Docker) на порту $UI_PORT...${NC}"
            tmux send-keys -t "$SESSION_NAME:open-webui" "cd $PROJECT_ROOT && docker compose --profile legacy up -d open-webui && docker compose --profile legacy logs -f open-webui" Enter
            ;;
        *)
            echo -e "${RED}Неизвестный UI профиль: $ui_profile${NC}"
            exit 1
            ;;
    esac
}

start_backend_services() {
    echo -e "${GREEN}Запуск Agent API на порту $AGENT_PORT...${NC}"
    tmux new-window -t "$SESSION_NAME" -n "agent-api"
    tmux send-keys -t "$SESSION_NAME:agent-api" "cd $BACKEND_DIR/orchestrator && $ACTIVATE_CMD && python agent_api.py 2>&1 | tee agent-api.log" Enter
    sleep 2

    echo -e "${GREEN}Запуск Document Server на порту $DOC_PORT...${NC}"
    tmux new-window -t "$SESSION_NAME" -n "doc-server"
    tmux send-keys -t "$SESSION_NAME:doc-server" "cd $BACKEND_DIR/services/document_server && $ACTIVATE_CMD && uvicorn mcp_document_server:app --host 0.0.0.0 --port $DOC_PORT 2>&1 | tee doc-server.log" Enter
    sleep 2

    echo -e "${GREEN}Запуск Legal Server на порту $LEGAL_PORT...${NC}"
    tmux new-window -t "$SESSION_NAME" -n "legal-server"
    tmux send-keys -t "$SESSION_NAME:legal-server" "cd $BACKEND_DIR/services/legal_server && $ACTIVATE_CMD && uvicorn mcp_legal_server:app --host 0.0.0.0 --port $LEGAL_PORT 2>&1 | tee legal-server.log" Enter
    sleep 2

    echo -e "${GREEN}Запуск Unified Model Server на порту $UMS_PORT...${NC}"
    tmux new-window -t "$SESSION_NAME" -n "ums"
    tmux send-keys -t "$SESSION_NAME:ums" "cd $BACKEND_DIR/services/model_manager && $ACTIVATE_CMD && python unified_model_server.py 2>&1 | tee ums.log" Enter

    echo -e "${GREEN}Открытие окна мониторинга...${NC}"
    tmux new-window -t "$SESSION_NAME" -n "monitor"
    tmux send-keys -t "$SESSION_NAME:monitor" "cd $BACKEND_DIR && echo -e '${GREEN}Система запущена.${NC}\nДля выхода нажмите ${YELLOW}Ctrl+B${NC} затем ${YELLOW}:kill-session${NC} (это остановит все сервисы, включая Docker).'" Enter
    tmux send-keys -t "$SESSION_NAME:monitor" "htop 2>/dev/null || top" Enter
}

wait_for_service() {
    local name="$1"
    local port="$2"
    local endpoint="${3:-/health}"
    local timeout="${4:-60}"
    local elapsed=0

    printf "  %-20s " "$name (:$port)"
    while [ $elapsed -lt $timeout ]; do
        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port$endpoint" 2>/dev/null)
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

wait_for_stack() {
    local ui_profile="$1"
    local ui_name

    if [ "$ui_profile" = "chainlit" ]; then
        ui_name="Chainlit UI"
    else
        ui_name="Open WebUI"
    fi

    echo ""
    echo -e "${YELLOW}Ожидание готовности сервисов...${NC}"

    SERVICES_OK=true
    wait_for_service "Agent API" "$AGENT_PORT" "/health" 30 || SERVICES_OK=false
    wait_for_service "Document Server" "$DOC_PORT" "/health" 30 || SERVICES_OK=false
    wait_for_service "Legal Server" "$LEGAL_PORT" "/health" 30 || SERVICES_OK=false
    wait_for_service "UMS" "$UMS_PORT" "/health" 60 || SERVICES_OK=false
    wait_for_service "$ui_name" "$UI_PORT" "/" 60 || SERVICES_OK=false

    echo ""
    echo -e "${YELLOW}Ожидание загрузки модели Qwen LLM (до 3 мин)...${NC}"
    wait_for_model 180
}

print_summary() {
    local ui_profile="$1"
    local ui_label
    local ui_window
    local ui_hint=""

    if [ "$ui_profile" = "chainlit" ]; then
        ui_label="Chainlit UI"
        ui_window="chainlit"
        ui_hint="  (login: admin/admin)"
    else
        ui_label="Open WebUI"
        ui_window="open-webui"
    fi

    if [ "$SERVICES_OK" = true ]; then
        SYSTEM_STATUS="${GREEN}ГОТОВА К РАБОТЕ${NC}"
    else
        SYSTEM_STATUS="${YELLOW}ЗАПУЩЕНА (некоторые сервисы не ответили)${NC}"
    fi

    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║        Agent Navigator Pro v3.0                          ║${NC}"
    echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC} Статус:          $SYSTEM_STATUS"
    echo -e "${GREEN}║${NC} Сессия tmux:     ${YELLOW}$SESSION_NAME${NC}"
    echo -e "${GREEN}║${NC} Conda окружение: ${YELLOW}$CONDA_ENV${NC}"
    echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC} $ui_label:     ${YELLOW}http://localhost:$UI_PORT${NC}$ui_hint"
    echo -e "${GREEN}║${NC} Agent API:       ${YELLOW}http://localhost:$AGENT_PORT${NC}"
    echo -e "${GREEN}║${NC} Document Server: ${YELLOW}http://localhost:$DOC_PORT${NC}"
    echo -e "${GREEN}║${NC} Legal Server:    ${YELLOW}http://localhost:$LEGAL_PORT${NC}"
    echo -e "${GREEN}║${NC} UMS:             ${YELLOW}http://localhost:$UMS_PORT${NC}"
    echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC} Окна tmux:"
    echo -e "${GREEN}║${NC}   - ${YELLOW}$ui_window${NC}:      Docker compose logs"
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
}
