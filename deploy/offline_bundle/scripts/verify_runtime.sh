#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$BUNDLE_ROOT/env.bundle"
COMPOSE_FILE="$BUNDLE_ROOT/compose.offline.yaml"

print_help() {
  cat <<'EOF'
verify_runtime.sh

Performs post-start verification for the offline bundle runtime.

Checks:
  - docker compose ps
  - UMS health endpoint
  - Agent API health endpoint
  - Document server health endpoint
  - Legal server health endpoint
  - Chainlit UI HTTP response

Flags:
  --timeout <seconds>   Max wait time per service group (default: 120)
EOF
}

TIMEOUT_S=120

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --timeout)
      TIMEOUT_S="$2"
      shift 2
      ;;
    --timeout=*)
      TIMEOUT_S="${1#*=}"
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
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

AGENT_API_PORT="${AGENT_API_PORT:-8000}"
DOCUMENT_SERVER_PORT="${DOCUMENT_SERVER_PORT:-8001}"
LEGAL_SERVER_PORT="${LEGAL_SERVER_PORT:-8002}"
UMS_PORT="${UMS_PORT:-8090}"
CHAINLIT_PORT="${CHAINLIT_PORT:-3000}"

docker compose -f "$COMPOSE_FILE" ps

wait_for_url() {
  local name="$1"
  local url="$2"
  local timeout="$3"
  local elapsed=0

  printf '%-20s ' "$name"
  while [[ "$elapsed" -lt "$timeout" ]]; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "ok"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "failed"
  return 1
}

wait_for_url "ums_health" "http://127.0.0.1:${UMS_PORT}/health" "$TIMEOUT_S"
wait_for_url "agent_api_health" "http://127.0.0.1:${AGENT_API_PORT}/health" "$TIMEOUT_S"
wait_for_url "document_health" "http://127.0.0.1:${DOCUMENT_SERVER_PORT}/health" "$TIMEOUT_S"
wait_for_url "legal_health" "http://127.0.0.1:${LEGAL_SERVER_PORT}/health" "$TIMEOUT_S"
wait_for_url "chainlit_ui" "http://127.0.0.1:${CHAINLIT_PORT}/" "$TIMEOUT_S"

echo "verify-runtime:ok"
