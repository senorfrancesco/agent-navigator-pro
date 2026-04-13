#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$BUNDLE_ROOT/compose.offline.yaml"
ENV_FILE="$BUNDLE_ROOT/env.bundle"
TMUX_SESSION_NAME="${1:-llm-tools-platform-offline}"

print_help() {
  cat <<'EOF'
stop_offline_bundle.sh

Останавливает offline docker runtime, monitoring profile и tmux workspace.

Использование:
  bash scripts/stop_offline_bundle.sh
  bash scripts/stop_offline_bundle.sh custom-session
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  print_help
  exit 0
fi

if [[ -f "$ENV_FILE" ]]; then
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile monitoring down --remove-orphans || true
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down --remove-orphans || true
fi

if command -v tmux >/dev/null 2>&1; then
  tmux kill-session -t "$TMUX_SESSION_NAME" 2>/dev/null || true
fi

echo "stop-offline-bundle:ok"
