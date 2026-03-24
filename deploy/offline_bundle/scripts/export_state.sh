#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$(dirname "$BUNDLE_ROOT")")"
BACKEND_ROOT="$PROJECT_ROOT/backend"
STATE_SRC="$BACKEND_ROOT/.data"
UPLOADS_SRC="$BACKEND_ROOT/open_webui_uploads"
LEGACY_FILES_SRC="$BACKEND_ROOT/orchestrator/.files"
ENV_SRC="$BACKEND_ROOT"

print_help() {
  cat <<'EOF'
export_state.sh

Copies runtime state into bundle-local state/ directories.
Default source paths:
  backend/.data
  backend/open_webui_uploads
  backend/orchestrator/.files
  backend/.env*

Flags:
  --source-root <path>   Override repository root
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --source-root)
      PROJECT_ROOT="$2"
      BACKEND_ROOT="$PROJECT_ROOT/backend"
      STATE_SRC="$BACKEND_ROOT/.data"
      UPLOADS_SRC="$BACKEND_ROOT/open_webui_uploads"
      LEGACY_FILES_SRC="$BACKEND_ROOT/orchestrator/.files"
      ENV_SRC="$BACKEND_ROOT"
      shift 2
      ;;
    --source-root=*)
      PROJECT_ROOT="${1#*=}"
      BACKEND_ROOT="$PROJECT_ROOT/backend"
      STATE_SRC="$BACKEND_ROOT/.data"
      UPLOADS_SRC="$BACKEND_ROOT/open_webui_uploads"
      LEGACY_FILES_SRC="$BACKEND_ROOT/orchestrator/.files"
      ENV_SRC="$BACKEND_ROOT"
      shift
      ;;
    *)
      echo "unknown-arg:$1" >&2
      exit 1
      ;;
  esac
done

mkdir -p \
  "$BUNDLE_ROOT/state/backend-data" \
  "$BUNDLE_ROOT/state/chainlit-data" \
  "$BUNDLE_ROOT/state/uploads" \
  "$BUNDLE_ROOT/state/legacy-orchestrator-files" \
  "$BUNDLE_ROOT/state/exported-env"

copy_if_exists() {
  local source_path="$1"
  local target_path="$2"
  if [[ -e "$source_path" ]]; then
    cp -a "$source_path" "$target_path"
    echo "copied:$source_path"
  else
    echo "skip-missing:$source_path"
  fi
}

rm -rf \
  "$BUNDLE_ROOT/state/backend-data/"* \
  "$BUNDLE_ROOT/state/chainlit-data/"* \
  "$BUNDLE_ROOT/state/legacy-orchestrator-files/"* \
  "$BUNDLE_ROOT/state/exported-env/"*

copy_if_exists "$STATE_SRC/orchestrator_state.db" "$BUNDLE_ROOT/state/backend-data/orchestrator_state.db"
copy_if_exists "$STATE_SRC/orchestrator_kb.db" "$BUNDLE_ROOT/state/backend-data/orchestrator_kb.db"
copy_if_exists "$STATE_SRC/ums_dynamic_models.json" "$BUNDLE_ROOT/state/backend-data/ums_dynamic_models.json"
copy_if_exists "$STATE_SRC/chainlit.db" "$BUNDLE_ROOT/state/chainlit-data/chainlit.db"
copy_if_exists "$STATE_SRC/chainlit.db-shm" "$BUNDLE_ROOT/state/chainlit-data/chainlit.db-shm"
copy_if_exists "$STATE_SRC/chainlit.db-wal" "$BUNDLE_ROOT/state/chainlit-data/chainlit.db-wal"
copy_if_exists "$ENV_SRC/.env" "$BUNDLE_ROOT/state/exported-env/.env"
copy_if_exists "$ENV_SRC/.env.runtime" "$BUNDLE_ROOT/state/exported-env/.env.runtime"
copy_if_exists "$ENV_SRC/.env.hardware.override" "$BUNDLE_ROOT/state/exported-env/.env.hardware.override"

if [[ -d "$UPLOADS_SRC" ]]; then
  rsync -rltD --delete --no-o --no-g --no-p "$UPLOADS_SRC"/ "$BUNDLE_ROOT/state/uploads"/
  echo "copied-dir:$UPLOADS_SRC"
else
  echo "skip-missing:$UPLOADS_SRC"
fi

if [[ -d "$LEGACY_FILES_SRC" ]]; then
  rsync -rltD --delete --no-o --no-g --no-p "$LEGACY_FILES_SRC"/ "$BUNDLE_ROOT/state/legacy-orchestrator-files"/
  echo "copied-legacy-dir:$LEGACY_FILES_SRC"
else
  echo "skip-missing:$LEGACY_FILES_SRC"
fi

find "$BUNDLE_ROOT/state" -type d -empty -exec touch {}/.gitkeep \;

echo "export-state:ok"
