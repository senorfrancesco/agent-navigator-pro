#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<EOF
run_container.sh

Совместимый алиас для старых container-вызовов.
Скрипт не содержит собственной orchestration-логики и сразу делегирует в:
  ./scripts/launcher.sh --target container

Целевой контейнерный контур теперь поднимает `Open WebUI` как основной UI.

Использование:
  ./scripts/run_container.sh
  ./scripts/run_container.sh --no-attach

Флаги:
  Все аргументы проксируются в launcher.sh.
  -h, --help
      Показать эту справку.
EOF
  exit 0
fi

exec bash "$SCRIPT_DIR/launcher.sh" --target container "$@"
