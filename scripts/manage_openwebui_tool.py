#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
from pathlib import Path
from typing import Any, Dict

from bootstrap_openwebui import (
    DEFAULT_ENV_PATH,
    DEFAULT_OPENWEBUI_BASE_URL,
    get_env_value,
    parse_env_file,
    request_json,
    sign_in,
)


FRONTMATTER_TITLE_PATTERN = re.compile(r"^\s*title:\s*(.+?)\s*$", re.MULTILINE)


class OpenWebUIAdminClient:
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

    def get_tool(self, tool_id: str) -> Dict[str, Any] | None:
        try:
            return self._request("GET", f"/api/v1/tools/id/{tool_id}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def create_tool(self, form_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/v1/tools/create", payload=form_data)

    def update_tool(self, tool_id: str, form_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", f"/api/v1/tools/id/{tool_id}/update", payload=form_data)

    def delete_tool(self, tool_id: str) -> bool:
        return bool(self._request("DELETE", f"/api/v1/tools/id/{tool_id}/delete"))


def parse_frontmatter_title(tool_content: str, *, default: str) -> str:
    match = FRONTMATTER_TITLE_PATTERN.search(tool_content)
    if not match:
        return default
    value = match.group(1).strip()
    return value or default


def build_tool_form(
    *,
    tool_id: str,
    tool_name: str,
    tool_content: str,
    description: str,
) -> Dict[str, Any]:
    return {
        "id": tool_id,
        "name": tool_name,
        "content": tool_content,
        "meta": {"description": description},
    }


def same_tool_payload(existing: Dict[str, Any], desired: Dict[str, Any]) -> bool:
    return (
        str(existing.get("name") or "") == str(desired.get("name") or "")
        and str(existing.get("content") or "") == str(desired.get("content") or "")
        and str((existing.get("meta") or {}).get("description") or "")
        == str((desired.get("meta") or {}).get("description") or "")
    )


def install_tool(
    client: OpenWebUIAdminClient,
    *,
    tool_id: str,
    tool_name: str,
    tool_path: Path,
    description: str,
) -> Dict[str, Any]:
    desired = build_tool_form(
        tool_id=tool_id,
        tool_name=tool_name,
        tool_content=tool_path.read_text(encoding="utf-8"),
        description=description,
    )
    existing = client.get_tool(tool_id)
    if existing is None:
        created = client.create_tool(desired)
        return {
            "status": "ok",
            "action": "created",
            "toolId": tool_id,
            "toolName": tool_name,
            "toolFile": str(tool_path),
            "toolMetaDescription": (created.get("meta") or {}).get("description"),
        }
    if same_tool_payload(existing, desired):
        return {
            "status": "ok",
            "action": "noop",
            "toolId": tool_id,
            "toolName": str(existing.get("name") or tool_name),
            "toolFile": str(tool_path),
            "toolMetaDescription": (existing.get("meta") or {}).get("description"),
        }
    updated = client.update_tool(tool_id, desired)
    return {
        "status": "ok",
        "action": "updated",
        "toolId": tool_id,
        "toolName": str(updated.get("name") or tool_name),
        "toolFile": str(tool_path),
        "toolMetaDescription": (updated.get("meta") or {}).get("description"),
    }


def delete_tool(client: OpenWebUIAdminClient, *, tool_id: str, tool_name: str) -> Dict[str, Any]:
    existing = client.get_tool(tool_id)
    if existing is None:
        return {
            "status": "ok",
            "action": "noop",
            "toolId": tool_id,
            "toolName": tool_name,
        }
    deleted = client.delete_tool(tool_id)
    return {
        "status": "ok" if deleted else "error",
        "action": "deleted" if deleted else "failed",
        "toolId": tool_id,
        "toolName": str(existing.get("name") or tool_name),
    }


def tool_status(client: OpenWebUIAdminClient, *, tool_id: str, tool_name: str) -> Dict[str, Any]:
    existing = client.get_tool(tool_id)
    if existing is None:
        return {
            "status": "ok",
            "present": False,
            "toolId": tool_id,
            "toolName": tool_name,
        }
    return {
        "status": "ok",
        "present": True,
        "toolId": tool_id,
        "toolName": str(existing.get("name") or tool_name),
        "toolMetaDescription": (existing.get("meta") or {}).get("description"),
        "toolSpecNames": [str(spec.get("name") or "") for spec in existing.get("specs") or []],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage a local Open WebUI Workspace Tool via the admin API.")
    parser.add_argument("command", choices=("install", "status", "delete"))
    parser.add_argument("--tool-file", default=None)
    parser.add_argument("--tool-id", required=True)
    parser.add_argument("--tool-name", default=None)
    parser.add_argument("--description", default="")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--openwebui-base-url", default=DEFAULT_OPENWEBUI_BASE_URL)
    return parser.parse_args(argv)


def resolve_tool_name(*, tool_path: Path | None, explicit_name: str | None, tool_id: str) -> str:
    if explicit_name:
        return explicit_name
    if tool_path is None:
        return tool_id
    tool_content = tool_path.read_text(encoding="utf-8")
    return parse_frontmatter_title(tool_content, default=tool_id)


def load_admin_client(*, env_file: Path, openwebui_base_url: str) -> OpenWebUIAdminClient:
    env_values = parse_env_file(env_file)
    admin_email = get_env_value("WEBUI_ADMIN_EMAIL", env_values, "admin@example.com")
    admin_password = get_env_value("WEBUI_ADMIN_PASSWORD", env_values, "change-me-now")
    admin_token = sign_in(openwebui_base_url, email=str(admin_email), password=str(admin_password))
    return OpenWebUIAdminClient(openwebui_base_url=openwebui_base_url, admin_token=admin_token)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    tool_path = Path(args.tool_file).resolve() if args.tool_file else None
    if args.command == "install" and tool_path is None:
        raise SystemExit("--tool-file is required for install")
    if tool_path is not None and not tool_path.is_file():
        raise SystemExit(f"Tool file not found: {tool_path}")

    tool_name = resolve_tool_name(tool_path=tool_path, explicit_name=args.tool_name, tool_id=args.tool_id)
    client = load_admin_client(env_file=Path(args.env_file), openwebui_base_url=args.openwebui_base_url)

    if args.command == "install":
        assert tool_path is not None
        result = install_tool(
            client,
            tool_id=args.tool_id,
            tool_name=tool_name,
            tool_path=tool_path,
            description=args.description,
        )
    elif args.command == "status":
        result = tool_status(client, tool_id=args.tool_id, tool_name=tool_name)
    else:
        result = delete_tool(client, tool_id=args.tool_id, tool_name=tool_name)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
