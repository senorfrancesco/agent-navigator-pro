#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = REPO_ROOT / "backend" / ".env"
DEFAULT_BACKEND_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_OPENWEBUI_BASE_URL = "http://127.0.0.1:3001"
DEFAULT_OPENWEBUI_MODEL = "raw.qwen-14b-llm"
DEFAULT_FUNCTION_CALLING_MODE = "native"
DEFAULT_TOOL_SERVER_TOKEN = "agent-navigator-tool-server-dev-token"
BOOTSTRAP_CONNECTION_ID = "agent_navigator_openapi_tool_server"
TOOL_SERVER_TOKEN_PLACEHOLDER = "SET_OPENAPI_TOOL_SERVER_TOKEN"
LEGACY_PROMPT_COMMANDS = {"/hw-fast", "/hw-deep", "hw-fast", "hw-deep"}
MISSING = object()
TOOL_SERVER_MANAGED_PATHS = (
    "url",
    "path",
    "type",
    "auth_type",
    "headers",
    "key",
    "config.bootstrap_id",
    "config.name",
)
TOOL_SERVER_UI_OWNED_PATHS = ("config.enable",)
WORKSPACE_TOOL_MANAGED_PATHS = ("id", "content", "meta.manifest.target_models")
WORKSPACE_TOOL_UI_OWNED_PATHS = ("name", "meta.description")
ACTION_FUNCTION_MANAGED_PATHS = ("id", "content", "meta.manifest.target_models")
ACTION_FUNCTION_UI_OWNED_PATHS = ("name", "meta.description", "is_active", "is_global")
PROMPT_MANAGED_PATHS = ("command", "content", "meta.binding_id")
PROMPT_UI_OWNED_PATHS = ("name", "meta.description", "tags", "access_grants", "is_production")


