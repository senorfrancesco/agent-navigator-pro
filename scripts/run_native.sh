#!/bin/bash

# ===========================================
# Native launch (host backend + `Open WebUI`/`Qdrant`)
# llm-tools-platform v3.0 (`Open WebUI` как основной UI)
# ===========================================

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
ENV_FILE="${LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE:-$BACKEND_DIR/.env}"
RUNTIME_ENV_FILE="${LLM_TOOLS_PLATFORM_RUNTIME_ENV_FILE:-$BACKEND_DIR/.env.runtime}"
ENV_ROOT="$(dirname "$ENV_FILE")"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/utils/env_loader.sh"

ATTACH_TMUX=true
FROM_LAUNCHER=false
SKIP_OPENWEBUI=false
SKIP_CHAINLIT_ALIAS=false
if [ -n "${LLM_TOOLS_PLATFORM_SKIP_RUNTIME_APPLY+x}" ]; then
  SKIP_RUNTIME_APPLY="${LLM_TOOLS_PLATFORM_SKIP_RUNTIME_APPLY}"
  EXPLICIT_RUNTIME_ENV_SELECTION=true
else
  SKIP_RUNTIME_APPLY=""
  EXPLICIT_RUNTIME_ENV_SELECTION=false
fi

print_help() {
  cat <<'EOF'
run_native.sh

Поднимает native runtime: tmux-сессию и backend-сервисы на host,
а также `Qdrant` и `Open WebUI` через Docker Compose.
По умолчанию использует только ручные настройки из backend/.env.
Оценка железа и рекомендации для backend/.env выполняются отдельно через ./scripts/evaluate_runtime.sh.

Использование:
  ./scripts/run_native.sh
  ./scripts/run_native.sh --no-attach
  ./scripts/run_native.sh --skip-openwebui --no-attach

Флаги:
  --from-launcher
      Внутренний флаг. Показывает, что orchestration уже выполнен через launcher.sh
      и скрипт должен сразу запускать native runtime, а не делегировать обратно.
  --no-attach
      Не подключаться к tmux после запуска; оставить сессию в фоне.
  --skip-openwebui
      Не запускать контейнер `Open WebUI`. `Qdrant` и backend-сервисы продолжают
      стартовать как обычно.
  --skip-chainlit
      Совместимый алиас для `--skip-openwebui`.
  --skip-runtime-apply
      Явно не загружать backend/.env.runtime для этого запуска.
  --apply-runtime
      Явно загрузить backend/.env.runtime для этого запуска.
  -h, --help
      Показать эту справку.

Примеры:
  ./scripts/run_native.sh
  ./scripts/run_native.sh --no-attach
  ./scripts/run_native.sh --skip-openwebui --no-attach
  ./scripts/run_native.sh --apply-runtime
  ./scripts/evaluate_runtime.sh recommend
  ./scripts/evaluate_runtime.sh plan
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
    --skip-openwebui)
      SKIP_OPENWEBUI=true
      ;;
    --skip-chainlit)
      SKIP_OPENWEBUI=true
      SKIP_CHAINLIT_ALIAS=true
      ;;
    --skip-runtime-apply)
      SKIP_RUNTIME_APPLY=true
      EXPLICIT_RUNTIME_ENV_SELECTION=true
      ;;
    --apply-runtime)
      SKIP_RUNTIME_APPLY=false
      EXPLICIT_RUNTIME_ENV_SELECTION=true
      ;;
    --interactive|--review-runtime|--non-interactive)
      echo "Флаг $arg больше не поддерживается в run_native.sh." >&2
      echo "Используйте ./scripts/evaluate_runtime.sh recommend или ./scripts/evaluate_runtime.sh plan." >&2
      exit 1
      ;;
    *)
      echo "Неизвестный аргумент: $arg" >&2
      echo "Используйте --help для списка флагов." >&2
      exit 1
      ;;
  esac
done

if [ "$EXPLICIT_RUNTIME_ENV_SELECTION" != true ]; then
  SKIP_RUNTIME_APPLY=true
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ENV_LOADER_PYTHON="$(resolve_env_loader_python || true)"

echo -e "${GREEN}=== Native запуск llm-tools-platform (host backend + Open WebUI/Qdrant) ===${NC}"

die() {
  echo -e "${RED}$1${NC}" >&2
  exit 1
}

