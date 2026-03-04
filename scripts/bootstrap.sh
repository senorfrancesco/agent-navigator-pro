#!/bin/bash

# Единый entrypoint запуска Agent Navigator Pro
# Пример: ./scripts/bootstrap.sh --ui chainlit

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/common.sh"

UI_PROFILE="chainlit"

usage() {
    echo "Usage: $0 [--ui chainlit|openwebui]"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ui)
            UI_PROFILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}Неизвестный аргумент: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

if [[ "$UI_PROFILE" != "chainlit" && "$UI_PROFILE" != "openwebui" ]]; then
    echo -e "${RED}Некорректный UI профиль: $UI_PROFILE${NC}"
    usage
    exit 1
fi

echo -e "${GREEN}=== Запуск системы Agent Navigator Pro v3.0 (${UI_PROFILE}) ===${NC}"

load_env_file
prepare_runtime
start_ui_window "$UI_PROFILE"
start_backend_services
wait_for_stack "$UI_PROFILE"
print_summary "$UI_PROFILE"
