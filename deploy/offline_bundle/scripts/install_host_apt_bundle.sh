#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/host_bundle_common.sh"

CHECK_ONLY=0
RECONFIGURE_DOCKER_RUNTIME=1
MANUAL_DRIVER=0
DISTRO=""
HOST_ROOT=""
LOCK_PATH=""
HOST_INSTALL_STATE_DIR=""
MANUAL_DRIVER_MARKER=""

validate_host_bundle_layout() {
  local host_root="$1"
  local lock_path="$2"
  local required_paths=(
    "$lock_path"
    "$host_root/pool"
    "$host_root/Packages.gz"
    "$host_root/Release"
  )

  for required_path in "${required_paths[@]}"; do
    if [[ ! -e "$required_path" ]]; then
      echo "missing-host-bundle-path:$required_path" >&2
      exit 1
    fi
  done
}

usage() {
  cat <<'EOF'
install_host_apt_bundle.sh

Устанавливает или проверяет host packages для Ubuntu 22.04/24.04 только из локального apt-bundle.

Flow:
  1. Показать информацию о системе и выбрать пакетную матрицу
  2. Сверить exact версии пакетов с versions.lock.json
  3. Установить missing/mismatched пакеты из локального pool/
  4. Настроить nvidia runtime для Docker
  5. Прогнать version-aware host verification

Flags:
  --distro <name>           ubuntu-22.04 или ubuntu-24.04 (если не задано, будет интерактивный выбор)
  --manual-driver           Не ставить и не требовать пакетный nvidia-driver-*, считать драйвер установленным вручную
  --check-only              Только проверить, без установки
  --skip-runtime-configure  Не вызывать nvidia-ctk runtime configure
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --distro)
      DISTRO="$2"
      shift 2
      ;;
    --distro=*)
      DISTRO="${1#*=}"
      shift
      ;;
    --manual-driver)
      MANUAL_DRIVER=1
      shift
      ;;
    --check-only)
      CHECK_ONLY=1
      shift
      ;;
    --skip-runtime-configure)
      RECONFIGURE_DOCKER_RUNTIME=0
      shift
      ;;
    *)
      echo "unknown-arg:$1" >&2
      exit 1
      ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "must-run-as-root" >&2
  exit 1
fi

detected_distro=""
if detected_distro="$(detect_host_bundle_distro 2>/dev/null)"; then
  :
else
  detected_distro=""
fi

if [[ -z "$DISTRO" ]]; then
  if [[ -t 0 ]]; then
    DISTRO="$(select_host_bundle_distro_interactive "$detected_distro")"
  elif [[ -n "$detected_distro" ]]; then
    DISTRO="$detected_distro"
  else
    echo "unsupported-os-and-no-distro-selected" >&2
    exit 1
  fi
fi

assert_supported_host_bundle_distro "$DISTRO"

if [[ -n "$detected_distro" && "$DISTRO" != "$detected_distro" ]]; then
  echo "distro-mismatch:selected-$DISTRO-detected-$detected_distro" >&2
  exit 1
fi

HOST_ROOT="$(host_bundle_root_for_distro "$BUNDLE_ROOT" "$DISTRO")"
LOCK_PATH="$HOST_ROOT/versions.lock.json"
HOST_INSTALL_STATE_DIR="$BUNDLE_ROOT/state/host-install"
MANUAL_DRIVER_MARKER="$HOST_INSTALL_STATE_DIR/manual-driver"

if [[ ! -f "$LOCK_PATH" ]]; then
  echo "missing-lock-file:$LOCK_PATH" >&2
  exit 1
fi

validate_host_bundle_layout "$HOST_ROOT" "$LOCK_PATH"

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  check_args=(--distro "$DISTRO")
  if [[ "$MANUAL_DRIVER" -eq 1 ]]; then
    check_args+=(--manual-driver)
  fi
  python3 "$SCRIPT_DIR/check_host_packages.py" "${check_args[@]}"
  echo "host-apt-install-check:ok"
  exit 0
fi

tmp_sources="$(mktemp)"
tmp_targets="$(mktemp)"
trap 'rm -f "$tmp_sources" "$tmp_targets"' EXIT
cat > "$tmp_sources" <<EOF
deb [trusted=yes] file:$HOST_ROOT ./
EOF

apt-get \
  -o Dir::Etc::sourcelist="$tmp_sources" \
  -o Dir::Etc::sourceparts="-" \
  update

python3 - <<'PY' "$LOCK_PATH" "$MANUAL_DRIVER" > "$tmp_targets"
import json
import subprocess
import sys
from pathlib import Path

lock = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
manual_driver = sys.argv[2] == "1"
targets = []
for package in lock.get("packages", []):
    name = package["name"]
    if manual_driver and name.startswith("nvidia-driver-"):
        continue
    version = package["version"]
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Version}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != version:
        targets.append(f"{name}={version}")
for item in targets:
    print(item)
PY

if [[ -s "$tmp_targets" ]]; then
  mapfile -t install_targets < "$tmp_targets"
  apt-get \
    -o Dir::Etc::sourcelist="$tmp_sources" \
    -o Dir::Etc::sourceparts="-" \
    install -y --no-install-recommends "${install_targets[@]}"
else
  echo "all-host-packages-already-correct"
fi

mkdir -p "$HOST_INSTALL_STATE_DIR"
if [[ "$MANUAL_DRIVER" -eq 1 ]]; then
  : > "$MANUAL_DRIVER_MARKER"
else
  rm -f "$MANUAL_DRIVER_MARKER"
fi

if [[ "$RECONFIGURE_DOCKER_RUNTIME" -eq 1 ]]; then
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi

check_args=(--distro "$DISTRO")
if [[ "$MANUAL_DRIVER" -eq 1 ]]; then
  check_args+=(--manual-driver)
fi
python3 "$SCRIPT_DIR/check_host_packages.py" "${check_args[@]}"

if [[ "$RECONFIGURE_DOCKER_RUNTIME" -eq 1 ]]; then
  python3 "$SCRIPT_DIR/verify_host_runtime.py"
else
  echo "runtime-configure-skipped"
fi

echo "install-host-apt-bundle:ok"
