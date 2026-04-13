#!/bin/bash

# ===========================================
# Скрипт для запуска всех компонентов системы
# llm-tools-platform v3.0 (`Open WebUI` как основной UI, `Qdrant` как векторное хранилище)
# ===========================================
# Использует tmux для управления несколькими процессами
# `Open WebUI` запускается через Docker как основной UI (порт 3001)
# `Chainlit` здесь не стартует и остаётся совместимым/отладочным путём

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
ENV_FILE="${LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE:-$BACKEND_DIR/.env}"
RUNTIME_ENV_FILE="${LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE:-$BACKEND_DIR/.env.runtime}"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/utils/env_loader.sh"

ATTACH_TMUX=true
FROM_LAUNCHER=false
SKIP_RUNTIME_APPLY="${LLM_TOOLS_PLATFORM_SKIP_RUNTIME_APPLY:-true}"

EXTERNAL_BACKEND_MODE="${BACKEND_MODE:-}"
EXTERNAL_VLLM_BASE_URL="${VLLM_BASE_URL:-}"
EXTERNAL_VLLM_PORT="${VLLM_PORT:-}"
EXTERNAL_VLLM_MODEL_ID_QWEN_14B_LLM="${VLLM_MODEL_ID_QWEN_14B_LLM:-}"

ENV_LOADER_PYTHON="$(resolve_env_loader_python || true)"

print_help() {
  cat <<EOF
run_all.sh

Поднимает container/compose runtime для текущего compose-стека с `Open WebUI`
и backend `Qdrant` для поиска по знаниям и документам сеанса.
При прямом вызове считается совместимой точкой запуска и делегирует в `launcher.sh`.

Использование:
  ./scripts/run_all.sh
  ./scripts/run_all.sh --no-attach

Флаги:
  --from-launcher
      Внутренний флаг. Используется launcher.sh после bootstrap/preflight шага,
      чтобы скрипт сразу выполнил container runtime path.
  --no-attach
      Не подключаться к tmux monitoring session после запуска.
  --skip-runtime-apply
      Не загружать runtime overrides для этого запуска.
  --apply-runtime
      Явно загрузить runtime overrides для этого запуска.
  -h, --help
      Показать эту справку.

Примеры:
  ./scripts/run_all.sh
  BACKEND_MODE=vllm ./scripts/run_all.sh --no-attach
  ./scripts/launcher.sh --target container --profile default
EOF
}

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            print_help
            exit 0
            ;;
        --from-launcher)
            FROM_LAUNCHER=true
            ;;
        --no-attach)
            ATTACH_TMUX=false
            ;;
        --skip-runtime-apply)
            SKIP_RUNTIME_APPLY=true
            ;;
        --apply-runtime)
            SKIP_RUNTIME_APPLY=false
            ;;
        *)
            echo "Неизвестный аргумент: $arg" >&2
            echo "Используйте --help для списка флагов." >&2
            exit 1
            ;;
    esac
done

if [ "$FROM_LAUNCHER" = false ]; then
    exec bash "$SCRIPT_DIR/launcher.sh" --target container $([ "$ATTACH_TMUX" = false ] && echo "--no-attach")
fi

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Запуск системы llm-tools-platform v3.0 (Open WebUI как основной UI, Qdrant как векторное хранилище) ===${NC}"

# -------------------------------------------
# Загрузка переменных окружения из .env
# -------------------------------------------
if [ -f "$ENV_FILE" ]; then
    echo -e "${BLUE}Загрузка переменных из .env...${NC}"
    load_env_file "$ENV_FILE" "backend env" || exit 1
else
    echo -e "${YELLOW}Предупреждение: .env файл не найден. Используются значения по умолчанию.${NC}"
    echo -e "${YELLOW}Создайте .env из .env.example: cp .env.example .env${NC}"
fi

if [ "$SKIP_RUNTIME_APPLY" != true ] && [ -f "$RUNTIME_ENV_FILE" ]; then
    echo -e "${BLUE}Загрузка runtime overrides $RUNTIME_ENV_FILE${NC}"
    load_env_file "$RUNTIME_ENV_FILE" "runtime overrides" || exit 1
fi

if [ -n "$EXTERNAL_BACKEND_MODE" ]; then
    BACKEND_MODE="$EXTERNAL_BACKEND_MODE"
fi
if [ -n "$EXTERNAL_VLLM_BASE_URL" ]; then
    VLLM_BASE_URL="$EXTERNAL_VLLM_BASE_URL"
fi
if [ -n "$EXTERNAL_VLLM_PORT" ]; then
    VLLM_PORT="$EXTERNAL_VLLM_PORT"
fi
if [ -n "$EXTERNAL_VLLM_MODEL_ID_QWEN_14B_LLM" ]; then
    VLLM_MODEL_ID_QWEN_14B_LLM="$EXTERNAL_VLLM_MODEL_ID_QWEN_14B_LLM"
fi

