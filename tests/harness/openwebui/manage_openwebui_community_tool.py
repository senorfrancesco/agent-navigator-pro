#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from manage_openwebui_tool import main as generic_main


FIXTURE_TOOL_ID = "community_sum_tool"
FIXTURE_TOOL_NAME = "Community Sum Tool"
FIXTURE_TOOL_DESCRIPTION = "Verification-only external tool fixture for Open WebUI native tool calling."
FIXTURE_TOOL_PATH = Path(__file__).resolve().with_name("openwebui_community_sum_tool.py")


def main(argv: list[str] | None = None) -> int:
    defaults = [
        "--tool-file",
        str(FIXTURE_TOOL_PATH),
        "--tool-id",
        FIXTURE_TOOL_ID,
        "--tool-name",
        FIXTURE_TOOL_NAME,
        "--description",
        FIXTURE_TOOL_DESCRIPTION,
    ]
    return generic_main([*defaults, *(argv or sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