warn() {
  echo -e "${YELLOW}$1${NC}"
}

resolve_backend_relative_path() {
  local raw_path="$1"
  if [ -z "$raw_path" ]; then
    return 0
  fi
  if [[ "$raw_path" = /* ]]; then
    printf '%s\n' "$raw_path"
    return 0
  fi
  printf '%s\n' "$ENV_ROOT/${raw_path#./}"
}

validate_file_path_var() {
  local var_name="$1"
  local value="${!var_name:-}"
  local resolved_value=""
  [ -z "$value" ] && return 0
  resolved_value="$(resolve_backend_relative_path "$value")"
  printf -v "$var_name" '%s' "$resolved_value"
  if [ ! -e "$resolved_value" ]; then
    die "env-invalid:$var_name:missing-file:$value"
  fi
  if [ ! -f "$resolved_value" ]; then
    die "env-invalid:$var_name:not-a-file:$value"
  fi
  if [ ! -r "$resolved_value" ]; then
    die "permission-denied:$var_name:$value"
  fi
}

validate_dir_path_var() {
  local var_name="$1"
  local value="${!var_name:-}"
  local resolved_value=""
  [ -z "$value" ] && return 0
  resolved_value="$(resolve_backend_relative_path "$value")"
  printf -v "$var_name" '%s' "$resolved_value"
  if [ ! -e "$resolved_value" ]; then
    die "env-invalid:$var_name:missing-dir:$value"
  fi
  if [ ! -d "$resolved_value" ]; then
    die "env-invalid:$var_name:not-a-dir:$value"
  fi
  if [ ! -r "$resolved_value" ]; then
    die "permission-denied:$var_name:$value"
  fi
}

ensure_writable_dir() {
  local dir_path="$1"
  local label="$2"
  if ! mkdir -p "$dir_path" 2>/dev/null; then
    die "permission-denied:$label:create-dir:$dir_path"
  fi
  if [ ! -d "$dir_path" ]; then
    die "env-invalid:$label:not-a-dir:$dir_path"
  fi
  if ! chmod u+rwx "$dir_path" 2>/dev/null; then
    :
  fi
  local probe_file="$dir_path/.llm_tools_platform_write_check.$$"
  if ! : > "$probe_file" 2>/dev/null; then
    if chmod u+rwx "$dir_path" 2>/dev/null && : > "$probe_file" 2>/dev/null; then
      :
    else
      die "permission-denied:$label:write-dir:$dir_path"
    fi
  fi
  rm -f "$probe_file"
}

echo -e "${BLUE}Загрузка backend env $ENV_FILE${NC}"
load_env_file "$ENV_FILE" "backend env" || die "env-load-failed:backend env:$ENV_FILE"
if [ "$SKIP_RUNTIME_APPLY" != true ]; then
  echo -e "${BLUE}Загрузка runtime overrides $RUNTIME_ENV_FILE${NC}"
  load_env_file "$RUNTIME_ENV_FILE" "runtime overrides" || die "env-load-failed:runtime overrides:$RUNTIME_ENV_FILE"
fi

CONDA_ENV="${CONDA_ENV:-base}"
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

list_conda_envs() {
  conda env list 2>/dev/null | awk 'NF > 0 && $1 !~ /^#/ {gsub(/\\*/, "", $1); print $1}'
}

resolve_conda_env() {
  if list_conda_envs | grep -Fxq "$CONDA_ENV"; then
    return 0
  fi
  if [ "$CONDA_ENV" != "base" ] && list_conda_envs | grep -Fxq "base"; then
    warn "Conda env '$CONDA_ENV' не найден, использую base"
    CONDA_ENV="base"
    return 0
  fi
  return 1
}

validate_native_runtime_env() {
  validate_file_path_var "MODEL_PATH_LLM"
  validate_file_path_var "MODEL_PATH_VLM"
  validate_file_path_var "MMPROJ_PATH"
  validate_dir_path_var "MODEL_PATH_EMBEDDING_INTENT"
  validate_dir_path_var "MODEL_PATH_EMBEDDING_RETRIEVAL"
  UPLOADS_DIR="$(resolve_backend_relative_path "${UPLOADS_DIR:-$BACKEND_DIR/open_webui_uploads}")"
  ensure_writable_dir "${UPLOADS_DIR:-$BACKEND_DIR/open_webui_uploads}" "UPLOADS_DIR"
}

