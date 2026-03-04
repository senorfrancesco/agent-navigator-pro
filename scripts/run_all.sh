#!/bin/bash

# Совместимость: Chainlit-профиль через единый bootstrap

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/bootstrap.sh" --ui chainlit "$@"
