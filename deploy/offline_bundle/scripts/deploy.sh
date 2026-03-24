#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$BUNDLE_ROOT/compose.offline.yaml"
ENV_FILE="$BUNDLE_ROOT/env.bundle"

print_help() {
  cat <<'EOF'
deploy.sh

Canonical offline deploy entrypoint for release/v1.0.

Flow:
  1. check_host.sh
  2. validate_bundle.py
  3. preflight_runtime.py
  4. load_images.sh
  5. restore_state.sh
  6. docker compose up -d
  7. verify_runtime.sh

Flags:
  --skip-image-load    Do not run load_images.sh
  --skip-host-check    Do not run check_host.sh
EOF
}

SKIP_IMAGE_LOAD=0
SKIP_HOST_CHECK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --skip-image-load)
      SKIP_IMAGE_LOAD=1
      shift
      ;;
    --skip-host-check)
      SKIP_HOST_CHECK=1
      shift
      ;;
    *)
      echo "unknown-arg:$1" >&2
      exit 1
      ;;
  esac
done

cd "$BUNDLE_ROOT"

if [[ "$SKIP_HOST_CHECK" -ne 1 ]]; then
  bash "$SCRIPT_DIR/check_host.sh"
  if [[ -f "$BUNDLE_ROOT/host_packages/ubuntu-24.04/versions.lock.json" || -f "$BUNDLE_ROOT/host_packages/ubuntu-22.04/versions.lock.json" ]]; then
    python3 "$SCRIPT_DIR/check_host_packages.py"
    python3 "$SCRIPT_DIR/verify_host_runtime.py"
  fi
fi

python3 "$SCRIPT_DIR/validate_bundle.py" --mode deploy
python3 "$SCRIPT_DIR/preflight_runtime.py" --env-file "$ENV_FILE"

if [[ "$SKIP_IMAGE_LOAD" -ne 1 ]]; then
  bash "$SCRIPT_DIR/load_images.sh"
fi

bash "$SCRIPT_DIR/restore_state.sh"

docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps
bash "$SCRIPT_DIR/verify_runtime.sh"

echo "deploy:ok"
