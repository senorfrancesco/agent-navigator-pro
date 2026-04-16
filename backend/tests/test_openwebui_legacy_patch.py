from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.responses import StreamingResponse


ROOT = Path(__file__).resolve().parents[2]
PATCH_MODULE_PATH = ROOT / "deploy" / "openwebui_legacy_patch" / "patch_html.py"
PATCH_SCRIPT_PATH = ROOT / "deploy" / "openwebui_legacy_patch" / "llm_tools_platform_autopoll.js"
ENTRYPOINT_PATH = ROOT / "deploy" / "openwebui_legacy_patch" / "entrypoint.sh"
DEEP_JOB_GUARD_PATH = ROOT / "deploy" / "openwebui_legacy_patch" / "openwebui_deep_job_guard.py"


def _load_patch_module():
    spec = importlib.util.spec_from_file_location("openwebui_patch_html", PATCH_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_deep_job_guard_module():
    spec = importlib.util.spec_from_file_location("openwebui_deep_job_guard", DEEP_JOB_GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_html_injection_is_idempotent():
    module = _load_patch_module()
    initial_html = "<html><body><div>app</div></body></html>"
    script = "console.log('patched');"

    patched_html, changed = module.inject_script(initial_html, script)
    patched_again, changed_again = module.inject_script(patched_html, script)

    assert changed is True
    assert module.PATCH_MARKER in patched_html
    assert "console.log('patched');" in patched_html
    assert changed_again is False
    assert patched_again == patched_html


def test_patch_assets_pin_expected_version_and_reload_contract():
    module = _load_patch_module()
    entrypoint = ENTRYPOINT_PATH.read_text(encoding="utf-8")
    script = PATCH_SCRIPT_PATH.read_text(encoding="utf-8")

    assert module.EXPECTED_OPENWEBUI_VERSION == "0.8.12"
    assert 'EXPECTED_VERSION="0.8.12"' in entrypoint
    assert "/app/backend/start.sh" in entrypoint
    assert "window.io" not in script
    assert "18000" not in script
    assert "api/chat/actions/equipment_deep_action" in script
    assert "message-" in script
    assert "result_message_id" in script
    assert "actions_disabled" in script
    assert "status_text" in script
    assert "statusHistory" in script
    assert "aria-label" in script
    assert "mergePersistedChatPayload" in script
    assert "terminalSnapshotByChat" in script
    assert "api/v1/chats/" in script
    assert "__llm_tools_platform_openwebui_autopoll_v2_loaded__" in script
    assert "window[PATCH_MARKER]" not in script


@pytest.mark.parametrize("tool_name", ["analyze_equipment_deep", "analyze_document_deep"])
def test_deep_job_guard_detects_empty_native_tool_result(tool_name: str):
    module = _load_deep_job_guard_module()

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-deep-1",
                    "function": {
                        "name": tool_name,
                        "arguments": "{}",
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-deep-1",
            "content": "",
        },
    ]

    launch_state = module.detect_unconfirmed_deep_job_launch(messages)

    assert launch_state is not None
    assert launch_state["call_id"] == "call-deep-1"
    assert "Не удалось запустить deep-job" in launch_state["message"]


@pytest.mark.parametrize("tool_name", ["analyze_equipment_deep", "analyze_document_deep"])
def test_deep_job_guard_accepts_confirmed_native_tool_result(tool_name: str):
    module = _load_deep_job_guard_module()

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-deep-2",
                    "function": {
                        "name": tool_name,
                        "arguments": "{}",
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-deep-2",
            "content": (
                "Глубокий анализ принят как deep-job.\n"
                "job_id: job-42\n"
                "status_url: /tool-server/tool-jobs/job-42"
            ),
        },
    ]

    assert module.detect_unconfirmed_deep_job_launch(messages) is None


