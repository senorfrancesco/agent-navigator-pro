#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from bootstrap_openwebui import (
    DEFAULT_ENV_PATH,
    DEFAULT_OPENWEBUI_BASE_URL,
    get_env_value,
    parse_env_file,
    sign_in,
)


PLAYWRIGHT_CLI = Path.home() / ".codex/skills/playwright/scripts/playwright_cli.sh"
DEFAULT_SESSION = "openwebui-followup"
DEFAULT_TOOL_ID = "community_sum_tool"
DEFAULT_TOOL_NAME = "Community Sum Tool"
DEFAULT_FIRST_MARKER = "FOLLOWUP_DIAG_FIRST"
DEFAULT_SECOND_MARKER = "FOLLOWUP_DIAG_SECOND"
DEFAULT_FIRST_RESULT_MARKER = "COMMUNITY_TOOL_OK:18"
DEFAULT_SECOND_RESULT_MARKER = "COMMUNITY_TOOL_OK:14"
DEFAULT_FIRST_PROMPT = (
    "Use Community Sum Tool to add 7 and 11. "
    "Reply only with the tool result. "
    "[FOLLOWUP_DIAG_FIRST]"
)
DEFAULT_SECOND_PROMPT = (
    "Can you add 5 and 9 using the same tool? "
    "[FOLLOWUP_DIAG_SECOND]"
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture the first and second Open WebUI /api/chat/completions requests for a clean follow-up diagnostic."
    )
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--openwebui-base-url", default=DEFAULT_OPENWEBUI_BASE_URL)
    parser.add_argument("--tool-id", default=DEFAULT_TOOL_ID)
    parser.add_argument("--tool-name", default=DEFAULT_TOOL_NAME)
    parser.add_argument("--first-marker", default=DEFAULT_FIRST_MARKER)
    parser.add_argument("--second-marker", default=DEFAULT_SECOND_MARKER)
    parser.add_argument("--first-prompt", default=DEFAULT_FIRST_PROMPT)
    parser.add_argument("--second-prompt", default=DEFAULT_SECOND_PROMPT)
    parser.add_argument("--first-result-marker", default=DEFAULT_FIRST_RESULT_MARKER)
    parser.add_argument("--second-result-marker", default=DEFAULT_SECOND_RESULT_MARKER)
    parser.add_argument("--output", default="/tmp/openwebui_followup_payload_harness.json")
    parser.add_argument("--timeout-ms", type=int, default=180000)
    parser.add_argument("--prep-pause", action="store_true", default=True)
    parser.add_argument("--no-prep-pause", action="store_false", dest="prep_pause")
    return parser.parse_args(argv)


def run_playwright(
    session: str,
    *args: str,
    raw: bool = False,
    input_text: str | None = None,
) -> str:
    if not PLAYWRIGHT_CLI.is_file():
        raise SystemExit(f"Playwright CLI wrapper not found: {PLAYWRIGHT_CLI}")
    cmd = [str(PLAYWRIGHT_CLI), f"-s={session}"]
    if raw:
        cmd.append("--raw")
    cmd.extend(args)
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "Playwright CLI failed.\n"
            f"command: {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc.stdout.strip()


def load_admin_token(*, env_file: Path, openwebui_base_url: str) -> str:
    env_values = parse_env_file(env_file)
    admin_email = get_env_value("WEBUI_ADMIN_EMAIL", env_values, "admin@example.com")
    admin_password = get_env_value("WEBUI_ADMIN_PASSWORD", env_values, "change-me-now")
    return sign_in(openwebui_base_url, email=str(admin_email), password=str(admin_password))


