#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_ENV_FILE="${AGENT_NAVIGATOR_BACKEND_ENV_FILE:-$PROJECT_ROOT/backend/.env}"
BACKEND_ENV_TEMPLATE_FILE="${AGENT_NAVIGATOR_BACKEND_ENV_TEMPLATE_FILE:-$PROJECT_ROOT/backend/.env.example}"
INSTALL_COORDINATOR_SCRIPT="$SCRIPT_DIR/install/install.sh"
DEFAULT_CHAINLIT_AUTH_SECRET="agent-navigator-secret-key-change-me"

MODE="check"
TARGET="native"
PLATFORM="auto"

print_help() {
  cat <<EOF
bootstrap_env.sh

Подготавливает bootstrap-окружение перед запуском или install-path.
Скрипт создаёт backend/.env из шаблона при отсутствии, проверяет critical secrets
и в режиме install делегирует в scripts/install/install.sh.

Использование:
  ./scripts/bootstrap_env.sh --check --target=native
  ./scripts/bootstrap_env.sh --install --target=native --platform=ubuntu

Флаги:
  --check
      Режим проверки bootstrap-слоя. Ничего не устанавливает, подготавливает .env
      и валидирует обязательные секреты для дальнейшего запуска.
  --install
      Режим install-handoff. После bootstrap-подготовки передаёт управление
      unified installer coordinator.
  --target=native|container
      Целевой режим. Обычно используется native; container нужен для согласованного
      CLI-контракта и install guidance.
  --platform=auto|ubuntu|ubuntu-server|wsl|windows
      Платформа install-path. Важен только вместе с --install.
  -h, --help
      Показать эту справку.

Примеры:
  ./scripts/bootstrap_env.sh --check --target=native
  ./scripts/bootstrap_env.sh --install --target=native --platform=wsl
EOF
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      print_help
      exit 0
      ;;
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

ensure_env_file() {
  if [ -f "$BACKEND_ENV_FILE" ]; then
    return 0
  fi

  if [ ! -f "$BACKEND_ENV_TEMPLATE_FILE" ]; then
    echo "backend env template not found: $BACKEND_ENV_TEMPLATE_FILE" >&2
    exit 1
  fi

  mkdir -p "$(dirname "$BACKEND_ENV_FILE")"
  cp "$BACKEND_ENV_TEMPLATE_FILE" "$BACKEND_ENV_FILE"
  echo "env-created:$BACKEND_ENV_FILE"
}

source_backend_env() {
  if [ -f "$BACKEND_ENV_FILE" ]; then
    set -a
    source "$BACKEND_ENV_FILE"
    set +a
  fi
}

generate_chainlit_auth_secret() {
  python -c 'import secrets; print(secrets.token_urlsafe(48))'
}

upsert_env_var() {
  local key="$1"
  local value="$2"
  local tmp_file
  tmp_file="$(mktemp)"

  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    $0 ~ ("^" key "=") {
      print key "=\"" value "\""
      replaced = 1
      next
    }
    { print }
    END {
      if (!replaced) {
        print key "=\"" value "\""
      }
    }
  ' "$BACKEND_ENV_FILE" > "$tmp_file"

  mv "$tmp_file" "$BACKEND_ENV_FILE"
}

ensure_chainlit_auth_secret() {
  local env_created="$1"
  local current="${CHAINLIT_AUTH_SECRET:-}"
  local event=""

  if [ -z "$current" ]; then
    event="secret-generated"
  elif [ "$current" = "$DEFAULT_CHAINLIT_AUTH_SECRET" ]; then
    if [ "$env_created" = "1" ]; then
      event="secret-generated"
    else
      event="secret-regenerated"
    fi
  fi

  if [ -z "$event" ]; then
    return 0
  fi

  local generated_secret
  generated_secret="$(generate_chainlit_auth_secret)"
  upsert_env_var "CHAINLIT_AUTH_SECRET" "$generated_secret"
  export CHAINLIT_AUTH_SECRET="$generated_secret"
  echo "$event:CHAINLIT_AUTH_SECRET"
}

env_created=0
if [ ! -f "$BACKEND_ENV_FILE" ]; then
  ensure_env_file
  env_created=1
fi

source_backend_env
ensure_chainlit_auth_secret "$env_created"
source_backend_env

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
