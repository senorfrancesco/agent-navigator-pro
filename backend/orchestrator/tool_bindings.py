from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent
from typing import Any, Dict, Iterable, Literal, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from orchestrator.tool_catalog import ToolName

BindingEntrypointType = Literal["direct_action", "prompt_shortcut", "followup_action"]
BindingResultMode = Literal["inline", "accepted_job", "rich_card"]


@dataclass(frozen=True)
class ToolBinding:
    binding_id: str
    label: str
    tool_name: ToolName
    entrypoint_type: BindingEntrypointType
    result_mode: BindingResultMode
    enabled: bool = True
    requires_document_context: bool = False
    requires_min_documents: int = 0
    default_args: Optional[Dict[str, Any]] = None
    slash_command: Optional[str] = None
    visibility_scope: str = "chat"
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "label": self.label,
            "tool_name": self.tool_name,
            "entrypoint_type": self.entrypoint_type,
            "result_mode": self.result_mode,
            "enabled": self.enabled,
            "requires_document_context": self.requires_document_context,
            "requires_min_documents": self.requires_min_documents,
            "default_args": dict(self.default_args or {}),
            "slash_command": self.slash_command,
            "visibility_scope": self.visibility_scope,
            "description": self.description,
        }


TOOL_BINDINGS: Tuple[ToolBinding, ...] = (
    ToolBinding(
        binding_id="equipment.fast.direct",
        label="Быстрый анализ оборудования",
        tool_name="analyze_equipment_fast",
        entrypoint_type="direct_action",
        result_mode="inline",
        default_args={"equipment_query": "{{chat_input}}"},
        description="Детерминированный one-shot запуск быстрого анализа оборудования.",
    ),
    ToolBinding(
        binding_id="equipment.deep.direct",
        label="Глубокий анализ оборудования",
        tool_name="analyze_equipment_deep",
        entrypoint_type="direct_action",
        result_mode="accepted_job",
        default_args={"equipment_query": "{{chat_input}}", "job_mode": "force_async"},
        description="Детерминированный long-running запуск глубокого анализа оборудования.",
    ),
    ToolBinding(
        binding_id="equipment.fast.prompt",
        label="Slash: быстрый анализ оборудования",
        tool_name="analyze_equipment_fast",
        entrypoint_type="prompt_shortcut",
        result_mode="inline",
        slash_command="/hw_fast",
        default_args={"equipment_query": "{{chat_input}}"},
        visibility_scope="operator_only",
        description="Convenience shortcut поверх backend-owned equipment fast tool.",
    ),
    ToolBinding(
        binding_id="equipment.deep.prompt",
        label="Slash: глубокий анализ оборудования",
        tool_name="analyze_equipment_deep",
        entrypoint_type="prompt_shortcut",
        result_mode="accepted_job",
        slash_command="/hw_deep",
        default_args={"equipment_query": "{{chat_input}}", "job_mode": "force_async"},
        visibility_scope="operator_only",
        description="Convenience shortcut поверх backend-owned equipment deep tool.",
    ),
    ToolBinding(
        binding_id="document.ask.direct",
        label="Спросить по документу",
        tool_name="ask_document",
        entrypoint_type="direct_action",
        result_mode="inline",
        enabled=False,
        requires_document_context=True,
        default_args={"question": "{{chat_input}}"},
        description="Будет включён после backend-owned document binding (`M2.1/M2.2`).",
    ),
    ToolBinding(
        binding_id="document.fast.direct",
        label="Быстрый анализ документа",
        tool_name="analyze_document_fast",
        entrypoint_type="direct_action",
        result_mode="inline",
        enabled=False,
        requires_document_context=True,
        default_args={"analysis_goal": "{{chat_input}}"},
        description="Будет включён после backend-owned document binding (`M2.1/M2.2`).",
    ),
    ToolBinding(
        binding_id="document.deep.direct",
        label="Глубокий анализ документа",
        tool_name="analyze_document_deep",
        entrypoint_type="direct_action",
        result_mode="accepted_job",
        enabled=False,
        requires_document_context=True,
        default_args={"analysis_goal": "{{chat_input}}", "job_mode": "force_async"},
        description="Будет включён после backend-owned document binding (`M2.1/M2.2`).",
    ),
    ToolBinding(
        binding_id="compare.fast.direct",
        label="Сравнить документы",
        tool_name="compare_documents_fast",
        entrypoint_type="direct_action",
        result_mode="inline",
        enabled=False,
        requires_document_context=True,
        requires_min_documents=2,
        default_args={"comparison_goal": "{{chat_input}}"},
        description="Будет включён после multi-document binding (`M2.2`).",
    ),
    ToolBinding(
        binding_id="compare.deep.direct",
        label="Глубокое сравнение документов",
        tool_name="compare_documents_deep",
        entrypoint_type="direct_action",
        result_mode="accepted_job",
        enabled=False,
        requires_document_context=True,
        requires_min_documents=2,
        default_args={"comparison_goal": "{{chat_input}}", "job_mode": "force_async"},
        description="Будет включён после multi-document binding (`M2.2`).",
    ),
)


