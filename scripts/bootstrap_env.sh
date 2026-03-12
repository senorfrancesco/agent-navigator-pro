#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

MODE="check"
TARGET="native"

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
    *)
      echo "Неизвестный аргумент bootstrap_env.sh: $arg" >&2
      exit 1
      ;;
  esac
done

if [ "${AGENT_NAVIGATOR_TEST_MODE:-0}" = "1" ]; then
  echo "bootstrap:test-mode mode=$MODE target=$TARGET"
  exit 0
fi

missing=0

check_cmd() {
  local cmd="$1"
  local label="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing:$label"
    missing=1
  fi
}

check_cmd "tmux" "tmux"
check_cmd "python" "python"

if [ "$TARGET" = "container" ]; then
  check_cmd "docker" "docker"
fi

if [ "$MODE" = "install" ]; then
  if [ -x "$SCRIPT_DIR/setup_ubuntu.sh" ]; then
    exec "$SCRIPT_DIR/setup_ubuntu.sh"
  fi
  echo "setup_ubuntu.sh not found or not executable" >&2
  exit 1
fi

if [ "$missing" -ne 0 ]; then
  echo "Bootstrap check failed. Use ./scripts/launcher.sh --install for guided install path." >&2
  exit 1
fi

echo "bootstrap:ok target=$TARGET"
