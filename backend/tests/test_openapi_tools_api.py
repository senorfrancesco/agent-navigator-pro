import os
import sys
from typing import Any, Dict

from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator import agent_api


def _auth_headers(token: str = "tool-secret", origin: str | None = "http://localhost:3001") -> Dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if origin is not None:
        headers["Origin"] = origin
    return headers


def test_tool_server_openapi_allows_unauthenticated_schema_probe(monkeypatch):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")

    client = TestClient(agent_api.app)
    response = client.get("/tool-server/openapi.json", headers={"Origin": "http://localhost:3001"})

    assert response.status_code == 200
    assert "/tools/analyze_equipment_fast" in response.json()["paths"]


def test_tool_server_config_allows_unauthenticated_probe(monkeypatch):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")

    client = TestClient(agent_api.app)
    response = client.get("/tool-server/api/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["features"]["system"] is False


def test_tool_server_config_returns_terminal_compatible_payload(monkeypatch):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")

    client = TestClient(agent_api.app)
    response = client.get("/tool-server/api/config", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["features"]["system"] is False
    assert payload["server"]["name"] == "llm-tools-platform OpenAPI Tool Server"
    assert payload["toolUx"]["enabledToolNames"] == ["analyze_equipment_fast", "analyze_equipment_deep"]
    assert "ask_document" in payload["toolUx"]["deferredToolNames"]


def test_tool_server_openapi_filters_non_tool_routes(monkeypatch):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")

    client = TestClient(agent_api.app)
    response = client.get("/tool-server/openapi.json", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert "/tools/ask_document" in payload["paths"]
    assert "/tool-jobs/{job_id}" in payload["paths"]
    assert "/tool-jobs/{job_id}/result" in payload["paths"]
    assert "/tool-jobs/{job_id}/delivery" in payload["paths"]
    assert "/tool-jobs/{job_id}/cancel" in payload["paths"]
    assert "/health" not in payload["paths"]
    assert "/v1/chat/completions" not in payload["paths"]
    assert "/execute_orchestration" not in payload["paths"]
    assert payload["paths"]["/tools/analyze_equipment_fast"]["post"]["summary"] == "Быстрый анализ оборудования"
    assert "когда использовать" in payload["paths"]["/tools/analyze_equipment_fast"]["post"]["description"].lower()
    assert "deferred" in payload["paths"]["/tools/compare_documents_fast"]["post"]["description"].lower()


def test_tool_server_openapi_rejects_disallowed_origin(monkeypatch):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")

    client = TestClient(agent_api.app)
    response = client.get(
        "/tool-server/openapi.json",
        headers=_auth_headers(origin="https://evil.example"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "tool-server-origin-denied"


def test_sync_tool_route_returns_completed_contract(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(orchestration_request, http_request=None):
        captured["request"] = orchestration_request
        return {
            "assistant_message": "Штраф 10 процентов [1]",
            "trace_id": "trace-tool-1",
            "route": "document_question",
            "source_scope_summary": "session",
            "sources": [
                {
                    "source_id": 1,
                    "display_name": "contract.pdf",
                    "source_origin": "document",
                    "page": 3,
                    "quote": "Штраф 10 процентов",
                }
            ],
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/ask_document",
        headers=_auth_headers(),
        json={
            "question": "Что написано про штраф?",
            "document_refs": [{"document_id": "doc-1", "label": "contract.pdf"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["tool_name"] == "ask_document"
    assert payload["assistant_message"] == "Штраф 10 процентов [1]"
    assert payload["available_actions"] == []
    assert payload["execution_metadata"]["requested_tool"] == "ask_document"
    assert payload["execution_metadata"]["routing_mode"] == "explicit"
    assert payload["sources"][0]["source_type"] == "document"
    assert captured["request"].requested_tool == "ask_document"
    assert captured["request"].routing_mode == "explicit"
    assert captured["request"].active_doc_ids == ["doc-1"]
    assert captured["request"].resolved_model_id is None


def test_tool_route_passes_current_model_from_user_inputs(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(orchestration_request, http_request=None):
        captured["request"] = orchestration_request
        return {
            "assistant_message": "Готово",
            "trace_id": "trace-tool-model-1",
            "route": "equipment",
            "source_scope_summary": "off",
            "sources": [],
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/analyze_equipment_fast",
        headers=_auth_headers(),
        json={
            "equipment_query": "Проверь оборудование",
            "user_inputs": {"current_model_id": "qwen-14b-llm"},
        },
    )

    assert response.status_code == 200
    assert captured["request"].resolved_model_id == "qwen-14b-llm"
    assert captured["request"].ui_state["current_model_id"] == "qwen-14b-llm"


def test_tool_route_current_model_reaches_execution_effective_settings(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(payload, deps=None):
        captured["payload"] = payload
        return {
            "assistant_message": "Готово",
            "trace_id": "trace-tool-model-effective",
            "route": "equipment",
            "source_scope_summary": "off",
            "sources": [],
            "effective_settings": payload["effective_settings"],
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "_build_api_execution_dependencies", lambda request, settings: object())
    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/analyze_equipment_fast",
        headers=_auth_headers(),
        json={
            "equipment_query": "Проверь оборудование",
            "user_inputs": {"current_model_id": "qwen-vl-8b"},
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["resolved_model_id"] == "qwen-vl-8b"
    assert captured["payload"]["effective_settings"]["resolved_model_id"] == "qwen-vl-8b"


def test_tool_route_passes_current_model_from_forwarded_header(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(orchestration_request, http_request=None):
        captured["request"] = orchestration_request
        return {
            "assistant_message": "Готово",
            "trace_id": "trace-tool-model-2",
            "route": "equipment",
            "source_scope_summary": "off",
            "sources": [],
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/analyze_equipment_fast",
        headers={**_auth_headers(), "X-OpenWebUI-Model-Id": "qwen-vl-8b"},
        json={"equipment_query": "Проверь оборудование"},
    )

    assert response.status_code == 200
    assert captured["request"].resolved_model_id == "qwen-vl-8b"
    assert captured["request"].ui_state["current_model_id"] == "qwen-vl-8b"


def test_tool_route_uses_forwarded_model_when_body_contains_legacy_wrapper(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(orchestration_request, http_request=None):
        captured["request"] = orchestration_request
        return {
            "assistant_message": "Готово",
            "trace_id": "trace-tool-model-legacy-wrapper",
            "route": "equipment",
            "source_scope_summary": "off",
            "sources": [],
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/analyze_equipment_fast",
        headers={**_auth_headers(), "X-OpenWebUI-Model-Id": "qwen-14b-llm"},
        json={
            "equipment_query": "Проверь оборудование",
            "user_inputs": {"current_model_id": "llm-tools-platform"},
        },
    )

    assert response.status_code == 200
    assert captured["request"].resolved_model_id == "qwen-14b-llm"
    assert captured["request"].ui_state["current_model_id"] == "qwen-14b-llm"


def test_completed_tool_result_strips_timing_footer_but_keeps_telemetry(monkeypatch):
    async def fake_execute(orchestration_request, http_request=None):
        return {
            "assistant_message": "Готово\n\n---\nTiming / Quality\n- Полный ответ: 10 мс",
            "trace_id": "trace-tool-footer",
            "route": "equipment",
            "source_scope_summary": "off",
            "sources": [],
            "telemetry": {"elapsed_ms": 10, "quality_summary": "LLM: да"},
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/analyze_equipment_fast",
        headers=_auth_headers(),
        json={"equipment_query": "Проверь оборудование"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant_message"] == "Готово"
    assert "Timing / Quality" not in payload["assistant_message"]
    assert payload["structured_result"]["telemetry"]["elapsed_ms"] == 10


def test_async_tool_route_returns_accepted_contract(monkeypatch):
    async def fake_execute(orchestration_request, http_request=None):
        return {
            "status": "accepted",
            "tool_name": "analyze_document_deep",
            "tool_label": "Deep document analysis",
            "job_id": "job-1",
            "status_url": "/tool-jobs/job-1",
            "submitted_at": "2026-04-07T12:00:00Z",
            "execution_metadata": {
                "requested_tool": "analyze_document_deep",
                "routing_mode": "explicit",
                "execution_mode": "async",
            },
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/analyze_document_deep",
        headers=_auth_headers(),
        json={
            "analysis_goal": "Найди риски",
            "document_refs": [{"document_id": "doc-1"}],
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["tool_name"] == "analyze_document_deep"
    assert payload["tool_label"] == "Deep document analysis"
    assert payload["job_id"] == "job-1"


def test_async_tool_route_persists_forwarded_openwebui_context_and_exposes_active_job(monkeypatch):
    def fake_submit_async_tool_job(*, request_payload, deps, execute_fn, route_prefix):
        store = agent_api.get_tool_job_store()
        return store.create_job(
            tool_name=request_payload["requested_tool"],
            route_prefix=route_prefix,
            request_payload=request_payload,
            execution_metadata={
                "requested_tool": request_payload["requested_tool"],
                "routing_mode": request_payload["routing_mode"],
                "execution_mode": "async",
            },
        )

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "_build_api_execution_dependencies", lambda request, settings: object())
    monkeypatch.setattr(agent_api, "submit_async_tool_job", fake_submit_async_tool_job)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tool-server/tools/analyze_document_deep",
        headers={
            **_auth_headers(),
            "X-OpenWebUI-Chat-Id": "chat-openwebui-1",
            "X-OpenWebUI-Message-Id": "message-openwebui-9",
            "X-OpenWebUI-Locale": "en-US",
        },
        json={
            "analysis_goal": "Найди риски",
            "document_refs": [{"document_id": "doc-1"}],
        },
    )

    assert response.status_code == 202
    payload = response.json()
    job_id = payload["job_id"]

    store = agent_api.get_tool_job_store()
    job = store.get(job_id)
    assert job is not None
    assert job.request_payload["chat_id"] == "chat-openwebui-1"
    assert job.request_payload["message_id"] == "message-openwebui-9"
    assert job.request_payload["ui_locale"] == "en"

    active_response = client.get(
        "/tool-server/tool-jobs/active/chat/chat-openwebui-1",
        headers=_auth_headers(),
    )

    assert active_response.status_code == 200
    active_payload = active_response.json()
    assert active_payload["job"] is not None
    assert active_payload["job"]["job_id"] == job_id
    assert active_payload["job"]["status"] == "queued"
    assert active_payload["job"]["tool_label"] == "Deep document analysis"
    assert active_payload["job"]["result_message_id"] is None


def test_prefixed_sync_tool_route_returns_completed_contract(monkeypatch):
    async def fake_execute(orchestration_request, http_request=None):
        return {
            "assistant_message": "Готово",
            "trace_id": "trace-tool-prefixed-sync",
            "route": "equipment_analysis",
            "source_scope_summary": "session",
            "sources": [],
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tool-server/tools/analyze_equipment_fast",
        headers=_auth_headers(),
        json={
            "equipment_query": "Проверь насос НП-100",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["tool_name"] == "analyze_equipment_fast"
    assert payload["assistant_message"] == "Готово"
    assert payload["available_actions"][0]["action_id"] == "equipment.deep.direct"
    assert payload["available_actions"][0]["action_type"] == "rerun_tool"
    assert payload["available_actions"][0]["payload"]["binding_id"] == "equipment.deep.direct"


def test_prefixed_async_tool_route_returns_prefixed_status_url(monkeypatch):
    async def fake_execute(request_payload, deps=None):
        return {
            "assistant_message": "Deep analysis completed",
            "trace_id": "trace-tool-prefixed-async",
            "route": "equipment_analysis",
            "source_scope_summary": "session",
            "sources": [],
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration", fake_execute)
    monkeypatch.setattr(agent_api, "_build_api_execution_dependencies", lambda request, settings: object())

    client = TestClient(agent_api.app)
    response = client.post(
        "/tool-server/tools/analyze_equipment_deep",
        headers=_auth_headers(),
        json={
            "equipment_query": "Проверь компрессор КМ-42",
            "ui_locale": "ru-RU",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["tool_label"] == "Глубокий анализ оборудования"
    assert payload["status_url"].startswith("/tool-server/tool-jobs/")
    assert payload["available_actions"][0]["action_id"] == "tool-job.status.open"
    assert payload["available_actions"][1]["action_id"] == "tool-job.result.open"
    assert payload["available_actions"][2]["action_type"] == "cancel_job"

    status_response = client.get(payload["status_url"], headers=_auth_headers())
    assert status_response.status_code == 200
    assert status_response.json()["tool_label"] == "Глубокий анализ оборудования"


def test_tool_job_result_returns_409_before_completion(monkeypatch):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")

    store = agent_api.get_tool_job_store()
    job = store.create_job(
        tool_name="analyze_document_deep",
        route_prefix=None,
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )

    client = TestClient(agent_api.app)
    response = client.get(f"/tool-jobs/{job.job_id}/result", headers=_auth_headers())

    assert response.status_code == 409
    assert response.json()["detail"] == f"job-not-ready:{job.job_id}"


def test_tool_job_cancel_route_returns_terminal_job_status(monkeypatch):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")

    store = agent_api.get_tool_job_store()
    job = store.create_job(
        tool_name="analyze_document_deep",
        route_prefix="/tool-server",
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    store.finish_completed(job.job_id, {"assistant_message": "done"})

    client = TestClient(agent_api.app)
    response = client.post(f"/tool-server/tool-jobs/{job.job_id}/cancel", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["job_id"] == job.job_id
    assert response.json()["status"] == "completed"


def test_tool_job_delivery_route_records_result_message_id(monkeypatch):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")

    store = agent_api.get_tool_job_store()
    job = store.create_job(
        tool_name="analyze_document_deep",
        route_prefix="/tool-server",
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    store.finish_completed(job.job_id, {"assistant_message": "done"})

    client = TestClient(agent_api.app)
    response = client.post(
        f"/tool-server/tool-jobs/{job.job_id}/delivery",
        headers=_auth_headers(),
        json={"result_message_id": "assistant-result-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == job.job_id
    assert payload["status"] == "completed"
    assert payload["result_message_id"] == "assistant-result-1"


def test_tool_job_delivery_route_is_idempotent(monkeypatch):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")

    store = agent_api.get_tool_job_store()
    job = store.create_job(
        tool_name="analyze_document_deep",
        route_prefix="/tool-server",
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    store.finish_completed(job.job_id, {"assistant_message": "done"})

    client = TestClient(agent_api.app)
    first = client.post(
        f"/tool-server/tool-jobs/{job.job_id}/delivery",
        headers=_auth_headers(),
        json={"result_message_id": "assistant-result-1"},
    )
    second = client.post(
        f"/tool-server/tool-jobs/{job.job_id}/delivery",
        headers=_auth_headers(),
        json={"result_message_id": "assistant-result-2"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["result_message_id"] == "assistant-result-1"
    assert second.json()["result_message_id"] == "assistant-result-1"


def test_completed_tool_route_exposes_report_artifact_with_download_link(monkeypatch):
    async def fake_execute(orchestration_request, http_request=None):
        return {
            "assistant_message": "Готовый отчёт.\n---\n**Отчет сохранен:** `Report_Test_123.pdf`",
            "trace_id": "trace-tool-report-1",
            "route": "document_analysis",
            "source_scope_summary": "session",
            "sources": [],
            "ui_effects": {
                "generated_report": "Готовый отчёт.\n---\n**Отчет сохранен:** `Report_Test_123.pdf`",
            },
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/analyze_document_fast",
        headers=_auth_headers(),
        json={
            "analysis_goal": "Выдели риски",
            "ui_locale": "ru-RU",
            "document_refs": [{"document_id": "doc-1"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["artifacts"] == [
        {
            "artifact_id": "Report_Test_123.pdf",
            "artifact_type": "report",
            "url": "/tool-server/tool-reports/Report_Test_123.pdf",
            "title": "Скачать отчёт",
            "metadata": {
                "filename": "Report_Test_123.pdf",
                "format": "pdf",
                "download_label": "Скачать отчёт",
            },
        }
    ]


def test_completed_tool_route_localizes_report_artifact_for_english_locale(monkeypatch):
    async def fake_execute(orchestration_request, http_request=None):
        return {
            "assistant_message": "Report is ready.\n---\n**Report saved:** `Report_Test_123.pdf`",
            "trace_id": "trace-tool-report-en-1",
            "route": "document_analysis",
            "source_scope_summary": "session",
            "sources": [],
            "ui_effects": {
                "generated_report": "Report is ready.\n---\n**Report saved:** `Report_Test_123.pdf`",
            },
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/analyze_document_fast",
        headers=_auth_headers(),
        json={
            "analysis_goal": "Highlight the risks",
            "ui_locale": "en-US",
            "document_refs": [{"document_id": "doc-1"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["artifacts"] == [
        {
            "artifact_id": "Report_Test_123.pdf",
            "artifact_type": "report",
            "url": "/tool-server/tool-reports/Report_Test_123.pdf",
            "title": "Download report",
            "metadata": {
                "filename": "Report_Test_123.pdf",
                "format": "pdf",
                "download_label": "Download report",
            },
        }
    ]


def test_tool_report_download_route_serves_pdf(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))

    report_path = tmp_path / "Report_Test_123.pdf"
    report_bytes = b"%PDF-1.4 test report"
    report_path.write_bytes(report_bytes)

    client = TestClient(agent_api.app)
    response = client.get(
        "/tool-server/tool-reports/Report_Test_123.pdf",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.content == report_bytes
    assert response.headers["content-type"] == "application/pdf"
    assert "Report_Test_123.pdf" in response.headers["content-disposition"]


def test_tool_route_accepts_eval_only_session_file_ref(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(orchestration_request, http_request=None):
        captured["request"] = orchestration_request
        return {
            "assistant_message": "Найдено условие",
            "trace_id": "trace-tool-2",
            "route": "document_question",
            "source_scope_summary": "session",
            "sources": [],
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/ask_document",
        headers=_auth_headers(),
        json={
            "question": "Что нашлось?",
            "document_refs": [{"session_file_ref": "session:contract.pdf"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert captured["request"].active_doc_ids == ["session:contract.pdf"]


def test_tool_route_accepts_eval_only_file_path(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(orchestration_request, http_request=None):
        captured["request"] = orchestration_request
        return {
            "assistant_message": "Файл принят",
            "trace_id": "trace-tool-3",
            "route": "document_question",
            "source_scope_summary": "session",
            "sources": [],
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/ask_document",
        headers=_auth_headers(),
        json={
            "question": "Используй локальный файл",
            "document_refs": [{"file_path": "/tmp/contract.pdf"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert captured["request"].active_doc_ids == ["/tmp/contract.pdf"]


def test_async_tool_route_translates_openwebui_upload_paths(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_execute(orchestration_request, http_request=None):
        captured["request"] = orchestration_request
        return {
            "status": "accepted",
            "tool_name": "analyze_equipment_deep",
            "job_id": "job-path-1",
            "status_url": "/tool-server/tool-jobs/job-path-1",
            "submitted_at": "2026-04-12T20:00:00Z",
            "available_actions": [],
            "execution_metadata": {
                "requested_tool": "analyze_equipment_deep",
                "routing_mode": "explicit",
                "execution_mode": "async",
            },
        }

    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")
    monkeypatch.setenv("HOST_UPLOADS_DIR", "/host/uploads")
    monkeypatch.setenv("OPENWEBUI_UPLOADS_CONTAINER_DIR", "/app/backend/data/uploads")
    monkeypatch.setattr(agent_api, "execute_orchestration_api", fake_execute)

    client = TestClient(agent_api.app)
    response = client.post(
        "/tools/analyze_equipment_deep",
        headers=_auth_headers(),
        json={
            "equipment_query": "Сравни требования и предложение по CPU и памяти.",
            "document_refs": [
                {
                    "file_id": "file-req-1",
                    "file_path": "/app/backend/data/uploads/req.pdf",
                    "label": "Requirements.pdf",
                },
                {
                    "file_id": "file-quote-1",
                    "file_path": "/app/backend/data/uploads/quote.pdf",
                    "label": "Quotation_12.pdf",
                },
            ],
            "user_inputs": {
                "session_docs": {
                    "Requirements.pdf": {
                        "path": "/app/backend/data/uploads/req.pdf",
                        "text": "Требование: CPU 8 ядер",
                    },
                    "Quotation_12.pdf": {
                        "path": "/app/backend/data/uploads/quote.pdf",
                        "text": "Предложение: CPU 2 x Xeon Silver",
                    },
                },
                "attachments_meta": [
                    {"name": "Requirements.pdf", "path": "/app/backend/data/uploads/req.pdf"},
                    {"name": "Quotation_12.pdf", "path": "/app/backend/data/uploads/quote.pdf"},
                ],
            },
        },
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-path-1"
    assert captured["request"].active_doc_ids == ["file-req-1", "file-quote-1"]
    assert captured["request"].session_docs["Requirements.pdf"]["path"] == "/host/uploads/req.pdf"
    assert captured["request"].session_docs["Quotation_12.pdf"]["path"] == "/host/uploads/quote.pdf"
    assert captured["request"].attachments_meta[0]["path"] == "/host/uploads/req.pdf"
    assert captured["request"].attachments_meta[1]["path"] == "/host/uploads/quote.pdf"


def test_debug_tool_job_transition_route_is_hidden_when_test_mode_disabled(monkeypatch):
    monkeypatch.delenv("LLM_TOOLS_PLATFORM_TEST_MODE", raising=False)

    client = TestClient(agent_api.app)
    response = client.post(
        "/debug/test/tool-jobs/job-1/transition",
        json={"status": "completed", "response": {"assistant_message": "ok"}},
    )

    assert response.status_code == 404


def test_debug_tool_job_transition_route_marks_job_completed_and_exposes_result(monkeypatch):
    monkeypatch.setenv("LLM_TOOLS_PLATFORM_TEST_MODE", "1")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_ALLOWED_ORIGINS", "http://localhost:3001")

    store = agent_api.get_tool_job_store()
    job = store.create_job(
        tool_name="analyze_equipment_deep",
        route_prefix="/tool-server",
        request_payload={"requested_tool": "analyze_equipment_deep"},
        execution_metadata={"execution_mode": "async"},
    )

    client = TestClient(agent_api.app)
    transition = client.post(
        f"/debug/test/tool-jobs/{job.job_id}/transition",
        json={
            "status": "completed",
            "response": {
                "assistant_message": "Готовый synthetic deep-job result.",
                "structured_result": {"summary": "ok"},
            },
        },
    )

    assert transition.status_code == 200
    assert transition.json()["status"] == "completed"
    assert transition.json()["job_id"] == job.job_id

    status_response = client.get(f"/tool-server/tool-jobs/{job.job_id}", headers=_auth_headers())
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"

    result_response = client.get(f"/tool-server/tool-jobs/{job.job_id}/result", headers=_auth_headers())
    assert result_response.status_code == 200
    assert result_response.json()["assistant_message"] == "Готовый synthetic deep-job result."
    assert result_response.json()["structured_result"] == {"summary": "ok"}