def list_tool_bindings() -> Tuple[ToolBinding, ...]:
    return TOOL_BINDINGS


def list_enabled_tool_bindings(entrypoint_type: BindingEntrypointType | None = None) -> Tuple[ToolBinding, ...]:
    return tuple(
        binding
        for binding in TOOL_BINDINGS
        if binding.enabled and (entrypoint_type is None or binding.entrypoint_type == entrypoint_type)
    )


def get_tool_binding(binding_id: str) -> ToolBinding:
    for binding in TOOL_BINDINGS:
        if binding.binding_id == binding_id:
            return binding
    raise KeyError(binding_id)


def get_direct_binding_for_tool(tool_name: ToolName) -> ToolBinding | None:
    for binding in TOOL_BINDINGS:
        if binding.tool_name == tool_name and binding.entrypoint_type == "direct_action" and binding.enabled:
            return binding
    return None


def build_result_available_actions(tool_name: ToolName, *, status_url: str | None = None) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    if tool_name == "analyze_equipment_fast":
        actions.append(
            {
                "action_id": "equipment.deep.direct",
                "label": "Глубокий анализ",
                "action_type": "rerun_tool",
                "payload": {
                    "binding_id": "equipment.deep.direct",
                    "tool_name": "analyze_equipment_deep",
                    "job_mode": "force_async",
                },
            }
        )
    elif tool_name == "analyze_equipment_deep" and status_url:
        actions.extend(
            [
                {
                    "action_id": "tool-job.status.open",
                    "label": "Проверить статус",
                    "action_type": "open_url",
                    "payload": {
                        "binding_id": "tool-job.status.open",
                        "url": status_url,
                    },
                },
                {
                    "action_id": "tool-job.result.open",
                    "label": "Открыть результат",
                    "action_type": "open_url",
                    "payload": {
                        "binding_id": "tool-job.result.open",
                        "url": f"{status_url}/result",
                    },
                },
                {
                    "action_id": "tool-job.cancel",
                    "label": "Отменить задачу",
                    "action_type": "cancel_job",
                    "payload": {
                        "binding_id": "tool-job.cancel",
                        "cancel_url": f"{status_url}/cancel",
                    },
                },
            ]
        )

    return actions


