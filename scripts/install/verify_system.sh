#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
DOC_PATH="$PROJECT_ROOT/docs/scripts/installers.md"

if [[ "${1:-}" == "--help" ]]; then
  cat <<EOF
verify_system.sh

Scaffold-only post-install verification step for the future installer framework.
See: $DOC_PATH
EOF
  exit 0
fi

echo "verify_system.sh is a scaffold only. See $DOC_PATH" >&2
exit 2
