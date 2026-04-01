#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$(dirname "$BUNDLE_ROOT")")"
SOURCE_ROOT="$PROJECT_ROOT/backend/models"
TARGET_ROOT="$BUNDLE_ROOT/models"
ENV_FILE="$BUNDLE_ROOT/env.bundle"

print_help() {
  cat <<'EOF'
export_models.sh

Copies local model assets into the bundle-local models/ directory.
Uses a staging directory and only replaces bundle-local models/ after
successful copy and validation.

Default source:
  backend/models

Flags:
  --source-root <path>   Override repository root
  --models-root <path>   Use explicit models directory instead of backend/models
  --env-file <path>      Validate exported layout against env file (default: env.bundle)
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
      SOURCE_ROOT="$PROJECT_ROOT/backend/models"
      shift 2
      ;;
    --source-root=*)
      PROJECT_ROOT="${1#*=}"
      SOURCE_ROOT="$PROJECT_ROOT/backend/models"
      shift
      ;;
    --models-root)
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --models-root=*)
      SOURCE_ROOT="${1#*=}"
      shift
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --env-file=*)
      ENV_FILE="${1#*=}"
      shift
      ;;
    *)
      echo "unknown-arg:$1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$TARGET_ROOT"

if [[ ! -d "$SOURCE_ROOT" ]]; then
  echo "missing-models-source:$SOURCE_ROOT"
  exit 1
fi

STAGING_ROOT="$(mktemp -d "$BUNDLE_ROOT/.models-staging.XXXXXX")"
STAGING_MODELS_DIR="$STAGING_ROOT/models"
BACKUP_ROOT="$BUNDLE_ROOT/.models-backup.$$"

cleanup() {
  rm -rf "$STAGING_ROOT"
  if [[ -d "$BACKUP_ROOT" ]]; then
    rm -rf "$BACKUP_ROOT"
  fi
}
trap cleanup EXIT

mkdir -p "$STAGING_MODELS_DIR"
rsync -rltD --delete --no-o --no-g --no-p "$SOURCE_ROOT"/ "$STAGING_MODELS_DIR"/
find "$STAGING_MODELS_DIR" -type d -empty -exec touch {}/.gitkeep \;

if [[ -f "$ENV_FILE" ]]; then
  python3 "$SCRIPT_DIR/validate_bundle.py" \
    --mode build \
    --skip-manifest \
    --env-file "$ENV_FILE" \
    --models-dir "$STAGING_MODELS_DIR"
else
  echo "skip-env-validation:$ENV_FILE"
fi

if [[ -d "$TARGET_ROOT" ]]; then
  mv "$TARGET_ROOT" "$BACKUP_ROOT"
fi
mv "$STAGING_MODELS_DIR" "$TARGET_ROOT"
rm -rf "$BACKUP_ROOT"

echo "export-models:ok"
