from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.operator_ui_api import (
    OperatorConfigApplyRequest,
    OperatorConfigPresetPreviewRequest,
    build_operator_state,
    operator_path_browser,
    operator_path_browser_validate,
    operator_config_apply,
    operator_config_preset_preview,
    operator_config_variants,
    operator_action_run,
    operator_health,
    OperatorActionRequest,
    operator_runtime_health,
    operator_metrics_summary,
    operator_grafana_links,
)


def test_operator_ui_state_exposes_python_control_plane_contract():
    state = build_operator_state()

    assert state["delivery"]["control_plane"] == "python"
    assert state["delivery"]["ui_mode"] == "web-first"
    assert state["delivery"]["shell_role"] == "compatibility"
    assert state["delivery"]["actions_backend"] == "python_operator_runner"


def test_operator_health_exposes_delivery_contract():
    payload = operator_health()

    assert payload["status"] == "ok"
    assert payload["operator_ui_served"] is True
    assert payload["delivery"]["control_plane"] == "python"
    assert payload["delivery"]["shell_role"] == "compatibility"


def test_operator_ui_state_exposes_observability_contract():
    state = build_operator_state()

    assert "metricsSummary" in state
    assert "grafanaLinks" in state
    assert {"overview", "services", "deploy"} <= set(state["metricsSummary"].keys())
    assert state["warnings"] == [] or "titleEn" in state["warnings"][0]
    assert "noteEn" in state["serviceRows"][0]
    assert "titleEn" in state["maintenanceActions"][0]
    assert "labelEn" in state["deploySurface"]["build"]


def test_operator_metrics_and_grafana_endpoints_return_payloads():
    request = SimpleNamespace(url=SimpleNamespace(port=18000))
    metrics_payload = operator_metrics_summary(request)
    grafana_payload = operator_grafana_links(request)

    assert {"overview", "services", "deploy"} <= set(metrics_payload.keys())
    assert "links" in grafana_payload


def test_operator_metrics_summary_exposes_timing_summary():
    metrics_payload = operator_metrics_summary(SimpleNamespace(url=SimpleNamespace(port=18000)))

    for section in ("overview", "services", "deploy"):
        assert isinstance(metrics_payload[section], list)
    assert "timingSummary" in metrics_payload
    assert {"latest", "by_executor"} <= set(metrics_payload["timingSummary"].keys())


def test_operator_config_apply_rejects_unknown_path():
    try:
        operator_config_apply(
            "missing",
            OperatorConfigApplyRequest(updates={"FOO": "bar"}),
        )
    except Exception as exc:  # FastAPI raises HTTPException directly in function tests
        assert getattr(exc, "status_code", None) == 404
    else:  # pragma: no cover - should not happen
        raise AssertionError("unknown path should raise HTTPException")


def test_operator_config_variants_and_preset_preview_return_payloads():
    variants = operator_config_variants("container")
    preview = operator_config_preset_preview(
        "container",
        OperatorConfigPresetPreviewRequest(preset_id="bundle-local-safe-ports"),
    )

    assert "variants" in variants
    assert preview["presetId"] == "bundle-local-safe-ports"
    assert preview["updates"]["AGENT_API_PORT"] == "18000"


def test_operator_runtime_health_returns_summary_for_path():
    payload = operator_runtime_health("native", SimpleNamespace(url=SimpleNamespace(port=18000)))

    assert payload["pathKey"] == "native"
    assert "status" in payload
    assert "checkCount" in payload
    assert "summary" in payload
    assert "reason" in payload
    assert "nextAction" in payload


def test_operator_runtime_health_marks_container_as_blocked_with_actionable_summary():
    payload = operator_runtime_health("container", SimpleNamespace(url=SimpleNamespace(port=8000)))

    assert payload["pathKey"] == "container"
    assert payload["status"] == "blocked"
    assert payload["reason"]
    assert payload["summary"]
    assert payload["nextAction"]


def test_operator_runtime_health_does_not_mark_native_running_from_static_files(monkeypatch):
    def fake_probe(url: str, timeout: float = 0.75) -> bool:
        return False

    monkeypatch.setattr("orchestrator.operator_ui_api._http_probe", fake_probe)

    payload = operator_runtime_health("native", SimpleNamespace(url=SimpleNamespace(port=18000)))

    assert payload["pathKey"] == "native"
    assert payload["status"] == "not_started"
    assert payload["runningChecks"] == 0


