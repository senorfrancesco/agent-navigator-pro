from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent
from typing import Any, Dict, Iterable, Literal, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from orchestrator.tool_catalog import (
    ToolDefinition,
    ToolName,
    get_tool_definition,
    list_openwebui_deferred_tool_definitions,
    list_openwebui_enabled_tool_definitions,
)

BindingEntrypointType = Literal["direct_action", "prompt_shortcut", "followup_action"]
BindingResultMode = Literal["inline", "accepted_job", "rich_card"]
OPENWEBUI_DEFAULT_MODEL = "raw.qwen-14b-llm"
OPENWEBUI_DEFAULT_FUNCTION_CALLING = "native"
OPENWEBUI_DEFAULT_RAG_EMBEDDING_MODEL = "labse-embedding"
OPENWEBUI_DEFAULT_RAG_EMBEDDING_BASE_URL = "http://host.docker.internal:8090/v1"
OPENWEBUI_QDRANT_URI = "http://qdrant:6333"
OPENWEBUI_QDRANT_COLLECTION_PREFIX = "anp-openwebui"
BACKEND_QDRANT_COLLECTION_NAME_SOURCE = "backend_env:QDRANT_COLLECTION_NAME"


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


def _tool(tool_name: ToolName) -> ToolDefinition:
    return get_tool_definition(tool_name)


def _direct_binding_description(tool_name: ToolName) -> str:
    tool = _tool(tool_name)
    return f"{tool.summary} Использовать, когда: {tool.use_when}"


def _deferred_binding_description(tool_name: ToolName) -> str:
    tool = _tool(tool_name)
    return tool.availability_note or f"{tool.label} пока остаётся deferred."


def _prompt_binding_description(tool_name: ToolName) -> str:
    tool = _tool(tool_name)
    return f"Команда для явного вызова `{tool.name}`. Использовать, когда: {tool.use_when}"


def _tool_catalog_entries(definitions: Iterable[ToolDefinition]) -> list[dict[str, Any]]:
    return [definition.to_catalog_entry() for definition in definitions]


