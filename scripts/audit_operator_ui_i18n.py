from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from orchestrator.operator_ui_api import build_operator_state  # noqa: E402


REQUIRED_BILINGUAL_KEYS = {
    "title",
    "description",
    "body",
    "label",
    "name",
    "role",
    "action",
    "note",
    "reason",
    "recommendedReason",
    "summaryTitle",
    "stagesTitle",
    "actionsTitle",
    "artifactsTitle",
    "logsTitle",
    "remediation",
    "pathLabel",
}


def collect_missing_bilingual_fields(payload: Any, path: str = "root") -> list[tuple[str, str, str]]:
    missing: list[tuple[str, str, str]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in REQUIRED_BILINGUAL_KEYS and isinstance(value, str):
                pair_key = f"{key}En"
                if pair_key not in payload:
                    missing.append((path, key, value))
            missing.extend(collect_missing_bilingual_fields(value, f"{path}.{key}"))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            missing.extend(collect_missing_bilingual_fields(item, f"{path}[{index}]"))
    return missing


def main() -> int:
    state = build_operator_state()
    missing = collect_missing_bilingual_fields(state)
    if not missing:
        print("operator-ui-i18n-audit:ok")
        return 0

    print("operator-ui-i18n-audit:missing")
    for path, key, value in missing:
        print(f"{path}: missing {key}En for {key}={value!r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
