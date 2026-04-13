#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openwebui_deep_job_scenarios import get_scenarios


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from bootstrap_openwebui import DEFAULT_ENV_PATH, parse_env_file, get_env_value


PLAYWRIGHT_SPEC = REPO_ROOT / "tests" / "e2e" / "openwebui" / "deep-job.spec.ts"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "output" / "openwebui-deep-job-eval"
DEFAULT_RAW_RESULTS_NAME = "results.raw.json"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Open WebUI deep-job eval slice: targeted backend checks, Playwright contour, and report enrichment."
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--scenario", action="append", dest="scenarios", default=[])
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-playwright", action="store_true")
    parser.add_argument("--openwebui-base-url", default="")
    parser.add_argument("--backend-base-url", default="")
    parser.add_argument("--matrix-timeout-ms", type=int, default=420_000)
    parser.add_argument("--poll-interval-ms", type=int, default=2_500)
    return parser.parse_args(argv)


def normalize_base_url(value: str, fallback: str) -> str:
    text = str(value or fallback).strip() or fallback
    return text.rstrip("/")


def resolve_output_dir(raw_value: str) -> Path:
    if raw_value.strip():
        return Path(raw_value).expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (DEFAULT_OUTPUT_ROOT / stamp).resolve()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n", encoding="utf-8")


def run_command(cmd: list[str], *, env: dict[str, str], cwd: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        process = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    return process.returncode


def _raw_sqlite_path_from_url(db_url: str) -> str | None:
    text = str(db_url or "").strip()
    if text.startswith("sqlite:///"):
        return text.split("sqlite:///", 1)[1]
    if text.startswith("sqlite://"):
        return text.split("sqlite://", 1)[1].lstrip("/")
    return None


def _tool_job_db_candidates(db_url: str) -> list[Path]:
    raw_path = _raw_sqlite_path_from_url(db_url)
    if not raw_path:
        return []

    candidate_roots = [
        Path.cwd(),
        REPO_ROOT,
        BACKEND_ROOT,
        BACKEND_ROOT / "orchestrator",
    ]
    candidates: list[Path] = []
    seen: set[Path] = set()

    if raw_path.startswith("/"):
        absolute_path = Path(raw_path).resolve()
        seen.add(absolute_path)
        candidates.append(absolute_path)

    for root in candidate_roots:
        candidate = (root / raw_path).resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)

    return candidates


def _tool_jobs_table_match_count(db_path: Path, job_ids: list[str]) -> tuple[int, int] | None:
    if not db_path.exists():
        return None

    with sqlite3.connect(str(db_path)) as conn:
        table_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'tool_jobs'"
        ).fetchone()
        if table_row is None:
            return None

        total_count = int(conn.execute("SELECT COUNT(*) FROM tool_jobs").fetchone()[0])
        if not job_ids:
            return (0, total_count)

        placeholders = ",".join("?" for _ in job_ids)
        match_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM tool_jobs WHERE job_id IN ({placeholders})",
                tuple(job_ids),
            ).fetchone()[0]
        )
        return (match_count, total_count)


def _select_tool_job_db_path(candidates: list[Path], job_ids: list[str]) -> Path | None:
    best_path: Path | None = None
    best_score = (-1, -1)
    for candidate in candidates:
        score = _tool_jobs_table_match_count(candidate, job_ids)
        if score is None:
            continue
        if score > best_score:
            best_score = score
            best_path = candidate
    return best_path


def resolve_tool_job_db_path(job_ids: list[str]) -> Path | None:
    sys.path.insert(0, str(BACKEND_ROOT))
    from orchestrator.tool_job_store import _resolve_tool_job_db_url  # type: ignore

    db_url = str(_resolve_tool_job_db_url()).strip()
    if not db_url.startswith("sqlite://"):
        return None
    return _select_tool_job_db_path(_tool_job_db_candidates(db_url), job_ids)