@pytest.mark.parametrize("tool_name", ["analyze_equipment_deep", "analyze_document_deep"])
def test_deep_job_guard_detects_empty_native_tool_result_in_output(tool_name: str):
    module = _load_deep_job_guard_module()

    output = [
        {
            "type": "function_call",
            "call_id": "call-deep-output-1",
            "name": tool_name,
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "call-deep-output-1",
            "output": "",
        },
    ]

    launch_state = module.detect_unconfirmed_deep_job_output(output)

    assert launch_state is not None
    assert launch_state["call_id"] == "call-deep-output-1"
    assert "Не удалось запустить deep-job" in launch_state["message"]


@pytest.mark.parametrize("tool_name", ["analyze_equipment_deep", "analyze_document_deep"])
def test_deep_job_guard_serialize_output_rewrites_unconfirmed_native_tool_result(monkeypatch, tool_name: str):
    module = _load_deep_job_guard_module()
    serialize_calls: list[object] = []

    async def fake_generate_chat_completion(*args, **kwargs):
        raise AssertionError("generate_chat_completion should not be called in serialize_output test")

    def fake_serialize_output(output):
        serialize_calls.append(output)
        return "orig"

    async def fake_chat_completion_files_handler(request, body, extra_params, user):
        return body, {"sources": []}

    async def fake_openai_generate_chat_completion(*args, **kwargs):
        return {"ok": True}

    open_webui_module = ModuleType("open_webui")
    open_webui_module.__path__ = []
    utils_module = ModuleType("open_webui.utils")
    utils_module.__path__ = []
    routers_module = ModuleType("open_webui.routers")
    routers_module.__path__ = []
    middleware_module = ModuleType("open_webui.utils.middleware")
    middleware_module.generate_chat_completion = fake_generate_chat_completion
    middleware_module.serialize_output = fake_serialize_output
    middleware_module.chat_completion_files_handler = fake_chat_completion_files_handler
    openai_module = ModuleType("open_webui.routers.openai")
    openai_module.generate_chat_completion = fake_openai_generate_chat_completion

    monkeypatch.setitem(sys.modules, "open_webui", open_webui_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils", utils_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers", routers_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils.middleware", middleware_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers.openai", openai_module)

    module.apply_patches()

    rewritten = middleware_module.serialize_output(
        [
            {
                "type": "function_call",
                "call_id": "call-deep-output-2",
                "name": tool_name,
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call-deep-output-2",
                "output": "",
            },
        ]
    )

    assert "Не удалось запустить deep-job" in rewritten
    assert serialize_calls == []


@pytest.mark.parametrize("tool_name", ["analyze_equipment_deep", "analyze_document_deep"])
def test_deep_job_guard_short_circuits_follow_up_model_turn(monkeypatch, tool_name: str):
    module = _load_deep_job_guard_module()
    upstream_calls: list[dict] = []

    async def fake_generate_chat_completion(*args, **kwargs):
        upstream_calls.append({"args": args, "kwargs": kwargs})
        return {"unexpected": True}

    def fake_serialize_output(output):
        return "orig"

    async def fake_chat_completion_files_handler(request, body, extra_params, user):
        return body, {"sources": []}

    async def fake_openai_generate_chat_completion(*args, **kwargs):
        return {"ok": True}

    open_webui_module = ModuleType("open_webui")
    open_webui_module.__path__ = []
    utils_module = ModuleType("open_webui.utils")
    utils_module.__path__ = []
    routers_module = ModuleType("open_webui.routers")
    routers_module.__path__ = []
    middleware_module = ModuleType("open_webui.utils.middleware")
    middleware_module.generate_chat_completion = fake_generate_chat_completion
    middleware_module.serialize_output = fake_serialize_output
    middleware_module.chat_completion_files_handler = fake_chat_completion_files_handler
    openai_module = ModuleType("open_webui.routers.openai")
    openai_module.generate_chat_completion = fake_openai_generate_chat_completion

    monkeypatch.setitem(sys.modules, "open_webui", open_webui_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils", utils_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers", routers_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils.middleware", middleware_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers.openai", openai_module)

    module.apply_patches()

    response = asyncio.run(
        middleware_module.generate_chat_completion(
            None,
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-deep-3",
                                "function": {
                                    "name": tool_name,
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call-deep-3",
                        "content": "",
                    },
                ]
            },
            None,
        )
    )

    assert isinstance(response, StreamingResponse)
    payload = asyncio.run(_collect_stream_payload(response))
    assert "Не удалось запустить deep-job" in payload
    assert upstream_calls == []


