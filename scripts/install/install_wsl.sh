#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
DOC_PATH="$PROJECT_ROOT/docs/scripts/installers.md"

is_wsl() {
  [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<EOF
install_wsl.sh

Wrapper для WSL install-path.
Проверяет, что запуск идёт внутри WSL, и затем делегирует в Linux-side setup.

Использование:
  ./scripts/install/install_wsl.sh

Флаги:
  -h, --help
      Показать эту справку.

Документация:
  $DOC_PATH
EOF
  exit 0
fi

if [ "${LLM_TOOLS_PLATFORM_TEST_MODE:-0}" = "1" ]; then
  echo "install-wsl:test-mode next=$SCRIPT_DIR/install_ubuntu.sh"
  exit 0
fi

if ! is_wsl; then
  echo "WSL environment not detected. See $DOC_PATH" >&2
  exit 1
fi

cat <<EOF
WSL detected.

Expected Docker path for WSL:
- Docker Desktop is running on Windows host
- WSL integration is enabled for this distro
- docker command is visible inside WSL

If docker is not available inside WSL, the installer will not silently continue.
It will print guidance and ask whether to skip the Docker step or stop.
See: $DOC_PATH
EOF
export LLM_TOOLS_PLATFORM_INSTALL_PROFILE="wsl"
exec bash "$SCRIPT_DIR/install_ubuntu.sh"
