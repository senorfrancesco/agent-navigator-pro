#!/bin/bash

# Convenience wrapper: container-based launch path
# Keeps scripts naming explicit (native vs container).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/run_all.sh"