BACKEND_MODE_RESOLVED="${BACKEND_MODE:-llama-cpp-python}"
VLLM_ENABLED=false
COMPOSE_PROFILE_ARGS=()
COMPOSE_LOG_TARGETS=("qdrant" "agent-api" "document-server" "legal-server" "ums" "open-webui")
COMPOSE_PROFILE_TEXT="(none)"
COMPOSE_SERVICES_TEXT="qdrant agent-api document-server legal-server ums open-webui"
COMPOSE_UP_MODE_TEXT="--no-build"
PHASE1_SERVICES=("qdrant" "document-server" "legal-server" "ums")
PHASE2_SERVICES=("agent-api" "open-webui")
VLLM_PORT="${VLLM_PORT:-8101}"
VLLM_SERVED_MODEL_ID="${VLLM_MODEL_ID_QWEN_14B_LLM:-qwen-14b-llm}"

if [ "$BACKEND_MODE_RESOLVED" = "vllm" ]; then
    VLLM_ENABLED=true
    COMPOSE_PROFILE_ARGS=("--profile" "vllm")
    COMPOSE_LOG_TARGETS=("qdrant" "agent-api" "document-server" "legal-server" "ums" "open-webui" "vllm")
    COMPOSE_PROFILE_TEXT="--profile vllm"
    COMPOSE_SERVICES_TEXT="qdrant agent-api document-server legal-server ums open-webui vllm"
    PHASE1_SERVICES=("qdrant" "document-server" "legal-server" "ums" "vllm")
fi

if [ "${LLM_TOOLS_PLATFORM_TEST_MODE:-0}" = "1" ]; then
    echo "run_all:test-mode backend_mode=$BACKEND_MODE_RESOLVED compose_profiles=$COMPOSE_PROFILE_TEXT compose_up_mode=$COMPOSE_UP_MODE_TEXT phase1_services=${PHASE1_SERVICES[*]} phase2_services=${PHASE2_SERVICES[*]} attach_tmux=$ATTACH_TMUX"
    exit 0
fi

CONDA_ENV="container-compose"

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
SESSION_NAME="llm-tools-platform"
echo -e "${YELLOW}Завершение существующих tmux/runtime/docker процессов...${NC}"
"$SCRIPT_DIR/stop_all.sh" >/dev/null 2>&1 || true

tmux new-session -d -s "$SESSION_NAME" -x 200 -y 50

# Порты из .env или значения по умолчанию
AGENT_PORT="${AGENT_API_PORT:-8000}"
DOC_PORT=8001
LEGAL_PORT=8002
UMS_PORT="${UMS_PORT:-8090}"
OPENWEBUI_PORT=3001
QDRANT_PORT=6333