def test_session_rag_handoff_patch_skips_local_file_rag_and_injects_thread_id(monkeypatch):
    module = _load_deep_job_guard_module()
    monkeypatch.setenv("OPENWEBUI_SESSION_RAG_HANDOFF", "preferred")

    upstream_file_calls: list[dict] = []
    upstream_openai_calls: list[dict] = []

    async def fake_generate_chat_completion(*args, **kwargs):
        return {"unexpected": True}

    def fake_serialize_output(output):
        return "orig"

    async def fake_chat_completion_files_handler(request, body, extra_params, user):
        upstream_file_calls.append({"body": body, "extra_params": extra_params})
        return body, {"sources": [{"source": {"id": "local-file-rag"}}]}

    async def fake_openai_generate_chat_completion(request, form_data, user=None, bypass_system_prompt=False):
        upstream_openai_calls.append({"form_data": form_data, "bypass_system_prompt": bypass_system_prompt})
        return {"ok": True, "payload": form_data}

    open_webui_module = ModuleType("open_webui")
    open_webui_module.__path__ = []
    utils_module = ModuleType("open_webui.utils")
    utils_module.__path__ = []
    routers_module = ModuleType("open_webui.routers")
    routers_module.__path__ = []
    middleware_module = ModuleType("open_webui.utils.middleware")
    middleware_module.generate_chat_completion = fake_generate_chat_completion
    middleware_module.serialize_output = fake_serialize_output
    middleware_module.chat_completion_files_handler = fake_chat_completion_files_handler
    openai_module = ModuleType("open_webui.routers.openai")
    openai_module.generate_chat_completion = fake_openai_generate_chat_completion

    monkeypatch.setitem(sys.modules, "open_webui", open_webui_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils", utils_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers", routers_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils.middleware", middleware_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers.openai", openai_module)

    module.apply_patches()

    body = {
        "model": "llm-tools-platform",
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "files": [
                {
                    "id": "file-1",
                    "name": "contract.pdf",
                    "type": "text",
                    "content": "Штраф 10 процентов",
                }
            ],
        },
    }

    patched_body, flags = asyncio.run(
        middleware_module.chat_completion_files_handler(None, body, {"__event_emitter__": None}, None)
    )
    openai_result = asyncio.run(
        openai_module.generate_chat_completion(
            None,
            {
                "model": "llm-tools-platform",
                "messages": [{"role": "user", "content": "Сделай сводку"}],
                "metadata": {
                    "chat_id": "chat-1",
                    "message_id": "msg-1",
                    "files": [
                        {
                            "id": "file-1",
                            "name": "contract.pdf",
                            "type": "text",
                            "content": "Штраф 10 процентов",
                        }
                    ],
                },
            },
            None,
        )
    )

    assert upstream_file_calls == []
    assert flags == {"sources": []}
    assert patched_body["metadata"]["llm_tools_platform_session_rag_handoff"]["enabled"] is True
    assert upstream_openai_calls[0]["form_data"]["thread_id"] == "chat-1"
    assert upstream_openai_calls[0]["form_data"]["files"][0]["id"] == "file-1"
    assert upstream_openai_calls[0]["form_data"]["openwebui_session_rag_handoff"]["chat_id"] == "chat-1"
    assert openai_result["ok"] is True