def parse_env_file(env_path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_env_value(key: str, env_values: Dict[str, str], default: str | None = None) -> str | None:
    return os.getenv(key) or env_values.get(key) or default


def request_json(
    *,
    method: str,
    url: str,
    payload: Dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def sign_in(openwebui_base_url: str, *, email: str, password: str) -> str:
    payload = request_json(
        method="POST",
        url=f"{openwebui_base_url.rstrip('/')}/api/v1/auths/signin",
        payload={"email": email, "password": password},
    )
    token = payload.get("token")
    if not token:
        raise RuntimeError("Open WebUI signin did not return a bearer token.")
    return str(token)


def fetch_binding_export(backend_base_url: str) -> Dict[str, Any]:
    encoded_base_url = urllib.parse.quote(backend_base_url.rstrip("/"), safe="")
    export_url = (
        f"{backend_base_url.rstrip('/')}/operator/tool-bindings/export/openwebui"
        f"?backend_base_url={encoded_base_url}"
    )
    return request_json(method="GET", url=export_url)


def split_tool_server_spec_url(container_tool_server_base_url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(container_tool_server_base_url.rstrip("/"))
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid tool server URL: {container_tool_server_base_url}")
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    base_path = parsed.path.rstrip("/")
    spec_path = f"{base_path}/openapi.json" if base_path else "/openapi.json"
    return base_url, spec_path


def normalized_connection_spec_url(connection: Dict[str, Any]) -> str:
    base_url = str(connection.get("url", "")).rstrip("/")
    path = str(connection.get("path", "")).strip()
    if path and not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def _get_path(data: Dict[str, Any], path: str, default: Any = MISSING) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _set_path(data: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _diff_paths(current: Dict[str, Any], desired: Dict[str, Any], paths: Iterable[str]) -> List[str]:
    diffs: List[str] = []
    for path in paths:
        current_value = _get_path(current, path, MISSING)
        desired_value = _get_path(desired, path, MISSING)
        if current_value != desired_value:
            diffs.append(path)
    return diffs


def _normalize_target_models(value: Any) -> List[str]:
    if value is MISSING or value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    normalized: List[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in normalized:
            normalized.append(text)
    return sorted(normalized)


def _target_models_equivalent(current_value: Any, desired_value: Any) -> tuple[bool, bool]:
    current_models = _normalize_target_models(current_value)
    desired_models = _normalize_target_models(desired_value)
    if current_models == desired_models:
        return True, False
    if desired_models == ["raw.*"]:
        if not current_models:
            return True, True
        if (
            current_models
            and all(model.startswith("raw.") for model in current_models)
            and DEFAULT_OPENWEBUI_MODEL in current_models
        ):
            return True, True
    return False, False


def _diff_managed_paths(
    current: Dict[str, Any],
    desired: Dict[str, Any],
    paths: Iterable[str],
) -> tuple[List[str], List[str]]:
    diffs: List[str] = []
    materialization_ignored: List[str] = []
    for path in paths:
        current_value = _get_path(current, path, MISSING)
        desired_value = _get_path(desired, path, MISSING)
        if path == "meta.manifest.target_models":
            equivalent, materialized = _target_models_equivalent(current_value, desired_value)
            if equivalent:
                if materialized:
                    materialization_ignored.append(path)
                continue
        if current_value != desired_value:
            diffs.append(path)
    return diffs, materialization_ignored


def _merge_preserving_paths(
    desired: Dict[str, Any],
    existing: Dict[str, Any],
    preserve_paths: Iterable[str],
) -> Dict[str, Any]:
    merged = copy.deepcopy(desired)
    for path in preserve_paths:
        existing_value = _get_path(existing, path, MISSING)
        if existing_value is not MISSING:
            _set_path(merged, path, copy.deepcopy(existing_value))
    return merged


def _empty_collection_summary() -> Dict[str, Any]:
    return {
        "createdIds": [],
        "updatedIds": [],
        "noopIds": [],
        "appliedChanges": [],
        "uiOwnedDriftIgnored": [],
        "materializationDriftIgnored": [],
    }


def _collection_has_changes(summary: Dict[str, Any]) -> bool:
    return bool(summary["createdIds"] or summary["updatedIds"] or summary["appliedChanges"])


def build_tool_server_connection(tool_server_export: Dict[str, Any], *, tool_server_token: str) -> Dict[str, Any]:
    base_url, spec_path = split_tool_server_spec_url(tool_server_export["containerReachableBaseUrl"])
    return {
        "url": base_url,
        "path": spec_path,
        "type": "openapi",
        "auth_type": "bearer",
        "headers": {},
        "key": tool_server_token,
        "config": {
            "enable": True,
            "bootstrap_id": BOOTSTRAP_CONNECTION_ID,
            "name": tool_server_export["name"],
        },
    }


def merge_tool_server_connections(
    existing_connections: Iterable[Dict[str, Any]],
    desired_connection: Dict[str, Any],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    target_url = normalized_connection_spec_url(desired_connection)
    updated = False

    for connection in existing_connections:
        config = connection.get("config", {}) or {}
        connection_url = normalized_connection_spec_url(connection)
        if config.get("bootstrap_id") == BOOTSTRAP_CONNECTION_ID or connection_url == target_url:
            if updated:
                continue
            preserved = dict(connection)
            preserved.update({k: v for k, v in desired_connection.items() if k != "config"})
            preserved_config = {**config, **desired_connection.get("config", {})}
            if "enable" in config:
                preserved_config["enable"] = config["enable"]
            preserved["config"] = preserved_config
            merged.append(preserved)
            updated = True
        else:
            merged.append(connection)

    if not updated:
        merged.append(desired_connection)

    return merged


def inject_tool_server_token(source: str, *, tool_server_token: str) -> str:
    return source.replace(TOOL_SERVER_TOKEN_PLACEHOLDER, tool_server_token)


def tool_form_from_export(tool_export: Dict[str, Any], *, tool_server_token: str) -> Dict[str, Any]:
    return {
        "id": tool_export["tool_id"],
        "name": tool_export["title"],
        "content": inject_tool_server_token(tool_export["pythonCode"], tool_server_token=tool_server_token),
        "meta": {
            "description": tool_export.get("description", ""),
            "manifest": {"target_models": tool_export.get("targetModels", [])},
        },
    }


def function_form_from_export(function_export: Dict[str, Any], *, tool_server_token: str) -> Dict[str, Any]:
    return {
        "id": function_export["action_id"],
        "name": function_export["title"],
        "content": inject_tool_server_token(function_export["pythonCode"], tool_server_token=tool_server_token),
        "meta": {
            "description": function_export.get("description", ""),
            "manifest": {"target_models": function_export.get("targetModels", [])},
        },
    }


def prompt_form_from_export(prompt_export: Dict[str, Any]) -> Dict[str, Any]:
    openwebui_prompt = prompt_export["openwebui"]
    return {
        "command": openwebui_prompt["command"],
        "name": openwebui_prompt["title"],
        "content": openwebui_prompt["content"],
        "data": {},
        "meta": {
            "description": prompt_export.get("description", ""),
            "binding_id": prompt_export.get("binding_id"),
        },
        "tags": ["agent-navigator", "equipment"],
        "access_grants": [],
        "is_production": True,
    }


def summarize_tool_server_reconcile(
    existing_connections: Iterable[Dict[str, Any]],
    desired_connection: Dict[str, Any],
) -> Dict[str, Any]:
    existing_list = list(existing_connections)
    target_url = normalized_connection_spec_url(desired_connection)
    matching = [
        connection
        for connection in existing_list
        if (connection.get("config", {}) or {}).get("bootstrap_id") == BOOTSTRAP_CONNECTION_ID
        or normalized_connection_spec_url(connection) == target_url
    ]
    if not matching:
        return {
            "action": "created",
            "appliedChanges": list(TOOL_SERVER_MANAGED_PATHS),
            "uiOwnedDriftIgnored": [],
        }

    managed_drift = _diff_paths(matching[0], desired_connection, TOOL_SERVER_MANAGED_PATHS)
    ui_owned_drift = _diff_paths(matching[0], desired_connection, TOOL_SERVER_UI_OWNED_PATHS)
    applied_changes = list(managed_drift)
    if len(matching) > 1:
        applied_changes.append("dedupe")
    return {
        "action": "updated" if applied_changes else "noop",
        "appliedChanges": applied_changes,
        "uiOwnedDriftIgnored": ui_owned_drift,
    }


def _reconcile_existing_resource(
    *,
    resource_id: str,
    desired_form: Dict[str, Any],
    existing: Dict[str, Any] | None,
    managed_paths: Iterable[str],
    ui_owned_paths: Iterable[str],
) -> Dict[str, Any]:
    if existing is None:
        return {
            "action": "created",
            "formData": desired_form,
            "appliedChanges": list(managed_paths),
            "uiOwnedDriftIgnored": [],
            "materializationDriftIgnored": [],
        }

    managed_drift, materialization_drift = _diff_managed_paths(existing, desired_form, managed_paths)
    ui_owned_drift = _diff_paths(existing, desired_form, ui_owned_paths)
    return {
        "action": "updated" if managed_drift else "noop",
        "formData": _merge_preserving_paths(desired_form, existing, ui_owned_paths),
        "appliedChanges": [f"{resource_id}.{path}" for path in managed_drift],
        "uiOwnedDriftIgnored": [f"{resource_id}.{path}" for path in ui_owned_drift],
        "materializationDriftIgnored": [f"{resource_id}.{path}" for path in materialization_drift],
    }


def reconcile_default_model(
    client: "OpenWebUIBootstrapClient",
    model_id: str,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    current = client.get_models_config()
    current_model_params = dict(current.get("DEFAULT_MODEL_PARAMS") or {})
    desired_model_params = dict(current_model_params)
    desired_model_params["function_calling"] = DEFAULT_FUNCTION_CALLING_MODE
    desired = {
        "DEFAULT_MODELS": model_id,
        "DEFAULT_PINNED_MODELS": current.get("DEFAULT_PINNED_MODELS"),
        "MODEL_ORDER_LIST": current.get("MODEL_ORDER_LIST") or [],
        "DEFAULT_MODEL_METADATA": current.get("DEFAULT_MODEL_METADATA") or {},
        "DEFAULT_MODEL_PARAMS": desired_model_params,
    }
    applied_changes = _diff_paths(
        current,
        desired,
        ("DEFAULT_MODELS", "DEFAULT_MODEL_PARAMS.function_calling"),
    )
    summary = {
        "action": "updated" if applied_changes else "noop",
        "appliedChanges": applied_changes,
        "uiOwnedDriftIgnored": [],
    }
    if not applied_changes:
        return current, summary
    return client.set_models_config(desired), summary


class OpenWebUIBootstrapClient:
    def __init__(self, *, openwebui_base_url: str, admin_token: str):
        self.openwebui_base_url = openwebui_base_url.rstrip("/")
        self.admin_token = admin_token

    def _request(self, method: str, path: str, payload: Dict[str, Any] | None = None) -> Any:
        return request_json(
            method=method,
            url=f"{self.openwebui_base_url}{path}",
            payload=payload,
            token=self.admin_token,
        )

    def get_tool_server_connections(self) -> List[Dict[str, Any]]:
        payload = self._request("GET", "/api/v1/configs/tool_servers")
        return list(payload.get("TOOL_SERVER_CONNECTIONS", []))

    def set_tool_server_connections(self, connections: List[Dict[str, Any]]) -> Any:
        return self._request(
            "POST",
            "/api/v1/configs/tool_servers",
            payload={"TOOL_SERVER_CONNECTIONS": connections},
        )

    def get_models_config(self) -> Dict[str, Any]:
        return dict(self._request("GET", "/api/v1/configs/models"))

    def set_models_config(self, form_data: Dict[str, Any]) -> Dict[str, Any]:
        return dict(self._request("POST", "/api/v1/configs/models", payload=form_data))

    def get_tool_by_id(self, tool_id: str) -> Dict[str, Any] | None:
        try:
            return self._request("GET", f"/api/v1/tools/id/{tool_id}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def create_tool(self, form_data: Dict[str, Any]) -> Any:
        return self._request("POST", "/api/v1/tools/create", payload=form_data)

    def update_tool(self, tool_id: str, form_data: Dict[str, Any]) -> Any:
        return self._request("POST", f"/api/v1/tools/id/{tool_id}/update", payload=form_data)

    def get_function_by_id(self, function_id: str) -> Dict[str, Any] | None:
        try:
            return self._request("GET", f"/api/v1/functions/id/{function_id}")
        except urllib.error.HTTPError as exc:
            if exc.code == 401 or exc.code == 404:
                return None
            raise

    def create_function(self, form_data: Dict[str, Any]) -> Any:
        return self._request("POST", "/api/v1/functions/create", payload=form_data)

    def update_function(self, function_id: str, form_data: Dict[str, Any]) -> Any:
        return self._request("POST", f"/api/v1/functions/id/{function_id}/update", payload=form_data)

    def toggle_function_active(self, function_id: str) -> Any:
        return self._request("POST", f"/api/v1/functions/id/{function_id}/toggle")

    def toggle_function_global(self, function_id: str) -> Any:
        return self._request("POST", f"/api/v1/functions/id/{function_id}/toggle/global")

    def list_prompts(self) -> List[Dict[str, Any]]:
        for path in ("/api/v1/prompts/list", "/api/v1/prompts/"):
            try:
                payload = self._request("GET", path)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue
                raise
            if isinstance(payload, dict):
                return list(payload.get("items", []))
            if isinstance(payload, list):
                return list(payload)
        return []

    def get_prompt_by_command(self, command: str) -> Dict[str, Any] | None:
        try:
            normalized_command = command.strip()
            for prompt in self.list_prompts():
                if str(prompt.get("command", "")).strip() == normalized_command:
                    return prompt
            return None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def create_prompt(self, form_data: Dict[str, Any]) -> Any:
        return self._request("POST", "/api/v1/prompts/create", payload=form_data)

    def update_prompt(self, prompt_id: str, form_data: Dict[str, Any]) -> Any:
        return self._request("POST", f"/api/v1/prompts/id/{prompt_id}/update", payload=form_data)

    def delete_prompt(self, prompt_id: str) -> Any:
        return self._request("DELETE", f"/api/v1/prompts/id/{prompt_id}/delete")


def upsert_tool_server(
    client: OpenWebUIBootstrapClient,
    *,
    tool_server_export: Dict[str, Any],
    tool_server_token: str,
) -> Dict[str, Any]:
    desired_connection = build_tool_server_connection(tool_server_export, tool_server_token=tool_server_token)
    existing_connections = client.get_tool_server_connections()
    summary = summarize_tool_server_reconcile(existing_connections, desired_connection)
    merged = merge_tool_server_connections(existing_connections, desired_connection)
    if merged != list(existing_connections):
        client.set_tool_server_connections(merged)
    return summary


def upsert_workspace_tools(
    client: OpenWebUIBootstrapClient,
    workspace_tools: Iterable[Dict[str, Any]],
    *,
    tool_server_token: str,
) -> Dict[str, Any]:
    summary = _empty_collection_summary()
    for tool_export in workspace_tools:
        form_data = tool_form_from_export(tool_export, tool_server_token=tool_server_token)
        existing = client.get_tool_by_id(form_data["id"])
        reconcile = _reconcile_existing_resource(
            resource_id=form_data["id"],
            desired_form=form_data,
            existing=existing,
            managed_paths=WORKSPACE_TOOL_MANAGED_PATHS,
            ui_owned_paths=WORKSPACE_TOOL_UI_OWNED_PATHS,
        )
        summary["appliedChanges"].extend(reconcile["appliedChanges"])
        summary["uiOwnedDriftIgnored"].extend(reconcile["uiOwnedDriftIgnored"])
        summary["materializationDriftIgnored"].extend(reconcile["materializationDriftIgnored"])
        if reconcile["action"] == "created":
            client.create_tool(reconcile["formData"])
            summary["createdIds"].append(form_data["id"])
        elif reconcile["action"] == "updated":
            client.update_tool(form_data["id"], reconcile["formData"])
            summary["updatedIds"].append(form_data["id"])
        else:
            summary["noopIds"].append(form_data["id"])
    return summary


def upsert_functions(
    client: OpenWebUIBootstrapClient,
    action_functions: Iterable[Dict[str, Any]],
    *,
    tool_server_token: str,
) -> Dict[str, Any]:
    summary = _empty_collection_summary()
    for function_export in action_functions:
        form_data = function_form_from_export(function_export, tool_server_token=tool_server_token)
        existing = client.get_function_by_id(form_data["id"])
        reconcile = _reconcile_existing_resource(
            resource_id=form_data["id"],
            desired_form=form_data,
            existing=existing,
            managed_paths=ACTION_FUNCTION_MANAGED_PATHS,
            ui_owned_paths=ACTION_FUNCTION_UI_OWNED_PATHS,
        )
        summary["appliedChanges"].extend(reconcile["appliedChanges"])
        summary["uiOwnedDriftIgnored"].extend(reconcile["uiOwnedDriftIgnored"])
        summary["materializationDriftIgnored"].extend(reconcile["materializationDriftIgnored"])
        if reconcile["action"] == "created":
            existing = client.create_function(reconcile["formData"])
            summary["createdIds"].append(form_data["id"])
            desired_active = bool(function_export.get("isActive", True))
            desired_global = bool(function_export.get("isGlobal", True))
            if bool(existing.get("is_active")) != desired_active:
                existing = client.toggle_function_active(form_data["id"])
                summary["appliedChanges"].append(f"{form_data['id']}.is_active")
            if bool(existing.get("is_global")) != desired_global:
                client.toggle_function_global(form_data["id"])
                summary["appliedChanges"].append(f"{form_data['id']}.is_global")
        elif reconcile["action"] == "updated":
            client.update_function(form_data["id"], reconcile["formData"])
            summary["updatedIds"].append(form_data["id"])
        else:
            summary["noopIds"].append(form_data["id"])
    return summary


def upsert_prompts(client: OpenWebUIBootstrapClient, prompts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    summary = _empty_collection_summary()
    for prompt_export in prompts:
        form_data = prompt_form_from_export(prompt_export)
        existing = client.get_prompt_by_command(form_data["command"])
        resource_id = str(existing.get("id") if existing else form_data["command"])
        reconcile = _reconcile_existing_resource(
            resource_id=resource_id,
            desired_form=form_data,
            existing=existing,
            managed_paths=PROMPT_MANAGED_PATHS,
            ui_owned_paths=PROMPT_UI_OWNED_PATHS,
        )
        summary["appliedChanges"].extend(reconcile["appliedChanges"])
        summary["uiOwnedDriftIgnored"].extend(reconcile["uiOwnedDriftIgnored"])
        summary["materializationDriftIgnored"].extend(reconcile["materializationDriftIgnored"])
        if reconcile["action"] == "created":
            client.create_prompt(reconcile["formData"])
            summary["createdIds"].append(form_data["command"])
        elif reconcile["action"] == "updated":
            client.update_prompt(existing["id"], reconcile["formData"])
            summary["updatedIds"].append(form_data["command"])
        else:
            summary["noopIds"].append(form_data["command"])
    return summary


def remove_legacy_prompts(client: OpenWebUIBootstrapClient) -> int:
    deleted_count = 0
    for prompt in client.list_prompts():
        command = str(prompt.get("command", "")).strip()
        prompt_id = str(prompt.get("id") or "").strip()
        if command in LEGACY_PROMPT_COMMANDS and prompt_id:
            client.delete_prompt(prompt_id)
            deleted_count += 1
    return deleted_count


def ensure_default_model(client: OpenWebUIBootstrapClient, model_id: str) -> Dict[str, Any]:
    config, _ = reconcile_default_model(client, model_id)
    return config


def bootstrap_openwebui(
    *,
    backend_base_url: str,
    openwebui_base_url: str,
    admin_email: str,
    admin_password: str,
    tool_server_token: str,
    default_model: str = DEFAULT_OPENWEBUI_MODEL,
) -> Dict[str, Any]:
    export_bundle = fetch_binding_export(backend_base_url)
    admin_token = sign_in(openwebui_base_url, email=admin_email, password=admin_password)
    client = OpenWebUIBootstrapClient(openwebui_base_url=openwebui_base_url, admin_token=admin_token)
    runtime_config = dict(export_bundle.get("runtimeConfig") or {})
    ownership = dict(export_bundle.get("ownership") or {})
    effective_default_model = str(runtime_config.get("defaultModel") or default_model)

    tool_server_summary = upsert_tool_server(
        client,
        tool_server_export=export_bundle["toolServer"],
        tool_server_token=tool_server_token,
    )
    workspace_tools_summary = upsert_workspace_tools(
        client,
        export_bundle.get("workspaceTools", []),
        tool_server_token=tool_server_token,
    )
    action_functions_summary = upsert_functions(
        client,
        export_bundle.get("actionFunctions", []),
        tool_server_token=tool_server_token,
    )
    workspace_prompts_summary = upsert_prompts(client, export_bundle.get("workspacePrompts", []))
    legacy_prompt_cleanup_count = remove_legacy_prompts(client)
    models_config, default_model_summary = reconcile_default_model(client, effective_default_model)
    drift_summary = {
        "toolServer": tool_server_summary,
        "workspaceTools": workspace_tools_summary,
        "actionFunctions": action_functions_summary,
        "workspacePrompts": workspace_prompts_summary,
        "defaultModelConfig": default_model_summary,
        "legacyPromptCleanupCount": legacy_prompt_cleanup_count,
    }
    drift_summary["noOp"] = (
        tool_server_summary.get("action") == "noop"
        and not _collection_has_changes(workspace_tools_summary)
        and not _collection_has_changes(action_functions_summary)
        and not _collection_has_changes(workspace_prompts_summary)
        and default_model_summary.get("action") == "noop"
        and legacy_prompt_cleanup_count == 0
    )

    return {
        "status": "ok",
        "toolServerName": export_bundle["toolServer"]["name"],
        "workspaceToolCount": len(export_bundle.get("workspaceTools", [])),
        "actionFunctionCount": len(export_bundle.get("actionFunctions", [])),
        "promptCount": len(export_bundle.get("workspacePrompts", [])),
        "legacyPromptCleanupCount": legacy_prompt_cleanup_count,
        "defaultModel": models_config.get("DEFAULT_MODELS"),
        "defaultFunctionCalling": (models_config.get("DEFAULT_MODEL_PARAMS") or {}).get("function_calling"),
        "driftSummary": drift_summary,
        "ownership": ownership,
        "runtimeConfig": runtime_config,
    }


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap Agent Navigator tools into Open WebUI.")
    parser.add_argument("--backend-base-url", default=DEFAULT_BACKEND_BASE_URL)
    parser.add_argument("--openwebui-base-url", default=DEFAULT_OPENWEBUI_BASE_URL)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--default-model", default=DEFAULT_OPENWEBUI_MODEL)
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    env_values = parse_env_file(Path(args.env_file))

    admin_email = get_env_value("WEBUI_ADMIN_EMAIL", env_values, "admin@example.com")
    admin_password = get_env_value("WEBUI_ADMIN_PASSWORD", env_values, "change-me-now")
    tool_server_token = get_env_value("OPENAPI_TOOL_SERVER_TOKEN", env_values, DEFAULT_TOOL_SERVER_TOKEN)

    if tool_server_token == DEFAULT_TOOL_SERVER_TOKEN:
        print(
            "warning: OPENAPI_TOOL_SERVER_TOKEN missing in backend/.env; using dev fallback token",
            file=sys.stderr,
        )

    result = bootstrap_openwebui(
        backend_base_url=args.backend_base_url,
        openwebui_base_url=args.openwebui_base_url,
        admin_email=str(admin_email),
        admin_password=str(admin_password),
        tool_server_token=str(tool_server_token),
        default_model=str(args.default_model),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