# -------------------------------------------
# Функция ожидания готовности сервиса
# -------------------------------------------
wait_for_service() {
    local name="$1"
    local port="$2"
    local endpoint="${3:-/health}"
    local timeout="${4:-60}"
    local elapsed=0

    printf "  %-20s " "$name (:$port)"
    while [ $elapsed -lt $timeout ]; do
        local http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port$endpoint" 2>/dev/null)
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

# Проверка загрузки модели в UMS через /status
wait_for_model() {
    local timeout="${1:-180}"
    local elapsed=0

    if [ "$VLLM_ENABLED" = true ]; then
        printf "  %-20s " "vLLM runtime"
        while [ $elapsed -lt $timeout ]; do
            local models=$(curl -sf "http://localhost:$VLLM_PORT/v1/models" 2>/dev/null || true)
            if [ -n "$models" ] && echo "$models" | grep -q "\"$VLLM_SERVED_MODEL_ID\""; then
                echo -e "${GREEN}✓ upstream готов${NC}"
                return 0
            fi
            sleep 3
            elapsed=$((elapsed + 3))
        done
        echo -e "${YELLOW}⚠ upstream не подтвердил $VLLM_SERVED_MODEL_ID за ${timeout}с${NC}"
        return 0
    fi

    printf "  %-20s " "Qwen-14B LLM"
    while [ $elapsed -lt $timeout ]; do
        local status
        status=$(curl -sf "http://localhost:$UMS_PORT/status" 2>/dev/null || true)
        if [ -n "$status" ]; then
            if echo "$status" | grep -q '"qwen-14b-llm"'; then
                echo -e "${GREEN}✓ загружена${NC}"
                return 0
            fi
        fi
        sleep 3
        elapsed=$((elapsed + 3))
    done
    echo -e "${YELLOW}⚠ не загружена за ${timeout}с (загрузится при первом запросе)${NC}"
    return 0
}

wait_for_infer_ready() {
    local timeout="${1:-180}"
    local elapsed=0

    printf "  %-20s " "UMS infer-ready"
    while [ $elapsed -lt $timeout ]; do
        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$UMS_PORT/ready/infer" 2>/dev/null || true)
        if [ "$http_code" = "200" ]; then
            echo -e "${GREEN}✓ готов${NC}"
            return 0
        fi
        sleep 3
        elapsed=$((elapsed + 3))
    done
    echo -e "${RED}✗ таймаут (${timeout}с)${NC}"
    return 1
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

# -------------------------------------------
# Запуск сервисов
# -------------------------------------------

# -------------------------------------------
# Ожидание готовности сервисов
# -------------------------------------------
echo ""
echo -e "${YELLOW}Проверка и запуск сервисов...${NC}"

SERVICES_OK=true

echo -e "${GREEN}Запуск backend services через Docker Compose...${NC}"
COMPOSE_ENV_PREFIX=""
cd "$PROJECT_ROOT"
env $COMPOSE_ENV_PREFIX docker compose "${COMPOSE_PROFILE_ARGS[@]}" up --no-build -d "${PHASE1_SERVICES[@]}"

# 1) Qdrant
wait_for_service "Qdrant" "$QDRANT_PORT" "/collections" 30 || SERVICES_OK=false

# 2) Document Server
wait_for_service "Document Server" "$DOC_PORT" "/health" 60 || SERVICES_OK=false

# 3) Legal Server
wait_for_service "Legal Server" "$LEGAL_PORT" "/health" 60 || SERVICES_OK=false

# 4) UMS
wait_for_service "UMS" "$UMS_PORT" "/health" 120 || SERVICES_OK=false

echo ""
echo -e "${YELLOW}Ожидание загрузки модели Qwen LLM (до 3 мин)...${NC}"
wait_for_model 180 || SERVICES_OK=false

echo ""
echo -e "${YELLOW}Проверка готовности UMS к первому infer (до 3 мин)...${NC}"
UMS_INFER_READY=true
wait_for_infer_ready 180 || UMS_INFER_READY=false
if [ "$UMS_INFER_READY" = false ]; then
    SERVICES_OK=false
fi

if [ "$UMS_INFER_READY" = true ]; then
    env $COMPOSE_ENV_PREFIX docker compose "${COMPOSE_PROFILE_ARGS[@]}" up --no-build --no-deps -d "${PHASE2_SERVICES[@]}"

    # 5) Agent API
    wait_for_service "Agent API" "$AGENT_PORT" "/health" 30 || SERVICES_OK=false

    # 6) Open WebUI (Docker)
    if [ "$VLLM_ENABLED" = true ]; then
        wait_for_service "vLLM" "$VLLM_PORT" "/health" 180 || SERVICES_OK=false
    fi
    wait_for_service "Open WebUI" "$OPENWEBUI_PORT" "/health" 60 || SERVICES_OK=false
else
    echo -e "${YELLOW}Пропуск запуска Agent API и Open WebUI: UMS infer-ready не подтвержден.${NC}"
fi

COMPOSE_LOG_CMD="cd $PROJECT_ROOT && env $COMPOSE_ENV_PREFIX docker compose ${COMPOSE_PROFILE_ARGS[*]} logs -f ${COMPOSE_LOG_TARGETS[*]}"
start_tmux_window "backend-compose" "$COMPOSE_LOG_CMD"

# 7) Monitor/Logs
echo -e "${GREEN}Открытие окна мониторинга...${NC}"
start_tmux_window "monitor" "cd $PROJECT_ROOT && echo -e '${GREEN}Compose stack с Open WebUI запущен.${NC}\nДля остановки используйте ${YELLOW}docker compose down${NC} или завершите tmux сессию.' && (docker compose ps || true) && (htop 2>/dev/null || top)"

if [ "$SERVICES_OK" = true ]; then
    SYSTEM_STATUS="${GREEN}ГОТОВА К РАБОТЕ${NC}"
else
    SYSTEM_STATUS="${YELLOW}ЗАПУЩЕНА (некоторые сервисы не ответили)${NC}"
fi

# -------------------------------------------
# Выводим информацию
# -------------------------------------------
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        llm-tools-platform v3.0                          ║${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Статус:          $SYSTEM_STATUS"
echo -e "${GREEN}║${NC} Сессия tmux:     ${YELLOW}$SESSION_NAME${NC}"
echo -e "${GREEN}║${NC} Runtime target:   ${YELLOW}container-compose${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Open WebUI:      ${YELLOW}http://localhost:$OPENWEBUI_PORT${NC}"
echo -e "${GREEN}║${NC} Agent API:       ${YELLOW}http://localhost:$AGENT_PORT${NC}"
echo -e "${GREEN}║${NC} Document Server: ${YELLOW}http://localhost:$DOC_PORT${NC}"
echo -e "${GREEN}║${NC} Legal Server:    ${YELLOW}http://localhost:$LEGAL_PORT${NC}"
echo -e "${GREEN}║${NC} UMS:             ${YELLOW}http://localhost:$UMS_PORT${NC}"
echo -e "${GREEN}║${NC} Qdrant:          ${YELLOW}http://localhost:$QDRANT_PORT${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Подключение: ${BLUE}tmux attach-session -t $SESSION_NAME${NC}"
echo -e "${GREEN}║${NC} Завершение:  ${BLUE}./scripts/stop_all.sh${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

if [ "$ATTACH_TMUX" = false ]; then
    echo -e "${YELLOW}tmux attach пропущен (--no-attach).${NC}"
fi
