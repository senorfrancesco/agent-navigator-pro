import json
from types import SimpleNamespace

import pytest

from orchestrator.tool_bindings import build_openwebui_binding_export


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _load_action_namespace(python_code: str) -> dict:
    namespace: dict = {}
    exec(python_code, namespace)
    return namespace


def _build_body_with_last_user_message(text: str) -> dict:
    return {
        "messages": [
            {
                "role": "user",
                "content": text,
            }
        ]
    }


@pytest.mark.asyncio
async def test_equipment_deep_action_returns_structured_job_context(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    action_payload = next(item for item in export["actionFunctions"] if item["action_id"] == "equipment_deep_action")
    namespace = _load_action_namespace(action_payload["pythonCode"])
    action = namespace["Action"]()

    def fake_urlopen(request, timeout=45):
        assert request.full_url.endswith("/tools/analyze_equipment_deep")
        return _FakeHTTPResponse(
            {
                "status": "accepted",
                "job_id": "job-42",
                "status_url": "/tool-server/tool-jobs/job-42",
            }
        )

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", fake_urlopen)

    response = await action.action(_build_body_with_last_user_message("Проверь насос НП-100"))

    assert response["job_id"] == "job-42"
    assert response["status_url"] == "/tool-server/tool-jobs/job-42"
    assert response["tool_job"]["job_id"] == "job-42"
    assert response["tool_job"]["status_url"] == "/tool-server/tool-jobs/job-42"
    assert "job_id: job-42" in response["content"]


@pytest.mark.asyncio
async def test_refresh_and_cancel_actions_restore_context_from_previous_deep_action(monkeypatch):
    export = build_openwebui_binding_export(backend_base_url="http://127.0.0.1:18000")
    actions = {item["action_id"]: item for item in export["actionFunctions"]}

    deep_namespace = _load_action_namespace(actions["equipment_deep_action"]["pythonCode"])
    refresh_namespace = _load_action_namespace(actions["tool_job_refresh_action"]["pythonCode"])
    cancel_namespace = _load_action_namespace(actions["tool_job_cancel_action"]["pythonCode"])

    deep_action = deep_namespace["Action"]()
    refresh_action = refresh_namespace["Action"]()
    cancel_action = cancel_namespace["Action"]()

    def fake_deep_urlopen(request, timeout=45):
        return _FakeHTTPResponse(
            {
                "status": "accepted",
                "job_id": "job-77",
                "status_url": "/tool-server/tool-jobs/job-77",
            }
        )

    refresh_calls = []

    def fake_refresh_urlopen(request, timeout=45):
        refresh_calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/tool-server/tool-jobs/job-77"):
            return _FakeHTTPResponse(
                {
                    "job_id": "job-77",
                    "status": "completed",
                }
            )
        return _FakeHTTPResponse(
            {
                "assistant_message": "Deep job completed successfully.",
            }
        )

    cancel_calls = []

    def fake_cancel_urlopen(request, timeout=45):
        cancel_calls.append((request.get_method(), request.full_url))
        return _FakeHTTPResponse(
            {
                "job_id": "job-77",
                "status": "cancelling",
            }
        )

    monkeypatch.setattr(deep_namespace["urllib"].request, "urlopen", fake_deep_urlopen)
    deep_response = await deep_action.action(_build_body_with_last_user_message("Проверь компрессор КМ-42"))

    nested_body = {
        "previous_action_result": deep_response,
        "messages": [
            {
                "role": "assistant",
                "content": deep_response["content"],
            }
        ],
    }

    monkeypatch.setattr(refresh_namespace["urllib"].request, "urlopen", fake_refresh_urlopen)
    refresh_response = await refresh_action.action(nested_body)

    assert refresh_response["content"] == "Deep job completed successfully."
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-77") in refresh_calls
    assert ("GET", "http://host.docker.internal:18000/tool-server/tool-jobs/job-77/result") in refresh_calls

    async def fake_event_call(payload):
        assert payload["type"] == "confirm"
        return True

    monkeypatch.setattr(cancel_namespace["urllib"].request, "urlopen", fake_cancel_urlopen)
    cancel_response = await cancel_action.action(nested_body, __event_call__=fake_event_call)

    assert "job_id: job-77" in cancel_response["content"]
    assert ("POST", "http://host.docker.internal:18000/tool-server/tool-jobs/job-77/cancel") in cancel_calls
