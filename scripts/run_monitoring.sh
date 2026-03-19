#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<EOF
run_monitoring.sh

Поднимает monitoring stack через Docker Compose profile "monitoring".

Использование:
  ./scripts/run_monitoring.sh

Флаги:
  У скрипта нет пользовательских флагов запуска.
  -h, --help
      Показать эту справку.

После запуска:
  Prometheus: http://localhost:9090
  Grafana:    http://localhost:3002
EOF
  exit 0
fi

docker compose --profile monitoring up -d prometheus grafana

echo "Monitoring stack started:"
echo "  Prometheus: http://localhost:9090"
echo "  Grafana:    http://localhost:3002"