def build_openwebui_binding_export(*, backend_base_url: str) -> Dict[str, Any]:
    normalized_base_url = backend_base_url.rstrip("/")
    container_base_url = _derive_container_base_url(normalized_base_url)
    direct_actions = [binding.to_dict() for binding in list_enabled_tool_bindings("direct_action")]
    prompts = [binding.to_dict() for binding in list_enabled_tool_bindings("prompt_shortcut")]
    return {
        "toolServer": {
            "name": "Agent Navigator OpenAPI Tool Server",
            "baseUrl": f"{normalized_base_url}/tool-server",
            "browserReachableBaseUrl": f"{normalized_base_url}/tool-server",
            "containerReachableBaseUrl": f"{container_base_url}/tool-server",
            "manualEnableRequired": True,
        },
        "workspaceTools": _build_openwebui_workspace_tools(container_tool_server_base_url=f"{container_base_url}/tool-server"),
        "directActions": direct_actions,
        "workspacePrompts": [
            {
                **binding,
                "promptTemplate": _prompt_template_for_binding(binding["tool_name"], binding["slash_command"]),
                "openwebui": {
                    "title": binding["label"],
                    "command": binding["slash_command"],
                    "content": _prompt_template_for_binding(binding["tool_name"], binding["slash_command"]),
                },
                "manualImportRequired": True,
            }
            for binding in prompts
        ],
        "actionFunctions": _build_openwebui_action_functions(container_tool_server_base_url=f"{container_base_url}/tool-server"),
        "importChecklist": [
            "1. Импортируйте `Agent Navigator OpenAPI Tool Server` как OpenAPI connection и включайте его в каждом целевом чате вручную.",
            "2. Добавьте `Workspace > Tools` для equipment fast/deep из export bundle как thin Python wrappers без доменной логики в Open WebUI.",
            "3. Добавьте `Workspace Prompts` `/hw_fast` и `/hw_deep` из export bundle без изменения команд.",
            "4. Импортируйте `Action Functions` и привяжите их только к raw-model provider (`raw.*`).",
            "5. В `Valves` каждого local tool и Action Function вставьте актуальный `OPENAPI_TOOL_SERVER_TOKEN` из backend `.env`.",
            "6. Для deep job flow проверьте `equipment_deep_action`, затем `tool_job_refresh_action` и `tool_job_cancel_action`.",
        ],
        "notes": [
            "Action Functions в Open WebUI остаются admin-managed glue layer поверх backend-owned tool server.",
            "Workspace > Tools остаётся primary explicit picker для equipment tools; prompts и actions являются secondary UX layers.",
            "Bindings для документов и compare intentionally disabled до завершения backend-owned document binding.",
        ],
    }


def summarize_tool_binding_catalog() -> Dict[str, Any]:
    bindings = list_tool_bindings()
    enabled = [binding for binding in bindings if binding.enabled]
    return {
        "total": len(bindings),
        "enabled": len(enabled),
        "directActionCount": len([binding for binding in enabled if binding.entrypoint_type == "direct_action"]),
        "promptShortcutCount": len([binding for binding in enabled if binding.entrypoint_type == "prompt_shortcut"]),
        "disabledDocumentDependentCount": len(
            [
                binding
                for binding in bindings
                if not binding.enabled and (binding.requires_document_context or binding.requires_min_documents > 0)
            ]
        ),
    }


def _prompt_template_for_binding(tool_name: ToolName, slash_command: str | None) -> str:
    if tool_name == "analyze_equipment_fast":
        return "Используй инструмент analyze_equipment_fast для краткого анализа оборудования по запросу пользователя."
    if tool_name == "analyze_equipment_deep":
        return "Используй инструмент analyze_equipment_deep для глубокого анализа оборудования. Если результат принят как job, сообщи об этом явно."
    return f"Используй инструмент {tool_name} по запросу пользователя."


def _derive_container_base_url(browser_base_url: str) -> str:
    parsed = urlsplit(browser_base_url)
    host = parsed.hostname or ""
    if host in {"127.0.0.1", "localhost"}:
        netloc = "host.docker.internal"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)).rstrip("/")
    return browser_base_url.rstrip("/")


