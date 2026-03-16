#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_ENV_FILE="${AGENT_NAVIGATOR_BACKEND_ENV_FILE:-$PROJECT_ROOT/backend/.env}"
INSTALL_COORDINATOR_SCRIPT="$SCRIPT_DIR/install/install.sh"

MODE="check"
TARGET="native"
PLATFORM="auto"

for arg in "$@"; do
  case "$arg" in
    --check)
      MODE="check"
      ;;
    --install)
      MODE="install"
      ;;
    --target=*)
      TARGET="${arg#*=}"
      ;;
    --platform=*)
      PLATFORM="${arg#*=}"
      ;;
    *)
      echo "Неизвестный аргумент bootstrap_env.sh: $arg" >&2
      exit 1
      ;;
  esac
done

if [ -f "$BACKEND_ENV_FILE" ]; then
  set -a
  source "$BACKEND_ENV_FILE"
  set +a
fi

if [ "${AGENT_NAVIGATOR_TEST_MODE:-0}" = "1" ] && [ "$MODE" != "install" ]; then
  echo "bootstrap:test-mode mode=$MODE target=$TARGET platform=$PLATFORM"
  exit 0
fi

missing=0
security_failed=0

check_cmd() {
  local cmd="$1"
  local label="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing:$label"
    missing=1
  fi
}

if [ "${AGENT_NAVIGATOR_SKIP_COMMAND_CHECKS:-0}" != "1" ]; then
  check_cmd "tmux" "tmux"
  check_cmd "python" "python"

  if [ "$TARGET" = "container" ]; then
    check_cmd "docker" "docker"
  fi
fi

if [ "$MODE" = "install" ]; then
  if [ -x "$INSTALL_COORDINATOR_SCRIPT" ]; then
    exec "$INSTALL_COORDINATOR_SCRIPT" "--target=$TARGET" "--platform=$PLATFORM"
  fi
  if [ -x "$SCRIPT_DIR/setup_ubuntu.sh" ]; then
    exec "$SCRIPT_DIR/setup_ubuntu.sh"
  fi
  echo "install.sh/setup_ubuntu.sh not found or not executable" >&2
  exit 1
fi

check_secret() {
  local name="$1"
  local insecure_value="$2"
  local current="${!name:-}"
  if [ -z "$current" ] || [ "$current" = "$insecure_value" ]; then
    echo "insecure-secret:$name"
    security_failed=1
  fi
}

if [ "${AGENT_NAVIGATOR_ALLOW_INSECURE_DEFAULTS:-0}" != "1" ]; then
  check_secret "CHAINLIT_AUTH_SECRET" "agent-navigator-secret-key-change-me"
  check_secret "CHAINLIT_ADMIN_PASSWORD" "admin"
  check_secret "GF_SECURITY_ADMIN_PASSWORD" "change-me-grafana"
fi

if [ "$missing" -ne 0 ]; then
  echo "Bootstrap check failed. Use ./scripts/launcher.sh --install for guided install path." >&2
  exit 1
fi

if [ "$security_failed" -ne 0 ]; then
  echo "Bootstrap security check failed. Replace default secrets in backend/.env or set AGENT_NAVIGATOR_ALLOW_INSECURE_DEFAULTS=1 only for local/dev." >&2
  exit 1
fi

echo "bootstrap:ok target=$TARGET"
