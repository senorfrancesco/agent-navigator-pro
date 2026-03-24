#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"

print_help() {
  cat <<'EOF'
check_host.sh

Проверяет только наличие обязательных host prerequisites для offline bundle.
Скрипт ничего не устанавливает и не исправляет автоматически.

Проверяет:
  - docker
  - docker compose plugin
  - nvidia-smi
  - наличие nvidia runtime в docker info
  - python3
  - curl
  - tmux
  - btop
  - наличие env.bundle
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  print_help
  exit 0
fi

missing=0

check_cmd() {
  local cmd="$1"
  local label="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing:$label"
    missing=1
  else
    echo "ok:$label"
  fi
}

warn_cmd() {
  local cmd="$1"
  local label="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "warning:missing-optional:$label"
  else
    echo "ok:$label"
  fi
}

check_cmd docker docker
check_cmd nvidia-smi nvidia-smi
check_cmd python3 python3
check_cmd curl curl
warn_cmd tmux tmux
warn_cmd btop btop

if docker compose version >/dev/null 2>&1; then
  echo "ok:docker-compose-plugin"
else
  echo "missing:docker-compose-plugin"
  missing=1
fi

docker_info_output="$(docker info 2>&1 || true)"
if [[ -z "$docker_info_output" ]]; then
  echo "missing:docker-daemon-access"
  echo "hint: ensure current user can access /var/run/docker.sock"
  missing=1
elif grep -qiE "permission denied|cannot connect|error during connect|is the docker daemon running" <<<"$docker_info_output"; then
  echo "missing:docker-daemon-access"
  echo "hint: ensure current user can access /var/run/docker.sock"
  missing=1
elif grep -qi "nvidia" <<<"$docker_info_output"; then
  echo "ok:nvidia-runtime"
else
  echo "missing:nvidia-runtime-in-docker-info"
  missing=1
fi

if [[ -f "$BUNDLE_ROOT/env.bundle" ]]; then
  echo "ok:env.bundle"
else
  echo "missing:env.bundle"
  echo "hint: copy env.bundle.example to env.bundle and set secrets"
  missing=1
fi

if [[ "$missing" -ne 0 ]]; then
  echo "host-check:failed"
  echo "bundle does not install missing host dependencies offline"
  exit 1
fi

echo "host-check:ok"
