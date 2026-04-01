#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
WHEELHOUSE_DIR="$BUNDLE_ROOT/wheelhouse"
BACKEND_REQ="$BUNDLE_ROOT/requirements.backend.lock.txt"
CHAINLIT_REQ="$BUNDLE_ROOT/requirements.chainlit.lock.txt"
UMS_REQ="$BUNDLE_ROOT/requirements.ums.lock.txt"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DOWNLOAD_WHEELS=1
TORCH_VERSION="${TORCH_VERSION:-2.10.0}"
TORCH_INDEX_URL="${TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu128}"

print_help() {
  cat <<'EOF'
build_wheelhouse.sh

Prepares host-side Python wheelhouse for offline bundle builds.

Downloads wheels for:
  - requirements.backend.lock.txt
  - requirements.chainlit.lock.txt
  - requirements.ums.lock.txt
  - torch==2.10.0 from cu128 wheel index

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
  --index-url "$TORCH_INDEX_URL" \
  "torch==${TORCH_VERSION}"

"$PYTHON_BIN" -m pip download \
  --dest "$WHEELHOUSE_DIR" \
  --find-links "$WHEELHOUSE_DIR" \
  -r "$BACKEND_REQ"

"$PYTHON_BIN" -m pip download \
  --dest "$WHEELHOUSE_DIR" \
  --find-links "$WHEELHOUSE_DIR" \
  -r "$CHAINLIT_REQ"

"$PYTHON_BIN" -m pip download \
  --dest "$WHEELHOUSE_DIR" \
  --find-links "$WHEELHOUSE_DIR" \
  -r "$UMS_REQ"

echo "build-wheelhouse:ok"