UPLOADS_DIR="${UPLOADS_DIR:-$BACKEND_DIR/open_webui_uploads}"
validate_native_runtime_env

AGENT_PORT="${AGENT_API_PORT:-8000}"
DOC_PORT="${DOC_PORT:-8001}"
LEGAL_PORT="${LEGAL_PORT:-8002}"
UMS_PORT="${UMS_PORT:-8090}"
OPENWEBUI_PORT="${OPENWEBUI_PORT:-3001}"
QDRANT_PORT="${QDRANT_PORT:-6333}"

MCP_DOCUMENT_SERVER_URL="${MCP_DOCUMENT_SERVER_URL:-http://localhost:8001}"
MCP_LEGAL_SERVER_URL="${MCP_LEGAL_SERVER_URL:-http://localhost:8002}"
UMS_URL="${UMS_URL:-http://localhost:8090}"
HOST_UPLOADS_DIR="${HOST_UPLOADS_DIR:-}"
QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:$QDRANT_PORT}"

first_non_empty() {
  local value=""
  for value in "$@"; do
    if [ -n "$value" ]; then
      printf '%s\n' "$value"
      return 0
    fi
  done
  printf 'auto\n'
}

print_startup_config_summary() {
  local config_source="backend/.env"
  local openwebui_mode="on"
  local llm_device_summary=""
  local vlm_device_summary=""
  local intent_device_summary=""
  local retrieval_device_summary=""
  local llm_gpu_summary=""
  local embed_gpu_summary=""

  if [ "$SKIP_RUNTIME_APPLY" != true ]; then
    config_source="backend/.env + backend/.env.runtime"
  fi
  if [ "$SKIP_OPENWEBUI" = true ]; then
    openwebui_mode="off"
  fi

  llm_device_summary="$(first_non_empty "${LLM_DEVICE_MODE:-}" "${DEVICE_MODE:-}")"
  vlm_device_summary="$(first_non_empty "${VLM_DEVICE_MODE:-}" "${LLM_DEVICE_MODE:-}" "${DEVICE_MODE:-}")"
  intent_device_summary="$(first_non_empty "${INTENT_EMBEDDER_DEVICE_MODE:-}")"
  retrieval_device_summary="$(first_non_empty "${RETRIEVAL_EMBEDDER_DEVICE_MODE:-}")"
  llm_gpu_summary="$(first_non_empty "${UMS_LLM_GPU_INDICES:-}")"
  embed_gpu_summary="$(first_non_empty "${UMS_EMBEDDING_GPU_INDEX:-}")"

  echo -e "${BLUE}Конфиг запуска:${NC}"
  echo "  runtime: source=${config_source} conda=${CONDA_ENV} backend=${BACKEND_MODE:-llama-cpp-python} profile=${UMS_RUNTIME_PROFILE:-adaptive} qdrant=on openwebui=${openwebui_mode}"
  echo "  placement: llm=${llm_device_summary} vlm=${vlm_device_summary} intent=${intent_device_summary} retrieval=${retrieval_device_summary} llm_gpus=${llm_gpu_summary} embed_gpu=${embed_gpu_summary}"
  echo "  ports: api=${AGENT_PORT} doc=${DOC_PORT} legal=${LEGAL_PORT} ums=${UMS_PORT} qdrant=${QDRANT_PORT} openwebui=${OPENWEBUI_PORT}"
}

render_ums_status_summary() {
  local status_json="$1"
  [ -z "$status_json" ] && return 0
  [ -z "$ENV_LOADER_PYTHON" ] && return 0
  "$ENV_LOADER_PYTHON" - "$status_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
placements = payload.get("placements") or {}
component_models = [
    ("llm", "qwen-14b-llm"),
    ("vlm", "qwen-vl-8b"),
    ("intent", "qwen3-embedding-0.6b"),
    ("retrieval", "labse-embedding"),
]
parts = []
for label, model_id in component_models:
    placement = placements.get(model_id)
    if not placement:
        continue
    placement_mode = placement.get("placement_mode") or "unknown"
    resolved_device = (
        placement.get("resolved_device")
        or placement.get("device_arg")
        or placement.get("requested_device")
        or "unknown"
    )
    gpu_indices = placement.get("gpu_indices") or []
    gpu_suffix = f"[{','.join(str(item) for item in gpu_indices)}]" if gpu_indices else ""
    parts.append(f"{label}={placement_mode}/{resolved_device}{gpu_suffix}")

if parts:
    backend_mode = payload.get("backend_mode") or "unknown"
    runtime_profile = payload.get("runtime_profile") or "unknown"
    print(f"  ums: backend={backend_mode} profile={runtime_profile} " + " ".join(parts))
PY
}

