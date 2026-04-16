#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict


DEFAULT_OPERATOR_BASE_URL = "http://127.0.0.1:8000"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read operator Qdrant diagnostics and validate namespace separation between backend session RAG and native Open WebUI Knowledge."
    )
    parser.add_argument("--operator-base-url", default=DEFAULT_OPERATOR_BASE_URL)
    parser.add_argument("--output", help="Optional path for JSON report output.")
    parser.add_argument("--expect-openwebui-collections", action="store_true")
    parser.add_argument("--expect-session-points", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def fetch_summary(operator_base_url: str) -> Dict[str, Any]:
    url = f"{operator_base_url.rstrip('/')}/operator/rag/qdrant/summary"
    request = urllib.request.Request(url, headers={"User-Agent": "qdrant-namespace-smoke/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def evaluate_summary(
    summary: Dict[str, Any],
    *,
    expect_openwebui_collections: bool,
    expect_session_points: bool,
) -> Dict[str, Any]:
    issues = list((summary.get("namespaces") or {}).get("issues") or [])
    if not bool((summary.get("namespaces") or {}).get("separationOk")):
        if not issues:
            issues.append("namespace separation check failed")

    openwebui_collections = list((summary.get("openWebUI") or {}).get("collections") or [])
    if expect_openwebui_collections and not openwebui_collections:
        issues.append("native Knowledge collections were expected but not detected")

    session_count = int(((summary.get("payloads") or {}).get("sessionCount")) or 0)
    handoff_enabled = bool(((summary.get("sessionHandoff") or {}).get("enabled")))
    if expect_session_points and not handoff_enabled:
        issues.append("backend session handoff is disabled in Open WebUI runtime")
    if expect_session_points and session_count <= 0:
        issues.append("backend session points were expected but not detected")

    return {
        "ok": not issues,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        summary = fetch_summary(args.operator_base_url)
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "ok": False,
            "issues": [f"failed to fetch operator qdrant summary: {exc}"],
            "summary": None,
        }
        if args.output:
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2

    evaluation = evaluate_summary(
        summary,
        expect_openwebui_collections=args.expect_openwebui_collections,
        expect_session_points=args.expect_session_points,
    )
    report = {
        "ok": evaluation["ok"],
        "issues": evaluation["issues"],
        "summary": summary,
    }
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if evaluation["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