def _build_openwebui_action_functions(*, container_tool_server_base_url: str) -> list[dict[str, Any]]:
    return [
        {
            "action_id": "equipment_fast_action",
            "title": "Быстрый анализ оборудования",
            "description": "Запускает analyze_equipment_fast по последнему пользовательскому запросу без prompt-угадывания модели.",
            "targetModels": ["raw.*"],
            "manualImportRequired": True,
            "isActive": True,
            "isGlobal": True,
            "pythonCode": _build_equipment_action_code(
                container_tool_server_base_url=container_tool_server_base_url,
                tool_name="analyze_equipment_fast",
                action_label="Быстрый анализ оборудования",
                force_async=False,
                priority=10,
            ),
        },
        {
            "action_id": "equipment_deep_action",
            "title": "Глубокий анализ оборудования",
            "description": "Запускает analyze_equipment_deep и возвращает accepted job contract с понятным status URL.",
            "targetModels": ["raw.*"],
            "manualImportRequired": True,
            "isActive": True,
            "isGlobal": True,
            "pythonCode": _build_equipment_action_code(
                container_tool_server_base_url=container_tool_server_base_url,
                tool_name="analyze_equipment_deep",
                action_label="Глубокий анализ оборудования",
                force_async=True,
                priority=20,
            ),
        },
        {
            "action_id": "tool_job_refresh_action",
            "title": "Обновить deep-job",
            "description": "Подтягивает текущий статус deep job и, если результат готов, открывает terminal tool result.",
            "targetModels": ["raw.*"],
            "manualImportRequired": True,
            "isActive": True,
            "isGlobal": True,
            "pythonCode": _build_tool_job_refresh_action_code(
                container_tool_server_base_url=container_tool_server_base_url,
            ),
        },
        {
            "action_id": "tool_job_cancel_action",
            "title": "Отменить deep-job",
            "description": "Отправляет cancel на активную tool job после явного подтверждения пользователя.",
            "targetModels": ["raw.*"],
            "manualImportRequired": True,
            "isActive": True,
            "isGlobal": True,
            "pythonCode": _build_tool_job_cancel_action_code(
                container_tool_server_base_url=container_tool_server_base_url,
            ),
        },
    ]


def _build_openwebui_workspace_tools(*, container_tool_server_base_url: str) -> list[dict[str, Any]]:
    return [
        {
            "tool_id": "equipment_fast_tool",
            "title": "Быстрый анализ оборудования",
            "description": "Явный local tool wrapper над backend endpoint /tool-server/tools/analyze_equipment_fast.",
            "targetModels": ["raw.*"],
            "manualImportRequired": True,
            "pythonCode": _build_openwebui_workspace_tool_code(
                container_tool_server_base_url=container_tool_server_base_url,
                tool_name="analyze_equipment_fast",
                method_name="analyze_equipment_fast",
                action_label="Быстрый анализ оборудования",
                result_mode="inline",
                priority=10,
            ),
        },
        {
            "tool_id": "equipment_deep_tool",
            "title": "Глубокий анализ оборудования",
            "description": "Явный local tool wrapper над backend endpoint /tool-server/tools/analyze_equipment_deep.",
            "targetModels": ["raw.*"],
            "manualImportRequired": True,
            "pythonCode": _build_openwebui_workspace_tool_code(
                container_tool_server_base_url=container_tool_server_base_url,
                tool_name="analyze_equipment_deep",
                method_name="analyze_equipment_deep",
                action_label="Глубокий анализ оборудования",
                result_mode="accepted_job",
                priority=20,
            ),
        },
    ]


def _build_openwebui_workspace_tool_code(
    *,
    container_tool_server_base_url: str,
    tool_name: str,
    method_name: str,
    action_label: str,
    result_mode: BindingResultMode,
    priority: int,
) -> str:
    is_async = result_mode == "accepted_job"
    payload_line = '"job_mode": "force_async",' if is_async else ""
    return dedent(
        f'''
        """
        title: {action_label}
        author: Agent Navigator
        version: 1.0.0
        requirements:
        """

        import asyncio
        import json
        import urllib.request
        from pydantic import BaseModel

        async def _request_json(method, url, token, payload=None):
            def _do_request():
                data = None if payload is None else json.dumps(payload).encode("utf-8")
                request = urllib.request.Request(
                    url,
                    data=data,
                    method=method,
                    headers={{
                        "Authorization": f"Bearer {{token}}",
                        "Content-Type": "application/json",
                    }},
                )
                with urllib.request.urlopen(request, timeout=45) as response:
                    return json.loads(response.read().decode("utf-8"))

            return await asyncio.to_thread(_do_request)

        class Tools:
            class Valves(BaseModel):
                tool_server_base_url: str = "{container_tool_server_base_url}"
                tool_server_token: str = "SET_OPENAPI_TOOL_SERVER_TOKEN"
                priority: int = {priority}

            def __init__(self):
                self.valves = self.Valves()

            async def {method_name}(self, query: str) -> str:
                """
                {action_label}.

                :param query: Текстовый запрос пользователя для анализа оборудования.
                :return: Готовый ответ backend tool server или accepted job summary.
                """
                payload = {{
                    "equipment_query": query,
                    {payload_line}
                }}
                payload = {{key: value for key, value in payload.items() if value is not None and value != ""}}
                response = await _request_json(
                    "POST",
                    f"{{self.valves.tool_server_base_url}}/tools/{tool_name}",
                    self.valves.tool_server_token,
                    payload,
                )

                if response.get("status") == "accepted":
                    return (
                        "Глубокий анализ принят как deep-job.\\n"
                        f"job_id: {{response.get('job_id', 'unknown')}}\\n"
                        f"status_url: {{response.get('status_url', '')}}"
                    )

                return response.get("assistant_message") or json.dumps(response, ensure_ascii=False, indent=2)
        '''
    ).strip()


