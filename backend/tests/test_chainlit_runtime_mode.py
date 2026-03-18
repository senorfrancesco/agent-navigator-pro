import importlib
import os
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestChainlitControlPlaneSettings:
    @pytest.fixture(autouse=True)
    def _setup_chainlit_mock(self):
        store = {}

        @dataclass
        class _RunRecord:
            run_id: str
            state_ref: str
            version: int
            status: str = "initialized"
            pending_action_id: str | None = None
            resume_state_blob: dict | None = None
            checkpoint_blob: dict | None = None

        class _FakeStateStore:
            def __init__(self):
                self.record = None

            async def get_or_create_run(self, *, thread_id, session_id, workflow_type, idempotency_key=None):
                if self.record is None:
                    self.record = _RunRecord("run-test", "run:run-test", 1)
                return self.record

            async def load_run(self, *, run_id=None, state_ref=None, thread_id=None, session_id=None):
                return self.record

            async def save_run(
                self,
                *,
                run_id,
                status,
                pending_action_id,
                resume_state_blob,
                checkpoint_blob,
                last_error=None,
                expected_version=None,
            ):
                if self.record is None:
                    self.record = _RunRecord(run_id, f"run:{run_id}", 1)
                self.record = _RunRecord(
                    self.record.run_id,
                    self.record.state_ref,
                    self.record.version + 1,
                    status=status,
                    pending_action_id=pending_action_id,
                    resume_state_blob=resume_state_blob,
                    checkpoint_blob=checkpoint_blob,
                )
                return self.record

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
        mock_cl.on_stop = lambda f: f
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
        fake_data_layer = MagicMock()
        fake_data_layer.update_thread = AsyncMock()

        sys.modules["chainlit"] = mock_cl
        sys.modules["chainlit.data"] = MagicMock(get_data_layer=MagicMock(return_value=fake_data_layer))
        sys.modules["chainlit.data.sql_alchemy"] = MagicMock()

        if "orchestrator.chainlit_app" in sys.modules:
            importlib.reload(sys.modules["orchestrator.chainlit_app"])
        else:
            import orchestrator.chainlit_app

        self._RunRecord = _RunRecord
        self._module = sys.modules["orchestrator.chainlit_app"]
        self._store = store
        self._mock_cl = mock_cl
        self._chat_settings_instance = chat_settings_instance
        self._fake_state_store = _FakeStateStore()
        self._fake_data_layer = fake_data_layer
        self._module.get_orchestration_state_store = lambda: self._fake_state_store
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

    def test_effective_settings_summary_includes_runtime_budget_metadata(self):
        self._store["runtime_budget_metadata"] = {
            "runtime_profile": "adaptive",
            "effective_context_tokens": 12288,
            "retrieved_context_tokens_budget": 7372,
        }

        summary = self._module._format_effective_settings_summary(
            self._module.resolve_effective_settings({"assistant_mode": "specific_tasks"})
        )

        assert "model_profile: `legal-compare`" in summary
        assert "intent_embedder: `qwen3-embedding-0.6b`" in summary
        assert "retrieval_embedder: `qwen3-embedding-0.6b`" in summary
        assert "device_mode: `prefer-gpu`" in summary
        assert "context_budget_profile: `legal-compare`" in summary
        assert "runtime_profile: `adaptive`" in summary
        assert "effective_context_tokens: `12288`" in summary
        assert "retrieved_context_tokens_budget: `7372`" in summary

    def test_build_execution_dependencies_uses_resolved_retrieval_embedder(self):
        self._store["effective_settings"] = self._module.resolve_effective_settings({"model_profile": "low-vram"})
        called = {}

        def fake_create_ums_embed_fn(**kwargs):
            called["model_id"] = kwargs.get("model_id")
            return "embed-fn"

        with patch("services.model_manager.ums_client.create_ums_embed_fn", side_effect=fake_create_ums_embed_fn):
            deps = self._module._build_execution_dependencies()
            assert deps.get_retrieval_embed_fn() == "embed-fn"

        assert called["model_id"] == self._store["effective_settings"]["resolved_retrieval_embedder_model_id"]

    def test_build_execution_request_includes_runtime_budget_metadata(self):
        self._store["runtime_budget_metadata"] = {
            "tier": 2,
            "hardware": {"gpu_count": 1, "total_vram_gb": 8.0},
        }

        payload = self._module._build_execution_request(
            message="Привет",
            trace_id="trace-1",
            new_files=[],
            session_docs={},
        )

        assert payload["runtime_budget_metadata"]["tier"] == 2
        assert payload["runtime_budget_metadata"]["hardware"]["gpu_count"] == 1

    @pytest.mark.asyncio
    async def test_on_stop_marks_cancel_and_cancels_active_execution_task(self):
        class _FakeTask:
            def __init__(self):
                self.cancel_called = False

            def done(self):
                return False

            def cancel(self):
                self.cancel_called = True

        progress_step = MagicMock()
        progress_step.update = AsyncMock()
        fake_task = _FakeTask()
        self._store["active_execution_task"] = fake_task
        self._store["documents_summary_progress"] = progress_step

        await self._module.on_stop()

        assert self._store["cancel_requested"] is True
        assert fake_task.cancel_called is True
        assert progress_step.name == "Остановка запроса"
        assert "освобождаю слот" in progress_step.output
        progress_step.update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_control_plane_settings_renders_multitab_panel(self):
        self._store["control_plane_state"] = {
            "assistant_mode": "specific_tasks",
            "runtime_mode": "specialized_tasks",
            "rag_scope": "session_rag",
            "model_profile": "legal-compare",
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
        assert use_case_inputs[0]["initial_value"] == "specific_tasks"
        assert use_case_inputs[0]["items"]["Specific Tasks"] == "specific_tasks"
        assert use_case_inputs[1]["items"]["Агентный режим: приоритет task-routing"] == "specialized_tasks"
        assert rag_inputs[0]["items"]["Session RAG"] == "session_rag"
        assert tabs[2]["inputs"][0]["items"]["Legal Compare"] == "legal-compare"
        assert prompt_inputs[0]["items"]["Task Router"] == "task-router"
        self._chat_settings_instance.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_settings_update_resolves_and_persists_effective_settings(self):
        await self._module.on_settings_update(
            {
                "assistant_mode": "specific_tasks",
                "runtime_mode": "chat_only",
                "rag_scope": "knowledge_base_rag",
                "knowledge_collection_id": "legal",
                "model_profile": "low-vram",
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
        assert effective["model_profile"] == "low-vram"
        assert effective["prompt_profile"] == "coding-assistant"
        assert effective["custom_system_prompt"] == "Пиши точные ответы."
        assert effective["device_mode"] == "low-vram"
        assert effective["context_budget_profile"] == "compact"
        assert self._store["runtime_mode"] == "chat_only"

    @pytest.mark.asyncio
    async def test_on_settings_update_switching_only_assistant_mode_applies_preset_fields(self):
        await self._module.on_settings_update(
            {
                "assistant_mode": "specific_tasks",
                "runtime_mode": "auto",
                "rag_scope": "off",
                "model_profile": "default-chat",
                "prompt_profile": "default-assistant",
                "temperature": 0.7,
                "top_p": 0.9,
                "max_tokens": 2048,
            }
        )

        raw = self._store["control_plane_state"]
        effective = self._store["effective_settings"]

        assert raw["assistant_mode"] == "specific_tasks"
        assert raw["runtime_mode"] == "specialized_tasks"
        assert raw["rag_scope"] == "session_rag"
        assert raw["model_profile"] == "legal-compare"
        assert raw["prompt_profile"] == "task-router"
        assert raw["tool_scope"] == "domain_tasks"
        assert raw["generation_overrides"] == {
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": 2048,
        }
        assert effective["assistant_mode"] == "specific_tasks"
        assert effective["runtime_mode"] == "specialized_tasks"
        assert effective["rag_scope"] == "session_rag"
        assert effective["model_profile"] == "legal-compare"
        assert effective["prompt_profile"] == "task-router"
        assert effective["tool_scope"] == "domain_tasks"
        assert effective["generation"] == {
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": 2048,
        }
        assert self._store["runtime_mode"] == "specialized_tasks"

    @pytest.mark.asyncio
    async def test_on_settings_update_preserves_only_explicit_overrides_after_assistant_mode_change(self):
        await self._module.on_settings_update(
            {
                "assistant_mode": "specific_tasks",
                "runtime_mode": "chat_only",
                "rag_scope": "off",
                "model_profile": "default-chat",
                "prompt_profile": "default-assistant",
                "temperature": 0.15,
                "top_p": 0.9,
                "max_tokens": 2048,
            }
        )

        raw = self._store["control_plane_state"]
        effective = self._store["effective_settings"]

        assert raw["assistant_mode"] == "specific_tasks"
        assert raw["runtime_mode"] == "chat_only"
        assert raw["rag_scope"] == "session_rag"
        assert raw["model_profile"] == "legal-compare"
        assert raw["prompt_profile"] == "task-router"
        assert raw["tool_scope"] == "domain_tasks"
        assert raw["generation_overrides"] == {
            "temperature": 0.15,
            "top_p": 0.8,
            "max_tokens": 2048,
        }
        assert effective["runtime_mode"] == "chat_only"
        assert effective["rag_scope"] == "session_rag"
        assert effective["model_profile"] == "legal-compare"
        assert effective["prompt_profile"] == "task-router"
        assert effective["tool_scope"] == "domain_tasks"
        assert effective["generation"] == {
            "temperature": 0.15,
            "top_p": 0.8,
            "max_tokens": 2048,
        }
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
            "Agentic (iterative)",
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
        with patch.object(self._module, "_send_control_plane_settings", new=AsyncMock()) as mock_send_settings:
            await self._module.on_chat_start()

        mock_send_settings.assert_awaited_once()
        self._mock_cl.Message.assert_not_called()
        assert self._store["runtime_mode"] == "auto"
        assert "control_plane_state" in self._store
        assert "effective_settings" in self._store
        assert self._store["run_id"] == "run-test"
        assert self._store["state_ref"] == "run:run-test"

    def test_chainlit_app_has_no_local_classifier_runtime_helpers(self):
        assert not hasattr(self._module, "_get_classifier_result")
        assert not hasattr(self._module, "_init_classifier")

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
        request_payload = mock_execute.await_args.args[0]
        assert "classifier_result" not in request_payload
        message_instance.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_chat_resume_prefers_backend_snapshot_over_thread_steps(self):
        resume_message = MagicMock()
        resume_message.send = AsyncMock()
        self._mock_cl.Message.return_value = resume_message
        self._fake_state_store.record = self._RunRecord(
            run_id="run-test",
            state_ref="run:run-test",
            version=3,
            status="waiting_action",
            pending_action_id="route-choice-1",
            resume_state_blob={
                "history": [{"role": "assistant", "content": "restored"}],
                "document_refs": [
                    {
                        "document_id": "doc-1",
                        "display_name": "contract.pdf",
                        "version": 1,
                        "path": "/tmp/contract.pdf",
                        "uploaded_at": 1.0,
                        "source_message_id": None,
                        "source_origin": "session",
                        "collection_id": None,
                    }
                ],
                "active_doc_ids": ["doc-1"],
                "pending_action": {"type": "choose_route", "route_choice_id": "route-choice-1"},
                "runtime_mode": "specialized_tasks",
                "control_plane_state": {"assistant_mode": "specific_tasks", "runtime_mode": "specialized_tasks"},
                "effective_settings": self._module.resolve_effective_settings(
                    {"assistant_mode": "specific_tasks", "runtime_mode": "specialized_tasks"}
                ),
            },
            checkpoint_blob={"route": "document_question"},
        )

        with patch.object(self._module, "_ensure_rag_index_for_active_docs", new=AsyncMock()) as mock_rag, patch.object(
            self._module,
            "_send_control_plane_settings",
            new=AsyncMock(),
        ) as mock_send:
            await self._module.on_chat_resume({"id": "thread-1", "steps": []})

        mock_rag.assert_awaited_once()
        mock_send.assert_awaited_once()
        resume_message.send.assert_awaited_once()
        content = self._mock_cl.Message.call_args.kwargs["content"]
        assert "Контекст восстановлен" in content
        assert "Active docs: `1`" in content
        assert "pending_action: `yes`" in content
        assert self._store["run_id"] == "run-test"
        assert self._store["state_ref"] == "run:run-test"
        assert self._store["active_doc_ids"] == ["doc-1"]
        assert self._store["pending_action"]["route_choice_id"] == "route-choice-1"
        assert self._store["documents_by_id"]["doc-1"]["display_name"] == "contract.pdf"
        assert self._store["documents_by_id"]["doc-1"]["text"] == ""

    @pytest.mark.asyncio
    async def test_get_runtime_budget_metadata_reads_ums_status(self):
        response = MagicMock()
        response.json.return_value = {
            "runtime_profile": "adaptive",
            "effective_context_tokens": 12288,
            "retrieved_context_tokens_budget": 7372,
            "generation_tokens_reserve": 1024,
            "context_budget_ratio": 0.6,
            "tier": {"rag_mode": "corrective", "rag_mode_label": "corrective retrieval"},
        }
        client = MagicMock()
        client.get = AsyncMock(return_value=response)

        with patch.object(self._module, "get_shared_client", new=AsyncMock(return_value=client)):
            metadata = await self._module._get_runtime_budget_metadata()

        assert metadata["runtime_profile"] == "adaptive"
        assert metadata["effective_context_tokens"] == 12288
        assert metadata["retrieved_context_tokens_budget"] == 7372
        assert metadata["rag_mode"] == "corrective"
        assert metadata["rag_mode_label"] == "corrective retrieval"
        assert self._store["runtime_budget_metadata"]["runtime_profile"] == "adaptive"

    @pytest.mark.asyncio
    async def test_update_progress_box_reuses_single_step_instance(self):
        created_steps = []

        class _FakeStep:
            def __init__(self, name, **kwargs):
                self.name = name
                self.output = ""
                self.send = AsyncMock()
                self.update = AsyncMock()
                self.remove = AsyncMock()
                created_steps.append(self)

        self._mock_cl.Step.side_effect = lambda **kwargs: _FakeStep(**kwargs)

        await self._module._update_progress_box(
            key="documents_summary_progress",
            title="Суммаризация фрагментов",
            content="1/10",
        )
        await self._module._update_progress_box(
            key="documents_summary_progress",
            title="Суммаризация фрагментов",
            content="2/10",
        )

        assert len(created_steps) == 1
        step = created_steps[0]
        step.send.assert_awaited_once()
        step.update.assert_awaited_once()
        assert step.name == "Суммаризация фрагментов"
        assert step.output == "2/10"
        assert self._store["documents_summary_progress"] is step

    @pytest.mark.asyncio
    async def test_render_execution_response_finalizes_progress_box_before_final_message(self):
        progress_step = MagicMock()
        progress_step.update = AsyncMock()
        message_instance = MagicMock()
        message_instance.send = AsyncMock()
        self._mock_cl.Message.return_value = message_instance
        self._store["documents_summary_progress"] = progress_step
        history = []

        await self._module._render_execution_response(
            {"assistant_message": "Финальный ответ"},
            history,
        )

        progress_step.update.assert_awaited_once()
        assert self._store["documents_summary_progress"] is None
        message_instance.send.assert_awaited_once()
        assert history == [{"role": "assistant", "content": "Финальный ответ"}]

    def test_render_doc_question_markdown_includes_scope_and_provenance(self):
        markdown = self._module._render_doc_question_markdown(
            {
                "answer_text": "Штраф составляет 3 процента [1]",
                "sources": [
                    {
                        "source_id": 1,
                        "document_id": "kb-doc",
                        "display_name": "kb_policy.txt",
                        "chunk_id": "kb-doc:0",
                        "collection_id": "legal",
                        "source_origin": "knowledge_base",
                        "section": "Статья 5",
                        "char_span": {"start_char": 0, "end_char": 42},
                        "page": 2,
                        "quote": "За просрочку поставки применяется штраф 3 процента.",
                        "raw_score": 0.91,
                        "normalized_score": 0.88,
                        "grade": None,
                        "z_score": None,
                    }
                ],
                "source_scope_summary": "knowledge_base+session_overlay",
                "answer_mode": "grounded_answer",
                "fallback_type": "none",
                "fallback_reason": None,
                "confidence": 0.9,
                "confidence_label": "high",
                "confidence_method": "heuristic_v1",
                "confidence_version": "1",
            }
        )

        assert "retrieval_scope: `knowledge_base+session_overlay`" in markdown
        assert "origin=knowledge_base:legal" in markdown
        assert "section=Статья 5" in markdown
        assert "page=2" in markdown

    def test_build_sources_from_rag_result_lifts_section_metadata(self):
        rag_result = SimpleNamespace(
            chunks=[
                SimpleNamespace(
                    index=0,
                    text="Уведомление направляется за 10 дней.",
                    score=0.75,
                    metadata={},
                )
            ]
        )
        rag_pipeline = SimpleNamespace(
            _chunks=[
                SimpleNamespace(
                    metadata={"doc_name": "contract.pdf", "section": "Статья 3"},
                    start_char=10,
                    end_char=42,
                )
            ]
        )

        sources = self._module._build_sources_from_rag_result(rag_result, rag_pipeline, max_sources=5)

        assert sources[0]["display_name"] == "contract.pdf"
        assert sources[0]["section"] == "Статья 3"

    def test_build_thread_metadata_includes_active_context_and_pending_action(self):
        self._store["control_plane_state"] = {
            "assistant_mode": "specific_tasks",
            "runtime_mode": "specialized_tasks",
            "rag_scope": "session_rag",
            "knowledge_collection_id": "legal",
        }
        self._store["documents_by_id"] = {
            "doc-1": {
                "document_id": "doc-1",
                "display_name": "contract.pdf",
                "version": 1,
                "path": "/tmp/contract.pdf",
                "text": "text",
                "uploaded_at": 1.0,
                "source_message_id": None,
            }
        }
        self._store["active_doc_ids"] = ["doc-1"]
        self._store["pending_action"] = {"type": "choose_route", "route_choice_id": "rc-1"}
        self._store["last_route"] = "document_question"
        self._store["last_executor"] = "doc_question"

        metadata = self._module._build_thread_metadata()

        assert metadata["assistant_mode"] == "specific_tasks"
        assert metadata["rag_scope"] == "session_rag"
        assert metadata["knowledge_collection_id"] == "legal"
        assert metadata["active_doc_ids"] == ["doc-1"]
        assert metadata["active_doc_labels"] == ["contract.pdf"]
        assert metadata["has_pending_action"] is True
        assert metadata["last_route"] == "document_question"
        assert metadata["last_executor"] == "doc_question"

    def test_build_backend_resume_snapshot_uses_document_refs(self):
        self._store["documents_by_id"] = {
            "doc-1": {
                "document_id": "doc-1",
                "display_name": "contract.pdf",
                "version": 1,
                "path": "/tmp/contract.pdf",
                "text": "secret text",
                "uploaded_at": 1.0,
                "source_message_id": None,
            }
        }
        self._store["active_doc_ids"] = ["doc-1"]

        snapshot = self._module._build_backend_resume_snapshot()

        assert "documents_by_id" not in snapshot
        assert snapshot["document_refs"] == [
            {
                "document_id": "doc-1",
                "display_name": "contract.pdf",
                "version": 1,
                "path": "/tmp/contract.pdf",
                "uploaded_at": 1.0,
                "source_message_id": None,
                "source_origin": None,
                "collection_id": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_sync_thread_presentation_updates_data_layer_with_name_and_metadata(self):
        self._store["thread_id"] = "thread-1"
        self._store["control_plane_state"] = {
            "assistant_mode": "rag_qa",
            "runtime_mode": "specialized_tasks",
            "rag_scope": "knowledge_base_rag",
        }

        await self._module._sync_thread_presentation(user_message="Сравни штрафы по договору")

        self._fake_data_layer.update_thread.assert_awaited_once()
        kwargs = self._fake_data_layer.update_thread.await_args.kwargs
        assert kwargs["thread_id"] == "thread-1"
        assert kwargs["name"] == "Сравни штрафы по договору"
        assert kwargs["metadata"]["assistant_mode"] == "rag_qa"
        assert kwargs["metadata"]["rag_scope"] == "knowledge_base_rag"

    def test_build_context_status_markdown_handles_empty_state(self):
        text = self._module._build_context_status_markdown(title="Текущий контекст")

        assert "Текущий контекст" in text
        assert "Active docs: `0`" in text
        assert "pending_action: `no`" in text

    @pytest.mark.asyncio
    async def test_ensure_rag_index_passes_runtime_budget_into_pipeline(self):
        self._store["documents_by_id"] = {
            "doc-1": {
                "document_id": "doc-1",
                "display_name": "contract.pdf",
                "version": 1,
                "path": "/tmp/contract.pdf",
                "text": "Штраф 10 процентов",
                "uploaded_at": 1.0,
                "source_message_id": None,
            }
        }
        self._store["active_doc_ids"] = ["doc-1"]
        init_kwargs = {}

        class _FakePipeline:
            def __init__(self, **kwargs):
                init_kwargs.update(kwargs)
                self.embed_fn = kwargs.get("embed_fn")
                self.rag_mode = kwargs.get("rag_mode")

            def index_documents(self, texts, doc_names=None):
                return []

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(self._module, "_get_runtime_budget_metadata", new=AsyncMock(return_value={
            "runtime_profile": "adaptive",
            "effective_context_tokens": 12288,
            "retrieved_context_tokens_budget": 7372,
            "generation_tokens_reserve": 1024,
            "context_budget_ratio": 0.6,
            "rag_mode": "corrective",
        })), patch(
            "orchestrator.rag.pipeline.AdaptiveRAGPipeline",
            _FakePipeline,
        ), patch(
            "services.model_manager.ums_client.create_ums_embed_fn",
            return_value=None,
        ), patch.object(self._module.asyncio, "to_thread", side_effect=fake_to_thread):
            await self._module._ensure_rag_index_for_active_docs(step_name=None)

        assert init_kwargs["rag_mode"] == "corrective"
        assert init_kwargs["effective_context_tokens"] == 12288
        assert init_kwargs["retrieved_context_ratio"] == 0.6
