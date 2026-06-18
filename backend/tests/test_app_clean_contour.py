from __future__ import annotations

from fastapi.testclient import TestClient
import httpx
import pytest


def test_chat_client_prefers_current_model_id(monkeypatch):
    from app.clients.openai_compatible import build_chat_client_config

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    config = build_chat_client_config(current_model_id="selected-model")

    assert config.base_url == "http://llm-runtime:8080/v1"
    assert config.api_key == "test-key"
    assert config.model_id == "selected-model"


def test_chat_client_uses_llm_model_id_as_fallback(monkeypatch):
    from app.clients.openai_compatible import build_chat_client_config

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    config = build_chat_client_config(current_model_id=None)

    assert config.api_key == "none"
    assert config.model_id == "fallback-model"


def test_chat_client_requires_model_id(monkeypatch):
    from app.clients.openai_compatible import ConfigurationError, build_chat_client_config

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.delenv("LLM_MODEL_ID", raising=False)

    with pytest.raises(ConfigurationError, match="LLM_MODEL_ID"):
        build_chat_client_config(current_model_id=None)


def test_tool_server_exports_openapi_and_test_tool(monkeypatch):
    from app.tool_server.server import create_tool_server_app

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(create_tool_server_app())

    schema_response = client.get("/tool-server/openapi.json")
    assert schema_response.status_code == 200
    assert "/tools/echo" in schema_response.json()["paths"]

    tool_response = client.post(
        "/tools/echo",
        json={"message": "ping", "current_model_id": "selected-model"},
    )

    assert tool_response.status_code == 200
    assert tool_response.json() == {
        "status": "completed",
        "tool_name": "echo",
        "assistant_message": "ping",
        "model_id": "selected-model",
    }


@pytest.mark.asyncio
async def test_chat_completion_uses_openai_compatible_endpoint(monkeypatch):
    from app.clients.openai_compatible import chat_completion

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Ответ по оборудованию",
                        },
                    }
                ]
            },
        )

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    result = await chat_completion(
        prompt="Проверь оборудование",
        current_model_id="selected-model",
        system_prompt="Ты анализируешь оборудование.",
        max_tokens=321,
        temperature=0.2,
        transport=httpx.MockTransport(handler),
    )

    assert result == "Ответ по оборудованию"
    assert captured["method"] == "POST"
    assert captured["url"] == "http://llm-runtime:8080/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert '"model":"selected-model"' in captured["payload"]
    assert '"max_tokens":321' in captured["payload"]
    assert '"temperature":0.2' in captured["payload"]


def test_tool_server_exports_analyze_equipment_fast(monkeypatch):
    from app.tool_server.server import create_tool_server_app

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(create_tool_server_app())
    response = client.get("/tool-server/openapi.json")

    assert response.status_code == 200
    assert "/tools/analyze_equipment_fast" in response.json()["paths"]