TOOL_BINDINGS: Tuple[ToolBinding, ...] = (
    ToolBinding(
        binding_id="equipment.fast.direct",
        label=_tool("analyze_equipment_fast").label,
        tool_name="analyze_equipment_fast",
        entrypoint_type="direct_action",
        result_mode="inline",
        default_args={"equipment_query": "{{chat_input}}"},
        description=_direct_binding_description("analyze_equipment_fast"),
    ),
    ToolBinding(
        binding_id="equipment.deep.direct",
        label=_tool("analyze_equipment_deep").label,
        tool_name="analyze_equipment_deep",
        entrypoint_type="direct_action",
        result_mode="accepted_job",
        default_args={"equipment_query": "{{chat_input}}", "job_mode": "force_async"},
        description=_direct_binding_description("analyze_equipment_deep"),
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
        description=_prompt_binding_description("analyze_equipment_fast"),
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
        description=_prompt_binding_description("analyze_equipment_deep"),
    ),
    ToolBinding(
        binding_id="document.ask.direct",
        label=_tool("ask_document").label,
        tool_name="ask_document",
        entrypoint_type="direct_action",
        result_mode="inline",
        enabled=False,
        requires_document_context=True,
        default_args={"question": "{{chat_input}}"},
        description=_deferred_binding_description("ask_document"),
    ),
    ToolBinding(
        binding_id="document.fast.direct",
        label=_tool("analyze_document_fast").label,
        tool_name="analyze_document_fast",
        entrypoint_type="direct_action",
        result_mode="inline",
        enabled=False,
        requires_document_context=True,
        default_args={"analysis_goal": "{{chat_input}}"},
        description=_deferred_binding_description("analyze_document_fast"),
    ),
    ToolBinding(
        binding_id="document.deep.direct",
        label=_tool("analyze_document_deep").label,
        tool_name="analyze_document_deep",
        entrypoint_type="direct_action",
        result_mode="accepted_job",
        enabled=False,
        requires_document_context=True,
        default_args={"analysis_goal": "{{chat_input}}", "job_mode": "force_async"},
        description=_deferred_binding_description("analyze_document_deep"),
    ),
    ToolBinding(
        binding_id="compare.fast.direct",
        label=_tool("compare_documents_fast").label,
        tool_name="compare_documents_fast",
        entrypoint_type="direct_action",
        result_mode="inline",
        enabled=False,
        requires_document_context=True,
        requires_min_documents=2,
        default_args={"comparison_goal": "{{chat_input}}"},
        description=_deferred_binding_description("compare_documents_fast"),
    ),
    ToolBinding(
        binding_id="compare.deep.direct",
        label=_tool("compare_documents_deep").label,
        tool_name="compare_documents_deep",
        entrypoint_type="direct_action",
        result_mode="accepted_job",
        enabled=False,
        requires_document_context=True,
        requires_min_documents=2,
        default_args={"comparison_goal": "{{chat_input}}", "job_mode": "force_async"},
        description=_deferred_binding_description("compare_documents_deep"),
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
    enabled_tools = _tool_catalog_entries(list_openwebui_enabled_tool_definitions())
    deferred_tools = _tool_catalog_entries(list_openwebui_deferred_tool_definitions())
    return {
        "toolServer": {
            "name": "llm-tools-platform OpenAPI Tool Server",
            "baseUrl": f"{normalized_base_url}/tool-server",
            "browserReachableBaseUrl": f"{normalized_base_url}/tool-server",
            "containerReachableBaseUrl": f"{container_base_url}/tool-server",
            "manualEnableRequired": False,
            "pickerVisible": False,
            "defaultBootstrapManaged": False,
        },
        "runtimeConfig": {
            "defaultModel": OPENWEBUI_DEFAULT_MODEL,
            "defaultFunctionCalling": OPENWEBUI_DEFAULT_FUNCTION_CALLING,
            "toolServerAuthSource": "backend_env:OPENAPI_TOOL_SERVER_TOKEN",
            "toolServerTokenPlaceholder": "SET_OPENAPI_TOOL_SERVER_TOKEN",
            "rag": {
                "vectorDb": "qdrant",
                "embeddingEngine": "openai",
                "embeddingModel": OPENWEBUI_DEFAULT_RAG_EMBEDDING_MODEL,
                "embeddingOpenAIBaseUrl": OPENWEBUI_DEFAULT_RAG_EMBEDDING_BASE_URL,
                "rerankingEngine": "",
            },
        },
        "knowledgeConfig": {
            "bootstrapMode": "manual_checklist",
            "nativeKnowledgeEnabled": True,
            "sessionFlow": "backend_owned_qdrant",
            "knowledgeFlow": "openwebui_native_qdrant",
            "embeddingModel": OPENWEBUI_DEFAULT_RAG_EMBEDDING_MODEL,
            "rerankingEnabled": False,
        },
        "qdrantConfig": {
            "provider": "qdrant",
            "uri": OPENWEBUI_QDRANT_URI,
            "collectionPrefix": OPENWEBUI_QDRANT_COLLECTION_PREFIX,
            "multitenancy": True,
            "backendCollectionNameSource": BACKEND_QDRANT_COLLECTION_NAME_SOURCE,
            "ownership": "shared_server_separate_namespaces",
        },
        "manualChecklist": {
            "sessionRag": [
                "Проверьте, что backend запущен с `KB_BACKEND=qdrant` и `QDRANT_COLLECTION_NAME=rag_chunks_v1`.",
                "Загрузите файл прямо в чат `Open WebUI`, а не в раздел `Knowledge`.",
                "Задайте следующий вопрос по тому же файлу и проверьте, что backend использует session-область `Qdrant`.",
            ],
            "knowledgeQdrant": [
                "Откройте `Admin Settings -> Documents` и включите `Qdrant` как внешнюю векторную базу.",
                "Укажите `QDRANT_URI`, префикс коллекций и multitenancy mode в runtime-конфиге `Open WebUI`.",
                "Настройте внешний embedding engine `openai` и модель `labse-embedding`, затем выполните `Reindex Knowledge Base`.",
            ],
            "verification": [
                "Проверьте, что session-файлы не появляются как native `Knowledge` objects автоматически.",
                "Проверьте, что native `Knowledge` создаёт отдельные коллекции с префиксом `anp-openwebui`.",
                "Убедитесь, что обычный chat upload и native `Knowledge` не смешивают backend и UI-owned collection policy.",
            ],
        },
        "preflightRequirements": {
            "requiredServices": ["agent-api", "open-webui", "qdrant", "ums"],
            "backendEnv": [
                "OPENAPI_TOOL_SERVER_TOKEN",
                "KB_BACKEND",
                "QDRANT_URL",
                "QDRANT_COLLECTION_NAME",
            ],
            "openWebUIRuntimeEnv": [
                "VECTOR_DB",
                "QDRANT_URI",
                "ENABLE_QDRANT_MULTITENANCY_MODE",
                "QDRANT_COLLECTION_PREFIX",
                "RAG_EMBEDDING_ENGINE",
                "RAG_OPENAI_API_BASE_URL",
                "RAG_EMBEDDING_MODEL",
            ],
            "manualOnly": [
                "native_openwebui_knowledge_connection",
                "knowledge_reindex",
            ],
        },
        "ownership": {
            "backendOwned": {
                "toolServerConnection": [],
                "workspaceToolFields": ["id", "name", "content", "meta.description", "meta.manifest.target_models"],
                "actionFunctionFields": ["id", "name", "content", "meta.description", "meta.manifest.target_models"],
                "promptFields": ["command", "content", "meta.binding_id"],
                "modelConfigFields": ["DEFAULT_MODELS", "DEFAULT_MODEL_PARAMS.function_calling"],
                "qdrantInfraFields": ["uri", "backendCollectionNameSource"],
            },
            "openWebUIOwned": {
                "toolServerConnection": [],
                "workspaceToolFields": [],
                "actionFunctionFields": ["is_active", "is_global"],
                "promptFields": ["name", "meta.description", "tags", "access_grants", "is_production"],
                "knowledgeFields": ["vectorDb", "collectionPrefix", "multitenancy", "embeddingEngine", "embeddingModel"],
            },
            "deferred": [
                "knowledge",
                "qdrant",
                "external_ingestion",
                "corpus_admin",
                "ask_document_role_decision",
            ],
        },
        "toolCatalog": {
            "enabled": enabled_tools,
            "deferred": deferred_tools,
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
        "actionFunctions": [],
        "importChecklist": [
            "1. Создайте только именованные tools `equipment_fast_tool` и `equipment_deep_tool` из export bundle как тонкие Python-обёртки без доменной логики в `Open WebUI`.",
            "2. Добавьте `Workspace Prompts` `/hw_fast` и `/hw_deep` из export bundle без изменения команд.",
            "3. В `Valves` каждого local tool вставьте актуальный `OPENAPI_TOOL_SERVER_TOKEN` из backend `.env`.",
            "4. Проверьте запуск инструмента долгого выполнения только через нативный путь `Open WebUI`: панель, автообновление и `Stop`.",
            "5. Не добавляйте `llm-tools-platform OpenAPI Tool Server` в список выбора чата по умолчанию; транспортный слой уже вызывается из wrappers.",
        ],
        "notes": [
            "Нативный long-running путь в `Open WebUI` остаётся единственной поддерживаемой пользовательской поверхностью для инструментов долгого выполнения.",
            "Раздел `Workspace > Tools` остаётся основным явным списком выбора только для включённых product tools; prompts являются дополнительным пользовательским слоем.",
            "Транспортный `llm-tools-platform OpenAPI Tool Server` остаётся отладочной и справочной точкой входа, а не chat-visible инструментом по умолчанию.",
            "Document и compare tools остаются deferred до завершения backend-owned upload/document binding и multi-document context.",
        ],
    }


def summarize_tool_binding_catalog() -> Dict[str, Any]:
    bindings = list_tool_bindings()
    enabled = [binding for binding in bindings if binding.enabled]
    enabled_tool_names = [definition.name for definition in list_openwebui_enabled_tool_definitions()]
    deferred_tool_names = [definition.name for definition in list_openwebui_deferred_tool_definitions()]
    return {
        "total": len(bindings),
        "enabled": len(enabled),
        "directActionCount": len([binding for binding in enabled if binding.entrypoint_type == "direct_action"]),
        "promptShortcutCount": len([binding for binding in enabled if binding.entrypoint_type == "prompt_shortcut"]),
        "enabledToolNames": enabled_tool_names,
        "deferredToolNames": deferred_tool_names,
        "enabledTools": _tool_catalog_entries(list_openwebui_enabled_tool_definitions()),
        "deferredTools": _tool_catalog_entries(list_openwebui_deferred_tool_definitions()),
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


def _build_openwebui_workspace_tools(*, container_tool_server_base_url: str) -> list[dict[str, Any]]:
    equipment_fast = _tool("analyze_equipment_fast")
    equipment_deep = _tool("analyze_equipment_deep")
    return [
        {
            "tool_id": "equipment_fast_tool",
            "title": equipment_fast.label,
            "description": f"{equipment_fast.summary} Использовать, когда: {equipment_fast.use_when}",
            "targetModels": ["raw.*"],
            "manualImportRequired": True,
            "pythonCode": _build_openwebui_workspace_tool_code(
                container_tool_server_base_url=container_tool_server_base_url,
                tool_name="analyze_equipment_fast",
                method_name="analyze_equipment_fast",
                action_label=equipment_fast.label,
                result_mode="inline",
                priority=10,
            ),
        },
        {
            "tool_id": "equipment_deep_tool",
            "title": equipment_deep.label,
            "description": f"{equipment_deep.summary} Использовать, когда: {equipment_deep.use_when}",
            "targetModels": ["raw.*"],
            "manualImportRequired": True,
            "pythonCode": _build_openwebui_workspace_tool_code(
                container_tool_server_base_url=container_tool_server_base_url,
                tool_name="analyze_equipment_deep",
                method_name="analyze_equipment_deep",
                action_label=equipment_deep.label,
                result_mode="accepted_job",
                priority=20,
            ),
        },
    ]


def _build_openwebui_workspace_tool_code(
    *,
    container_tool_server_base_url: str,
    tool_name: ToolName,
    method_name: str,
    action_label: str,
    result_mode: BindingResultMode,
    priority: int,
) -> str:
    is_async = result_mode == "accepted_job"
    payload_line = '"job_mode": "force_async",' if is_async else ""
    tool = _tool(tool_name)
    document_tool_name: ToolName = "analyze_document_deep" if is_async else "analyze_document_fast"
    document_action_label = _tool(document_tool_name).label
    document_default_prompt = (
        "Сделай глубокий анализ загруженного документа."
        if is_async
        else "Сделай быстрый анализ загруженного документа."
    )
    pair_default_prompt = (
        "Сделай глубокое сравнение загруженных документов."
        if is_async
        else "Сравни загруженные документы по ключевым различиям."
    )
    if not is_async:
        return dedent(
            f'''
            """
            title: {action_label}
            author: llm-tools-platform
            version: 1.0.0
            requirements:
            """

            import asyncio
            import json
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

            def _load_chat_messages(chat_id):
                if not chat_id:
                    return {{}}
                try:
                    from open_webui.models.chats import Chats
                except Exception:
                    return {{}}
                try:
                    chat_record = Chats.get_chat_by_id(chat_id)
                except Exception:
                    return {{}}
                payload = getattr(chat_record, "chat", None)
                if not isinstance(payload, dict):
                    return {{}}
                history = payload.get("history")
                if not isinstance(history, dict):
                    return {{}}
                messages = history.get("messages")
                if not isinstance(messages, dict):
                    return {{}}
                return messages

            def _select_latest_user_message(messages):
                candidates = []
                for message in (messages or {{}}).values():
                    if not isinstance(message, dict):
                        continue
                    if str(message.get("role") or "").strip() != "user":
                        continue
                    timestamp = message.get("timestamp")
                    try:
                        order_key = int(timestamp)
                    except Exception:
                        order_key = -1
                    candidates.append((order_key, message))
                if not candidates:
                    return {{}}
                candidates.sort(key=lambda item: item[0])
                return candidates[-1][1]

            def _iter_tool_sources(messages):
                for message in (messages or {{}}).values():
                    if not isinstance(message, dict):
                        continue
                    sources = message.get("sources")
                    if not isinstance(sources, list):
                        continue
                    for item in sources:
                        if isinstance(item, dict):
                            yield item

            def _collect_document_context(chat_id):
                messages = _load_chat_messages(chat_id)
                user_message = _select_latest_user_message(messages)
                session_docs = {{}}
                attachments_meta = []
                document_refs = []
                seen_names = set()

                for file_entry in user_message.get("files") or []:
                    if not isinstance(file_entry, dict):
                        continue
                    file_info = file_entry.get("file") or {{}}
                    if not isinstance(file_info, dict):
                        file_info = {{}}
                    name = str(
                        file_entry.get("name")
                        or file_info.get("filename")
                        or (file_info.get("meta") or {{}}).get("name")
                        or ""
                    ).strip()
                    path = str(file_info.get("path") or "").strip()
                    file_id = str(file_entry.get("id") or file_info.get("id") or "").strip()
                    if not name or not path or name in seen_names:
                        continue
                    seen_names.add(name)
                    session_docs[name] = {{
                        "path": path,
                        "text": "",
                        "document_id": file_id or name,
                        "display_name": name,
                        "source_origin": "open_webui_workspace_tool",
                    }}
                    attachments_meta.append({{
                        "name": name,
                        "path": path,
                        "text": "",
                    }})
                    document_ref = {{"label": name}}
                    if file_id:
                        document_ref["file_id"] = file_id
                    document_ref["file_path"] = path
                    document_refs.append(document_ref)

                source_text_by_name = {{}}
                source_text_by_id = {{}}
                for item in _iter_tool_sources(messages):
                    source = item.get("source") or {{}}
                    if not isinstance(source, dict) or source.get("type") != "file":
                        continue
                    file_info = source.get("file") or {{}}
                    if not isinstance(file_info, dict):
                        file_info = {{}}
                    name = str(
                        source.get("name")
                        or file_info.get("filename")
                        or (file_info.get("meta") or {{}}).get("name")
                        or ""
                    ).strip()
                    file_id = str(source.get("id") or file_info.get("id") or "").strip()
                    chunks = item.get("document") or []
                    if isinstance(chunks, list):
                        text = "\\n\\n".join(
                            str(chunk).strip()
                            for chunk in chunks
                            if str(chunk).strip()
                        ).strip()
                    else:
                        text = _content_to_text(chunks)
                    if not text:
                        continue
                    if name:
                        source_text_by_name[name] = text
                    if file_id:
                        source_text_by_id[file_id] = text

                for document_ref in document_refs:
                    name = str(document_ref.get("label") or "").strip()
                    file_id = str(document_ref.get("file_id") or "").strip()
                    text = source_text_by_id.get(file_id) or source_text_by_name.get(name) or ""
                    if not name or name not in session_docs:
                        continue
                    session_docs[name]["text"] = text

                for item in attachments_meta:
                    name = str(item.get("name") or "").strip()
                    if name and name in session_docs:
                        item["text"] = str((session_docs.get(name) or {{}}).get("text") or "")

                return {{
                    "messages": messages,
                    "document_refs": document_refs,
                    "user_inputs": {{
                        "session_docs": session_docs,
                        "attachments_meta": attachments_meta,
                    }},
                    "latest_user_text": _content_to_text(user_message.get("content")),
                }}

            def _select_tool_request(query, document_context):
                prompt = str(query or "").strip() or str(document_context.get("latest_user_text") or "").strip()
                document_refs = document_context.get("document_refs") or []
                user_inputs = document_context.get("user_inputs") or {{}}
                doc_count = len(document_refs)

                if doc_count == 1:
                    return {{
                        "tool_name": "{document_tool_name}",
                        "payload": {{
                            "analysis_goal": prompt or "{document_default_prompt}",
                            "document_refs": document_refs,
                            "user_inputs": user_inputs,
                            {payload_line}
                        }},
                    }}

                if doc_count >= 2:
                    return {{
                        "tool_name": "{tool_name}",
                        "payload": {{
                            "equipment_query": prompt or "{pair_default_prompt}",
                            "document_refs": document_refs,
                            "user_inputs": user_inputs,
                            {payload_line}
                        }},
                    }}

                if not prompt:
                    return {{
                        "error": "Не удалось определить пользовательский запрос для `{action_label}`."
                    }}

                return {{
                    "tool_name": "{tool_name}",
                    "payload": {{
                        "equipment_query": prompt,
                        {payload_line}
                    }},
                }}

            class Tools:
                class Valves(BaseModel):
                    tool_server_base_url: str = "{container_tool_server_base_url}"
                    tool_server_token: str = "SET_OPENAPI_TOOL_SERVER_TOKEN"
                    priority: int = {priority}

                def __init__(self):
                    self.valves = self.Valves()

                async def {method_name}(self, query: str, __chat_id__=None) -> str:
                    """
                    {tool.summary}

                    Использовать, когда: {tool.use_when}

                    :param query: {tool.input_summary}
                    :return: {tool.output_summary}
                    """
                    routed = _select_tool_request(query, _collect_document_context(__chat_id__))
                    if routed.get("error"):
                        return routed["error"]
                    target_tool_name = str(routed.get("tool_name") or "{tool_name}")
                    payload = dict(routed.get("payload") or {{}})
                    payload = {{key: value for key, value in payload.items() if value is not None and value != ""}}
                    response = await _request_json(
                        "POST",
                        f"{{self.valves.tool_server_base_url}}/tools/{{target_tool_name}}",
                        self.valves.tool_server_token,
                        payload,
                    )
                    return response.get("assistant_message") or json.dumps(response, ensure_ascii=False, indent=2)
            '''
        ).strip()

    template = dedent(
        '''
        """
        title: __ACTION_LABEL__
        author: llm-tools-platform
        version: 1.0.0
        requirements:
        """

        import asyncio
        import json
        import time
        import urllib.request
        from pydantic import BaseModel
        from uuid import uuid4

        AUTO_POLL_INTERVAL_SECONDS = 2
        AUTO_POLL_MAX_SECONDS = 900
        AUTO_POLL_MAX_ERRORS = 3
        MESSAGE_SETTLE_TIMEOUT_SECONDS = 5
        MESSAGE_SETTLE_POLL_SECONDS = 0.1
        TERMINAL_REAPPLY_DELAY_SECONDS = 0.5
        TERMINAL_REAPPLY_ATTEMPTS = 5

        async def _request_json(method, url, token, payload=None):
            def _do_request():
                data = None if payload is None else json.dumps(payload).encode("utf-8")
                request = urllib.request.Request(
                    url,
                    data=data,
                    method=method,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(request, timeout=45) as response:
                    return json.loads(response.read().decode("utf-8"))

            return await asyncio.to_thread(_do_request)

        def _tool_server_origin(tool_server_base_url):
            return str(tool_server_base_url).split("/tool-server", 1)[0].rstrip("/")

        def _absolute_status_url(tool_server_base_url, candidate):
            candidate = str(candidate or "").strip()
            if not candidate:
                return candidate
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate
            if candidate.startswith("/"):
                return f"{_tool_server_origin(tool_server_base_url)}{candidate}"
            return f"{_tool_server_origin(tool_server_base_url)}/{candidate.lstrip('/')}"

        def _load_message(chat_id, message_id):
            try:
                from open_webui.models.chats import Chats
            except Exception:
                return {}
            try:
                return Chats.get_message_by_id_and_message_id(chat_id, message_id) or {}
            except Exception:
                return {}

        def _load_chat_messages(chat_id):
            try:
                from open_webui.models.chats import Chats
            except Exception:
                return {}
            try:
                chat_record = Chats.get_chat_by_id(chat_id)
            except Exception:
                return {}

            payload = getattr(chat_record, "chat", None)
            if not isinstance(payload, dict):
                return {}
            history = payload.get("history")
            if not isinstance(history, dict):
                return {}
            messages = history.get("messages")
            if not isinstance(messages, dict):
                return {}
            return messages

        def _persist_message(chat_id, message_id, patch):
            try:
                from open_webui.models.chats import Chats
            except Exception:
                return None
            try:
                return Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, patch)
            except Exception:
                return None

        def _append_status(chat_id, message_id, status):
            try:
                from open_webui.models.chats import Chats
            except Exception:
                return None
            try:
                return Chats.add_message_status_to_chat_by_id_and_message_id(chat_id, message_id, status)
            except Exception:
                return None

        def _build_tool_job(job_id, status_url, status, tool_name):
            return {
                "job_id": job_id,
                "status_url": status_url,
                "tool_name": tool_name,
                "status": status,
            }

        def _status_payload_patch(status_payload):
            if not isinstance(status_payload, dict):
                return {}
            patch = {}
            status_text = str(status_payload.get("status_text") or "").strip()
            if status_text:
                patch["status_text"] = status_text
            status_history = status_payload.get("status_history")
            if isinstance(status_history, list):
                patch["status_history"] = status_history
            progress = status_payload.get("progress")
            if progress is not None:
                patch["progress"] = progress
            embeds = status_payload.get("embeds")
            if embeds is not None:
                patch["embeds"] = embeds
            sources = status_payload.get("sources")
            if sources is not None:
                patch["sources"] = sources
            artifacts = status_payload.get("artifacts")
            if artifacts is not None:
                patch["artifacts"] = artifacts
            result_preview = str(status_payload.get("result_preview") or "").strip()
            if result_preview:
                patch["result_preview"] = result_preview
            return patch

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

        def _status_url_candidates(status_url):
            normalized = str(status_url or "").strip()
            if not normalized:
                return []
            candidates = [normalized]
            if "/tool-server/" in normalized:
                suffix = normalized.split("/tool-server", 1)[1]
                candidates.append(f"/tool-server{suffix}")
            seen = set()
            unique = []
            for candidate in candidates:
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    unique.append(candidate)
            return unique

        def _message_matches_job(message, *, job_id, status_url):
            if not isinstance(message, dict):
                return False
            if str(message.get("tool_job_result_for") or "").strip():
                return False
            if str(message.get("role") or "").strip() not in {"", "assistant"}:
                return False
            if str(message.get("job_id") or "").strip() == job_id:
                return True

            tool_job = message.get("tool_job") or {}
            if str(tool_job.get("job_id") or "").strip() == job_id:
                return True

            status_candidates = _status_url_candidates(status_url)
            content = str(message.get("content") or "")
            if job_id and f"job_id: {job_id}" in content:
                return True
            if any(candidate and candidate in content for candidate in status_candidates):
                return True

            tool_job_status_url = str(tool_job.get("status_url") or "").strip()
            if tool_job_status_url and tool_job_status_url in status_candidates:
                return True

            for status_entry in message.get("statusHistory") or []:
                if not isinstance(status_entry, dict):
                    continue
                if str(status_entry.get("job_id") or "").strip() == job_id:
                    return True
                status_entry_url = str(status_entry.get("status_url") or "").strip()
                if status_entry_url and status_entry_url in status_candidates:
                    return True

            return False

        def _resolve_job_message_id(chat_id, fallback_message_id, job_id, status_url):
            fallback_id = str(fallback_message_id or "").strip()
            messages = _load_chat_messages(chat_id)
            if not messages:
                return fallback_id

            best_id = fallback_id
            best_score = -1
            status_candidates = _status_url_candidates(status_url)

            for candidate_id, message in messages.items():
                if not _message_matches_job(message, job_id=job_id, status_url=status_url):
                    continue

                score = 0
                content = str(message.get("content") or "")
                if str(message.get("role") or "").strip() == "assistant":
                    score += 5
                if str(message.get("job_status") or "").strip() in {"accepted", "queued", "running", "cancelling"}:
                    score += 3
                if message.get("output") is not None:
                    score += 1
                if job_id and f"job_id: {job_id}" in content:
                    score += 4
                if any(candidate and f"status_url: {candidate}" in content for candidate in status_candidates):
                    score += 4
                if str(candidate_id) == fallback_id:
                    score += 1

                if score >= best_score:
                    best_score = score
                    best_id = str(candidate_id)

            return best_id or fallback_id

        def _select_latest_user_message(messages):
            candidates = []
            for message in (messages or {}).values():
                if not isinstance(message, dict):
                    continue
                if str(message.get("role") or "").strip() != "user":
                    continue
                timestamp = message.get("timestamp")
                try:
                    order_key = int(timestamp)
                except Exception:
                    order_key = -1
                candidates.append((order_key, message))
            if not candidates:
                return {}
            candidates.sort(key=lambda item: item[0])
            return candidates[-1][1]

        def _iter_tool_sources(messages):
            for message in (messages or {}).values():
                if not isinstance(message, dict):
                    continue
                sources = message.get("sources")
                if not isinstance(sources, list):
                    continue
                for item in sources:
                    if isinstance(item, dict):
                        yield item

        def _collect_document_context(chat_id):
            messages = _load_chat_messages(chat_id)
            user_message = _select_latest_user_message(messages)
            session_docs = {}
            attachments_meta = []
            document_refs = []
            seen_names = set()

            for file_entry in user_message.get("files") or []:
                if not isinstance(file_entry, dict):
                    continue
                file_info = file_entry.get("file") or {}
                if not isinstance(file_info, dict):
                    file_info = {}
                name = str(
                    file_entry.get("name")
                    or file_info.get("filename")
                    or (file_info.get("meta") or {}).get("name")
                    or ""
                ).strip()
                path = str(file_info.get("path") or "").strip()
                file_id = str(file_entry.get("id") or file_info.get("id") or "").strip()
                if not name or not path or name in seen_names:
                    continue
                seen_names.add(name)
                session_docs[name] = {
                    "path": path,
                    "text": "",
                    "document_id": file_id or name,
                    "display_name": name,
                    "source_origin": "open_webui_workspace_tool",
                }
                attachments_meta.append(
                    {
                        "name": name,
                        "path": path,
                        "text": "",
                    }
                )
                document_ref = {"label": name}
                if file_id:
                    document_ref["file_id"] = file_id
                document_ref["file_path"] = path
                document_refs.append(document_ref)

            source_text_by_name = {}
            source_text_by_id = {}
            for item in _iter_tool_sources(messages):
                source = item.get("source") or {}
                if not isinstance(source, dict) or source.get("type") != "file":
                    continue
                file_info = source.get("file") or {}
                if not isinstance(file_info, dict):
                    file_info = {}
                name = str(
                    source.get("name")
                    or file_info.get("filename")
                    or (file_info.get("meta") or {}).get("name")
                    or ""
                ).strip()
                file_id = str(source.get("id") or file_info.get("id") or "").strip()
                chunks = item.get("document") or []
                if isinstance(chunks, list):
                    text = "\\n\\n".join(
                        str(chunk).strip()
                        for chunk in chunks
                        if str(chunk).strip()
                    ).strip()
                else:
                    text = _content_to_text(chunks)
                if not text:
                    continue
                if name:
                    source_text_by_name[name] = text
                if file_id:
                    source_text_by_id[file_id] = text

            for document_ref in document_refs:
                name = str(document_ref.get("label") or "").strip()
                file_id = str(document_ref.get("file_id") or "").strip()
                text = source_text_by_id.get(file_id) or source_text_by_name.get(name) or ""
                if not name or name not in session_docs:
                    continue
                session_docs[name]["text"] = text

            for item in attachments_meta:
                name = str(item.get("name") or "").strip()
                if name and name in session_docs:
                    item["text"] = str((session_docs.get(name) or {}).get("text") or "")

            return {
                "document_refs": document_refs,
                "user_inputs": {
                    "session_docs": session_docs,
                    "attachments_meta": attachments_meta,
                },
                "latest_user_text": _content_to_text(user_message.get("content")),
            }

        def _select_tool_request(query, chat_id):
            document_context = _collect_document_context(chat_id)
            document_refs = document_context.get("document_refs") or []
            user_inputs = document_context.get("user_inputs") or {}
            prompt = str(query or "").strip() or str(document_context.get("latest_user_text") or "").strip()

            if len(document_refs) == 1:
                return {
                    "tool_name": "__DOCUMENT_TOOL_NAME__",
                    "action_label": "__DOCUMENT_ACTION_LABEL__",
                    "payload": {
                        "analysis_goal": prompt or "__DOCUMENT_DEFAULT_PROMPT__",
                        "document_refs": document_refs,
                        "user_inputs": user_inputs,
                        "job_mode": "force_async",
                    },
                }

            if len(document_refs) >= 2:
                return {
                    "tool_name": "__TOOL_NAME__",
                    "action_label": "__ACTION_LABEL__",
                    "payload": {
                        "equipment_query": prompt or "__PAIR_DEFAULT_PROMPT__",
                        "document_refs": document_refs,
                        "user_inputs": user_inputs,
                        "job_mode": "force_async",
                    },
                }

            if not prompt:
                return {
                    "error": "Не удалось определить пользовательский запрос для `__ACTION_LABEL__`."
                }

            return {
                "tool_name": "__TOOL_NAME__",
                "action_label": "__ACTION_LABEL__",
                "payload": {
                    "equipment_query": prompt,
                    "job_mode": "force_async",
                },
            }

        def _resolve_terminal_content(existing, *, fallback_content):
            current_content = str((existing or {}).get("content") or "").strip()
            if (
                current_content
                and current_content not in {"Завершено — результат добавлен ниже.", "deep-job отменён."}
                and not current_content.startswith("deep-job завершён со статусом")
            ):
                return current_content

            for status_entry in existing.get("statusHistory") or []:
                if not isinstance(status_entry, dict):
                    continue
                description = str(status_entry.get("description") or "").strip()
                if description and "job_id:" in description and "status_url:" in description:
                    return description

            return fallback_content

        def _persist_terminal_message(
            chat_id,
            message_id,
            *,
            content,
            job_id,
            status_url,
            status,
            tool_name,
            result_message_id=None,
            actions_disabled=True,
            status_payload=None,
        ):
            existing = _load_message(chat_id, message_id)
            children_ids = list(existing.get("childrenIds") or [])
            persisted_content = (
                _resolve_terminal_content(existing, fallback_content=content)
                if status in {"completed", "failed", "cancelled"}
                else content
            )
            patch = {
                "id": message_id,
                "role": existing.get("role", "assistant"),
                "content": persisted_content,
                "done": True,
                "childrenIds": children_ids,
                "tool_job": _build_tool_job(job_id, status_url, status, tool_name),
                "job_status": status,
                "actions_disabled": actions_disabled,
            }
            patch.update(_status_payload_patch(status_payload))
            if result_message_id:
                patch["result_message_id"] = result_message_id
            _persist_message(chat_id, message_id, patch)
            return patch

        def _create_result_message(chat_id, accepted_message_id, *, model_name, job_id, result_payload):
            accepted = _load_message(chat_id, accepted_message_id)
            existing_result_id = str(accepted.get("result_message_id") or "").strip()
            if existing_result_id:
                return existing_result_id

            result_message_id = str(uuid4())
            result_message = {
                "id": result_message_id,
                "parentId": accepted_message_id,
                "childrenIds": [],
                "role": "assistant",
                "content": result_payload.get("assistant_message") or json.dumps(result_payload, ensure_ascii=False, indent=2),
                "model": model_name or accepted.get("model") or "__DEFAULT_MODEL__",
                "timestamp": int(time.time()),
                "done": True,
                "job_id": job_id,
                "tool_job_result_for": accepted_message_id,
            }
            if result_payload.get("sources") is not None:
                result_message["sources"] = result_payload.get("sources")
            if result_payload.get("embeds") is not None:
                result_message["embeds"] = result_payload.get("embeds")
            if result_payload.get("output") is not None:
                result_message["output"] = result_payload.get("output")
            if result_payload.get("files") is not None:
                result_message["files"] = result_payload.get("files")

            children_ids = list(accepted.get("childrenIds") or [])
            if result_message_id not in children_ids:
                children_ids.append(result_message_id)

            _persist_message(
                chat_id,
                accepted_message_id,
                {
                    "id": accepted_message_id,
                    "childrenIds": children_ids,
                    "result_message_id": result_message_id,
                },
            )
            _persist_message(chat_id, result_message_id, result_message)
            return result_message_id

        async def _record_terminal_delivery(status_url, token, result_message_id):
            if not status_url or not result_message_id:
                return
            try:
                await _request_json(
                    "POST",
                    f"{status_url}/delivery",
                    token,
                    {"result_message_id": result_message_id},
                )
            except Exception:
                return

        async def _emit_custom_event(__event_emitter__, event_type, data):
            if __event_emitter__ is None:
                return
            await __event_emitter__({"type": event_type, "data": data})

        def _poller_registry(app_state):
            registry = getattr(app_state, "llm_tools_platform_deep_job_pollers", None)
            if not isinstance(registry, dict):
                registry = {}
                setattr(app_state, "llm_tools_platform_deep_job_pollers", registry)
            return registry

        def _poller_key(chat_id, message_id, job_id):
            return f"{chat_id}:{message_id}:{job_id}"

        async def _wait_for_message_settle(chat_id, message_id):
            deadline = time.time() + MESSAGE_SETTLE_TIMEOUT_SECONDS
            last_seen = {}
            while time.time() < deadline:
                current = _load_message(chat_id, message_id) or {}
                if current:
                    last_seen = current
                    if current.get("done") and current.get("output") is not None:
                        return current
                await asyncio.sleep(MESSAGE_SETTLE_POLL_SECONDS)
            return last_seen

        async def _reapply_terminal_branch(
            chat_id,
            message_id,
            *,
            content,
            job_id,
            status_url,
            status,
            tool_name,
            result_message_id=None,
            actions_disabled=True,
        ):
            for _ in range(max(int(TERMINAL_REAPPLY_ATTEMPTS), 1)):
                await asyncio.sleep(TERMINAL_REAPPLY_DELAY_SECONDS)
                _persist_terminal_message(
                    chat_id,
                    message_id,
                        content=content,
                        job_id=job_id,
                        status_url=status_url,
                        status=status,
                        tool_name=tool_name,
                        result_message_id=result_message_id,
                        actions_disabled=actions_disabled,
                    )
                if result_message_id:
                    _persist_message(chat_id, result_message_id, {"id": result_message_id})

        async def _run_auto_poll(
            *,
            app_state,
            chat_id,
            message_id,
            model_name,
            job_id,
            status_url,
            tool_name,
            token,
            __event_emitter__,
        ):
            registry = _poller_registry(app_state)
            key = _poller_key(chat_id, message_id, job_id)
            deadline = time.time() + AUTO_POLL_MAX_SECONDS
            consecutive_errors = 0
            await asyncio.sleep(AUTO_POLL_INTERVAL_SECONDS)

            try:
                while time.time() < deadline:
                    entry = registry.get(key) or {}
                    if entry.get("stop_requested"):
                        return
                    target_message_id = _resolve_job_message_id(chat_id, message_id, job_id, status_url)
                    entry["resolved_message_id"] = target_message_id
                    registry[key] = entry

                    try:
                        status_payload = await _request_json("GET", status_url, token)
                    except Exception:
                        consecutive_errors += 1
                        if consecutive_errors >= AUTO_POLL_MAX_ERRORS:
                            _persist_terminal_message(
                                chat_id,
                                target_message_id,
                                content="Автообновление остановлено, используйте refresh.",
                                job_id=job_id,
                                status_url=status_url,
                                status=str(entry.get("job_status") or "accepted"),
                                tool_name=str(entry.get("tool_name") or tool_name or "__TOOL_NAME__"),
                                result_message_id=entry.get("result_message_id"),
                                actions_disabled=False,
                            )
                            await _emit_custom_event(
                                __event_emitter__,
                                "replace",
                                {"content": "Автообновление остановлено, используйте refresh."},
                            )
                            return
                        await asyncio.sleep(AUTO_POLL_INTERVAL_SECONDS)
                        continue

                    consecutive_errors = 0
                    job_status = str(status_payload.get("status") or "unknown")
                    resolved_tool_name = str(entry.get("tool_name") or tool_name or "__TOOL_NAME__")
                    current_message = _load_message(chat_id, target_message_id)
                    if current_message:
                        current_patch = {
                            "id": target_message_id,
                            "tool_job": _build_tool_job(job_id, status_url, job_status, resolved_tool_name),
                            "job_status": job_status,
                            "actions_disabled": job_status in {"completed", "failed", "cancelled"},
                        }
                        current_patch.update(_status_payload_patch(status_payload))
                        _persist_message(chat_id, target_message_id, current_patch)

                    if job_status in {"accepted", "queued", "running", "cancelling", "unknown"}:
                        current_status = str(entry.get("job_status") or "accepted")
                        if job_status != current_status:
                            entry["job_status"] = job_status
                            registry[key] = entry
                            _append_status(
                                chat_id,
                                target_message_id,
                                {
                                    "description": f"Статус deep-job изменился: {job_status}",
                                    "status": job_status,
                                    "job_id": job_id,
                                    "status_url": status_url,
                                    "tool_name": resolved_tool_name,
                                },
                            )
                        await asyncio.sleep(AUTO_POLL_INTERVAL_SECONDS)
                        continue

                    if job_status == "completed":
                        await _wait_for_message_settle(chat_id, target_message_id)
                        result_payload = await _request_json("GET", f"{status_url}/result", token)
                        result_message_id = _create_result_message(
                            chat_id,
                            target_message_id,
                            model_name=model_name,
                            job_id=job_id,
                            result_payload=result_payload,
                        )
                        await _record_terminal_delivery(status_url, token, result_message_id)
                        entry["result_message_id"] = result_message_id
                        entry["job_status"] = "completed"
                        registry[key] = entry
                        terminal_patch = _persist_terminal_message(
                            chat_id,
                            target_message_id,
                            content="Завершено — результат добавлен ниже.",
                            job_id=job_id,
                            status_url=status_url,
                            status="completed",
                            tool_name=resolved_tool_name,
                            result_message_id=result_message_id,
                            actions_disabled=True,
                            status_payload=status_payload,
                        )
                        await _reapply_terminal_branch(
                            chat_id,
                            target_message_id,
                            content=terminal_patch["content"],
                            job_id=job_id,
                            status_url=status_url,
                            status="completed",
                            tool_name=resolved_tool_name,
                            result_message_id=result_message_id,
                            actions_disabled=True,
                        )
                        await _emit_custom_event(
                            __event_emitter__,
                            "replace",
                            {"content": terminal_patch["content"]},
                        )
                        await _emit_custom_event(
                            __event_emitter__,
                            "chat:message:new",
                            {
                                "message_id": result_message_id,
                                "parent_message_id": target_message_id,
                                "job_id": job_id,
                                "reload": True,
                            },
                        )
                        await _emit_custom_event(
                            __event_emitter__,
                            "chat:message:meta",
                            {
                                "job_status": "completed",
                                "job_id": job_id,
                                "status_url": status_url,
                                "result_message_id": result_message_id,
                                "actions_disabled": True,
                                "reload": True,
                            },
                        )
                        return

                    error_summary = str(status_payload.get("error_summary") or "").strip()
                    terminal_content = (
                        f"deep-job завершён со статусом {job_status}."
                        if job_status != "cancelled"
                        else "deep-job отменён."
                    )
                    if error_summary:
                        terminal_content = terminal_content + f"\\nerror: {error_summary}"
                    try:
                        result_payload = await _request_json("GET", f"{status_url}/result", token)
                    except Exception:
                        result_payload = {"assistant_message": terminal_content}
                    terminal_content = str(result_payload.get("assistant_message") or "").strip() or terminal_content
                    await _wait_for_message_settle(chat_id, target_message_id)
                    result_message_id = _create_result_message(
                        chat_id,
                        target_message_id,
                        model_name=model_name,
                        job_id=job_id,
                        result_payload=result_payload,
                    )
                    await _record_terminal_delivery(status_url, token, result_message_id)
                    entry["result_message_id"] = result_message_id
                    entry["job_status"] = job_status
                    registry[key] = entry
                    terminal_patch = _persist_terminal_message(
                        chat_id,
                        target_message_id,
                        content=terminal_content,
                        job_id=job_id,
                        status_url=status_url,
                        status=job_status,
                        tool_name=resolved_tool_name,
                        result_message_id=result_message_id,
                        actions_disabled=True,
                        status_payload=status_payload,
                    )
                    await _reapply_terminal_branch(
                        chat_id,
                        target_message_id,
                        content=terminal_patch["content"],
                        job_id=job_id,
                        status_url=status_url,
                        status=job_status,
                        tool_name=resolved_tool_name,
                        result_message_id=result_message_id,
                        actions_disabled=True,
                    )
                    await _emit_custom_event(__event_emitter__, "replace", {"content": terminal_patch["content"]})
                    await _emit_custom_event(
                        __event_emitter__,
                        "chat:message:new",
                        {
                            "message_id": result_message_id,
                            "parent_message_id": target_message_id,
                            "job_id": job_id,
                            "reload": True,
                        },
                    )
                    await _emit_custom_event(
                        __event_emitter__,
                        "chat:message:meta",
                        {
                            "job_status": job_status,
                            "job_id": job_id,
                            "status_url": status_url,
                            "result_message_id": result_message_id,
                            "actions_disabled": True,
                            "reload": True,
                        },
                    )
                    return

                _persist_terminal_message(
                    chat_id,
                    entry.get("resolved_message_id") or message_id,
                    content="Автообновление остановлено, используйте refresh.",
                    job_id=job_id,
                    status_url=status_url,
                    status="accepted",
                    tool_name=str(entry.get("tool_name") or tool_name or "__TOOL_NAME__"),
                    actions_disabled=False,
                )
                await _emit_custom_event(
                    __event_emitter__,
                    "replace",
                    {"content": "Автообновление остановлено, используйте refresh."},
                )
                await _emit_custom_event(
                    __event_emitter__,
                    "chat:message:meta",
                    {
                        "job_status": "accepted",
                        "job_id": job_id,
                        "status_url": status_url,
                        "result_message_id": None,
                        "actions_disabled": False,
                        "reload": True,
                    },
                )
            finally:
                registry.pop(key, None)

        class Tools:
            class Valves(BaseModel):
                tool_server_base_url: str = "__TOOL_SERVER_BASE_URL__"
                tool_server_token: str = "SET_OPENAPI_TOOL_SERVER_TOKEN"
                priority: int = __PRIORITY__

            def __init__(self):
                self.valves = self.Valves()

            async def __METHOD_NAME__(
                self,
                query: str,
                __request__=None,
                __event_emitter__=None,
                __chat_id__=None,
                __message_id__=None,
                __model__=None,
            ) -> str:
                """
                __TOOL_SUMMARY__

                Использовать, когда: __TOOL_USE_WHEN__

                :param query: __TOOL_INPUT_SUMMARY__
                :return: __TOOL_OUTPUT_SUMMARY__
                """
                routed = _select_tool_request(query, __chat_id__)
                if routed.get("error"):
                    return routed["error"]
                target_tool_name = str(routed.get("tool_name") or "__TOOL_NAME__")
                target_action_label = str(routed.get("action_label") or "__ACTION_LABEL__")
                payload = dict(routed.get("payload") or {})
                payload = {key: value for key, value in payload.items() if value is not None and value != ""}
                try:
                    response = await _request_json(
                        "POST",
                        f"{self.valves.tool_server_base_url}/tools/{target_tool_name}",
                        self.valves.tool_server_token,
                        payload,
                    )
                except Exception as exc:
                    return (
                        "Не удалось запустить deep-job.\\n"
                        f"error: {exc}"
                    )

                if response.get("status") == "accepted":
                    status_url = str(response.get("status_url", ""))
                    job_id = str(response.get("job_id", "unknown"))
                    if not status_url or not job_id or job_id == "unknown":
                        return (
                            "Не удалось запустить deep-job.\\n"
                            "Инструмент не вернул подтверждение запуска (`job_id` и `status_url`)."
                        )
                    if __request__ is not None and __chat_id__ and __message_id__:
                        normalized_status_url = _absolute_status_url(self.valves.tool_server_base_url, status_url)
                        registry = _poller_registry(__request__.app.state)
                        key = _poller_key(str(__chat_id__), str(__message_id__), job_id)
                        existing = registry.get(key)
                        existing_task = existing.get("task") if isinstance(existing, dict) else None
                        if existing_task is None or existing_task.done():
                            model_name = getattr(__model__, "id", None) or getattr(__model__, "model", None) or str(__model__ or "__DEFAULT_MODEL__")
                            entry = {
                                "job_status": "accepted",
                                "result_message_id": None,
                                "stop_requested": False,
                                "tool_name": target_tool_name,
                            }
                            task = asyncio.create_task(
                                _run_auto_poll(
                                    app_state=__request__.app.state,
                                    chat_id=str(__chat_id__),
                                    message_id=str(__message_id__),
                                    model_name=model_name,
                                    job_id=job_id,
                                    status_url=normalized_status_url,
                                    tool_name=target_tool_name,
                                    token=self.valves.tool_server_token,
                                    __event_emitter__=__event_emitter__,
                                )
                            )
                            entry["task"] = task
                            registry[key] = entry

                    return (
                        f"{target_action_label} принят как deep-job.\\n"
                        "Прогресс отображается в блоке `Deep job`."
                    )

                assistant_message = str(response.get("assistant_message") or "").strip()
                if assistant_message:
                    return assistant_message
                return (
                    "Не удалось запустить deep-job.\\n"
                    "Инструмент не вернул подтверждение запуска (`job_id` и `status_url`)."
                )
        '''
    ).strip()
    return (
        template.replace("__TOOL_SERVER_BASE_URL__", container_tool_server_base_url)
        .replace("__PRIORITY__", str(priority))
        .replace("__ACTION_LABEL__", action_label)
        .replace("__TOOL_NAME__", tool_name)
        .replace("__DOCUMENT_TOOL_NAME__", document_tool_name)
        .replace("__DOCUMENT_ACTION_LABEL__", document_action_label)
        .replace("__DOCUMENT_DEFAULT_PROMPT__", document_default_prompt)
        .replace("__PAIR_DEFAULT_PROMPT__", pair_default_prompt)
        .replace("__DEFAULT_MODEL__", OPENWEBUI_DEFAULT_MODEL)
        .replace("__METHOD_NAME__", method_name)
        .replace("__TOOL_SUMMARY__", tool.summary)
        .replace("__TOOL_USE_WHEN__", tool.use_when)
        .replace("__TOOL_INPUT_SUMMARY__", tool.input_summary)
        .replace("__TOOL_OUTPUT_SUMMARY__", tool.output_summary)
    )
