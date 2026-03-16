#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
DOC_PATH="$PROJECT_ROOT/docs/scripts/installers.md"

if [[ "${1:-}" == "--help" ]]; then
  cat <<EOF
system_check.sh

Scaffold-only helper layer for future installer verification helpers.
See: $DOC_PATH
EOF
  exit 0
fi

echo "system_check.sh is a scaffold only. See $DOC_PATH" >&2
exit 2
