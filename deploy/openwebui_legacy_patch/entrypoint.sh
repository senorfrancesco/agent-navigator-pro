#!/usr/bin/env bash
set -euo pipefail

PATCH_DIR="/agent-nav-patch"
EXPECTED_VERSION="0.8.12"
ACTUAL_VERSION="$(python -c 'import json; from pathlib import Path; print(json.loads(Path("/app/package.json").read_text()).get("version", ""))')"

if [[ "${ACTUAL_VERSION}" != "${EXPECTED_VERSION}" ]]; then
  echo "Open WebUI version mismatch: expected ${EXPECTED_VERSION}, got ${ACTUAL_VERSION}" >&2
  exit 1
fi

python "${PATCH_DIR}/patch_html.py" /app/build/index.html "${PATCH_DIR}/agent_nav_autopoll.js" --marker "agent-nav-openwebui-autopoll-v2"

export PYTHONPATH="${PATCH_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export AGENT_NAV_OPENWEBUI_DEEP_JOB_GUARD=1

exec /app/backend/start.sh
