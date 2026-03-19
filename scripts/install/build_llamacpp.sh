#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
DOC_PATH="$PROJECT_ROOT/docs/scripts/installers.md"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<EOF
build_llamacpp.sh

Scaffold-only шаг будущего installer framework.
Предназначен для сборки llama.cpp, но сейчас служит заглушкой и ничего не собирает.

Флаги:
  -h, --help
      Показать эту справку.

Документация:
  $DOC_PATH
EOF
  exit 0
fi

echo "build_llamacpp.sh is a scaffold only. See $DOC_PATH" >&2
exit 2
