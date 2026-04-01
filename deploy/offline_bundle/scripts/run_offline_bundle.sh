#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$(dirname "$BUNDLE_ROOT")")"

exec python3 "$PROJECT_ROOT/backend/orchestrator/operator_shell_compat.py" offline-run --bundle-root "$BUNDLE_ROOT" "$@"
