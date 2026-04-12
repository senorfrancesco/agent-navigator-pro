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
    assert merged[0]["config"]["enable"] is False
    assert merged[1]["url"] == "http://keep.example"


def test_merge_tool_server_connections_collapses_legacy_url_path_shape():
    existing = [
        {
            "url": "http://host.docker.internal:8000/tool-server",
            "path": "openapi.json",
            "type": "openapi",
            "auth_type": "bearer",
            "key": "old-token",
            "headers": {},
            "config": {"enable": True, "access_grants": []},
        },
        {
            "url": "http://host.docker.internal:8000",
            "path": "/tool-server/openapi.json",
            "type": "openapi",
            "auth_type": "bearer",
            "key": "new-token",
            "headers": {},
            "config": {"enable": True, "bootstrap_id": bootstrap_openwebui.BOOTSTRAP_CONNECTION_ID},
        },
    ]
    desired = {
        "url": "http://host.docker.internal:8000",
        "path": "/tool-server/openapi.json",
        "type": "openapi",
        "auth_type": "bearer",
        "key": "expected-token",
        "headers": {},
        "config": {"enable": True, "bootstrap_id": bootstrap_openwebui.BOOTSTRAP_CONNECTION_ID},
    }

    merged = bootstrap_openwebui.merge_tool_server_connections(existing, desired)

    assert len(merged) == 1
    assert merged[0]["url"] == "http://host.docker.internal:8000"
    assert merged[0]["path"] == "/tool-server/openapi.json"
    assert merged[0]["key"] == "expected-token"
    assert merged[0]["config"]["bootstrap_id"] == bootstrap_openwebui.BOOTSTRAP_CONNECTION_ID


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