def _build_equipment_action_code(
    *,
    container_tool_server_base_url: str,
    tool_name: str,
    action_label: str,
    force_async: bool,
    priority: int,
) -> str:
    payload_line = '"job_mode": "force_async",' if force_async else ""
    return dedent(
        f"""
        import asyncio
        import json
        import urllib.error
        import urllib.request
        from pydantic import BaseModel

        def _content_to_text(content):
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                chunks = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        value = str(item.get("text", "")).strip()
                        if value:
                            chunks.append(value)
                return "\\n".join(chunks).strip()
            if isinstance(content, dict):
                return str(content.get("content", "")).strip()
            return ""

        def _extract_last_user_text(body):
            messages = body.get("messages") or []
            for message in reversed(messages):
                if message.get("role") == "user":
                    text = _content_to_text(message.get("content"))
                    if text:
                        return text
            return _content_to_text(body.get("content")) or _content_to_text(body.get("message", {{}}).get("content"))

        async def _request_json(method, url, token, payload=None):
            def _do_request():
                data = None if payload is None else json.dumps(payload).encode("utf-8")
                request = urllib.request.Request(
                    url,
                    data=data,
                    method=method,
                    headers={{
                        "Authorization": f"Bearer {{token}}",
                        "Content-Type": "application/json",
                    }},
                )
                with urllib.request.urlopen(request, timeout=45) as response:
                    return json.loads(response.read().decode("utf-8"))

            return await asyncio.to_thread(_do_request)

        class Action:
            class Valves(BaseModel):
                tool_server_base_url: str = "{container_tool_server_base_url}"
                tool_server_token: str = "SET_OPENAPI_TOOL_SERVER_TOKEN"
                priority: int = {priority}

            def __init__(self):
                self.valves = self.Valves()

            async def action(self, body: dict, __event_emitter__=None, __event_call__=None, __id__=None):
                user_text = _extract_last_user_text(body)
                if not user_text:
                    return {{"content": "Не удалось определить последний пользовательский запрос для `{action_label}`."}}

                if __event_emitter__:
                    await __event_emitter__({{
                        "type": "status",
                        "data": {{"description": "Запускаю `{tool_name}` через Agent Navigator Tools..."}},
                    }})

                payload = {{
                    "equipment_query": user_text,
                    {payload_line}
                }}
                payload = {{key: value for key, value in payload.items() if value is not None and value != ""}}
                response = await _request_json(
                    "POST",
                    f"{{self.valves.tool_server_base_url}}/tools/{tool_name}",
                    self.valves.tool_server_token,
                    payload,
                )

                if response.get("status") == "accepted":
                    status_url = response.get("status_url", "")
                    job_id = response.get("job_id", "unknown")
                    return {{
                        "content": (
                            f"`{action_label}` принят как deep-job.\\n"
                            f"job_id: {{job_id}}\\n"
                            f"status_url: {{status_url}}"
                        ),
                        "job_id": job_id,
                        "status_url": status_url,
                        "tool_job": {{
                            "job_id": job_id,
                            "status_url": status_url,
                            "tool_name": "{tool_name}",
                            "status": "accepted",
                        }},
                    }}

                return {{
                    "content": response.get("assistant_message")
                    or json.dumps(response, ensure_ascii=False, indent=2)
                }}
        """
    ).strip()


