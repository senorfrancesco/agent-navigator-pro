#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
DEFAULT_TOOL_SERVER_TOKEN = "agent-navigator-tool-server-dev-token"
BOOTSTRAP_CONNECTION_ID = "agent_navigator_openapi_tool_server"
TOOL_SERVER_TOKEN_PLACEHOLDER = "SET_OPENAPI_TOOL_SERVER_TOKEN"


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
    target_url = f"{desired_connection['url'].rstrip('/')}{desired_connection['path']}"
    updated = False

    for connection in existing_connections:
        config = connection.get("config", {}) or {}
        connection_url = f"{str(connection.get('url', '')).rstrip('/')}{connection.get('path', '')}"
        if config.get("bootstrap_id") == BOOTSTRAP_CONNECTION_ID or connection_url == target_url:
            preserved = dict(connection)
            preserved.update({k: v for k, v in desired_connection.items() if k != "config"})
            preserved["config"] = {**config, **desired_connection.get("config", {})}
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


def upsert_tool_server(client: OpenWebUIBootstrapClient, *, tool_server_export: Dict[str, Any], tool_server_token: str) -> None:
    desired_connection = build_tool_server_connection(tool_server_export, tool_server_token=tool_server_token)
    merged = merge_tool_server_connections(client.get_tool_server_connections(), desired_connection)
    client.set_tool_server_connections(merged)


def upsert_workspace_tools(
    client: OpenWebUIBootstrapClient,
    workspace_tools: Iterable[Dict[str, Any]],
    *,
    tool_server_token: str,
) -> None:
    for tool_export in workspace_tools:
        form_data = tool_form_from_export(tool_export, tool_server_token=tool_server_token)
        existing = client.get_tool_by_id(form_data["id"])
        if existing is None:
            client.create_tool(form_data)
        else:
            client.update_tool(form_data["id"], form_data)


def upsert_functions(
    client: OpenWebUIBootstrapClient,
    action_functions: Iterable[Dict[str, Any]],
    *,
    tool_server_token: str,
) -> None:
    for function_export in action_functions:
        form_data = function_form_from_export(function_export, tool_server_token=tool_server_token)
        existing = client.get_function_by_id(form_data["id"])
        if existing is None:
            existing = client.create_function(form_data)
        else:
            existing = client.update_function(form_data["id"], form_data)

        if bool(existing.get("is_active")) != bool(function_export.get("isActive", True)):
            existing = client.toggle_function_active(form_data["id"])
        if bool(existing.get("is_global")) != bool(function_export.get("isGlobal", True)):
            client.toggle_function_global(form_data["id"])


def upsert_prompts(client: OpenWebUIBootstrapClient, prompts: Iterable[Dict[str, Any]]) -> None:
    for prompt_export in prompts:
        form_data = prompt_form_from_export(prompt_export)
        existing = client.get_prompt_by_command(form_data["command"])
        if existing is None:
            client.create_prompt(form_data)
        else:
            client.update_prompt(existing["id"], form_data)


def bootstrap_openwebui(
    *,
    backend_base_url: str,
    openwebui_base_url: str,
    admin_email: str,
    admin_password: str,
    tool_server_token: str,
) -> Dict[str, Any]:
    export_bundle = fetch_binding_export(backend_base_url)
    admin_token = sign_in(openwebui_base_url, email=admin_email, password=admin_password)
    client = OpenWebUIBootstrapClient(openwebui_base_url=openwebui_base_url, admin_token=admin_token)

    upsert_tool_server(client, tool_server_export=export_bundle["toolServer"], tool_server_token=tool_server_token)
    upsert_workspace_tools(client, export_bundle.get("workspaceTools", []), tool_server_token=tool_server_token)
    upsert_functions(client, export_bundle.get("actionFunctions", []), tool_server_token=tool_server_token)
    upsert_prompts(client, export_bundle.get("workspacePrompts", []))

    return {
        "status": "ok",
        "toolServerName": export_bundle["toolServer"]["name"],
        "workspaceToolCount": len(export_bundle.get("workspaceTools", [])),
        "actionFunctionCount": len(export_bundle.get("actionFunctions", [])),
        "promptCount": len(export_bundle.get("workspacePrompts", [])),
    }


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap Agent Navigator tools into Open WebUI.")
    parser.add_argument("--backend-base-url", default=DEFAULT_BACKEND_BASE_URL)
    parser.add_argument("--openwebui-base-url", default=DEFAULT_OPENWEBUI_BASE_URL)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
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
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
