#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"

print_help() {
  cat <<'EOF'
build_bundle.sh

Build-side orchestration script for producing the offline bundle.
Runs local build/export helpers, generates manifest.json, and validates deploy-ready output.

Отдельный host apt bundle для Ubuntu 24.04 готовится через:
  bash scripts/build_host_apt_bundle.sh
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  print_help
  exit 0
fi

mkdir -p "$BUNDLE_ROOT/images" "$BUNDLE_ROOT/wheelhouse" "$BUNDLE_ROOT/state"

bash "$SCRIPT_DIR/build_wheelhouse.sh"
bash "$SCRIPT_DIR/export_images.sh"
bash "$SCRIPT_DIR/export_models.sh"
bash "$SCRIPT_DIR/export_state.sh"
python3 "$SCRIPT_DIR/generate_manifest.py"
python3 "$SCRIPT_DIR/validate_bundle.py" --mode deploy

echo "build-bundle:ok"