def test_operator_runtime_health_marks_native_running_from_live_primary_probes(monkeypatch):
    def fake_probe(url: str, timeout: float = 0.75) -> bool:
        return any(port in url for port in (":8000", ":3000", ":8090"))

    monkeypatch.setattr("orchestrator.operator_ui_api._http_probe", fake_probe)

    payload = operator_runtime_health("native", SimpleNamespace(url=SimpleNamespace(port=18000)))

    assert payload["pathKey"] == "native"
    assert payload["status"] == "running"


def test_operator_state_marks_container_rows_not_started_without_bundle_runtime(monkeypatch):
    monkeypatch.setattr("orchestrator.operator_ui_api._offline_bundle_running_services", lambda _root: set())
    monkeypatch.setattr("orchestrator.operator_ui_api._http_probe", lambda _url, timeout=0.75: True)

    state = build_operator_state(18000)
    container_rows = [row for row in state["serviceRows"] if row["path"] == "container"]

    assert container_rows
    assert all(row["status"] == "not_started" for row in container_rows)


def test_operator_state_marks_partial_container_runtime_when_compose_services_missing(monkeypatch):
    monkeypatch.setattr("orchestrator.operator_ui_api._offline_bundle_running_services", lambda _root: {"agent-api", "chainlit"})
    monkeypatch.setattr("orchestrator.operator_ui_api._http_probe", lambda _url, timeout=0.75: True)

    state = build_operator_state(18000)
    container_rows = [row for row in state["serviceRows"] if row["path"] == "container"]

    degraded = [row for row in container_rows if row["status"] == "degraded"]
    assert degraded


@pytest.mark.asyncio
async def test_operator_action_run_returns_job_snapshot(monkeypatch):
    fake_job = SimpleNamespace(
        to_dict=lambda: {
            "job_id": "job-123",
            "action_id": "runtime.native.launch",
            "status": "queued",
        }
    )
    monkeypatch.setattr(
        "orchestrator.operator_ui_api.start_action_job",
        AsyncMock(return_value=fake_job),
    )

    payload = await operator_action_run(
        SimpleNamespace(url=SimpleNamespace(port=9000)),
        OperatorActionRequest(action_id="runtime.native.launch"),
    )

    assert payload["job_id"] == "job-123"
    assert payload["status"] == "queued"


@pytest.mark.asyncio
async def test_operator_action_run_accepts_runtime_stop_action(monkeypatch):
    fake_job = SimpleNamespace(
        to_dict=lambda: {
            "job_id": "job-stop-123",
            "action_id": "runtime.native.stop",
            "status": "queued",
        }
    )
    monkeypatch.setattr(
        "orchestrator.operator_ui_api.start_action_job",
        AsyncMock(return_value=fake_job),
    )

    payload = await operator_action_run(
        SimpleNamespace(url=SimpleNamespace(port=9000)),
        OperatorActionRequest(action_id="runtime.native.stop"),
    )

    assert payload["job_id"] == "job-stop-123"
    assert payload["action_id"] == "runtime.native.stop"


def test_operator_path_browser_lists_allowed_roots():
    payload = operator_path_browser()

    assert payload["kind"] == "file"
    assert payload["cwd"] == ""
    assert any(entry["type"] == "directory" for entry in payload["entries"])


def test_operator_path_browser_validate_accepts_repo_path():
    payload = operator_path_browser_validate(path=str(Path.cwd()), kind="directory")

    assert payload["exists"] is True
    assert payload["valid"] is True


def test_operator_path_browser_validate_reports_model_file_semantics(tmp_path, monkeypatch):
    candidate = tmp_path / "model.gguf"
    candidate.write_text("weights", encoding="utf-8")
    monkeypatch.setattr("orchestrator.operator_ui_api.PATH_BROWSER_ROOTS", [tmp_path])

    payload = operator_path_browser_validate(
        path=str(candidate),
        kind="file",
        field_key="HOST_MODEL_PATH_LLM",
    )

    assert payload["valid"] is True
    assert payload["status"] == "ok"
    assert "GGUF extension detected" in payload["checks"]


@pytest.mark.asyncio
async def test_operator_action_run_blocks_bundle_port_conflict():
    with pytest.raises(Exception) as exc_info:
        await operator_action_run(
            SimpleNamespace(url=SimpleNamespace(port=8000)),
            OperatorActionRequest(action_id="deploy.runtime.run"),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert "port-conflict:agent-api:8000" in str(getattr(exc_info.value, "detail", ""))