def build_run_code(
    *,
    first_prompt: str,
    second_prompt: str,
    first_marker: str,
    second_marker: str,
    first_result_marker: str,
    second_result_marker: str,
    timeout_ms: int,
) -> str:
    first_prompt_js = json.dumps(first_prompt, ensure_ascii=False)
    second_prompt_js = json.dumps(second_prompt, ensure_ascii=False)
    first_marker_js = json.dumps(first_marker, ensure_ascii=False)
    second_marker_js = json.dumps(second_marker, ensure_ascii=False)
    first_result_marker_js = json.dumps(first_result_marker, ensure_ascii=False)
    second_result_marker_js = json.dumps(second_result_marker, ensure_ascii=False)
    timeout_js = json.dumps(timeout_ms)
    return textwrap.dedent(
        f"""
        async (page) => {{
          const firstPrompt = {first_prompt_js};
          const secondPrompt = {second_prompt_js};
          const firstMarker = {first_marker_js};
          const secondMarker = {second_marker_js};
          const firstResultMarker = {first_result_marker_js};
          const secondResultMarker = {second_result_marker_js};
          const resultTimeoutMs = {timeout_js};

          const captured = [];
          const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
          const addToolLike = (value, out) => {{
            if (!value) {{
              return;
            }}
            if (Array.isArray(value)) {{
              for (const item of value) {{
                addToolLike(item, out);
              }}
              return;
            }}
            if (typeof value === "string") {{
              const trimmed = value.trim();
              if (trimmed) {{
                out.add(trimmed);
              }}
              return;
            }}
            if (typeof value === "object") {{
              for (const key of ["id", "tool_id", "toolId", "name", "title", "slug", "value"]) {{
                if (key in value) {{
                  addToolLike(value[key], out);
                }}
              }}
            }}
          }};
          const extractToolRefs = (payload) => {{
            const refs = new Set();
            if (!payload || typeof payload !== "object") {{
              return [];
            }}
            const keys = [
              "tool_ids",
              "selected_tool_ids",
              "toolids",
              "selectedtoolids",
              "selected_tools",
              "tool",
              "tool_id",
              "toolid",
              "requested_tool",
              "tool_choice",
              "tools",
            ];
            for (const key of keys) {{
              if (!(key in payload)) {{
                continue;
              }}
              const value = payload[key];
              if (key === "tools" && Array.isArray(value)) {{
                for (const item of value) {{
                  addToolLike(item, refs);
                }}
                continue;
              }}
              addToolLike(value, refs);
            }}
            return Array.from(refs);
          }};

          page.on("request", (request) => {{
            if (request.method() !== "POST" || !request.url().includes("/api/chat/completions")) {{
              return;
            }}
            const rawBody = request.postData() || "";
            let parsedBody = null;
            try {{
              parsedBody = JSON.parse(rawBody);
            }} catch (error) {{
              parsedBody = null;
            }}
            captured.push({{
              url: request.url(),
              method: request.method(),
              rawBody,
              parsedBody,
              toolRefs: extractToolRefs(parsedBody),
              capturedAt: Date.now(),
            }});
          }});

          const composer = page.locator(".tiptap.ProseMirror").first();
          await composer.waitFor({{ state: "visible", timeout: 30000 }});

          const typeAndSend = async (prompt) => {{
            await composer.click();
            await page.keyboard.type(prompt, {{ delay: 20 }});
            await page.keyboard.press("Enter");
          }};

          const waitForCapture = async (marker, timeoutMs) => {{
            const deadline = Date.now() + timeoutMs;
            while (Date.now() < deadline) {{
              const found = captured.find((entry) => entry.rawBody.includes(marker));
              if (found) {{
                return found;
              }}
              await page.waitForTimeout(250);
            }}
            return null;
          }};

          const waitForResultMarker = async (timeoutMs) => {{
            try {{
              await page.waitForFunction(
                (needle) => document.body.innerText.includes(needle),
                firstResultMarker,
                {{ timeout: timeoutMs }}
              );
              return true;
            }} catch (error) {{
              return false;
            }}
          }};

          await typeAndSend(firstPrompt);
          const firstRequest = await waitForCapture(firstMarker, 30000);
          const firstResultSeen = await waitForResultMarker(resultTimeoutMs);

          await typeAndSend(secondPrompt);
          const secondRequest = await waitForCapture(secondMarker, 30000);
          const secondResultSeen = await page.waitForFunction(
            (needle) => document.body.innerText.includes(needle),
            secondResultMarker,
            {{ timeout: resultTimeoutMs }}
          ).then(() => true).catch(() => false);

          return {{
            pageUrl: page.url(),
            firstMarker,
            secondMarker,
            firstResultMarker,
            secondResultMarker,
            firstRequest,
            secondRequest,
            firstResultSeen,
            secondResultSeen,
            capturedCount: captured.length,
            captured,
            visibleTextSample: normalize(await page.locator("body").innerText()).slice(0, 4000),
          }};
        }}
        """
    ).strip()