def load_tool_job_payloads(job_ids: list[str]) -> dict[str, dict[str, Any]]:
    db_path = resolve_tool_job_db_path(job_ids)
    if db_path is None or not db_path.exists() or not job_ids:
        return {}

    placeholders = ",".join("?" for _ in job_ids)
    query = (
        "SELECT job_id, status, error_summary, request_payload_blob, response_blob "
        f"FROM tool_jobs WHERE job_id IN ({placeholders})"
    )
    payloads: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(query, tuple(job_ids)).fetchall()
        except sqlite3.OperationalError:
            return {}
    for row in rows:
        payloads[str(row["job_id"])] = {
            "status": row["status"],
            "error_summary": row["error_summary"],
            "request_payload": json.loads(row["request_payload_blob"]) if row["request_payload_blob"] else None,
            "response_payload": json.loads(row["response_blob"]) if row["response_blob"] else None,
        }
    return payloads


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _extract_effective_prompt_from_request_payload(request_payload: dict[str, Any]) -> str:
    equipment_query = str(request_payload.get("equipment_query") or "").strip()
    if equipment_query:
        return equipment_query
    return str(request_payload.get("message") or "").strip()


def result_text(run: dict[str, Any]) -> str:
    final_chat = run.get("final_chat") or {}
    result_message = (final_chat.get("result_message") or {}) if isinstance(final_chat, dict) else {}
    accepted_message = (final_chat.get("accepted_message") or {}) if isinstance(final_chat, dict) else {}
    result_excerpt = normalize_text(result_message.get("content_excerpt"))
    if result_excerpt:
        return result_excerpt
    return normalize_text(accepted_message.get("content_excerpt"))


def prompt_focus_matches(query: str, fragments: list[str]) -> bool:
    normalized_query = normalize_text(query)
    if not normalized_query:
        return False
    return all(normalize_text(fragment) in normalized_query for fragment in fragments if normalize_text(fragment))