def test_analyze_equipment_fast_uses_current_model(monkeypatch):
    from app.tool_server import handlers
    from app.tool_server.server import create_tool_server_app

    captured = {}

    async def fake_chat_completion(**kwargs):
        captured.update(kwargs)
        return "Модель проверила оборудование.\n\n---\nTiming / Quality\nслужебные данные"

    monkeypatch.setattr(handlers, "chat_completion", fake_chat_completion)
    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(create_tool_server_app())
    response = client.post(
        "/tools/analyze_equipment_fast",
        json={
            "equipment_query": "Проверь сервер Dell",
            "current_model_id": "selected-model",
            "ui_locale": "ru-RU",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "completed",
        "tool_name": "analyze_equipment_fast",
        "assistant_message": "Модель проверила оборудование.",
        "model_id": "selected-model",
    }
    assert captured["prompt"] == "Проверь сервер Dell"
    assert captured["current_model_id"] == "selected-model"
    assert "оборудование" in captured["system_prompt"].lower()


def test_analyze_equipment_fast_uses_fallback_model(monkeypatch):
    from app.tool_server import handlers
    from app.tool_server.server import create_tool_server_app

    async def fake_chat_completion(**kwargs):
        return "Готово"

    monkeypatch.setattr(handlers, "chat_completion", fake_chat_completion)
    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(create_tool_server_app())
    response = client.post(
        "/tools/analyze_equipment_fast",
        json={"equipment_query": "Проверь оборудование"},
    )

    assert response.status_code == 200
    assert response.json()["model_id"] == "fallback-model"


def test_analyze_equipment_fast_reports_missing_model(monkeypatch):
    from app.tool_server.server import create_tool_server_app

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.delenv("LLM_MODEL_ID", raising=False)

    client = TestClient(create_tool_server_app())
    response = client.post(
        "/tools/analyze_equipment_fast",
        json={"equipment_query": "Проверь оборудование"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "tool_configuration_error",
        "message": "Не настроена LLM-модель: передайте current_model_id или задайте LLM_MODEL_ID.",
    }


def test_tool_server_exports_deep_tool_and_job_routes(monkeypatch):
    from app.tool_server.server import create_tool_server_app

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(create_tool_server_app())
    response = client.get("/tool-server/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/tools/analyze_equipment_deep" in paths
    assert "/tool-jobs/{job_id}" in paths
    assert "/tool-jobs/{job_id}/result" in paths
    assert "/tool-jobs/{job_id}/cancel" in paths


def test_analyze_equipment_deep_returns_accepted_job(monkeypatch):
    from app.tool_server.server import create_tool_server_app

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(create_tool_server_app())
    response = client.post(
        "/tools/analyze_equipment_deep",
        json={"equipment_query": "Проверь оборудование", "current_model_id": "selected-model"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["tool_name"] == "analyze_equipment_deep"
    assert payload["job_id"]
    assert payload["status_url"] == f"/tool-jobs/{payload['job_id']}"
    assert payload["result_ref"] == f"/tool-jobs/{payload['job_id']}/result"


def test_tool_job_status_and_not_ready_result(monkeypatch):
    from app.tool_server.server import create_tool_server_app

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(create_tool_server_app())
    accepted = client.post(
        "/tools/analyze_equipment_deep",
        json={"equipment_query": "Проверь оборудование"},
    ).json()

    status_response = client.get(accepted["status_url"])
    result_response = client.get(accepted["result_ref"])

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "queued"
    assert result_response.status_code == 409
    assert result_response.json()["detail"]["code"] == "tool_job_not_ready"


def test_tool_job_cancel_reaches_store(monkeypatch):
    from app.tool_server.server import create_tool_server_app

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(create_tool_server_app())
    accepted = client.post(
        "/tools/analyze_equipment_deep",
        json={"equipment_query": "Проверь оборудование"},
    ).json()

    cancel_response = client.post(f"/tool-jobs/{accepted['job_id']}/cancel")

    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] in {"cancelling", "cancelled"}


def test_tool_job_completed_result_strips_timing_footer(monkeypatch):
    from app.tool_server import jobs
    from app.tool_server.server import create_tool_server_app

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(create_tool_server_app())
    accepted = client.post(
        "/tools/analyze_equipment_deep",
        json={"equipment_query": "Проверь оборудование"},
    ).json()
    jobs.get_store().finish_completed(
        accepted["job_id"],
        {"assistant_message": "Готово\n\n---\nTiming / Quality\nслужебные данные"},
    )

    result_response = client.get(accepted["result_ref"])

    assert result_response.status_code == 200
    assert result_response.json()["assistant_message"] == "Готово"


def test_tool_job_unknown_returns_404(monkeypatch):
    from app.tool_server.server import create_tool_server_app

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(create_tool_server_app())
    response = client.get("/tool-jobs/missing-job")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "tool_job_not_found"


def test_agent_api_mounts_clean_tool_server_without_replacing_legacy_routes(monkeypatch):
    from orchestrator.agent_api import app as agent_api_app

    monkeypatch.setenv("LLM_BASE_URL", "http://llm-runtime:8080/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "fallback-model")

    client = TestClient(agent_api_app)

    clean_schema_response = client.get("/app-tools/tool-server/openapi.json")
    assert clean_schema_response.status_code == 200
    assert "/tools/echo" in clean_schema_response.json()["paths"]

    clean_tool_response = client.post(
        "/app-tools/tools/echo",
        json={"message": "ping", "current_model_id": "selected-model"},
    )
    assert clean_tool_response.status_code == 200
    assert clean_tool_response.json()["assistant_message"] == "ping"

    clean_deep_response = client.post(
        "/app-tools/tools/analyze_equipment_deep",
        json={"equipment_query": "Проверь оборудование"},
    )
    assert clean_deep_response.status_code == 202
    clean_deep_payload = clean_deep_response.json()
    assert clean_deep_payload["status_url"] == f"/app-tools/tool-jobs/{clean_deep_payload['job_id']}"
    assert clean_deep_payload["result_ref"] == f"/app-tools/tool-jobs/{clean_deep_payload['job_id']}/result"

    legacy_schema_response = client.get("/tool-server/openapi.json")
    assert legacy_schema_response.status_code == 200
    assert "/tools/echo" not in legacy_schema_response.json()["paths"]
