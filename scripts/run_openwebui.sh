#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
WEBUI_PORT="${OPEN_WEBUI_PORT:-3001}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<EOF
run_openwebui.sh

legacy/eval helper для Open WebUI contour.
Скрипт сохраняется для обратной совместимости, но не поднимает backend runtime и не заменяет canonical запуск через launcher.sh.

Использование:
  ./scripts/run_openwebui.sh

Флаги:
  У скрипта нет пользовательских флагов.
  -h, --help
      Показать эту справку.
EOF
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "Ошибка: docker не найден в PATH." >&2
    exit 1
fi

echo "Запуск Open WebUI evaluation contour через docker compose profile legacy..."
echo "Backend должен быть поднят отдельно через canonical runtime path."

cd "$PROJECT_ROOT"
docker compose --profile legacy up -d open-webui

echo ""
echo "Open WebUI доступен на: http://localhost:${WEBUI_PORT}"
echo "Логи: docker compose logs --tail=200 -f open-webui"
echo "Остановка: docker compose --profile legacy stop open-webui"
