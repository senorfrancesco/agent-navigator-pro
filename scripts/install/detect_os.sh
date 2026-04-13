#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
DOC_PATH="$PROJECT_ROOT/docs/scripts/installers.md"
FORMAT="text"

print_help() {
  cat <<EOF
detect_os.sh

Безопасный detector платформы для install-path.
Safe platform detector for llm-tools-platform installer path.

Использование:
  ./scripts/install/detect_os.sh
  ./scripts/install/detect_os.sh --print-platform
  ./scripts/install/detect_os.sh --format=env

Флаги:
  --print-platform
      Печатает только итоговую платформу: windows, wsl, ubuntu, ubuntu-server или unsupported.
  --format=env
      Печатает результат в виде ENV-переменных.
  -h, --help
      Показать эту справку.

Документация:
  $DOC_PATH
EOF
}

is_wsl() {
  [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      print_help
      exit 0
      ;;
    --print-platform)
      FORMAT="platform"
      ;;
    --format=env)
      FORMAT="env"
      ;;
    *)
      echo "Неизвестный аргумент detect_os.sh: $arg" >&2
      exit 1
      ;;
  esac
done

os_id="unknown"
os_like=""
version_id="unknown"
platform="unsupported"
host_class="unknown"

if [[ "${OS:-}" == "Windows_NT" ]]; then
  platform="windows"
  host_class="desktop"
elif is_wsl; then
  platform="wsl"
  host_class="wsl"
elif [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  os_id="${ID:-unknown}"
  os_like="${ID_LIKE:-}"
  version_id="${VERSION_ID:-unknown}"
  if [[ "$os_id" == "ubuntu" ]] || [[ "$os_id" == "debian" ]] || [[ " $os_like " == *" ubuntu "* ]] || [[ " $os_like " == *" debian "* ]]; then
    if command -v systemctl >/dev/null 2>&1 && systemctl get-default 2>/dev/null | grep -qi "graphical"; then
      platform="ubuntu"
      host_class="desktop"
    elif [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
      platform="ubuntu"
      host_class="desktop"
    else
      platform="ubuntu-server"
      host_class="server"
    fi
  fi
fi

case "$FORMAT" in
  platform)
    printf '%s\n' "$platform"
    ;;
  env)
    cat <<EOF
PLATFORM=$platform
OS_ID=$os_id
OS_LIKE=$os_like
VERSION_ID=$version_id
HOST_CLASS=$host_class
EOF
    ;;
  *)
    cat <<EOF
platform=$platform
os_id=$os_id
os_like=$os_like
version_id=$version_id
host_class=$host_class
EOF
    ;;
esac