print_ums_status_summary() {
  local status_json=""
  status_json=$(curl -sf "http://localhost:$UMS_PORT/status" 2>/dev/null || true)
  render_ums_status_summary "$status_json"
}

if [ "${LLM_TOOLS_PLATFORM_SKIP_CONDA_CHECKS:-0}" != "1" ]; then
  echo -e "${YELLOW}Активация conda окружения: $CONDA_ENV${NC}"
  if ! command -v conda >/dev/null 2>&1 && [ ! -f "$HOME/miniconda3/etc/profile.d/conda.sh" ] && [ ! -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    die "env-invalid:CONDA:not-installed"
  fi
  if ! resolve_conda_env; then
    AVAILABLE_ENVS="$(list_conda_envs | paste -sd ',' -)"
    die "env-invalid:CONDA_ENV:not-found:$CONDA_ENV available=${AVAILABLE_ENVS:-none}"
  fi
  if ! find_conda; then
    die "env-invalid:CONDA_ENV:activate-failed:$CONDA_ENV"
  fi
fi

print_startup_config_summary

if [ "${LLM_TOOLS_PLATFORM_TEST_MODE:-0}" = "1" ]; then
  echo "run_native:test-mode validated conda_env=${CONDA_ENV} uploads_dir=${UPLOADS_DIR} skip_openwebui=${SKIP_OPENWEBUI} skip_chainlit_compat=${SKIP_CHAINLIT_ALIAS}"
  exit 0
fi

ensure_docker_compose() {
  command -v docker >/dev/null 2>&1 || die "missing:docker"
  docker compose version >/dev/null 2>&1 || die "missing:docker-compose"
}

normalize_native_qdrant_url() {
  local current_value="$1"
  if [ -z "$current_value" ]; then
    printf 'http://127.0.0.1:%s\n' "$QDRANT_PORT"
    return 0
  fi
  if printf '%s' "$current_value" | grep -Eq '://qdrant(:|/|$)'; then
    printf 'http://127.0.0.1:%s\n' "$QDRANT_PORT"
    return 0
  fi
  printf '%s\n' "$current_value"
}

ensure_docker_compose
QDRANT_URL="$(normalize_native_qdrant_url "${QDRANT_URL:-}")"

if ! command -v tmux &> /dev/null; then
  die "missing:tmux"
fi

if [ "$SKIP_CHAINLIT_ALIAS" = true ]; then
  warn "Флаг --skip-chainlit устарел; используйте --skip-openwebui."
fi

SESSION_NAME="llm-tools-platform-native"
echo -e "${YELLOW}Завершение существующей native tmux сессии и runtime-процессов...${NC}"
"$SCRIPT_DIR/stop_native.sh" >/dev/null 2>&1 || true

tmux new-session -d -s "$SESSION_NAME" -x 220 -y 60

if [ -n "$CONDA_SH_PATH" ]; then
  ACTIVATE_CMD="source $CONDA_SH_PATH && conda activate $CONDA_ENV"
else
  ACTIVATE_CMD="eval \"\$(conda shell.bash hook)\" && conda activate $CONDA_ENV"
fi

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

echo ""
echo -e "${YELLOW}Проверка и запуск сервисов...${NC}"

SERVICES_OK=true

# 0) Qdrant
echo -e "${GREEN}Запуск Qdrant через Docker Compose на порту $QDRANT_PORT...${NC}"
(
  cd "$PROJECT_ROOT"
  docker compose up --no-build -d qdrant
)
wait_for_service "Qdrant" "$QDRANT_PORT" "/collections" 30 || SERVICES_OK=false

# 1) Document Server
echo -e "${GREEN}Запуск Document Server на порту $DOC_PORT...${NC}"
start_tmux_window "doc-server" "cd $BACKEND_DIR/services/document_server && $ACTIVATE_CMD && export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_document_server:app --host 0.0.0.0 --port $DOC_PORT 2>&1 | tee doc-server.log"
wait_for_service "Document Server" "$DOC_PORT" "/health" 30 || SERVICES_OK=false

# 2) Legal Server
echo -e "${GREEN}Запуск Legal Server на порту $LEGAL_PORT...${NC}"
start_tmux_window "legal-server" "cd $BACKEND_DIR/services/legal_server && $ACTIVATE_CMD && export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_legal_server:app --host 0.0.0.0 --port $LEGAL_PORT 2>&1 | tee legal-server.log"
wait_for_service "Legal Server" "$LEGAL_PORT" "/health" 30 || SERVICES_OK=false

# 3) UMS
echo -e "${GREEN}Запуск UMS на порту $UMS_PORT...${NC}"
start_tmux_window "ums" "cd $BACKEND_DIR && $ACTIVATE_CMD && export PYTHONPATH='$BACKEND_DIR' && python services/model_manager/unified_model_server.py 2>&1 | tee services/model_manager/ums.log"
wait_for_service "UMS" "$UMS_PORT" "/health" 90 || SERVICES_OK=false

echo ""
echo -e "${YELLOW}Ожидание загрузки модели Qwen LLM (до 3 мин)...${NC}"
wait_for_model 180 || SERVICES_OK=false

echo ""
echo -e "${YELLOW}Проверка готовности UMS к первому infer (до 3 мин)...${NC}"
UMS_INFER_READY=true
wait_for_infer_ready 180 || UMS_INFER_READY=false
if [ "$UMS_INFER_READY" = false ]; then
  SERVICES_OK=false
else
  print_ums_status_summary
fi

# 4) Agent API
if [ "$UMS_INFER_READY" = true ]; then
  echo -e "${GREEN}Запуск Agent API на порту $AGENT_PORT...${NC}"
  start_tmux_window "agent-api" "cd $BACKEND_DIR/orchestrator && $ACTIVATE_CMD && export MCP_DOCUMENT_SERVER_URL='$MCP_DOCUMENT_SERVER_URL' MCP_LEGAL_SERVER_URL='$MCP_LEGAL_SERVER_URL' UMS_URL='$UMS_URL' QDRANT_URL='$QDRANT_URL' UPLOADS_DIR='$UPLOADS_DIR' HOST_UPLOADS_DIR='$HOST_UPLOADS_DIR' && python agent_api.py 2>&1 | tee agent-api.log"
  wait_for_service "Agent API" "$AGENT_PORT" "/health" 30 || SERVICES_OK=false

  # 5) Open WebUI (Docker)
  if [ "$SKIP_OPENWEBUI" = true ]; then
    echo -e "${YELLOW}Пропуск запуска Open WebUI (--skip-openwebui).${NC}"
  else
    echo -e "${GREEN}Запуск Open WebUI через Docker Compose на порту $OPENWEBUI_PORT...${NC}"
    (
      cd "$PROJECT_ROOT"
      docker compose up --no-build --no-deps -d open-webui
    )
    wait_for_service "Open WebUI" "$OPENWEBUI_PORT" "/health" 60 || SERVICES_OK=false
  fi
else
  echo -e "${YELLOW}Пропуск запуска Agent API и Open WebUI: UMS infer-ready не подтвержден.${NC}"
fi

# 6) Monitor
start_tmux_window "monitor" "cd $PROJECT_ROOT && echo 'Native mode: backend на host, Qdrant и Open WebUI через Docker Compose' && (docker compose ps || true) && (htop 2>/dev/null || top)"

if [ "$SERVICES_OK" = true ]; then
  SYSTEM_STATUS="${GREEN}ГОТОВА К РАБОТЕ${NC}"
else
  SYSTEM_STATUS="${YELLOW}ЗАПУЩЕНА (частично)${NC}"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║      llm-tools-platform v3.0 (Native Dev)               ║${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC} Статус:          $SYSTEM_STATUS"
echo -e "${GREEN}║${NC} tmux сессия:     ${YELLOW}$SESSION_NAME${NC}"
if [ "$SKIP_OPENWEBUI" = true ]; then
  echo -e "${GREEN}║${NC} Open WebUI:      ${YELLOW}пропущен (--skip-openwebui)${NC}"
else
  echo -e "${GREEN}║${NC} Open WebUI:      ${YELLOW}http://localhost:$OPENWEBUI_PORT${NC}"
fi
echo -e "${GREEN}║${NC} Qdrant:          ${YELLOW}http://localhost:$QDRANT_PORT${NC}"
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
