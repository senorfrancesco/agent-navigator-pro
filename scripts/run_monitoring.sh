#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

docker compose --profile monitoring up -d prometheus grafana

echo "Monitoring stack started:"
echo "  Prometheus: http://localhost:9090"
echo "  Grafana:    http://localhost:3002"
