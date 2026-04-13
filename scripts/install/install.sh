#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
DOC_PATH="$PROJECT_ROOT/docs/scripts/installers.md"
DETECT_SCRIPT="$SCRIPT_DIR/detect_os.sh"
TARGET="native"
DRY_RUN=0
PLATFORM="auto"

print_help() {
  cat <<EOF
install.sh

Единая точка входа в guided installer llm-tools-platform.

Использование:
  ./scripts/install/install.sh --target=native --platform=auto
  ./scripts/install/install.sh --target=native --platform=ubuntu-server
  ./scripts/install/install.sh --target=container --dry-run

Флаги:
  --target=native|container
      Целевой install path. `native` выбирает platform wrapper и heavy install,
      `container` только печатает guidance без host-install действий.
  --platform=auto|ubuntu|ubuntu-server|wsl|windows
      Платформа, для которой выбирается wrapper installer.
  --dry-run
      Ничего не выполнять, только показать какой wrapper был бы вызван.
  -h, --help
      Показать эту справку.

Документация:
  $DOC_PATH
EOF
}

wrapper_for_platform() {
  case "$1" in
    ubuntu) echo "$SCRIPT_DIR/install_ubuntu.sh" ;;
    ubuntu-server) echo "$SCRIPT_DIR/install_ubuntu_server.sh" ;;
    wsl) echo "$SCRIPT_DIR/install_wsl.sh" ;;
    windows) echo "$SCRIPT_DIR/install_windows.ps1" ;;
    *)
      return 1
      ;;
  esac
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      print_help
      exit 0
      ;;
    --target=*)
      TARGET="${arg#*=}"
      ;;
    --platform=*)
      PLATFORM="${arg#*=}"
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    *)
      echo "Неизвестный аргумент install.sh: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ "$PLATFORM" == "auto" ]]; then
  PLATFORM="$(bash "$DETECT_SCRIPT" --print-platform)"
fi

if [ "${LLM_TOOLS_PLATFORM_TEST_MODE:-0}" = "1" ]; then
  WRAPPER_PATH="$(wrapper_for_platform "$PLATFORM" 2>/dev/null || true)"
  COORDINATOR="$(basename "${WRAPPER_PATH:-unsupported}")"
  echo "install:test-mode target=$TARGET platform=$PLATFORM dry_run=$DRY_RUN coordinator=$COORDINATOR"
  exit 0
fi

if [ "$TARGET" = "container" ]; then
  echo "Container target does not require host installer. Use docker compose / launcher container path instead." >&2
  echo "See: $DOC_PATH" >&2
  exit 0
fi

if [ "$TARGET" != "native" ]; then
  echo "Неподдерживаемый installer target: $TARGET" >&2
  exit 1
fi

WRAPPER_PATH="$(wrapper_for_platform "$PLATFORM" 2>/dev/null || true)"
if [[ -z "${WRAPPER_PATH:-}" ]]; then
  echo "Неподдерживаемая installer platform: $PLATFORM" >&2
  echo "See: $DOC_PATH" >&2
  exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "install.sh dry-run: would execute $WRAPPER_PATH for target=$TARGET platform=$PLATFORM"
  exit 0
fi

if [[ "$PLATFORM" == "windows" ]]; then
  if [ ! -f "$WRAPPER_PATH" ]; then
    echo "Windows installer script not found: $WRAPPER_PATH" >&2
    exit 1
  fi
  echo "Windows installer entrypoint: $WRAPPER_PATH" >&2
  echo "Run it from PowerShell, for example:" >&2
  echo "  powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1" >&2
  exit 0
fi

if [ ! -x "$WRAPPER_PATH" ]; then
  echo "Installer wrapper not found or not executable: $WRAPPER_PATH" >&2
  exit 1
fi

exec "$WRAPPER_PATH"
