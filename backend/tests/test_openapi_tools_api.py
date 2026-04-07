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


def test_tool_server_openapi_requires_bearer_token(monkeypatch):
    monkeypatch.setenv("OPENAPI_TOOL_SERVER_TOKEN", "tool-secret")

    client = TestClient(agent_api.app)
    response = client.get("/tool-server/openapi.json")

    assert response.status_code == 401


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
    assert "/health" not in payload["paths"]
    assert "/v1/chat/completions" not in payload["paths"]
    assert "/execute_orchestration" not in payload["paths"]


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


def test_async_tool_route_returns_accepted_contract(monkeypatch):
    async def fake_execute(orchestration_request, http_request=None):
        return {
            "status": "accepted",
            "tool_name": "analyze_document_deep",
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
    assert payload["job_id"] == "job-1"


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
