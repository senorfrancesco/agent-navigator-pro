#!/usr/bin/env bash

set -euo pipefail

HOST_BUNDLE_SUPPORTED_DISTROS=("ubuntu-22.04" "ubuntu-24.04")

detect_host_bundle_distro() {
  if [[ ! -f /etc/os-release ]]; then
    return 1
  fi

  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    return 1
  fi

  case "${VERSION_ID:-}" in
    22.04)
      printf '%s\n' "ubuntu-22.04"
      ;;
    24.04)
      printf '%s\n' "ubuntu-24.04"
      ;;
    *)
      return 1
      ;;
  esac
}

host_bundle_root_for_distro() {
  local bundle_root="$1"
  local distro="$2"
  printf '%s\n' "$bundle_root/host_packages/$distro"
}

print_host_system_info() {
  local pretty_name="unknown"
  local version_id="unknown"
  local host_variant="unknown"

  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    pretty_name="${PRETTY_NAME:-unknown}"
    version_id="${VERSION_ID:-unknown}"
  fi

  if command -v dpkg-query >/dev/null 2>&1; then
    if dpkg-query -W -f='${Status}' ubuntu-server >/dev/null 2>&1; then
      host_variant="server"
    elif dpkg-query -W -f='${Status}' ubuntu-desktop >/dev/null 2>&1; then
      host_variant="desktop"
    fi
  fi

  echo "system-info:pretty-name=$pretty_name"
  echo "system-info:version-id=$version_id"
  echo "system-info:variant=$host_variant"
  echo "system-info:arch=$(uname -m)"
  echo "system-info:kernel=$(uname -r)"
}

select_host_bundle_distro_interactive() {
  local detected="${1:-}"
  echo "Выбор пакетной матрицы для host install bundle"
  print_host_system_info
  echo
  echo "Поддерживаемые варианты:"
  echo "  1) ubuntu-24.04"
  echo "  2) ubuntu-22.04"
  if [[ -n "$detected" ]]; then
    echo "  detected: $detected"
  fi
  echo

  local default_choice="1"
  if [[ "$detected" == "ubuntu-22.04" ]]; then
    default_choice="2"
  fi

  local choice
  read -r -p "Выберите целевой дистрибутив [${default_choice}]: " choice
  choice="${choice:-$default_choice}"

  case "$choice" in
    1|ubuntu-24.04)
      printf '%s\n' "ubuntu-24.04"
      ;;
    2|ubuntu-22.04)
      printf '%s\n' "ubuntu-22.04"
      ;;
    *)
      echo "unsupported-selection:$choice" >&2
      return 1
      ;;
  esac
}

assert_supported_host_bundle_distro() {
  local distro="$1"
  case "$distro" in
    ubuntu-22.04|ubuntu-24.04)
      ;;
    *)
      echo "unsupported-distro:$distro" >&2
      return 1
      ;;
  esac
}