def analyze_result(
    *,
    payload: dict[str, Any],
    tool_id: str,
    first_marker: str,
    second_marker: str,
    first_result_marker: str,
    second_result_marker: str,
) -> dict[str, Any]:
    first = payload.get("firstRequest") or {}
    second = payload.get("secondRequest") or {}
    first_refs = [str(item) for item in first.get("toolRefs") or []]
    second_refs = [str(item) for item in second.get("toolRefs") or []]
    first_has_expected = tool_id in first_refs
    second_has_expected = tool_id in second_refs
    second_extra_refs = sorted(ref for ref in second_refs if ref != tool_id)
    first_raw = str(first.get("rawBody") or "")
    second_raw = str(second.get("rawBody") or "")
    first_marker_present = first_marker in first_raw
    second_marker_present = second_marker in second_raw
    first_result_seen = bool(payload.get("firstResultSeen"))
    second_result_seen = bool(payload.get("secondResultSeen"))
    visible_text = str(payload.get("visibleTextSample") or "")

    if not first_raw or not second_raw:
        classification = "incomplete"
        reason = "Не удалось снять оба request body."
    elif not second_has_expected:
        classification = "state_bug"
        reason = "Во втором request body отсутствует ожидаемый tool_id."
    elif second_extra_refs:
        classification = "contamination"
        reason = "Во втором request body есть дополнительные tool refs."
    elif second_result_seen or second_result_marker in visible_text:
        classification = "success"
        reason = "Ожидаемый tool сохранился, и deterministic result стал видимым."
    else:
        classification = "tool_selection_or_runtime"
        reason = "Request body чистый, но deterministic result не наблюдается в UI."

    return {
        "classification": classification,
        "reason": reason,
        "toolId": tool_id,
        "firstMarker": first_marker,
        "secondMarker": second_marker,
        "expectedFirstResultMarker": first_result_marker,
        "expectedSecondResultMarker": second_result_marker,
        "firstHasExpectedTool": first_has_expected,
        "secondHasExpectedTool": second_has_expected,
        "firstMarkerPresent": first_marker_present,
        "secondMarkerPresent": second_marker_present,
        "firstResultSeen": first_result_seen,
        "secondResultSeen": second_result_seen,
        "secondExtraToolRefs": second_extra_refs,
        "firstToolRefs": first_refs,
        "secondToolRefs": second_refs,
        "firstRequestBody": first_raw,
        "secondRequestBody": second_raw,
        "pageUrl": payload.get("pageUrl"),
        "visibleTextSample": visible_text,
        "capturedCount": payload.get("capturedCount"),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    base_url = str(args.openwebui_base_url).rstrip("/")
    env_file = Path(args.env_file)
    first_prompt = args.first_prompt
    second_prompt = args.second_prompt
    if args.first_marker not in first_prompt:
        first_prompt = f"{first_prompt} [{args.first_marker}]"
    if args.second_marker not in second_prompt:
        second_prompt = f"{second_prompt} [{args.second_marker}]"

    token = load_admin_token(env_file=env_file, openwebui_base_url=base_url)

    run_playwright(args.session, "delete-data")
    run_playwright(args.session, "open", f"{base_url}/")
    run_playwright(args.session, "localstorage-set", "token", token)
    run_playwright(args.session, "reload")
    run_playwright(args.session, "goto", f"{base_url}/?temporary-chat=true")

    if args.prep_pause:
        print(
            textwrap.dedent(
                f"""
                Open WebUI готов к diagnostic harness.

                Подготовьте чистый chat:
                - включите только tool `{args.tool_name}` / `{args.tool_id}`;
                - не включайте memory/additional tools;
                - затем нажмите Enter, чтобы harness зафиксировал первый и второй POST /api/chat/completions.
                """
            ).strip()
        )
        if sys.stdin.isatty():
            input()

    run_code = build_run_code(
        first_prompt=first_prompt,
        second_prompt=second_prompt,
        first_marker=args.first_marker,
        second_marker=args.second_marker,
        first_result_marker=args.first_result_marker,
        second_result_marker=args.second_result_marker,
        timeout_ms=args.timeout_ms,
    )
    payload_raw = run_playwright(args.session, "run-code", run_code, raw=True)
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Unexpected Playwright output (not JSON): {payload_raw}") from exc

    report = analyze_result(
        payload=payload,
        tool_id=args.tool_id,
        first_marker=args.first_marker,
        second_marker=args.second_marker,
        first_result_marker=args.first_result_marker,
        second_result_marker=args.second_result_marker,
    )
    report["session"] = args.session
    report["openWebUIBaseUrl"] = base_url
    report["outputPath"] = args.output
    report["toolName"] = args.tool_name
    report["firstMarker"] = args.first_marker
    report["secondMarker"] = args.second_marker
    report["firstPrompt"] = first_prompt
    report["secondPrompt"] = second_prompt
    report["firstResultMarker"] = args.first_result_marker
    report["secondResultMarker"] = args.second_result_marker

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
