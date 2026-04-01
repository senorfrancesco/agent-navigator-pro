#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"
IMAGES_DIR="$BUNDLE_ROOT/images"

print_help() {
  cat <<'EOF'
load_images.sh

Загружает все image archives из каталога images/.
Поддерживаются:
  - *.tar
  - *.tar.gz
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  print_help
  exit 0
fi

shopt -s nullglob
archives=("$IMAGES_DIR"/*.tar "$IMAGES_DIR"/*.tar.gz)

if [[ "${#archives[@]}" -eq 0 ]]; then
  echo "no-image-archives-found:$IMAGES_DIR"
  exit 1
fi

for archive in "${archives[@]}"; do
  echo "loading:$archive"
  if [[ "$archive" == *.tar.gz ]]; then
    gzip -dc "$archive" | docker load
  else
    docker load -i "$archive"
  fi
done

echo "images-load:ok"
