from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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
    assert "__llm_tools_platform_openwebui_autopoll_v2_loaded__" in script
    assert "window[PATCH_MARKER]" not in script


def test_deep_job_guard_detects_empty_native_tool_result():
    module = _load_deep_job_guard_module()

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-deep-1",
                    "function": {
                        "name": "analyze_equipment_deep",
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


def test_deep_job_guard_accepts_confirmed_native_tool_result():
    module = _load_deep_job_guard_module()

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-deep-2",
                    "function": {
                        "name": "analyze_equipment_deep",
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


def test_deep_job_guard_short_circuits_follow_up_model_turn(monkeypatch):
    module = _load_deep_job_guard_module()
    upstream_calls: list[dict] = []

    async def fake_generate_chat_completion(*args, **kwargs):
        upstream_calls.append({"args": args, "kwargs": kwargs})
        return {"unexpected": True}

    def fake_serialize_output(output):
        return "orig"

    open_webui_module = ModuleType("open_webui")
    open_webui_module.__path__ = []
    utils_module = ModuleType("open_webui.utils")
    utils_module.__path__ = []
    middleware_module = ModuleType("open_webui.utils.middleware")
    middleware_module.generate_chat_completion = fake_generate_chat_completion
    middleware_module.serialize_output = fake_serialize_output

    monkeypatch.setitem(sys.modules, "open_webui", open_webui_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils", utils_module)
    monkeypatch.setitem(sys.modules, "open_webui.utils.middleware", middleware_module)

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
                                    "name": "analyze_equipment_deep",
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


async def _collect_stream_payload(response: StreamingResponse) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(str(chunk))
    return "".join(chunks)
