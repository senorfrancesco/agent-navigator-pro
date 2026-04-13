#!/usr/bin/env bash
set -euo pipefail

PATCH_DIR="/llm-tools-platform-patch"
EXPECTED_VERSION="0.8.12"
ACTUAL_VERSION="$(python -c 'import json; from pathlib import Path; print(json.loads(Path("/app/package.json").read_text()).get("version", ""))')"

if [[ "${ACTUAL_VERSION}" != "${EXPECTED_VERSION}" ]]; then
  echo "Open WebUI version mismatch: expected ${EXPECTED_VERSION}, got ${ACTUAL_VERSION}" >&2
  exit 1
fi

python "${PATCH_DIR}/patch_html.py" /app/build/index.html "${PATCH_DIR}/llm_tools_platform_autopoll.js" --marker "llm-tools-platform-openwebui-autopoll-v2"

export PYTHONPATH="${PATCH_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export LLM_TOOLS_PLATFORM_OPENWEBUI_DEEP_JOB_GUARD=1

exec /app/backend/start.sh
