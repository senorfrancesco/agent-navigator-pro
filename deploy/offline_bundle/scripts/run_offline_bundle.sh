#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$BUNDLE_ROOT/compose.offline.yaml"
ENV_FILE="$BUNDLE_ROOT/env.bundle"

WITH_MONITORING=0
START_TMUX=1
ATTACH_TMUX=0
SKIP_IMAGE_LOAD=0
SKIP_HOST_CHECK=0
TMUX_SESSION_NAME="agent-nav-offline"

print_help() {
  cat <<'EOF'
run_offline_bundle.sh

Единый operator entrypoint для offline docker runtime.
Это docker-oriented аналог launcher/run path: поднимает стек, при необходимости
включает monitoring profile, прогоняет bundle preflight и создаёт tmux workspace с btop.

Использование:
  bash scripts/run_offline_bundle.sh
  bash scripts/run_offline_bundle.sh --with-monitoring
  bash scripts/run_offline_bundle.sh --with-monitoring --attach-tmux

Флаги:
  --with-monitoring    Поднять compose profile monitoring
  --no-tmux           Не запускать tmux workspace
  --attach-tmux       После старта подключиться к tmux-сессии
  --skip-image-load   Не вызывать load_images.sh
  --skip-host-check   Не вызывать check_host.sh
  --tmux-session <n>  Имя tmux-сессии
  -h, --help          Показать справку
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --with-monitoring)
      WITH_MONITORING=1
      shift
      ;;
    --no-tmux)
      START_TMUX=0
      shift
      ;;
    --attach-tmux)
      ATTACH_TMUX=1
      shift
      ;;
    --skip-image-load)
      SKIP_IMAGE_LOAD=1
      shift
      ;;
    --skip-host-check)
      SKIP_HOST_CHECK=1
      shift
      ;;
    --tmux-session)
      TMUX_SESSION_NAME="$2"
      shift 2
      ;;
    --tmux-session=*)
      TMUX_SESSION_NAME="${1#*=}"
      shift
      ;;
    *)
      echo "unknown-arg:$1" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing:env.bundle" >&2
  echo "hint: cp env.bundle.example env.bundle" >&2
  exit 1
fi

cd "$BUNDLE_ROOT"

DEPLOY_ARGS=()
if [[ "$SKIP_IMAGE_LOAD" -eq 1 ]]; then
  DEPLOY_ARGS+=("--skip-image-load")
fi
if [[ "$SKIP_HOST_CHECK" -eq 1 ]]; then
  DEPLOY_ARGS+=("--skip-host-check")
fi

bash "$SCRIPT_DIR/deploy.sh" "${DEPLOY_ARGS[@]}"

if [[ "$WITH_MONITORING" -eq 1 ]]; then
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile monitoring up -d
fi

if [[ "$START_TMUX" -eq 1 ]]; then
  bash "$SCRIPT_DIR/launch_tmux_workspace.sh" "$TMUX_SESSION_NAME"
  if [[ "$ATTACH_TMUX" -eq 1 ]]; then
    exec tmux attach -t "$TMUX_SESSION_NAME"
  fi
fi

set -a
source "$ENV_FILE"
set +a

CHAINLIT_PORT="${CHAINLIT_PORT:-3000}"
AGENT_API_PORT="${AGENT_API_PORT:-8000}"
UMS_PORT="${UMS_PORT:-8090}"
PROMETHEUS_PORT="${PROMETHEUS_PORT:-9090}"
GRAFANA_PORT="${GRAFANA_PORT:-3002}"

echo "run-offline-bundle:ok"
echo "Chainlit:   http://localhost:${CHAINLIT_PORT}"
echo "Agent API:  http://localhost:${AGENT_API_PORT}"
echo "UMS:        http://localhost:${UMS_PORT}"
if [[ "$WITH_MONITORING" -eq 1 ]]; then
  echo "Prometheus: http://localhost:${PROMETHEUS_PORT}"
  echo "Grafana:    http://localhost:${GRAFANA_PORT}"
fi
if [[ "$START_TMUX" -eq 1 ]]; then
  echo "tmux:       tmux attach -t ${TMUX_SESSION_NAME}"
fi
