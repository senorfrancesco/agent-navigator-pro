#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
WHEELHOUSE_DIR="$BUNDLE_ROOT/wheelhouse"
BACKEND_REQ="$BUNDLE_ROOT/requirements.backend.lock.txt"
CHAINLIT_REQ="$BUNDLE_ROOT/requirements.chainlit.lock.txt"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DOWNLOAD_WHEELS=1

print_help() {
  cat <<'EOF'
build_wheelhouse.sh

Prepares host-side Python wheelhouse for optional offline utilities.
Flags:
  --skip-download   Only create the wheelhouse directory
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --skip-download)
      DOWNLOAD_WHEELS=0
      shift
      ;;
    *)
      echo "unknown-arg:$1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$WHEELHOUSE_DIR"

if [[ "$DOWNLOAD_WHEELS" -eq 0 ]]; then
  echo "build-wheelhouse:skipped"
  exit 0
fi

"$PYTHON_BIN" -m pip download \
  --dest "$WHEELHOUSE_DIR" \
  -r "$BACKEND_REQ" \
  -r "$CHAINLIT_REQ"

echo "build-wheelhouse:ok"