def test_remove_legacy_prompts_deletes_known_command_drift(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        if method == "GET":
            assert path == "/api/v1/prompts/list"
            return {
                "items": [
                    {"id": "canonical-fast", "command": "/hw_fast"},
                    {"id": "legacy-fast", "command": "hw-fast"},
                    {"id": "legacy-deep", "command": "hw-deep"},
                    {"id": "legacy-slash-fast", "command": "/hw-fast"},
                    {"id": "other", "command": "/other"},
                ],
                "total": 4,
            }
        assert method == "DELETE"
        return True

    monkeypatch.setattr(client, "_request", fake_request)

    deleted = bootstrap_openwebui.remove_legacy_prompts(client)

    assert deleted == 3
    assert calls == [
        ("GET", "/api/v1/prompts/list", None),
        ("DELETE", "/api/v1/prompts/id/legacy-fast/delete", None),
        ("DELETE", "/api/v1/prompts/id/legacy-deep/delete", None),
        ("DELETE", "/api/v1/prompts/id/legacy-slash-fast/delete", None),
    ]


def test_cleanup_tool_server_connections_removes_canonical_picker_entries_only(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    updates: list[list[dict]] = []
    existing_connections = [
        {
            "url": "http://host.docker.internal:8000/tool-server",
            "path": "openapi.json",
            "type": "openapi",
            "auth_type": "bearer",
            "key": "legacy-token",
            "headers": {},
            "config": {"enable": True, "name": "Agent Navigator OpenAPI Tool Server"},
        },
        {
            "url": "http://127.0.0.1:8000",
            "path": "/tool-server/openapi.json",
            "type": "openapi",
            "auth_type": "bearer",
            "key": "browser-token",
            "headers": {},
            "config": {"enable": True, "bootstrap_id": bootstrap_openwebui.BOOTSTRAP_CONNECTION_ID},
        },
        {
            "url": "http://keep.example",
            "path": "/openapi.json",
            "type": "openapi",
            "auth_type": "bearer",
            "key": "keep-token",
            "headers": {},
            "config": {"enable": True, "name": "Third Party Tools"},
        },
    ]

    monkeypatch.setattr(client, "get_tool_server_connections", lambda: existing_connections)
    monkeypatch.setattr(client, "set_tool_server_connections", lambda connections: updates.append(connections))

    summary = bootstrap_openwebui.cleanup_tool_server_connections(
        client,
        tool_server_export={
            "name": "Agent Navigator OpenAPI Tool Server",
            "baseUrl": "http://127.0.0.1:8000/tool-server",
            "browserReachableBaseUrl": "http://127.0.0.1:8000/tool-server",
            "containerReachableBaseUrl": "http://host.docker.internal:8000/tool-server",
        },
    )

    assert summary == {
        "action": "updated",
        "removedCount": 2,
        "removedConnectionNames": [
            "Agent Navigator OpenAPI Tool Server",
            "Agent Navigator OpenAPI Tool Server",
        ],
    }
    assert updates == [
        [
            {
                "url": "http://keep.example",
                "path": "/openapi.json",
                "type": "openapi",
                "auth_type": "bearer",
                "key": "keep-token",
                "headers": {},
                "config": {"enable": True, "name": "Third Party Tools"},
            }
        ]
    ]


def test_cleanup_fixture_workspace_tools_deletes_community_sum_tool_only(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    deleted_ids: list[str] = []

    def fake_get_tool_by_id(tool_id: str):
        if tool_id == "community_sum_tool":
            return {"id": "community_sum_tool", "name": "Community Sum Tool"}
        if tool_id == "equipment_fast_tool":
            return {"id": "equipment_fast_tool", "name": "Быстрый анализ оборудования"}
        return None

    monkeypatch.setattr(client, "get_tool_by_id", fake_get_tool_by_id)
    monkeypatch.setattr(client, "delete_tool", lambda tool_id: deleted_ids.append(tool_id))

    summary = bootstrap_openwebui.cleanup_fixture_workspace_tools(client)

    assert summary == {
        "action": "updated",
        "deletedIds": ["community_sum_tool"],
    }
    assert deleted_ids == ["community_sum_tool"]


def test_cleanup_user_settings_tool_servers_removes_canonical_direct_servers_only(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    updates: list[dict] = []
    current_settings = {
        "ui": {
            "version": "0.8.12",
            "memory": True,
            "toolServers": [
                {
                    "type": "openapi",
                    "url": "http://127.0.0.1:8000/tool-server",
                    "spec_type": "url",
                    "path": "openapi.json",
                    "auth_type": "bearer",
                    "key": "legacy-token",
                    "config": {"enable": True},
                    "info": {"name": "Agent Navigator Tools"},
                },
                {
                    "type": "openapi",
                    "url": "http://keep.example",
                    "spec_type": "url",
                    "path": "openapi.json",
                    "auth_type": "bearer",
                    "key": "keep-token",
                    "config": {"enable": True},
                    "info": {"name": "Third Party Tools"},
                },
            ],
        }
    }

    monkeypatch.setattr(client, "get_user_settings", lambda: current_settings)
    monkeypatch.setattr(client, "update_user_settings", lambda form_data: updates.append(form_data) or form_data)

    summary = bootstrap_openwebui.cleanup_user_settings_tool_servers(
        client,
        tool_server_export={
            "name": "Agent Navigator OpenAPI Tool Server",
            "baseUrl": "http://127.0.0.1:8000/tool-server",
            "browserReachableBaseUrl": "http://127.0.0.1:8000/tool-server",
            "containerReachableBaseUrl": "http://host.docker.internal:8000/tool-server",
        },
    )

    assert summary == {
        "action": "updated",
        "removedCount": 1,
        "removedToolServerNames": ["Agent Navigator Tools"],
    }
    assert updates == [
        {
            "ui": {
                "version": "0.8.12",
                "memory": True,
                "toolServers": [
                    {
                        "type": "openapi",
                        "url": "http://keep.example",
                        "spec_type": "url",
                        "path": "openapi.json",
                        "auth_type": "bearer",
                        "key": "keep-token",
                        "config": {"enable": True},
                        "info": {"name": "Third Party Tools"},
                    }
                ],
            }
        }
    ]


def test_ensure_default_model_preserves_existing_model_config(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        if method == "GET":
            assert path == "/api/v1/configs/models"
            return {
                "DEFAULT_MODELS": "agent-navigator",
                "DEFAULT_PINNED_MODELS": None,
                "MODEL_ORDER_LIST": ["agent-navigator"],
                "DEFAULT_MODEL_METADATA": {"agent-navigator": {"hidden": False}},
                "DEFAULT_MODEL_PARAMS": {"temperature": 0.2},
            }
        assert method == "POST"
        return payload

    monkeypatch.setattr(client, "_request", fake_request)

    result = bootstrap_openwebui.ensure_default_model(client, "raw.qwen-14b-llm")

    assert result["DEFAULT_MODELS"] == "raw.qwen-14b-llm"
    assert calls == [
        ("GET", "/api/v1/configs/models", None),
        (
            "POST",
            "/api/v1/configs/models",
            {
                "DEFAULT_MODELS": "raw.qwen-14b-llm",
                "DEFAULT_PINNED_MODELS": None,
                "MODEL_ORDER_LIST": ["agent-navigator"],
                "DEFAULT_MODEL_METADATA": {"agent-navigator": {"hidden": False}},
                "DEFAULT_MODEL_PARAMS": {"temperature": 0.2, "function_calling": "native"},
            },
        ),
    ]


def test_ensure_default_model_is_noop_when_native_function_calling_already_enabled(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    calls: list[tuple[str, str, dict | None]] = []
    current_config = {
        "DEFAULT_MODELS": "raw.qwen-14b-llm",
        "DEFAULT_PINNED_MODELS": None,
        "MODEL_ORDER_LIST": ["raw.qwen-14b-llm"],
        "DEFAULT_MODEL_METADATA": {"raw.qwen-14b-llm": {"hidden": False}},
        "DEFAULT_MODEL_PARAMS": {"temperature": 0.2, "function_calling": "native"},
    }

    def fake_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        assert method == "GET"
        assert path == "/api/v1/configs/models"
        return current_config

    monkeypatch.setattr(client, "_request", fake_request)

    result = bootstrap_openwebui.ensure_default_model(client, "raw.qwen-14b-llm")

    assert result == current_config
    assert calls == [("GET", "/api/v1/configs/models", None)]


def test_upsert_workspace_tools_preserves_ui_owned_metadata(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    updates: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        client,
        "get_tool_by_id",
        lambda tool_id: {
            "id": tool_id,
            "name": "Переименованный tool",
            "content": "old-content",
            "meta": {
                "description": "user-edited-description",
                "manifest": {"target_models": ["raw.legacy"]},
            },
        },
    )
    monkeypatch.setattr(client, "create_tool", lambda form_data: (_ for _ in ()).throw(AssertionError("create should not be called")))
    monkeypatch.setattr(client, "update_tool", lambda tool_id, form_data: updates.append((tool_id, form_data)))

    summary = bootstrap_openwebui.upsert_workspace_tools(
        client,
        [
            {
                "tool_id": "equipment_fast_tool",
                "title": "Быстрый анализ оборудования",
                "description": "backend-description",
                "targetModels": ["raw.*"],
                "pythonCode": "new-content",
            }
        ],
        tool_server_token="tool-token",
    )

    assert summary["updatedIds"] == ["equipment_fast_tool"]
    assert summary["uiOwnedDriftIgnored"] == []
    assert summary["appliedChanges"] == [
        "equipment_fast_tool.name",
        "equipment_fast_tool.content",
        "equipment_fast_tool.meta.description",
        "equipment_fast_tool.meta.manifest.target_models",
    ]
    assert updates == [
        (
            "equipment_fast_tool",
            {
                "id": "equipment_fast_tool",
                "name": "Быстрый анализ оборудования",
                "content": "new-content",
                "meta": {
                    "description": "backend-description",
                    "manifest": {"target_models": ["raw.*"]},
                },
            },
        )
    ]


def test_upsert_workspace_tools_treats_materialized_raw_targets_as_noop(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    updates: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        client,
        "get_tool_by_id",
        lambda tool_id: {
            "id": tool_id,
            "name": "Быстрый анализ оборудования",
            "content": "stable-content",
            "meta": {
                "description": "stable-description",
                "manifest": {"target_models": ["raw.other", "raw.qwen-14b-llm"]},
            },
        },
    )
    monkeypatch.setattr(client, "create_tool", lambda form_data: (_ for _ in ()).throw(AssertionError("create should not be called")))
    monkeypatch.setattr(client, "update_tool", lambda tool_id, form_data: updates.append((tool_id, form_data)))

    summary = bootstrap_openwebui.upsert_workspace_tools(
        client,
        [
            {
                "tool_id": "equipment_fast_tool",
                "title": "Быстрый анализ оборудования",
                "description": "stable-description",
                "targetModels": ["raw.*"],
                "pythonCode": "stable-content",
            }
        ],
        tool_server_token="tool-token",
    )

    assert summary["updatedIds"] == []
    assert summary["noopIds"] == ["equipment_fast_tool"]
    assert summary["appliedChanges"] == []
    assert summary["materializationDriftIgnored"] == [
        "equipment_fast_tool.meta.manifest.target_models",
    ]
    assert updates == []


def test_upsert_workspace_tools_updates_noncanonical_raw_targets(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    updates: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        client,
        "get_tool_by_id",
        lambda tool_id: {
            "id": tool_id,
            "name": "Быстрый анализ оборудования",
            "content": "stable-content",
            "meta": {
                "description": "stable-description",
                "manifest": {"target_models": ["raw.other"]},
            },
        },
    )
    monkeypatch.setattr(client, "create_tool", lambda form_data: (_ for _ in ()).throw(AssertionError("create should not be called")))
    monkeypatch.setattr(client, "update_tool", lambda tool_id, form_data: updates.append((tool_id, form_data)))

    summary = bootstrap_openwebui.upsert_workspace_tools(
        client,
        [
            {
                "tool_id": "equipment_fast_tool",
                "title": "Быстрый анализ оборудования",
                "description": "stable-description",
                "targetModels": ["raw.*"],
                "pythonCode": "stable-content",
            }
        ],
        tool_server_token="tool-token",
    )

    assert summary["updatedIds"] == ["equipment_fast_tool"]
    assert summary["materializationDriftIgnored"] == []
    assert summary["appliedChanges"] == [
        "equipment_fast_tool.meta.manifest.target_models",
    ]
    assert updates == [
        (
            "equipment_fast_tool",
            {
                "id": "equipment_fast_tool",
                "name": "Быстрый анализ оборудования",
                "content": "stable-content",
                "meta": {
                    "description": "stable-description",
                    "manifest": {"target_models": ["raw.*"]},
                },
            },
        )
    ]


def test_upsert_functions_preserves_existing_ui_owned_flags(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    updates: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        client,
        "get_function_by_id",
        lambda function_id: {
            "id": function_id,
            "name": "Переименованная action",
            "content": "old-action-content",
            "meta": {
                "description": "user-action-description",
                "manifest": {"target_models": ["raw.legacy"]},
            },
            "is_active": False,
            "is_global": False,
        },
    )
    monkeypatch.setattr(client, "create_function", lambda form_data: (_ for _ in ()).throw(AssertionError("create should not be called")))
    monkeypatch.setattr(client, "update_function", lambda function_id, form_data: updates.append((function_id, form_data)) or {})
    monkeypatch.setattr(client, "toggle_function_active", lambda function_id: (_ for _ in ()).throw(AssertionError("existing flags should be preserved")))
    monkeypatch.setattr(client, "toggle_function_global", lambda function_id: (_ for _ in ()).throw(AssertionError("existing flags should be preserved")))

    summary = bootstrap_openwebui.upsert_functions(
        client,
        [
            {
                "action_id": "equipment_fast_action",
                "title": "Быстрый анализ оборудования",
                "description": "backend-description",
                "targetModels": ["raw.*"],
                "pythonCode": "new-action-content",
                "isActive": True,
                "isGlobal": True,
            }
        ],
        tool_server_token="tool-token",
    )

    assert summary["updatedIds"] == ["equipment_fast_action"]
    assert summary["uiOwnedDriftIgnored"] == [
        "equipment_fast_action.is_active",
        "equipment_fast_action.is_global",
    ]
    assert summary["appliedChanges"] == [
        "equipment_fast_action.name",
        "equipment_fast_action.content",
        "equipment_fast_action.meta.description",
        "equipment_fast_action.meta.manifest.target_models",
    ]
    assert updates == [
        (
            "equipment_fast_action",
            {
                "id": "equipment_fast_action",
                "name": "Быстрый анализ оборудования",
                "content": "new-action-content",
                "meta": {
                    "description": "backend-description",
                    "manifest": {"target_models": ["raw.*"]},
                },
                "is_active": False,
                "is_global": False,
            },
        )
    ]


def test_upsert_functions_treats_materialized_raw_targets_as_noop(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    updates: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        client,
        "get_function_by_id",
        lambda function_id: {
            "id": function_id,
            "name": "Быстрый анализ оборудования",
            "content": "stable-action-content",
            "meta": {
                "description": "stable-description",
                "manifest": {},
            },
            "is_active": True,
            "is_global": True,
        },
    )
    monkeypatch.setattr(client, "create_function", lambda form_data: (_ for _ in ()).throw(AssertionError("create should not be called")))
    monkeypatch.setattr(client, "update_function", lambda function_id, form_data: updates.append((function_id, form_data)) or {})
    monkeypatch.setattr(client, "toggle_function_active", lambda function_id: (_ for _ in ()).throw(AssertionError("noop should not toggle active")))
    monkeypatch.setattr(client, "toggle_function_global", lambda function_id: (_ for _ in ()).throw(AssertionError("noop should not toggle global")))

    summary = bootstrap_openwebui.upsert_functions(
        client,
        [
            {
                "action_id": "equipment_fast_action",
                "title": "Быстрый анализ оборудования",
                "description": "stable-description",
                "targetModels": ["raw.*"],
                "pythonCode": "stable-action-content",
                "isActive": True,
                "isGlobal": True,
            }
        ],
        tool_server_token="tool-token",
    )

    assert summary["updatedIds"] == []
    assert summary["noopIds"] == ["equipment_fast_action"]
    assert summary["appliedChanges"] == []
    assert summary["materializationDriftIgnored"] == [
        "equipment_fast_action.meta.manifest.target_models",
    ]
    assert updates == []


def test_bootstrap_openwebui_summary_reports_native_function_calling(monkeypatch):
    monkeypatch.setattr(
        bootstrap_openwebui,
        "fetch_binding_export",
        lambda backend_base_url: {
            "toolServer": {"name": "Agent Navigator OpenAPI Tool Server"},
            "runtimeConfig": {
                "defaultModel": "raw.qwen-14b-llm",
                "defaultFunctionCalling": "native",
            },
            "ownership": {
                "backendOwned": {"toolServerConnection": ["url"]},
                "openWebUIOwned": {"toolServerConnection": ["config.enable"]},
            },
            "workspaceTools": [{}, {}],
            "actionFunctions": [{}, {}, {}, {}],
            "workspacePrompts": [{}, {}],
        },
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "sign_in",
        lambda openwebui_base_url, *, email, password: "admin-token",
    )
    monkeypatch.setattr(bootstrap_openwebui, "remove_legacy_prompts", lambda client: 0)
    monkeypatch.setattr(
        bootstrap_openwebui,
        "reconcile_default_model",
        lambda client, model_id: (
            {
                "DEFAULT_MODELS": model_id,
                "DEFAULT_MODEL_PARAMS": {"temperature": 0.2, "function_calling": "native"},
            },
            {"action": "noop", "appliedChanges": [], "uiOwnedDriftIgnored": []},
        ),
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "reconcile_task_config",
        lambda client: (
            {"ENABLE_FOLLOW_UP_GENERATION": False},
            {"action": "noop", "appliedChanges": [], "uiOwnedDriftIgnored": []},
        ),
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "cleanup_tool_server_connections",
        lambda *args, **kwargs: {"action": "noop", "removedCount": 0, "removedConnectionNames": []},
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "cleanup_fixture_workspace_tools",
        lambda *args, **kwargs: {"action": "noop", "deletedIds": []},
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "cleanup_user_settings_tool_servers",
        lambda *args, **kwargs: {"action": "noop", "removedCount": 0, "removedToolServerNames": []},
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "upsert_workspace_tools",
        lambda *args, **kwargs: {
            "createdIds": [],
            "updatedIds": [],
            "noopIds": ["equipment_fast_tool", "equipment_deep_tool"],
            "appliedChanges": [],
            "uiOwnedDriftIgnored": [],
            "materializationDriftIgnored": [],
        },
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "upsert_functions",
        lambda *args, **kwargs: {
            "createdIds": [],
            "updatedIds": [],
            "noopIds": [
                "equipment_fast_action",
                "equipment_deep_action",
                "tool_job_refresh_action",
                "tool_job_cancel_action",
            ],
            "appliedChanges": [],
            "uiOwnedDriftIgnored": [],
            "materializationDriftIgnored": [],
        },
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "upsert_prompts",
        lambda *args, **kwargs: {
            "createdIds": [],
            "updatedIds": [],
            "noopIds": ["/hw_fast", "/hw_deep"],
            "appliedChanges": [],
            "uiOwnedDriftIgnored": [],
            "materializationDriftIgnored": [],
        },
    )

    result = bootstrap_openwebui.bootstrap_openwebui(
        backend_base_url="http://127.0.0.1:8000",
        openwebui_base_url="http://127.0.0.1:3001",
        admin_email="admin@example.com",
        admin_password="secret",
        tool_server_token="tool-token",
    )

    assert result["status"] == "ok"
    assert result["defaultModel"] == "raw.qwen-14b-llm"
    assert result["defaultFunctionCalling"] == "native"
    assert result["runtimeConfig"]["defaultModel"] == "raw.qwen-14b-llm"
    assert result["ownership"]["backendOwned"]["toolServerConnection"] == ["url"]
    assert result["driftSummary"]["noOp"] is True


def test_reconcile_task_config_disables_follow_up_without_touching_other_settings(monkeypatch):
    client = bootstrap_openwebui.OpenWebUIBootstrapClient(
        openwebui_base_url="http://127.0.0.1:3001",
        admin_token="secret-token",
    )
    calls: list[tuple[str, str, dict | None]] = []
    current_config = {
        "TASK_MODEL": "local.task-model",
        "TASK_MODEL_EXTERNAL": "external.task-model",
        "ENABLE_TITLE_GENERATION": True,
        "TITLE_GENERATION_PROMPT_TEMPLATE": "title",
        "IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE": "image",
        "ENABLE_AUTOCOMPLETE_GENERATION": True,
        "AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH": 512,
        "TAGS_GENERATION_PROMPT_TEMPLATE": "tags",
        "FOLLOW_UP_GENERATION_PROMPT_TEMPLATE": "follow-up",
        "ENABLE_FOLLOW_UP_GENERATION": True,
        "ENABLE_TAGS_GENERATION": True,
        "ENABLE_SEARCH_QUERY_GENERATION": False,
        "ENABLE_RETRIEVAL_QUERY_GENERATION": False,
        "QUERY_GENERATION_PROMPT_TEMPLATE": "query",
        "TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE": "tools",
        "VOICE_MODE_PROMPT_TEMPLATE": "voice",
    }

    def fake_request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        if method == "GET":
            assert path == "/api/v1/tasks/config"
            return current_config
        assert method == "POST"
        return payload

    monkeypatch.setattr(client, "_request", fake_request)

    result, summary = bootstrap_openwebui.reconcile_task_config(client)

    assert result["ENABLE_FOLLOW_UP_GENERATION"] is False
    assert result["TASK_MODEL"] == "local.task-model"
    assert result["TASK_MODEL_EXTERNAL"] == "external.task-model"
    assert summary == {
        "action": "updated",
        "appliedChanges": ["ENABLE_FOLLOW_UP_GENERATION"],
        "uiOwnedDriftIgnored": [],
    }
    assert calls == [
        ("GET", "/api/v1/tasks/config", None),
        (
            "POST",
            "/api/v1/tasks/config/update",
            {
                "TASK_MODEL": "local.task-model",
                "TASK_MODEL_EXTERNAL": "external.task-model",
                "ENABLE_TITLE_GENERATION": True,
                "TITLE_GENERATION_PROMPT_TEMPLATE": "title",
                "IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE": "image",
                "ENABLE_AUTOCOMPLETE_GENERATION": True,
                "AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH": 512,
                "TAGS_GENERATION_PROMPT_TEMPLATE": "tags",
                "FOLLOW_UP_GENERATION_PROMPT_TEMPLATE": "follow-up",
                "ENABLE_FOLLOW_UP_GENERATION": False,
                "ENABLE_TAGS_GENERATION": True,
                "ENABLE_SEARCH_QUERY_GENERATION": False,
                "ENABLE_RETRIEVAL_QUERY_GENERATION": False,
                "QUERY_GENERATION_PROMPT_TEMPLATE": "query",
                "TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE": "tools",
                "VOICE_MODE_PROMPT_TEMPLATE": "voice",
            },
        ),
    ]


def test_bootstrap_openwebui_summary_reports_follow_up_disabled_task_policy(monkeypatch):
    monkeypatch.setattr(
        bootstrap_openwebui,
        "fetch_binding_export",
        lambda backend_base_url: {
            "toolServer": {"name": "Agent Navigator OpenAPI Tool Server"},
            "runtimeConfig": {
                "defaultModel": "raw.qwen-14b-llm",
                "defaultFunctionCalling": "native",
            },
            "ownership": {
                "backendOwned": {"toolServerConnection": ["url"]},
                "openWebUIOwned": {"toolServerConnection": ["config.enable"]},
            },
            "workspaceTools": [],
            "actionFunctions": [],
            "workspacePrompts": [],
        },
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "sign_in",
        lambda openwebui_base_url, *, email, password: "admin-token",
    )
    monkeypatch.setattr(bootstrap_openwebui, "remove_legacy_prompts", lambda client: 0)
    monkeypatch.setattr(
        bootstrap_openwebui,
        "reconcile_default_model",
        lambda client, model_id: (
            {
                "DEFAULT_MODELS": model_id,
                "DEFAULT_MODEL_PARAMS": {"temperature": 0.2, "function_calling": "native"},
            },
            {"action": "noop", "appliedChanges": [], "uiOwnedDriftIgnored": []},
        ),
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "reconcile_task_config",
        lambda client: (
            {"ENABLE_FOLLOW_UP_GENERATION": False},
            {"action": "updated", "appliedChanges": ["ENABLE_FOLLOW_UP_GENERATION"], "uiOwnedDriftIgnored": []},
        ),
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "cleanup_tool_server_connections",
        lambda *args, **kwargs: {
            "action": "updated",
            "removedCount": 2,
            "removedConnectionNames": [
                "Agent Navigator OpenAPI Tool Server",
                "Agent Navigator OpenAPI Tool Server",
            ],
        },
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "cleanup_fixture_workspace_tools",
        lambda *args, **kwargs: {"action": "updated", "deletedIds": ["community_sum_tool"]},
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "cleanup_user_settings_tool_servers",
        lambda *args, **kwargs: {"action": "updated", "removedCount": 1, "removedToolServerNames": ["Agent Navigator Tools"]},
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "upsert_workspace_tools",
        lambda *args, **kwargs: {
            "createdIds": [],
            "updatedIds": [],
            "noopIds": [],
            "appliedChanges": [],
            "uiOwnedDriftIgnored": [],
            "materializationDriftIgnored": [],
        },
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "upsert_functions",
        lambda *args, **kwargs: {
            "createdIds": [],
            "updatedIds": [],
            "noopIds": [],
            "appliedChanges": [],
            "uiOwnedDriftIgnored": [],
            "materializationDriftIgnored": [],
        },
    )
    monkeypatch.setattr(
        bootstrap_openwebui,
        "upsert_prompts",
        lambda *args, **kwargs: {
            "createdIds": [],
            "updatedIds": [],
            "noopIds": [],
            "appliedChanges": [],
            "uiOwnedDriftIgnored": [],
            "materializationDriftIgnored": [],
        },
    )

    result = bootstrap_openwebui.bootstrap_openwebui(
        backend_base_url="http://127.0.0.1:8000",
        openwebui_base_url="http://127.0.0.1:3001",
        admin_email="admin@example.com",
        admin_password="secret",
        tool_server_token="tool-token",
    )

    assert result["taskConfig"]["ENABLE_FOLLOW_UP_GENERATION"] is False
    assert result["driftSummary"]["toolServerCleanup"] == {
        "action": "updated",
        "removedCount": 2,
        "removedConnectionNames": [
            "Agent Navigator OpenAPI Tool Server",
            "Agent Navigator OpenAPI Tool Server",
        ],
    }
    assert result["driftSummary"]["workspaceToolCleanup"] == {
        "action": "updated",
        "deletedIds": ["community_sum_tool"],
    }
    assert result["driftSummary"]["userToolServerCleanup"] == {
        "action": "updated",
        "removedCount": 1,
        "removedToolServerNames": ["Agent Navigator Tools"],
    }
    assert result["driftSummary"]["taskConfig"] == {
        "action": "updated",
        "appliedChanges": ["ENABLE_FOLLOW_UP_GENERATION"],
        "uiOwnedDriftIgnored": [],
    }
    assert result["driftSummary"]["noOp"] is False
