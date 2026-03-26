#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$(dirname "$BUNDLE_ROOT")")"
IMAGES_DIR="$BUNDLE_ROOT/images"
BACKEND_APP_IMAGE="${BACKEND_APP_IMAGE:-agent-nav-backend-app-offline:v1.0}"
UMS_IMAGE="${UMS_IMAGE:-agent-nav-ums-offline:v1.0}"
CHAINLIT_IMAGE="${CHAINLIT_IMAGE:-agent-nav-chainlit-offline:v1.0}"
VLLM_IMAGE="${VLLM_IMAGE:-agent-nav-vllm-offline:v1.0}"
BUILD_IMAGES=1
UMS_BUILD_MODE="${UMS_BUILD_MODE:-offline}"

print_help() {
  cat <<'EOF'
export_images.sh

Builds or exports the expected offline images into images/.
This script runs on the connected build machine, not on the offline target host.

Для локальной offline-сборки UMS заранее ожидаются:
  - deploy/offline_bundle/vendor/llama.cpp
  - deploy/offline_bundle/wheelhouse с torch cu128 и Python wheels

Flags:
  --skip-build   Do not build backend-app/ums/chainlit images before export
  --ums-build-mode <offline|connected>
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --skip-build)
      BUILD_IMAGES=0
      shift
      ;;
    --ums-build-mode)
      UMS_BUILD_MODE="${2:-}"
      if [[ -z "$UMS_BUILD_MODE" ]]; then
        echo "missing-value:--ums-build-mode" >&2
        exit 1
      fi
      shift 2
      ;;
    *)
      echo "unknown-arg:$1" >&2
      exit 1
      ;;
  esac
done

case "$UMS_BUILD_MODE" in
  offline|connected)
    ;;
  *)
    echo "invalid-ums-build-mode:$UMS_BUILD_MODE" >&2
    exit 1
    ;;
esac

mkdir -p "$IMAGES_DIR"

require_ums_build_inputs() {
  if [[ ! -f "$BUNDLE_ROOT/vendor/llama.cpp/CMakeLists.txt" ]]; then
    echo "missing:vendor/llama.cpp/CMakeLists.txt" >&2
    echo "hint: clone https://github.com/ggml-org/llama.cpp into deploy/offline_bundle/vendor/llama.cpp" >&2
    exit 1
  fi
  if [[ ! -d "$BUNDLE_ROOT/wheelhouse" ]]; then
    echo "missing:wheelhouse-dir" >&2
    echo "hint: run bash deploy/offline_bundle/scripts/build_wheelhouse.sh before building UMS image" >&2
    exit 1
  fi
  if ! find "$BUNDLE_ROOT/wheelhouse" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' -o -name '*.zip' \) | grep -q .; then
    echo "missing:wheelhouse" >&2
    echo "hint: run bash deploy/offline_bundle/scripts/build_wheelhouse.sh before building UMS image" >&2
    exit 1
  fi
}

if [[ "$BUILD_IMAGES" -eq 1 ]]; then
  if [[ "$UMS_BUILD_MODE" == "offline" ]]; then
    require_ums_build_inputs
    UMS_DOCKERFILE="$BUNDLE_ROOT/Dockerfile.ums.offline"
  else
    UMS_DOCKERFILE="$BUNDLE_ROOT/Dockerfile.ums.connected"
  fi

  docker build \
    -f "$BUNDLE_ROOT/Dockerfile.backend.offline" \
    -t "$BACKEND_APP_IMAGE" \
    "$PROJECT_ROOT"

  docker build \
    -f "$UMS_DOCKERFILE" \
    -t "$UMS_IMAGE" \
    "$PROJECT_ROOT"

  docker build \
    -f "$BUNDLE_ROOT/Dockerfile.chainlit.offline" \
    -t "$CHAINLIT_IMAGE" \
    "$PROJECT_ROOT"
fi

save_image() {
  local image_ref="$1"
  local archive_name="$2"
  if docker image inspect "$image_ref" >/dev/null 2>&1; then
    echo "saving:$image_ref -> $archive_name"
    docker save -o "$IMAGES_DIR/$archive_name" "$image_ref"
  else
    echo "skip-missing-image:$image_ref"
  fi
}

save_image "$BACKEND_APP_IMAGE" "agent-nav-backend-app-offline_v1.0.tar"
save_image "$UMS_IMAGE" "agent-nav-ums-offline_v1.0.tar"
save_image "$CHAINLIT_IMAGE" "agent-nav-chainlit-offline_v1.0.tar"
save_image "$VLLM_IMAGE" "agent-nav-vllm-offline_v1.0.tar"

echo "export-images:ok"