def _build_tool_job_refresh_action_code(*, container_tool_server_base_url: str) -> str:
    return dedent(
        f"""
        import asyncio
        import json
        import re
        import urllib.error
        import urllib.request
        from urllib.parse import urlsplit
        from pydantic import BaseModel

        def _tool_server_origin(tool_server_base_url):
            parsed = urlsplit(tool_server_base_url)
            return f"{{parsed.scheme}}://{{parsed.netloc}}"

        def _normalize_status_url(candidate, tool_server_base_url):
            if not candidate:
                return None
            candidate = str(candidate).strip()
            if not candidate:
                return None
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate
            if candidate.startswith("/"):
                return f"{{_tool_server_origin(tool_server_base_url)}}{{candidate}}"
            return f"{{_tool_server_origin(tool_server_base_url)}}/{{candidate.lstrip('/')}}"

        def _extract_tool_job_context(payload):
            if isinstance(payload, dict):
                if payload.get("status_url") or payload.get("job_id"):
                    return {{
                        "status_url": payload.get("status_url"),
                        "job_id": payload.get("job_id"),
                    }}
                tool_job = payload.get("tool_job")
                if isinstance(tool_job, dict):
                    nested = _extract_tool_job_context(tool_job)
                    if nested:
                        return nested
                for value in payload.values():
                    nested = _extract_tool_job_context(value)
                    if nested:
                        return nested
            elif isinstance(payload, list):
                for item in payload:
                    nested = _extract_tool_job_context(item)
                    if nested:
                        return nested
            return None

        def _extract_status_url(body, tool_server_base_url):
            context = _extract_tool_job_context(body)
            if context:
                status_url = _normalize_status_url(context.get("status_url"), tool_server_base_url)
                if status_url:
                    return status_url
                job_id = str(context.get("job_id") or "").strip()
                if job_id:
                    return f"{{_tool_server_origin(tool_server_base_url)}}/tool-server/tool-jobs/{{job_id}}"
            serialized = json.dumps(body, ensure_ascii=False)
            full_match = re.search(r"https?://[^\\s\\\"]+/tool-server/tool-jobs/[A-Za-z0-9._-]+", serialized)
            if full_match:
                return full_match.group(0)
            path_match = re.search(r"/tool-server/tool-jobs/[A-Za-z0-9._-]+", serialized)
            if path_match:
                return f"{{_tool_server_origin(tool_server_base_url)}}{{path_match.group(0)}}"
            return None

        async def _request_json(method, url, token):
            def _do_request():
                request = urllib.request.Request(
                    url,
                    method=method,
                    headers={{
                        "Authorization": f"Bearer {{token}}",
                        "Content-Type": "application/json",
                    }},
                )
                with urllib.request.urlopen(request, timeout=45) as response:
                    return json.loads(response.read().decode("utf-8"))

            return await asyncio.to_thread(_do_request)

        class Action:
            class Valves(BaseModel):
                tool_server_base_url: str = "{container_tool_server_base_url}"
                tool_server_token: str = "SET_OPENAPI_TOOL_SERVER_TOKEN"
                priority: int = 30

            def __init__(self):
                self.valves = self.Valves()

            async def action(self, body: dict, __event_emitter__=None, __event_call__=None, __id__=None):
                status_url = _extract_status_url(body, self.valves.tool_server_base_url)
                if not status_url:
                    return {{"content": "Не удалось определить `status_url` для deep-job."}}

                status_payload = await _request_json("GET", status_url, self.valves.tool_server_token)
                if status_payload.get("status") != "completed":
                    return {{
                        "content": (
                            f"Текущий статус deep-job: {{status_payload.get('status', 'unknown')}}\\n"
                            f"job_id: {{status_payload.get('job_id', 'unknown')}}\\n"
                            f"status_url: {{status_url}}"
                        )
                    }}

                result_payload = await _request_json("GET", f"{{status_url}}/result", self.valves.tool_server_token)
                return {{
                    "content": result_payload.get("assistant_message")
                    or json.dumps(result_payload, ensure_ascii=False, indent=2)
                }}
        """
    ).strip()


