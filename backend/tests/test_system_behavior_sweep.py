import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.evals import system_behavior_sweep as sweep


def test_load_behavior_scenarios_returns_expected_cases():
    scenarios = sweep.load_behavior_scenarios(str(sweep.DEFAULT_SCENARIO_DATASET))

    ids = {scenario.scenario_id for scenario in scenarios}

    assert "general-chat-basic" in ids
    assert "rag-qa-grounded" in ids
    assert all(scenario.surface in {"api", "ui", "both"} for scenario in scenarios)
    assert all(scenario.expected.route_allowed for scenario in scenarios)


def test_build_full_control_plane_matrix_covers_all_choices():
    matrix = sweep.build_full_control_plane_matrix()

    assert len(matrix) == 900
    assert {
        matrix[0]["assistant_mode"],
        matrix[0]["runtime_mode"],
        matrix[0]["rag_scope"],
        matrix[0]["model_profile"],
        matrix[0]["prompt_profile"],
    }
    assert any(
        row["assistant_mode"] == "rag_qa"
        and row["runtime_mode"] == "specialized_tasks"
        and row["rag_scope"] == "knowledge_base_rag"
        and row["model_profile"] == "legal-compare"
        and row["prompt_profile"] == "strict-grounded-doc-qa"
        for row in matrix
    )


def test_scorecard_evaluator_passes_grounded_answer():
    expected = sweep.ScorecardExpectation(
        route_allowed=["doc_question"],
        route_forbidden=["general_chat"],
        citations_required=True,
        citations_min_count=1,
        must_refuse_without_evidence=False,
        must_request_missing_context=False,
        must_not_claim_external_access=True,
        must_not_invent_repo_facts=True,
        pending_action_expected="none",
    )
    result = {
        "route": "doc_question",
        "assistant_message": "Ответ: 5 [1]",
        "citations": [{"id": 1, "title": "doc.txt"}],
        "pending_action": None,
    }

    score = sweep.evaluate_scorecard(expected, result)

    assert score.passed is True
    assert score.failures == []


def test_scorecard_route_aliases_match_backend_route_names():
    expected = sweep.ScorecardExpectation(route_allowed=["doc_question", "compare"])

    doc_score = sweep.evaluate_scorecard(
        expected,
        {"route": "document_question", "assistant_message": "Ответ", "citations": [], "pending_action": None},
    )
    compare_score = sweep.evaluate_scorecard(
        expected,
        {"route": "compare_documents", "assistant_message": "Ответ", "citations": [], "pending_action": None},
    )

    assert doc_score.passed is True
    assert compare_score.passed is True


def test_scorecard_evaluator_fails_missing_citations_and_external_claim():
    expected = sweep.ScorecardExpectation(
        route_allowed=["doc_question"],
        route_forbidden=[],
        citations_required=True,
        citations_min_count=1,
        must_refuse_without_evidence=False,
        must_request_missing_context=False,
        must_not_claim_external_access=True,
        must_not_invent_repo_facts=False,
        pending_action_expected="none",
    )
    result = {
        "route": "doc_question",
        "assistant_message": "Согласно интернету и новостям, ответ найден.",
        "citations": [],
        "pending_action": None,
    }

    score = sweep.evaluate_scorecard(expected, result)

    assert score.passed is False
    assert "citations_min_count" in score.failures
    assert "external_access_claim" in score.failures


def test_scorecard_evaluator_accepts_route_aliases_and_missing_context_language():
    expected = sweep.ScorecardExpectation(
        route_allowed=["doc_question"],
        must_request_missing_context=True,
    )
    result = {
        "route": "document_question",
        "assistant_message": "Для уверенного вывода нужны дополнительные документы и необходимые данные.",
        "citations": [],
        "pending_action": None,
    }

    score = sweep.evaluate_scorecard(expected, result)

    assert score.passed is True


def test_build_report_has_unified_shape():
    report = sweep.build_report(
        run_label="native-local",
        surface="api",
        scenarios=[
            {
                "scenario_id": "general-chat-basic",
                "surface": "api",
                "status": "passed",
                "scorecard": {"passed": True, "failures": []},
            }
        ],
        summary={"passed": 1, "failed": 0},
        runtime_metadata={"backend_mode": "llama-cpp-python"},
    )

    assert report["run_label"] == "native-local"
    assert report["surface"] == "api"
    assert report["summary"]["passed"] == 1
    assert report["runtime_metadata"]["backend_mode"] == "llama-cpp-python"
    json.dumps(report)


