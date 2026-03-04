#!/bin/bash

# Совместимость: Open WebUI (legacy) через единый bootstrap

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/bootstrap.sh" --ui openwebui "$@"
