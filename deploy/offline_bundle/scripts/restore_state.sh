#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
MANIFEST_PATH="$BUNDLE_ROOT/manifest.json"

usage_error() {
  echo "$1" >&2
  exit 1
}

ensure_dir() {
  local dir_path="$1"
  mkdir -p "$dir_path"
}

validate_manifest_required_files() {
  python3 - "$MANIFEST_PATH" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
if not manifest_path.exists():
    print(f"missing:{manifest_path}")
    sys.exit(2)

payload = json.loads(manifest_path.read_text(encoding="utf-8"))
missing = []
checksum_mismatch = []
for rel_path in payload.get("required_state_paths", []):
    path = manifest_path.parent / rel_path
    if not path.exists():
        missing.append(rel_path)
for rel_path in payload.get("required_state_files", []):
    path = manifest_path.parent / rel_path
    if not path.exists():
        missing.append(rel_path)
        continue
    expected = payload.get("checksums", {}).get(rel_path)
    if expected:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            checksum_mismatch.append(rel_path)

if missing:
    for rel_path in missing:
        print(f"missing-required:{rel_path}")
    sys.exit(1)

if checksum_mismatch:
    for rel_path in checksum_mismatch:
        print(f"checksum-mismatch:{rel_path}")
    sys.exit(1)

print("manifest-required-state:ok")
PY
}

print_help() {
  cat <<'EOF'
restore_state.sh

Проверяет, что bundle содержит обязательный runtime state, и готовит локальные каталоги
для compose.offline.yaml. Скрипт не тянет данные из сети и не меняет файлы вне bundle root.

Flags:
  --allow-missing-manifest   Разрешить запуск без manifest.json
EOF
}

ALLOW_MISSING_MANIFEST=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --allow-missing-manifest)
      ALLOW_MISSING_MANIFEST=true
      shift
      ;;
    *)
      usage_error "unknown-arg:$1"
      ;;
  esac
done

for dir_path in \
  "$BUNDLE_ROOT/state/backend-data" \
  "$BUNDLE_ROOT/state/chainlit-data" \
  "$BUNDLE_ROOT/state/uploads" \
  "$BUNDLE_ROOT/state/legacy-orchestrator-files" \
  "$BUNDLE_ROOT/state/exported-env" \
  "$BUNDLE_ROOT/models"; do
  ensure_dir "$dir_path"
done

if [[ -f "$MANIFEST_PATH" ]]; then
  validate_manifest_required_files
elif [[ "$ALLOW_MISSING_MANIFEST" == true ]]; then
  echo "skip-missing-manifest:$MANIFEST_PATH"
else
  usage_error "missing-manifest:$MANIFEST_PATH"
fi

find "$BUNDLE_ROOT/state" -type d -empty -exec touch {}/.gitkeep \;
find "$BUNDLE_ROOT/models" -type d -empty -exec touch {}/.gitkeep \;

echo "restore-state:ok"
