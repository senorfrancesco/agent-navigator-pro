#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
SETUP_SCRIPT="$PROJECT_ROOT/scripts/setup_ubuntu.sh"

if [[ "${1:-}" == "--help" ]]; then
  cat <<EOF
install_ubuntu_server.sh

Ubuntu Server installer wrapper for Agent Navigator.
Delegates heavy install to scripts/setup_ubuntu.sh with server profile metadata.
EOF
  exit 0
fi

if [ "${AGENT_NAVIGATOR_TEST_MODE:-0}" = "1" ]; then
  echo "install-ubuntu-server:test-mode setup=$SETUP_SCRIPT"
  exit 0
fi

if [ ! -x "$SETUP_SCRIPT" ]; then
  echo "setup_ubuntu.sh not found or not executable: $SETUP_SCRIPT" >&2
  exit 1
fi

export AGENT_NAVIGATOR_INSTALL_PROFILE="ubuntu-server"
exec "$SETUP_SCRIPT"
