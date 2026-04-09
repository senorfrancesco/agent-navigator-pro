from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_openwebui.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_openwebui", SCRIPT_PATH)
bootstrap_openwebui = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(bootstrap_openwebui)


def test_build_tool_server_connection_splits_origin_and_spec_path():
    connection = bootstrap_openwebui.build_tool_server_connection(
        {
            "name": "Agent Navigator OpenAPI Tool Server",
            "containerReachableBaseUrl": "http://host.docker.internal:8000/tool-server",
        },
        tool_server_token="secret-token",
    )

    assert connection["url"] == "http://host.docker.internal:8000"
    assert connection["path"] == "/tool-server/openapi.json"
    assert connection["auth_type"] == "bearer"
    assert connection["key"] == "secret-token"
    assert connection["config"]["bootstrap_id"] == bootstrap_openwebui.BOOTSTRAP_CONNECTION_ID


def test_merge_tool_server_connections_updates_existing_bootstrap_entry():
    existing = [
        {
            "url": "http://old.example",
            "path": "/old/openapi.json",
            "type": "openapi",
            "auth_type": "bearer",
            "key": "old",
            "headers": {},
            "config": {"enable": False, "bootstrap_id": bootstrap_openwebui.BOOTSTRAP_CONNECTION_ID},
        },
        {
            "url": "http://keep.example",
            "path": "/openapi.json",
            "type": "openapi",
            "auth_type": "bearer",
            "key": "keep",
            "headers": {},
            "config": {"enable": True},
        },
    ]
    desired = {
        "url": "http://host.docker.internal:8000",
        "path": "/tool-server/openapi.json",
        "type": "openapi",
        "auth_type": "bearer",
        "key": "new-token",
        "headers": {},
        "config": {"enable": True, "bootstrap_id": bootstrap_openwebui.BOOTSTRAP_CONNECTION_ID},
    }

    merged = bootstrap_openwebui.merge_tool_server_connections(existing, desired)

    assert len(merged) == 2
    assert merged[0]["url"] == desired["url"]
    assert merged[0]["path"] == desired["path"]
    assert merged[0]["key"] == "new-token"
    assert merged[0]["config"]["enable"] is True
    assert merged[1]["url"] == "http://keep.example"


def test_tool_and_function_forms_are_built_from_export_metadata():
    tool_form = bootstrap_openwebui.tool_form_from_export(
        {
            "tool_id": "equipment_fast_tool",
            "title": "Быстрый анализ оборудования",
            "description": "desc",
            "targetModels": ["raw.*"],
            "pythonCode": "token = 'SET_OPENAPI_TOOL_SERVER_TOKEN'\nclass Tools:\n    pass\n",
        },
        tool_server_token="real-token",
    )
    function_form = bootstrap_openwebui.function_form_from_export(
        {
            "action_id": "equipment_fast_action",
            "title": "Быстрый анализ оборудования",
            "description": "desc",
            "targetModels": ["raw.*"],
            "pythonCode": "token = 'SET_OPENAPI_TOOL_SERVER_TOKEN'\nclass Action:\n    pass\n",
        },
        tool_server_token="real-token",
    )
    prompt_form = bootstrap_openwebui.prompt_form_from_export(
        {
            "binding_id": "equipment.fast.prompt",
            "description": "desc",
            "openwebui": {
                "command": "/hw_fast",
                "title": "Slash: быстрый анализ оборудования",
                "content": "Используй analyze_equipment_fast",
            },
        }
    )

    assert tool_form["id"] == "equipment_fast_tool"
    assert tool_form["meta"]["manifest"]["target_models"] == ["raw.*"]
    assert "class Tools" in tool_form["content"]
    assert "real-token" in tool_form["content"]
    assert "SET_OPENAPI_TOOL_SERVER_TOKEN" not in tool_form["content"]
    assert function_form["id"] == "equipment_fast_action"
    assert function_form["meta"]["manifest"]["target_models"] == ["raw.*"]
    assert "class Action" in function_form["content"]
    assert "real-token" in function_form["content"]
    assert "SET_OPENAPI_TOOL_SERVER_TOKEN" not in function_form["content"]
    assert prompt_form["command"] == "/hw_fast"
    assert prompt_form["meta"]["binding_id"] == "equipment.fast.prompt"
    assert prompt_form["is_production"] is True


def test_get_prompt_by_command_uses_prompt_list_endpoint(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        assert method == "GET"
        assert path == "/api/v1/prompts/list"
        return {
            "items": [
                {"id": "prompt-fast", "command": "/hw_fast"},
                {"id": "prompt-deep", "command": "/hw_deep"},
            ],
            "total": 2,
        }

    monkeypatch.setattr(client, "_request", fake_request)

    prompt = client.get_prompt_by_command("/hw_fast")

    assert prompt == {"id": "prompt-fast", "command": "/hw_fast"}
    assert calls == [("GET", "/api/v1/prompts/list", None)]
