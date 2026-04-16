from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = PROJECT_ROOT / "tests" / "harness" / "openwebui"
HARNESS_PATH = HARNESS_DIR / "openwebui_deep_job_eval.py"


def _load_harness_module():
    module_name = "openwebui_deep_job_eval_harness_test"
    if str(HARNESS_DIR) not in sys.path:
        sys.path.insert(0, str(HARNESS_DIR))
    spec = importlib.util.spec_from_file_location(module_name, HARNESS_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_tool_job_db(path: Path, job_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE tool_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT,
                error_summary TEXT,
                request_payload_blob TEXT,
                response_blob TEXT
            );
            """
        )
        for job_id in job_ids:
            conn.execute(
                """
                INSERT INTO tool_jobs(job_id, status, error_summary, request_payload_blob, response_blob)
                VALUES (?, 'completed', NULL, '{}', '{}')
                """,
                (job_id,),
            )
        conn.commit()


def test_tool_job_db_candidates_include_backend_orchestrator_root():
    harness = _load_harness_module()

    candidates = harness._tool_job_db_candidates("sqlite:///.data/orchestrator_state.db")

    assert harness.REPO_ROOT / ".data" / "orchestrator_state.db" in candidates
    assert harness.BACKEND_ROOT / ".data" / "orchestrator_state.db" in candidates
    assert harness.BACKEND_ROOT / "orchestrator" / ".data" / "orchestrator_state.db" in candidates


def test_select_tool_job_db_path_prefers_candidate_with_matching_job_id(tmp_path: Path):
    harness = _load_harness_module()
    empty_db = tmp_path / "repo-root" / ".data" / "orchestrator_state.db"
    matching_db = tmp_path / "backend" / "orchestrator" / ".data" / "orchestrator_state.db"

    _create_tool_job_db(empty_db, ["job-other"])
    _create_tool_job_db(matching_db, ["job-target", "job-other"])

    selected = harness._select_tool_job_db_path([empty_db, matching_db], ["job-target"])

    assert selected == matching_db


def test_select_tool_job_db_path_falls_back_to_existing_tool_jobs_table(tmp_path: Path):
    harness = _load_harness_module()
    empty_table_db = tmp_path / "repo-root" / ".data" / "orchestrator_state.db"
    populated_db = tmp_path / "backend" / "orchestrator" / ".data" / "orchestrator_state.db"

    _create_tool_job_db(empty_table_db, [])
    _create_tool_job_db(populated_db, ["job-one", "job-two"])

    selected = harness._select_tool_job_db_path([empty_table_db, populated_db], [])

    assert selected == populated_db


def test_extract_effective_prompt_from_request_payload_prefers_equipment_query():
    harness = _load_harness_module()

    prompt = harness._extract_effective_prompt_from_request_payload(
        {
            "equipment_query": "Сфокусируйся на процессорах.",
            "message": "Общий текст",
        }
    )

    assert prompt == "Сфокусируйся на процессорах."


def test_extract_effective_prompt_from_request_payload_falls_back_to_message():
    harness = _load_harness_module()

    prompt = harness._extract_effective_prompt_from_request_payload(
        {
            "message": "Сфокусируйся на памяти и дисках.",
            "attachments_meta": [{"name": "Requirements.pdf"}],
        }
    )

    assert prompt == "Сфокусируйся на памяти и дисках."


def test_evaluate_native_backend_processes_reports_missing_services():
    harness = _load_harness_module()

    report = harness.evaluate_native_backend_processes(
        "\n".join(
            [
                "101 python agent_api.py",
                "102 /home/seral/anaconda3/envs/diploma_llm/bin/python /tmp/uvicorn mcp_document_server:app --port 8001",
            ]
        )
    )

    assert report["status"] == "failed"
    assert sorted(report["present"]) == ["agent-api", "document-server"]
    assert sorted(report["missing"]) == ["legal-server", "ums"]


def test_evaluate_action_function_sync_detects_delivery_drift():
    harness = _load_harness_module()

    report = harness.evaluate_action_function_sync(
        {
            "equipment_deep_action": "statusHistory _record_terminal_delivery /delivery",
            "tool_job_refresh_action": "statusHistory _record_terminal_delivery /delivery",
            "tool_job_cancel_action": "statusHistory",
        },
        {
            "equipment_deep_action": "statusHistory",
            "tool_job_refresh_action": "statusHistory",
            "tool_job_cancel_action": "statusHistory",
        },
    )

    assert report["status"] == "drift"
    assert report["drift_function_ids"] == [
        "equipment_deep_action",
        "tool_job_refresh_action",
    ]
    assert report["missing_fragments"]["equipment_deep_action"] == ["/delivery", "_record_terminal_delivery"]