def classify_runs(runs: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[str(run.get("pair_key") or "")].append(run)

    pair_sensitivity: dict[str, str] = {}
    for pair_key, pair_runs in grouped.items():
        if len(pair_runs) < 2:
            continue
        ordered = sorted(pair_runs, key=lambda item: str(item.get("prompt_variant") or ""))
        texts = [result_text(item) for item in ordered]
        if all(texts) and len(set(texts)) == 1:
            pair_sensitivity[pair_key] = "insensitive"
        else:
            pair_sensitivity[pair_key] = "sensitive"

    for run in runs:
        request_payload = (run.get("tool_job_db") or {}).get("request_payload") or {}
        raw_equipment_query = str(request_payload.get("equipment_query") or "").strip()
        effective_prompt = _extract_effective_prompt_from_request_payload(request_payload)
        prompt_ok = prompt_focus_matches(effective_prompt, list(run.get("expected_focus_fragments") or []))
        run["raw_equipment_query"] = raw_equipment_query
        run["equipment_query"] = effective_prompt
        run["equipment_query_matches_prompt"] = prompt_ok
        run["pair_sensitivity"] = pair_sensitivity.get(str(run.get("pair_key") or ""), "n/a")

        job_id = str(run.get("job_id") or "").strip()
        terminal = (((run.get("backend_terminal") or {}) if isinstance(run.get("backend_terminal"), dict) else {}) or {}).get("statusPayload") or {}
        terminal_status = str(terminal.get("status") or "").strip().lower()
        ui = run.get("ui") or {}
        final_chat = run.get("final_chat") or {}
        accepted_message = (final_chat.get("accepted_message") or {}) if isinstance(final_chat, dict) else {}
        result_message = (final_chat.get("result_message") or {}) if isinstance(final_chat, dict) else {}
        final_text = normalize_text(result_message.get("content_excerpt") or accepted_message.get("content_excerpt"))
        expected_mode = str(run.get("expected_mode") or "")

        classification = "ok"
        if not run.get("action_response_ok") or not job_id:
            classification = "binding_bug"
        elif not effective_prompt or not prompt_ok:
            classification = "binding_bug"
        elif expected_mode == "negative":
            if not final_text:
                classification = "parser_weakness"
            elif any(marker in final_text for marker in ("не удалось", "не извлеч", "нет содерж", "пуст", "ошиб")):
                classification = "ok"
            else:
                classification = "hallucination_risk"
        elif terminal_status in {"failed", "cancelled"}:
            classification = "parser_weakness"
        elif terminal_status == "completed" and (
            ui.get("refresh_hidden_after") is False or ui.get("cancel_hidden_after") is False
        ):
            classification = "ui_only_defect"
        elif run.get("pair_sensitivity") == "insensitive":
            classification = "prompt_insensitive"
        elif not ui.get("result_visible") and not ui.get("result_visible_after_refresh"):
            classification = "ui_only_defect"

        run["classification"] = classification


def enrich_runs(raw_runs: list[dict[str, Any]], scenarios: list[Any]) -> list[dict[str, Any]]:
    scenario_map = {scenario.scenario_id: scenario for scenario in scenarios}
    job_ids = [str(item.get("job_id") or "").strip() for item in raw_runs if str(item.get("job_id") or "").strip()]
    tool_jobs = load_tool_job_payloads(job_ids)

    enriched: list[dict[str, Any]] = []
    for raw in raw_runs:
        scenario = scenario_map.get(str(raw.get("scenario_id") or ""))
        merged = dict(raw)
        if scenario is not None:
            merged["expected_focus_fragments"] = list(scenario.expected_focus_fragments)
            merged["document_path"] = str(scenario.document_path)
        else:
            merged["expected_focus_fragments"] = []
        job_id = str(raw.get("job_id") or "").strip()
        merged["tool_job_db"] = tool_jobs.get(job_id)
        enriched.append(merged)

    classify_runs(enriched)
    return enriched


def build_summary_markdown(
    *,
    output_dir: Path,
    pytest_exit: int | None,
    playwright_exit: int | None,
    enriched_runs: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# Open WebUI deep-job eval")
    lines.append("")
    lines.append(f"- Output: `{output_dir}`")
    lines.append(f"- Pytest exit: `{pytest_exit}`" if pytest_exit is not None else "- Pytest exit: `skipped`")
    lines.append(f"- Playwright exit: `{playwright_exit}`" if playwright_exit is not None else "- Playwright exit: `skipped`")
    lines.append("")
    lines.append("## Matrix")
    lines.append("")
    lines.append("| Scenario | File | Terminal | Query ok | Bubble | Class |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for run in enriched_runs:
        if run.get("kind") != "matrix":
            continue
        terminal = (((run.get("backend_terminal") or {}) if isinstance(run.get("backend_terminal"), dict) else {}) or {}).get("statusPayload") or {}
        status = str(terminal.get("status") or "n/a")
        ui = run.get("ui") or {}
        bubble_visible = bool(ui.get("result_visible"))
        lines.append(
            "| {scenario} | {file} | {status} | {query_ok} | {bubble} | {classification} |".format(
                scenario=run.get("scenario_id"),
                file=run.get("document_name") or "n/a",
                status=status,
                query_ok="yes" if run.get("equipment_query_matches_prompt") else "no",
                bubble="yes" if bubble_visible else "no",
                classification=run.get("classification"),
            )
        )
    lines.append("")
    lines.append("## Current Contract")
    lines.append("")
    lines.append("- `equipment_deep_action` получает пользовательский prompt и формирует backend explicit-tool запрос к `/tool-server/tools/analyze_equipment_deep`.")
    lines.append("- `deep-job` считается принятым, если есть `job_id` и рабочий `status_url`, а запись читается из `tool_jobs`.")
    lines.append("- Итоговый backend-owned ответ сейчас материализуется через `tool_job_refresh_action` и сохраняется в `history.messages` как дочернее assistant-сообщение.")
    lines.append("- Второй обязательный модельный проход поверх deep-job результата в этом срезе не требуется.")
    lines.append("")
    lines.append("## Future Synthesis Layer")
    lines.append("")
    lines.append("- Будущий слой должен принимать `structured_result`, краткое summary и ссылки на artifacts.")
    lines.append("- Будущий слой не должен зависеть от повторной подачи полного сырого deep-отчёта в чат-модель.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    env_values = parse_env_file(Path(args.env_file))
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scenarios = get_scenarios(args.scenarios)
    manifest_path = output_dir / "manifest.json"
    write_json(
        manifest_path,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scenarios": [
                {
                    **asdict(scenario),
                    "document_path": str(scenario.document_path),
                    "expected_focus_fragments": list(scenario.expected_focus_fragments),
                }
                for scenario in scenarios
            ],
        },
    )

    openwebui_base_url = normalize_base_url(
        args.openwebui_base_url,
        get_env_value("OPENWEBUI_BASE_URL", env_values, "http://127.0.0.1:3001") or "http://127.0.0.1:3001",
    )
    backend_base_url = normalize_base_url(
        args.backend_base_url,
        get_env_value("AGENT_API_BASE_URL", env_values, "http://127.0.0.1:8000") or "http://127.0.0.1:8000",
    )

    common_env = os.environ.copy()
    common_env.update(
        {
            "OPENWEBUI_DEEP_JOB_MANIFEST": str(manifest_path),
            "OPENWEBUI_EVAL_OUTPUT_DIR": str(output_dir),
            "OPENWEBUI_BASE_URL": openwebui_base_url,
            "OPENWEBUI_BACKEND_BASE_URL": backend_base_url,
            "BASE_URL": openwebui_base_url,
            "OPENWEBUI_MATRIX_TIMEOUT_MS": str(args.matrix_timeout_ms),
            "OPENWEBUI_MATRIX_POLL_INTERVAL_MS": str(args.poll_interval_ms),
            "WEBUI_ADMIN_EMAIL": str(get_env_value("WEBUI_ADMIN_EMAIL", env_values, "admin@example.com")),
            "WEBUI_ADMIN_PASSWORD": str(get_env_value("WEBUI_ADMIN_PASSWORD", env_values, "change-me-now")),
        }
    )

    pytest_exit: int | None = None
    playwright_exit: int | None = None

    if not args.skip_pytest:
        pytest_exit = run_command(
            [
                "pytest",
                "backend/tests/test_tool_bindings_actions.py",
                "backend/tests/test_openapi_tools_api.py",
                "backend/tests/test_openwebui_legacy_patch.py",
                "-q",
            ],
            env=common_env,
            cwd=REPO_ROOT,
            log_path=output_dir / "pytest.log",
        )

    if not args.skip_playwright:
        playwright_exit = run_command(
            [
                "npm",
                "run",
                "test:e2e",
                "--",
                str(PLAYWRIGHT_SPEC),
                "--reporter=list",
            ],
            env=common_env,
            cwd=REPO_ROOT,
            log_path=output_dir / "playwright.log",
        )

    raw_results_path = output_dir / DEFAULT_RAW_RESULTS_NAME
    if raw_results_path.exists():
        raw_results = json.loads(raw_results_path.read_text(encoding="utf-8"))
    else:
        raw_results = []
    if not isinstance(raw_results, list):
        raw_results = []

    enriched_runs = enrich_runs(list(raw_results), scenarios)
    write_json(output_dir / "results.enriched.json", enriched_runs)

    summary_text = build_summary_markdown(
        output_dir=output_dir,
        pytest_exit=pytest_exit,
        playwright_exit=playwright_exit,
        enriched_runs=enriched_runs,
    )
    (output_dir / "summary.md").write_text(f"{summary_text}\n", encoding="utf-8")

    exit_code = 0
    for code in (pytest_exit, playwright_exit):
        if code not in (None, 0):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
