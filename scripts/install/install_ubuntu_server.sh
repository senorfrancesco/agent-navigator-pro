#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
SETUP_SCRIPT="$PROJECT_ROOT/scripts/setup_ubuntu.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<EOF
install_ubuntu_server.sh

Wrapper для Ubuntu Server install-path.
Делегирует heavy install в scripts/setup_ubuntu.sh с server-oriented профилем.

Использование:
  ./scripts/install/install_ubuntu_server.sh

Флаги:
  -h, --help
      Показать эту справку.
EOF
  exit 0
fi

if [ "${LLM_TOOLS_PLATFORM_TEST_MODE:-0}" = "1" ]; then
  echo "install-ubuntu-server:test-mode setup=$SETUP_SCRIPT"
  exit 0
fi

if [ ! -x "$SETUP_SCRIPT" ]; then
  echo "setup_ubuntu.sh not found or not executable: $SETUP_SCRIPT" >&2
  exit 1
fi

export LLM_TOOLS_PLATFORM_INSTALL_PROFILE="ubuntu-server"
exec "$SETUP_SCRIPT"
