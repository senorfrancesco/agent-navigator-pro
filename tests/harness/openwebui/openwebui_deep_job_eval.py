#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from openwebui_deep_job_scenarios import get_scenarios


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
BOOTSTRAP_SCRIPT = REPO_ROOT / "scripts" / "bootstrap_openwebui.py"
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from bootstrap_openwebui import (
    DEFAULT_ENV_PATH,
    get_env_value,
    parse_env_file,
)


PLAYWRIGHT_SPEC = REPO_ROOT / "tests" / "e2e" / "openwebui" / "deep-job.spec.ts"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "output" / "openwebui-deep-job-eval"
DEFAULT_RAW_RESULTS_NAME = "results.raw.json"
NATIVE_BACKEND_PROCESS_MARKERS = {
    "agent-api": "python agent_api.py",
    "document-server": "uvicorn mcp_document_server:app",
    "legal-server": "uvicorn mcp_legal_server:app",
    "ums": "python services/model_manager/unified_model_server.py",
}


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


def run_command_capture(cmd: list[str], *, env: dict[str, str], cwd: Path, log_path: Path) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    output = process.stdout or ""
    log_path.write_text(output, encoding="utf-8")
    return process.returncode, output


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


def _replace_host(value: str, host: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return value
    netloc = host
    if parsed.port is not None:
        netloc = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def probe_http_endpoint(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read(256).decode("utf-8", "ignore")
            return {
                "url": url,
                "ok": True,
                "status_code": int(getattr(response, "status", 200)),
                "body_excerpt": payload,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(256).decode("utf-8", "ignore")
        return {
            "url": url,
            "ok": False,
            "status_code": int(exc.code),
            "error": str(exc),
            "body_excerpt": body,
        }
    except Exception as exc:  # pragma: no cover - exercised via integration contour
        return {
            "url": url,
            "ok": False,
            "status_code": None,
            "error": str(exc),
        }


def evaluate_native_backend_processes(ps_output: str) -> dict[str, Any]:
    present: list[str] = []
    missing: list[str] = []
    for service_name, marker in NATIVE_BACKEND_PROCESS_MARKERS.items():
        if marker in ps_output:
            present.append(service_name)
        else:
            missing.append(service_name)
    return {
        "status": "ok" if not missing else "failed",
        "present": present,
        "missing": missing,
    }


def _build_container_probe_urls(backend_base_url: str, env_values: dict[str, str]) -> dict[str, str]:
    ums_base_url = normalize_base_url(
        get_env_value("AGENT_API_UMS_URL", env_values, "http://127.0.0.1:8090") or "http://127.0.0.1:8090",
        "http://127.0.0.1:8090",
    )
    return {
        "agent-api": _replace_host(f"{backend_base_url}/health", "host.docker.internal"),
        "ums": _replace_host(f"{ums_base_url}/health", "host.docker.internal"),
    }


def run_mixed_runtime_preflight(
    *,
    env: dict[str, str],
    env_values: dict[str, str],
    cwd: Path,
    output_dir: Path,
    openwebui_base_url: str,
    backend_base_url: str,
) -> dict[str, Any]:
    endpoint_checks = [
        {"name": "openwebui", **probe_http_endpoint(f"{openwebui_base_url}/")},
        {"name": "agent-api", **probe_http_endpoint(f"{backend_base_url}/health")},
        {"name": "tool-server-openapi", **probe_http_endpoint(f"{backend_base_url}/tool-server/openapi.json")},
        {"name": "document-server", **probe_http_endpoint("http://127.0.0.1:8001/health")},
        {"name": "legal-server", **probe_http_endpoint("http://127.0.0.1:8002/health")},
        {"name": "ums", **probe_http_endpoint("http://127.0.0.1:8090/health")},
    ]

    _, ps_output = run_command_capture(
        ["ps", "-eo", "pid,cmd"],
        env=env,
        cwd=cwd,
        log_path=output_dir / "preflight.processes.log",
    )
    process_report = evaluate_native_backend_processes(ps_output)

    container_probe_urls = _build_container_probe_urls(backend_base_url, env_values)
    probe_code = (
        "import json, urllib.request\n"
        f"targets = {json.dumps(container_probe_urls, ensure_ascii=False)}\n"
        "results = {}\n"
        "for name, url in targets.items():\n"
        "    try:\n"
        "        with urllib.request.urlopen(url, timeout=5) as response:\n"
        "            results[name] = {'ok': True, 'status_code': getattr(response, 'status', 200), 'url': url}\n"
        "    except Exception as exc:\n"
        "        results[name] = {'ok': False, 'error': str(exc), 'url': url}\n"
        "print(json.dumps(results, ensure_ascii=False))\n"
    )
    container_probe_exit, container_probe_output = run_command_capture(
        [
            "docker",
            "compose",
            "--profile",
            "legacy",
            "exec",
            "-T",
            "open-webui",
            "python",
            "-c",
            probe_code,
        ],
        env=env,
        cwd=cwd,
        log_path=output_dir / "preflight.container.log",
    )
    try:
        container_probe = json.loads(container_probe_output.strip() or "{}")
    except json.JSONDecodeError:
        container_probe = {
            "status": "failed",
            "output": container_probe_output.strip(),
        }
    container_probe_status = (
        "ok"
        if container_probe_exit == 0
        and isinstance(container_probe, dict)
        and container_probe
        and all(bool(item.get("ok")) for item in container_probe.values() if isinstance(item, dict))
        else "failed"
    )

    status = "ok"
    if any(not bool(item.get("ok")) for item in endpoint_checks):
        status = "failed"
    if process_report["status"] != "ok":
        status = "failed"
    if container_probe_status != "ok":
        status = "failed"

    return {
        "status": status,
        "endpoints": endpoint_checks,
        "native_backend_processes": process_report,
        "openwebui_container_probe": {
            "status": container_probe_status,
            "checks": container_probe,
        },
    }


def run_openwebui_bootstrap(
    *,
    env: dict[str, str],
    cwd: Path,
    output_dir: Path,
    env_file: Path,
    openwebui_base_url: str,
    backend_base_url: str,
) -> dict[str, Any]:
    exit_code, output = run_command_capture(
        [
            sys.executable,
            str(BOOTSTRAP_SCRIPT),
            "--backend-base-url",
            backend_base_url,
            "--openwebui-base-url",
            openwebui_base_url,
            "--env-file",
            str(env_file),
        ],
        env=env,
        cwd=cwd,
        log_path=output_dir / "bootstrap.log",
    )
    payload: dict[str, Any] | None = None
    if output.strip():
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = None
    return {
        "status": "ok" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "payload": payload,
    }


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
        if str(run.get("kind") or "") != "matrix":
            if str(run.get("kind") or "") == "native":
                run["classification"] = str(run.get("launch_classification") or "n/a")
            continue
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
    runtime_preflight: dict[str, Any] | None,
    bootstrap_report: dict[str, Any] | None,
) -> str:
    lines: list[str] = []
    lines.append("# Open WebUI deep-job eval")
    lines.append("")
    lines.append(f"- Output: `{output_dir}`")
    lines.append(f"- Pytest exit: `{pytest_exit}`" if pytest_exit is not None else "- Pytest exit: `skipped`")
    lines.append(f"- Playwright exit: `{playwright_exit}`" if playwright_exit is not None else "- Playwright exit: `skipped`")
    lines.append("")
    lines.append("## Preflight")
    lines.append("")
    if runtime_preflight is None:
        lines.append("- Runtime preflight: `skipped`")
    else:
        lines.append(f"- Runtime preflight: `{runtime_preflight.get('status')}`")
    if bootstrap_report is None:
        lines.append("- Bootstrap sync: `skipped`")
    else:
        lines.append(f"- Bootstrap sync: `{bootstrap_report.get('status')}`")
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
    native_runs = [run for run in enriched_runs if run.get("kind") == "native"]
    if native_runs:
        lines.append("## Native")
        lines.append("")
        lines.append("| Scenario | Launch | Tool | Job | Tool job |")
        lines.append("| --- | --- | --- | --- | --- |")
        for run in native_runs:
            tool_job_db = (run.get("tool_job_db") or {}) if isinstance(run.get("tool_job_db"), dict) else {}
            lines.append(
                "| {scenario} | {launch} | {tool} | {job} | {tool_job} |".format(
                    scenario=run.get("scenario_id"),
                    launch=run.get("launch_classification") or "n/a",
                    tool=run.get("native_tool_name") or "n/a",
                    job=run.get("job_id") or "n/a",
                    tool_job=tool_job_db.get("status") or "missing",
                )
            )
            persisted_output = str(run.get("persisted_function_call_output") or "").strip()
            if persisted_output:
                lines.append(f"  - persisted output: `{persisted_output[:240]}`")
        lines.append("")
    lines.append("## Current Contract")
    lines.append("")
    lines.append("- Long-running инструмент запускается только через нативный tool surface `Open WebUI`, а состояние читается из `tool_jobs`.")
    lines.append("- Запуск считается подтверждённым только при наличии `job_id` и рабочего `status_url`.")
    lines.append("- Итоговый backend-owned ответ материализуется нативной панелью `Open WebUI` без отдельного legacy-контура.")
    lines.append("- Основной пользовательский путь: панель long-running выполнения, автообновление состояния, `Stop`, terminal result и `Download report`.")
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
    runtime_preflight = run_mixed_runtime_preflight(
        env=common_env,
        env_values=env_values,
        cwd=REPO_ROOT,
        output_dir=output_dir,
        openwebui_base_url=openwebui_base_url,
        backend_base_url=backend_base_url,
    )
    write_json(output_dir / "runtime-preflight.json", runtime_preflight)

    bootstrap_report: dict[str, Any] | None = None
    if runtime_preflight["status"] == "ok":
        bootstrap_report = run_openwebui_bootstrap(
            env=common_env,
            cwd=REPO_ROOT,
            output_dir=output_dir,
            env_file=Path(args.env_file).resolve(),
            openwebui_base_url=openwebui_base_url,
            backend_base_url=backend_base_url,
        )
        write_json(output_dir / "bootstrap.json", bootstrap_report)
    else:
        bootstrap_report = {"status": "skipped", "reason": "runtime_preflight_failed"}
        write_json(output_dir / "bootstrap.json", bootstrap_report)

    if not args.skip_pytest:
        pytest_exit = run_command(
            [
                "pytest",
                "backend/tests/test_tool_bindings_actions.py",
                "backend/tests/test_openapi_tools_api.py",
                "-q",
            ],
            env=common_env,
            cwd=REPO_ROOT,
            log_path=output_dir / "pytest.log",
        )

    if not args.skip_playwright:
        if runtime_preflight["status"] != "ok" or (bootstrap_report or {}).get("status") != "ok":
            playwright_exit = 1
            (output_dir / "playwright.log").write_text(
                "Playwright contour skipped because runtime preflight or bootstrap sync failed.\n",
                encoding="utf-8",
            )
        else:
            common_env["OPENWEBUI_SKIP_AUTOSYNC"] = "1"
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
        runtime_preflight=runtime_preflight,
        bootstrap_report=bootstrap_report,
    )
    (output_dir / "summary.md").write_text(f"{summary_text}\n", encoding="utf-8")

    exit_code = 0
    for code in (pytest_exit, playwright_exit):
        if code not in (None, 0):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
