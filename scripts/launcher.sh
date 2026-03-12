#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
RUNTIME_ENV_FILE="${AGENT_NAVIGATOR_RUNTIME_ENV_FILE:-$BACKEND_DIR/.env.runtime}"
TARGET="native"
PROFILE="${UMS_RUNTIME_PROFILE:-adaptive}"
NO_ATTACH=false
REPORT_ONLY=false
INSTALL=false

while [ $# -gt 0 ]; do
  case "$1" in
    --target)
      TARGET="$2"
      shift 2
      ;;
    --target=*)
      TARGET="${1#*=}"
      shift
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --profile=*)
      PROFILE="${1#*=}"
      shift
      ;;
    --no-attach)
      NO_ATTACH=true
      shift
      ;;
    --report-only)
      REPORT_ONLY=true
      shift
      ;;
    --install)
      INSTALL=true
      shift
      ;;
    *)
      echo "Неизвестный аргумент launcher.sh: $1" >&2
      exit 1
      ;;
  esac
done

if [ "$INSTALL" = true ]; then
  bash "$SCRIPT_DIR/bootstrap_env.sh" --install "--target=$TARGET"
  exit $?
fi

bash "$SCRIPT_DIR/bootstrap_env.sh" --check "--target=$TARGET"

PREFLIGHT_ARGS=("apply" "--profile" "$PROFILE" "--output" "$RUNTIME_ENV_FILE")
if [ "$REPORT_ONLY" = true ]; then
  PREFLIGHT_ARGS+=("--report-only")
fi

PREFLIGHT_OUTPUT="$(python "$SCRIPT_DIR/runtime_preflight.py" "${PREFLIGHT_ARGS[@]}")"
echo "$PREFLIGHT_OUTPUT"

if [ "$REPORT_ONLY" = true ]; then
  exit 0
fi

if [ "${AGENT_NAVIGATOR_TEST_MODE:-0}" = "1" ]; then
  echo "launcher:test-mode target=$TARGET profile=$PROFILE env_runtime=$RUNTIME_ENV_FILE no_attach=$NO_ATTACH"
  exit 0
fi

if [ "$TARGET" = "native" ]; then
  exec bash "$SCRIPT_DIR/run_native.sh" --from-launcher $([ "$NO_ATTACH" = true ] && echo "--no-attach")
elif [ "$TARGET" = "container" ]; then
  exec bash "$SCRIPT_DIR/run_all.sh" --from-launcher $([ "$NO_ATTACH" = true ] && echo "--no-attach")
else
  echo "Неподдерживаемый target: $TARGET" >&2
  exit 1
fi