def _build_tool_job_cancel_action_code(*, container_tool_server_base_url: str) -> str:
    return dedent(
        f"""
        import asyncio
        import json
        import re
        import urllib.error
        import urllib.request
        from urllib.parse import urlsplit
        from pydantic import BaseModel

        def _tool_server_origin(tool_server_base_url):
            parsed = urlsplit(tool_server_base_url)
            return f"{{parsed.scheme}}://{{parsed.netloc}}"

        def _normalize_status_url(candidate, tool_server_base_url):
            if not candidate:
                return None
            candidate = str(candidate).strip()
            if not candidate:
                return None
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate
            if candidate.startswith("/"):
                return f"{{_tool_server_origin(tool_server_base_url)}}{{candidate}}"
            return f"{{_tool_server_origin(tool_server_base_url)}}/{{candidate.lstrip('/')}}"

        def _extract_tool_job_context(payload):
            if isinstance(payload, dict):
                if payload.get("status_url") or payload.get("job_id"):
                    return {{
                        "status_url": payload.get("status_url"),
                        "job_id": payload.get("job_id"),
                    }}
                tool_job = payload.get("tool_job")
                if isinstance(tool_job, dict):
                    nested = _extract_tool_job_context(tool_job)
                    if nested:
                        return nested
                for value in payload.values():
                    nested = _extract_tool_job_context(value)
                    if nested:
                        return nested
            elif isinstance(payload, list):
                for item in payload:
                    nested = _extract_tool_job_context(item)
                    if nested:
                        return nested
            return None

        def _extract_status_url(body, tool_server_base_url):
            context = _extract_tool_job_context(body)
            if context:
                status_url = _normalize_status_url(context.get("status_url"), tool_server_base_url)
                if status_url:
                    return status_url
                job_id = str(context.get("job_id") or "").strip()
                if job_id:
                    return f"{{_tool_server_origin(tool_server_base_url)}}/tool-server/tool-jobs/{{job_id}}"
            serialized = json.dumps(body, ensure_ascii=False)
            full_match = re.search(r"https?://[^\\s\\\"]+/tool-server/tool-jobs/[A-Za-z0-9._-]+", serialized)
            if full_match:
                return full_match.group(0)
            path_match = re.search(r"/tool-server/tool-jobs/[A-Za-z0-9._-]+", serialized)
            if path_match:
                return f"{{_tool_server_origin(tool_server_base_url)}}{{path_match.group(0)}}"
            return None

        async def _request_json(method, url, token):
            def _do_request():
                request = urllib.request.Request(
                    url,
                    method=method,
                    headers={{
                        "Authorization": f"Bearer {{token}}",
                        "Content-Type": "application/json",
                    }},
                )
                with urllib.request.urlopen(request, timeout=45) as response:
                    return json.loads(response.read().decode("utf-8"))

            return await asyncio.to_thread(_do_request)

        class Action:
            class Valves(BaseModel):
                tool_server_base_url: str = "{container_tool_server_base_url}"
                tool_server_token: str = "SET_OPENAPI_TOOL_SERVER_TOKEN"
                priority: int = 40

            def __init__(self):
                self.valves = self.Valves()

            async def action(self, body: dict, __event_emitter__=None, __event_call__=None, __id__=None):
                status_url = _extract_status_url(body, self.valves.tool_server_base_url)
                if not status_url:
                    return {{"content": "Не удалось определить `status_url` для отмены deep-job."}}

                if __event_call__:
                    decision = await __event_call__({{
                        "type": "confirm",
                        "data": {{
                            "title": "Отменить deep-job?",
                            "content": "Будет вызван backend cancel route для активной tool job.",
                        }},
                    }})
                    if decision is False:
                        return {{"content": "Отмена deep-job прервана пользователем."}}

                cancel_payload = await _request_json("POST", f"{{status_url}}/cancel", self.valves.tool_server_token)
                return {{
                    "content": (
                        f"Cancel request отправлен.\\n"
                        f"job_id: {{cancel_payload.get('job_id', 'unknown')}}\\n"
                        f"status: {{cancel_payload.get('status', 'unknown')}}"
                    )
                }}
        """
    ).strip()