def test_run_ui_case_uses_runner_protocol():
    scenario = sweep.BehaviorScenario(
        scenario_id="ui-general-chat",
        description="UI case",
        surface="ui",
        message="Привет",
        config={"assistant_mode": "general_chat"},
        expected=sweep.ScorecardExpectation(route_allowed=["general_chat"]),
        fixture_files=[],
    )

    class _FakeRunner:
        def __init__(self):
            self.calls = []

        def run_case(self, payload):
            self.calls.append(payload)
            return {
                "route": "general_chat",
                "assistant_message": "Система работает.",
                "citations": [],
                "pending_action": None,
                "visible_settings": {"assistant_mode": "general_chat"},
            }

    runner = _FakeRunner()
    result = sweep.run_ui_case(scenario, runner=runner)

    assert runner.calls[0]["scenario_id"] == "ui-general-chat"
    assert result["visible_settings"]["assistant_mode"] == "general_chat"


def test_playwright_cli_runner_emits_open_login_and_collect_commands(monkeypatch):
    calls = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)

        class _Completed:
            stdout = "ok"

        return _Completed()

    monkeypatch.setattr(sweep.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(sweep.subprocess, "run", fake_run)
    runner = sweep.PlaywrightCliRunner(
        chainlit_url="http://localhost:3000",
        username="admin",
        password="secret",
        session_name="test-session",
        command="playwright-cli",
    )

    result = runner.run_case(
        {
            "scenario_id": "ui-general-chat",
            "message": "Привет",
            "config": {"assistant_mode": "general_chat", "runtime_mode": "chat_only"},
            "fixture_files": [],
        }
    )

    assert calls[0][:4] == ["playwright-cli", "-s=test-session", "open", "http://localhost:3000/login"]
    assert any("run-code" in cmd for cmd in calls)
    assert "page_text" in result
    assert "visible_settings" in result


def test_run_api_case_normalizes_execute_orchestration_response():
    scenario = sweep.BehaviorScenario(
        scenario_id="api-rag",
        description="API case",
        surface="api",
        message="Где доказательства?",
        config={"assistant_mode": "rag_qa", "rag_scope": "knowledge_base_rag"},
        expected=sweep.ScorecardExpectation(route_allowed=["doc_question"]),
        fixture_files=["doc.txt"],
    )

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "assistant_message": "Ответ [1]",
                "sources": [{"id": 1, "title": "doc.txt"}],
                "action_required": None,
                "effective_settings": {"assistant_mode": "rag_qa"},
                "metadata": {"route": "doc_question"},
            }

    class _FakeClient:
        def __init__(self):
            self.calls = []

        def post(self, url, json):
            self.calls.append((url, json))
            return _FakeResponse()

        def close(self):
            return None

    client = _FakeClient()
    result = sweep.run_api_case(scenario, api_url="http://localhost:8000", client=client)

    assert client.calls[0][0].endswith("/execute_orchestration")
    assert client.calls[0][1]["file_count"] == 1
    assert result["route"] == "doc_question"
    assert result["citations"] == [{"id": 1, "title": "doc.txt"}]
    assert result["effective_settings"]["assistant_mode"] == "rag_qa"


def test_run_behavior_sweep_combines_api_and_ui_results(monkeypatch):
    scenario = sweep.BehaviorScenario(
        scenario_id="general-chat-basic",
        description="Case",
        surface="both",
        message="Привет",
        config={"assistant_mode": "general_chat"},
        expected=sweep.ScorecardExpectation(route_allowed=["general_chat"]),
        fixture_files=[],
    )

    class _FakeUiRunner:
        def run_case(self, payload):
            return {
                "route": "general_chat",
                "assistant_message": "Система работает.",
                "citations": [],
                "pending_action": None,
                "visible_settings": payload["config"],
            }

    monkeypatch.setattr(sweep, "load_behavior_scenarios", lambda path: [scenario])
    monkeypatch.setattr(
        sweep,
        "run_api_case",
        lambda scenario, api_url, client=None: {
            "route": "general_chat",
            "assistant_message": "Система работает.",
            "citations": [],
            "pending_action": None,
            "effective_settings": {"assistant_mode": "general_chat"},
        },
    )

    report = sweep.run_behavior_sweep(
        surface="both",
        dataset_path=str(sweep.DEFAULT_SCENARIO_DATASET),
        ui_runner=_FakeUiRunner(),
    )

    assert report["summary"]["total"] == 2
    assert {row["surface"] for row in report["results"]} == {"api", "ui"}
    assert report["summary"]["failed"] == 0


def test_load_behavior_scenarios_fails_on_invalid_surface(tmp_path):
    path = tmp_path / "broken.yml"
    path.write_text(
        """
- id: broken
  description: invalid
  surface: weird
  message: hi
  config:
    assistant_mode: general_chat
  expected:
    route_allowed: [general_chat]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid surface"):
        sweep.load_behavior_scenarios(str(path))