def test_session_rag_handoff_patch_reroutes_raw_chat_upload_to_backend_wrapper(monkeypatch):
    module = _load_deep_job_guard_module()
    monkeypatch.setenv("OPENWEBUI_SESSION_RAG_HANDOFF", "preferred")

    upstream_openai_calls: list[dict] = []

    async def fake_generate_chat_completion(*args, **kwargs):
        return {"unexpected": True}

    def fake_serialize_output(output):
        return "orig"

    async def fake_chat_completion_files_handler(request, body, extra_params, user):
        return body, {"sources": [{"source": {"id": "local-file-rag"}}]}

    async def fake_openai_generate_chat_completion(request, form_data, user=None, bypass_system_prompt=False):
        upstream_openai_calls.append({"form_data": form_data, "bypass_system_prompt": bypass_system_prompt})
        return {"ok": True, "payload": form_data}

    open_webui_module = ModuleType("open_webui")
    open_webui_module.__path__ = []
    utils_module = ModuleType("open_webui.utils")
    utils_module.__path__ = []
    routers_module = ModuleType("open_webui.routers")
    routers_module.__path__ = []
    middleware_module = ModuleType("open_webui.utils.middleware")
    middleware_module.generate_chat_completion = fake_generate_chat_completion
    middleware_module.serialize_output = fake_serialize_output
    middleware_module.chat_completion_files_handler = fake_chat_completion_files_handler
    chat_module = ModuleType("open_webui.utils.chat")
    chat_module.generate_openai_chat_completion = fake_openai_generate_chat_completion
    openai_module = ModuleType("open_webui.routers.openai")
    openai_module.generate_chat_completion = fake_openai_generate_chat_completion

    monkeypatch.setitem(sys.modules, "open_webui", open_webui_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils", utils_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers", routers_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils.middleware", middleware_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils.chat", chat_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers.openai", openai_module)

    module.apply_patches()

    openai_result = asyncio.run(
        chat_module.generate_openai_chat_completion(
            None,
            {
                "model": "raw.qwen-14b-llm",
                "messages": [{"role": "user", "content": "Что сказано про штраф?"}],
                "metadata": {
                    "chat_id": "local:chat-2",
                    "message_id": "msg-2",
                },
                "files": [
                    {
                        "id": "file-2",
                        "name": "contract-openwebui-e2e.txt",
                        "type": "text",
                        "content": "Штраф 17 процентов",
                    }
                ],
            },
            None,
        )
    )

    assert upstream_openai_calls[0]["form_data"]["model"] == "llm-tools-platform"
    assert upstream_openai_calls[0]["form_data"]["thread_id"] == "local:chat-2"
    assert upstream_openai_calls[0]["form_data"]["session_id"] == "local:chat-2"
    assert upstream_openai_calls[0]["form_data"]["files"][0]["id"] == "file-2"
    assert upstream_openai_calls[0]["form_data"]["openwebui_session_rag_handoff"]["chat_id"] == "local:chat-2"
    assert upstream_openai_calls[0]["form_data"]["openwebui_session_rag_handoff"]["original_model"] == "raw.qwen-14b-llm"
    assert openai_result["ok"] is True


def test_session_rag_handoff_patch_preferred_mode_falls_back_to_original_model(monkeypatch):
    module = _load_deep_job_guard_module()
    monkeypatch.setenv("OPENWEBUI_SESSION_RAG_HANDOFF", "preferred")

    upstream_models: list[str] = []

    async def fake_generate_chat_completion(*args, **kwargs):
        return {"unexpected": True}

    def fake_serialize_output(output):
        return "orig"

    async def fake_chat_completion_files_handler(request, body, extra_params, user):
        return body, {"sources": []}

    async def fake_openai_generate_chat_completion(request, form_data, user=None, bypass_system_prompt=False):
        upstream_models.append(str(form_data.get("model")))
        if form_data.get("model") == "llm-tools-platform":
            raise RuntimeError("backend wrapper unavailable")
        return {"ok": True, "model": form_data.get("model")}

    open_webui_module = ModuleType("open_webui")
    open_webui_module.__path__ = []
    utils_module = ModuleType("open_webui.utils")
    utils_module.__path__ = []
    routers_module = ModuleType("open_webui.routers")
    routers_module.__path__ = []
    middleware_module = ModuleType("open_webui.utils.middleware")
    middleware_module.generate_chat_completion = fake_generate_chat_completion
    middleware_module.serialize_output = fake_serialize_output
    middleware_module.chat_completion_files_handler = fake_chat_completion_files_handler
    chat_module = ModuleType("open_webui.utils.chat")
    chat_module.generate_openai_chat_completion = fake_openai_generate_chat_completion
    openai_module = ModuleType("open_webui.routers.openai")
    openai_module.generate_chat_completion = fake_openai_generate_chat_completion

    monkeypatch.setitem(sys.modules, "open_webui", open_webui_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils", utils_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers", routers_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils.middleware", middleware_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils.chat", chat_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers.openai", openai_module)

    module.apply_patches()

    result = asyncio.run(
        chat_module.generate_openai_chat_completion(
            None,
            {
                "model": "raw.qwen-14b-llm",
                "messages": [{"role": "user", "content": "Что сказано про штраф?"}],
                "metadata": {"chat_id": "local:chat-3", "message_id": "msg-3"},
                "files": [{"id": "file-3", "name": "contract.txt", "type": "text", "content": "Штраф 17 процентов"}],
            },
            None,
        )
    )

    assert result == {"ok": True, "model": "raw.qwen-14b-llm"}
    assert upstream_models == ["llm-tools-platform", "raw.qwen-14b-llm"]


def test_session_rag_handoff_patch_required_mode_does_not_fallback(monkeypatch):
    module = _load_deep_job_guard_module()
    monkeypatch.setenv("OPENWEBUI_SESSION_RAG_HANDOFF", "required")

    upstream_models: list[str] = []

    async def fake_generate_chat_completion(*args, **kwargs):
        return {"unexpected": True}

    def fake_serialize_output(output):
        return "orig"

    async def fake_chat_completion_files_handler(request, body, extra_params, user):
        return body, {"sources": []}

    async def fake_openai_generate_chat_completion(request, form_data, user=None, bypass_system_prompt=False):
        upstream_models.append(str(form_data.get("model")))
        raise RuntimeError("backend wrapper unavailable")

    open_webui_module = ModuleType("open_webui")
    open_webui_module.__path__ = []
    utils_module = ModuleType("open_webui.utils")
    utils_module.__path__ = []
    routers_module = ModuleType("open_webui.routers")
    routers_module.__path__ = []
    middleware_module = ModuleType("open_webui.utils.middleware")
    middleware_module.generate_chat_completion = fake_generate_chat_completion
    middleware_module.serialize_output = fake_serialize_output
    middleware_module.chat_completion_files_handler = fake_chat_completion_files_handler
    chat_module = ModuleType("open_webui.utils.chat")
    chat_module.generate_openai_chat_completion = fake_openai_generate_chat_completion
    openai_module = ModuleType("open_webui.routers.openai")
    openai_module.generate_chat_completion = fake_openai_generate_chat_completion

    monkeypatch.setitem(sys.modules, "open_webui", open_webui_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils", utils_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers", routers_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils.middleware", middleware_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils.chat", chat_module)
    monkeypatch.setitem(sys.modules, "open_webui.routers.openai", openai_module)

    module.apply_patches()

    try:
        asyncio.run(
            chat_module.generate_openai_chat_completion(
                None,
                {
                    "model": "raw.qwen-14b-llm",
                    "messages": [{"role": "user", "content": "Что сказано про штраф?"}],
                    "metadata": {"chat_id": "local:chat-4", "message_id": "msg-4"},
                    "files": [{"id": "file-4", "name": "contract.txt", "type": "text", "content": "Штраф 17 процентов"}],
                },
                None,
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "backend wrapper unavailable"
    else:
        raise AssertionError("required handoff mode must not fall back to the original raw model")

    assert upstream_models == ["llm-tools-platform"]


async def _collect_stream_payload(response: StreamingResponse) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(str(chunk))
    return "".join(chunks)
