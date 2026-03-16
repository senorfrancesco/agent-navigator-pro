#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
DOC_PATH="$PROJECT_ROOT/docs/scripts/installers.md"

is_wsl() {
  [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null
}

if [[ "${1:-}" == "--help" ]]; then
  cat <<EOF
install_wsl.sh

WSL installer wrapper for Agent Navigator.
Runs Linux-side setup after validating WSL environment.
See: $DOC_PATH
EOF
  exit 0
fi

if [ "${AGENT_NAVIGATOR_TEST_MODE:-0}" = "1" ]; then
  echo "install-wsl:test-mode next=$SCRIPT_DIR/install_ubuntu.sh"
  exit 0
fi

if ! is_wsl; then
  echo "WSL environment not detected. See $DOC_PATH" >&2
  exit 1
fi

echo "WSL detected. Ensure Docker Desktop WSL integration is enabled on the Windows host before continuing."
export AGENT_NAVIGATOR_INSTALL_PROFILE="wsl"
exec bash "$SCRIPT_DIR/install_ubuntu.sh"
