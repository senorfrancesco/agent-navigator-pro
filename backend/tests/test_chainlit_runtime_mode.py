import importlib
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestChainlitControlPlaneSettings:
    @pytest.fixture(autouse=True)
    def _setup_chainlit_mock(self):
        store = {}

        mock_cl = MagicMock()
        mock_session = MagicMock()
        mock_session.get.side_effect = lambda key, default=None: store.get(key, default)
        mock_session.set.side_effect = lambda key, value: store.__setitem__(key, value)
        mock_cl.user_session = mock_session
        mock_cl.Message = MagicMock()
        mock_cl.AskActionMessage = MagicMock()
        mock_cl.Action = MagicMock()
        mock_cl.Step = MagicMock()
        mock_cl.User = MagicMock()
        mock_cl.Starter = MagicMock(side_effect=lambda **kwargs: kwargs)
        mock_cl.ChatProfile = MagicMock(side_effect=lambda **kwargs: kwargs)
        mock_cl.ChatSettings = MagicMock()
        mock_cl.password_auth_callback = lambda f: f
        mock_cl.data_layer = lambda f: f
        mock_cl.on_chat_start = lambda f: f
        mock_cl.on_chat_resume = lambda f: f
        mock_cl.on_message = lambda f: f
        mock_cl.on_settings_update = lambda f: f
        mock_cl.set_starters = lambda f: f
        mock_cl.set_chat_profiles = lambda f: f
        mock_cl.input_widget = SimpleNamespace(
            Select=MagicMock(side_effect=lambda **kwargs: kwargs),
            TextInput=MagicMock(side_effect=lambda **kwargs: kwargs),
            Slider=MagicMock(side_effect=lambda **kwargs: kwargs),
            NumberInput=MagicMock(side_effect=lambda **kwargs: kwargs),
            Tab=MagicMock(side_effect=lambda **kwargs: kwargs),
        )

        chat_settings_instance = MagicMock()
        chat_settings_instance.send = AsyncMock()
        mock_cl.ChatSettings.return_value = chat_settings_instance

        sys.modules["chainlit"] = mock_cl
        sys.modules["chainlit.data"] = MagicMock()
        sys.modules["chainlit.data.sql_alchemy"] = MagicMock()

        if "orchestrator.chainlit_app" in sys.modules:
            importlib.reload(sys.modules["orchestrator.chainlit_app"])
        else:
            import orchestrator.chainlit_app

        self._module = sys.modules["orchestrator.chainlit_app"]
        self._store = store
        self._mock_cl = mock_cl
        self._chat_settings_instance = chat_settings_instance
        yield

        for mod_name in ["chainlit", "chainlit.data", "chainlit.data.sql_alchemy"]:
            sys.modules.pop(mod_name, None)
        sys.modules.pop("orchestrator.chainlit_app", None)

    def test_get_runtime_mode_normalizes_invalid_session_value(self):
        self._store["runtime_mode"] = "broken_mode"

        assert self._module._get_runtime_mode() == "auto"

    def test_chainlit_app_uses_backend_routing_helpers_directly(self):
        assert not hasattr(self._module, "_detect_intent")
        assert not hasattr(self._module, "_is_social_query")
        assert not hasattr(self._module, "_is_low_confidence")
        assert not hasattr(self._module, "_build_route_choice_prompt")
        assert not hasattr(self._module, "_build_route_choice_state")
        assert not hasattr(self._module, "_resolve_pending_route_choice")
        assert not hasattr(self._module, "_is_docs_summary_query")

    def test_apply_control_plane_preset_updates_session_state(self):
        effective = self._module._apply_control_plane_preset("preset:rag_qa")

        assert self._store["control_plane_state"]["assistant_mode"] == "rag_qa"
        assert effective["assistant_mode"] == "rag_qa"
        assert effective["runtime_mode"] == "specialized_tasks"
        assert effective["rag_scope"] == "knowledge_base_rag"
        assert self._store["runtime_mode"] == "specialized_tasks"

    @pytest.mark.asyncio
    async def test_send_control_plane_settings_renders_multitab_panel(self):
        self._store["control_plane_state"] = {
            "assistant_mode": "specific_tasks",
            "runtime_mode": "specialized_tasks",
            "rag_scope": "session_rag",
            "model_profile": "analyst",
            "prompt_profile": "task-router",
            "custom_system_prompt": "Всегда указывай ограничения.",
            "generation_overrides": {
                "temperature": 0.2,
                "top_p": 0.8,
                "max_tokens": 1024,
            },
        }

        await self._module._send_control_plane_settings()

        self._mock_cl.ChatSettings.assert_called_once()
        tabs = self._mock_cl.ChatSettings.call_args.args[0]
        assert [tab["id"] for tab in tabs] == [
            "use_case",
            "rag",
            "model",
            "prompt",
            "generation",
        ]

        use_case_inputs = tabs[0]["inputs"]
        rag_inputs = tabs[1]["inputs"]
        prompt_inputs = tabs[3]["inputs"]
        generation_inputs = tabs[4]["inputs"]

        assert [widget["id"] for widget in use_case_inputs] == ["assistant_mode", "runtime_mode"]
        assert [widget["id"] for widget in rag_inputs] == ["rag_scope", "knowledge_collection_id"]
        assert [widget["id"] for widget in prompt_inputs] == ["prompt_profile", "custom_system_prompt"]
        assert [widget["id"] for widget in generation_inputs] == ["temperature", "top_p", "max_tokens"]
        self._chat_settings_instance.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_settings_update_resolves_and_persists_effective_settings(self):
        await self._module.on_settings_update(
            {
                "assistant_mode": "specific_tasks",
                "runtime_mode": "chat_only",
                "rag_scope": "knowledge_base_rag",
                "knowledge_collection_id": "legal",
                "model_profile": "coder",
                "prompt_profile": "coding-assistant",
                "custom_system_prompt": "  Пиши точные ответы.  ",
                "temperature": 0.15,
                "top_p": 0.42,
                "max_tokens": 512,
            }
        )

        raw = self._store["control_plane_state"]
        effective = self._store["effective_settings"]

        assert raw["assistant_mode"] == "specific_tasks"
        assert raw["runtime_mode"] == "chat_only"
        assert raw["rag_scope"] == "knowledge_base_rag"
        assert raw["generation_overrides"] == {
            "temperature": 0.15,
            "top_p": 0.42,
            "max_tokens": 512,
        }
        assert effective["assistant_mode"] == "specific_tasks"
        assert effective["runtime_mode"] == "chat_only"
        assert effective["rag_scope"] == "knowledge_base_rag"
        assert effective["knowledge_collection_id"] == "legal"
        assert effective["model_profile"] == "coder"
        assert effective["prompt_profile"] == "coding-assistant"
        assert effective["custom_system_prompt"] == "Пиши точные ответы."
        assert self._store["runtime_mode"] == "chat_only"

    def test_set_pending_route_choice_writes_both_session_keys(self):
        payload = {"type": "choose_route", "route_choice_id": "rc-1"}

        self._module._set_pending_route_choice(payload)

        assert self._store["pending_action"] == payload
        assert self._store["pending_route_choice"] == payload

    def test_apply_session_state_patch_clears_both_pending_keys(self):
        self._store["pending_action"] = {"type": "choose_route"}
        self._store["pending_route_choice"] = {"type": "choose_route"}

        self._module._apply_session_state_patch({"pending_action": None})

        assert self._store["pending_action"] is None
        assert self._store["pending_route_choice"] is None

    def test_default_starters_expose_use_case_presets(self):
        starters = self._module._default_starters()

        assert [starter["label"] for starter in starters] == [
            "General Chat",
            "Coding Assistant",
            "Agentic",
            "Specific Tasks",
            "RAG Q&A",
        ]
        assert [starter["command"] for starter in starters] == [
            "preset:general_chat",
            "preset:coding",
            "preset:agentic",
            "preset:specific_tasks",
            "preset:rag_qa",
        ]

    @pytest.mark.asyncio
    async def test_on_chat_start_sends_control_plane_settings(self):
        created = []

        def _fake_create_task(coro):
            created.append(coro)
            coro.close()
            return None

        with patch.object(self._module, "_send_control_plane_settings", new=AsyncMock()) as mock_send_settings, patch.object(
            self._module.asyncio,
            "create_task",
            side_effect=_fake_create_task,
        ):
            await self._module.on_chat_start()

        mock_send_settings.assert_awaited_once()
        assert self._store["runtime_mode"] == "auto"
        assert "control_plane_state" in self._store
        assert "effective_settings" in self._store

    @pytest.mark.asyncio
    async def test_on_message_uses_backend_execution_path_instead_of_local_execute_intent(self):
        message_instance = MagicMock()
        message_instance.send = AsyncMock()
        self._mock_cl.Message.return_value = message_instance

        fake_message = SimpleNamespace(content="Привет", elements=[], command=None)

        assert not hasattr(self._module, "_execute_intent")

        with patch.object(
            self._module,
            "_backend_execute_orchestration",
            new=AsyncMock(
                return_value={
                    "route": "general_chat",
                    "executor": "chat",
                    "assistant_message": "Привет!",
                    "action_required": None,
                    "session_state_patch": {"active_mode": "chat"},
                    "ui_effects": {"clear_pending_action": True},
                    "state_ref": "session:test",
                    "pending_action_id": None,
                    "effective_settings": self._module.resolve_effective_settings({}),
                }
            ),
        ) as mock_execute:
            await self._module.on_message(fake_message)

        mock_execute.assert_awaited_once()
        message_instance.send.assert_awaited_once()
