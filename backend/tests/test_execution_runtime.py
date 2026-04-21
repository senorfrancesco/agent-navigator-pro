import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import asyncio
from itertools import repeat
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.execution_runtime import (
    ExecutionDependencies,
    _DOCUMENTS_SUMMARY_CACHE,
    _extract_quality_signals_from_result,
    _execute_document_analysis,
    _execute_equipment,
    _execute_documents_summary,
    _run_graph,
    execute_orchestration,
)
from orchestrator.doc_question_heuristics import (
    build_doc_question_deterministic_fallback,
    citations_are_valid,
    compute_confidence_v1,
    extract_citation_ids,
    has_sufficient_evidence_v1,
)
from orchestrator.state_store import get_orchestration_state_store
from services.observability import render_metrics_text, reset_observability_metrics
from services.model_manager.ums_client import UMSBusyError


def setup_function():
    reset_observability_metrics()
    _DOCUMENTS_SUMMARY_CACHE.clear()


def teardown_function():
    reset_observability_metrics()
    _DOCUMENTS_SUMMARY_CACHE.clear()


def _strip_timing_footer(text: str) -> str:
    return str(text).split("\n\n---\nTiming / Quality", 1)[0]


@pytest.fixture(autouse=True)
def _stub_backend_classifier_resolution(monkeypatch):
    async def _noop_classifier_result(**kwargs):
        return None

    monkeypatch.setattr(
        "orchestrator.execution_runtime._resolve_classifier_result_for_request",
        _noop_classifier_result,
    )


def _build_minimal_deps() -> ExecutionDependencies:
    return ExecutionDependencies(
        infer_assistant_text=AsyncMock(return_value="Привет!"),
        build_prompt=lambda query, history, system_msg="": f"{system_msg}\n{query}",
        get_profile_system_prompt=lambda: "Ты ассистент.",
        has_retrieval_adapter=lambda: False,
        get_retrieval_embed_fn=lambda: None,
        get_knowledge_base_store=lambda: None,
        get_active_doc_ids=lambda: [],
        get_all_docs=lambda: [],
        get_active_docs=lambda: [],
        get_report_docs=lambda: [],
        resolve_target_doc_name=lambda query, docs: None,
        is_report_query=lambda query: False,
        ensure_rag_index_for_doc_ids=AsyncMock(return_value=False),
        get_rag_pipeline=lambda: None,
        build_sources_from_rag_result=lambda rag_result, rag_pipeline, max_sources=5: [],
        reindex_sources=lambda sources: sources,
        build_doc_question_deterministic_fallback=lambda **kwargs: {
            "answer_text": "Недостаточно данных.",
            "sources": [],
            "answer_mode": "insufficient_evidence",
            "fallback_type": kwargs.get("fallback_type", "insufficient_evidence"),
            "fallback_reason": kwargs.get("fallback_reason"),
            "confidence": 0.1,
            "confidence_label": "low",
            "confidence_method": "heuristic_v1",
            "confidence_version": "1",
        },
        render_doc_question_markdown=lambda payload: payload["answer_text"],
        build_doc_question_prompt_with_sources=lambda query, history, sources: query,
        citations_are_valid=lambda answer_text, source_count: True,
        needs_doc_question_regen=lambda answer_text, has_session_docs: False,
        extract_citation_ids=lambda answer_text: [],
        has_sufficient_evidence=lambda **kwargs: False,
        compute_confidence_v1=lambda sources, cited_ids, answer_mode, **kwargs: (0.4, "medium"),
        strip_model_source_sections=lambda answer_text: answer_text,
        to_host_path=lambda path: path,
        active_set_status_line=lambda: "Активный набор: 0 документов",
        attach_and_register_report=AsyncMock(),
        update_progress_box=AsyncMock(),
        clear_progress_box=AsyncMock(),
        is_cancelled=lambda: False,
    )


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_REQ_PDF = os.path.join(_REPO_ROOT, "documents", "Requirements.pdf")
_QUOTE_PDF = os.path.join(_REPO_ROOT, "documents", "Quotation_12.pdf")


def test_execute_orchestration_adds_state_ref_and_pending_action_metadata():
    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Сравни эти два документа",
                "session_id": "session-1",
                "runtime_mode": "specialized_tasks",
                "file_count": 2,
                "has_session_docs": True,
                "session_docs": {
                    "old.pdf": {"text": "Старая версия"},
                    "new.pdf": {"text": "Новая версия"},
                },
                "attachments_meta": [
                    {"name": "old.pdf", "path": "/tmp/old.pdf"},
                    {"name": "new.pdf", "path": "/tmp/new.pdf"},
                ],
                "active_doc_ids": ["old", "new"],
                "classifier_result": {
                    "intent": "general_chat",
                    "confidence": 0.44,
                    "margin": 0.005,
                    "needs_rag": False,
                },
            }
        )
    )

    assert response["route"] == "compare_documents"
    assert response["action_required"]["type"] == "choose_route"
    assert response["pending_action_id"]
    assert response["state_ref"].startswith("run:")
    assert response["run_id"]
    assert response["state_version"] == 2
    assert response["ui_effects"]["set_pending_action"]["type"] == "choose_route"


def test_execute_orchestration_runs_chat_handler_via_backend_dependencies():
    deps = _build_minimal_deps()

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет",
                "session_id": "session-2",
                "runtime_mode": "chat_only",
                "history": [],
            },
            deps=deps,
        )
    )

    assert response["executor"] == "chat"
    assert _strip_timing_footer(response["assistant_message"]) == "Привет!"
    deps.infer_assistant_text.assert_awaited_once()


def test_execute_orchestration_includes_telemetry_and_footer_for_chat_response():
    deps = _build_minimal_deps()

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет",
                "session_id": "session-telemetry-chat",
                "runtime_mode": "chat_only",
                "history": [],
            },
            deps=deps,
        )
    )

    telemetry = response.get("telemetry") or {}

    assert telemetry["executor"] == "chat"
    assert telemetry["elapsed_ms"] >= 0
    assert telemetry["started_at"]
    assert telemetry["completed_at"]
    assert telemetry["stage_timings"]
    assert telemetry["quality_summary"]
    assert "Timing / Quality" in response["assistant_message"]
    assert "Полный ответ" in response["assistant_message"]
    assert "Качество" in response["assistant_message"]


def test_execute_orchestration_attaches_model_execution_metadata_from_dependencies():
    deps = _build_minimal_deps()
    deps.get_model_execution_events = lambda: [
        {
            "role_key": "llm.default_chat",
            "primary_model_id": "qwen-14b-llm",
            "fallback_model_id": "qwen-14b-llm",
            "used_model_id": "qwen-14b-llm",
            "fallback_used": False,
            "attempt_count": 1,
            "status": "completed",
        }
    ]

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет",
                "session_id": "session-model-execution",
                "runtime_mode": "chat_only",
                "history": [],
            },
            deps=deps,
        )
    )

    assert _strip_timing_footer(response["assistant_message"]) == "Привет!"
    assert response["model_execution"]["fallback_used"] is False
    assert response["model_execution"]["events"][0]["used_model_id"] == "qwen-14b-llm"


def test_extract_quality_signals_accepts_model_execution_list():
    signals = _extract_quality_signals_from_result(
        executor="document_analysis",
        result={
            "assistant_message": "Готово.",
            "quality_signals": {"structured_output_ok": True, "parsed_items": 3},
            "model_execution": [
                {
                    "role_key": "llm.document_analysis",
                    "primary_model_id": "qwen-14b-llm",
                    "fallback_model_id": "qwen-7b",
                    "used_model_id": "qwen-7b",
                    "fallback_used": True,
                    "attempt_count": 2,
                    "status": "fallback_completed",
                }
            ],
        },
        response={},
    )

    assert signals["fallback_used"] is True
    assert signals["parsed_items"] == 3
    assert signals["report_generated"] is False


def test_execute_orchestration_general_chat_regenerates_on_language_contamination():
    deps = _build_minimal_deps()
    deps.infer_assistant_text = AsyncMock(
        side_effect=[
            "系统 готова. Система работает.",
            "Система работает.",
        ]
    )

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет! Ответь одной короткой фразой, что система работает.",
                "assistant_mode": "general_chat",
                "runtime_mode": "chat_only",
                "rag_scope": "off",
                "history": [],
            },
            deps=deps,
        )
    )

    assert _strip_timing_footer(response["assistant_message"]) == "Система работает."
    assert deps.infer_assistant_text.await_count == 2


def test_execute_orchestration_general_chat_skips_language_guard_for_translation_request():
    deps = _build_minimal_deps()
    deps.infer_assistant_text = AsyncMock(return_value="System works.")

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Переведи на английский: система работает.",
                "assistant_mode": "general_chat",
                "runtime_mode": "chat_only",
                "rag_scope": "off",
                "history": [],
            },
            deps=deps,
        )
    )

    assert _strip_timing_footer(response["assistant_message"]) == "System works."
    deps.infer_assistant_text.assert_awaited_once()


def test_execute_orchestration_general_chat_regenerates_on_english_answer_to_russian_query():
    deps = _build_minimal_deps()
    deps.infer_assistant_text = AsyncMock(
        side_effect=[
            "Hello, the system works.",
            "Система работает.",
        ]
    )

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет! Ответь одной короткой фразой, что система работает.",
                "assistant_mode": "general_chat",
                "runtime_mode": "chat_only",
                "rag_scope": "off",
                "history": [],
            },
            deps=deps,
        )
    )

    assert _strip_timing_footer(response["assistant_message"]) == "Система работает."
    assert deps.infer_assistant_text.await_count == 2


def test_execute_orchestration_resolves_classifier_result_in_backend_when_missing(monkeypatch):
    deps = _build_minimal_deps()
    captured = {}

    async def fake_classifier_result(*, query, effective_settings):
        captured["query"] = query
        captured["assistant_mode"] = effective_settings.get("assistant_mode")
        return {
            "intent": "general_chat",
            "confidence": 0.81,
            "margin": 0.42,
            "needs_rag": False,
            "source": "embedder",
        }

    def fake_decide(**kwargs):
        captured["classifier_result"] = kwargs.get("classifier_result")
        return {
            "route": "general_chat",
            "executor": "chat",
            "assistant_message": None,
            "action_required": None,
            "session_state_patch": {},
            "trace_id": kwargs.get("trace_id") or "trace-test",
            "reason": "classifier",
            "confidence": kwargs.get("classifier_result", {}).get("confidence", 0.0),
            "margin": kwargs.get("classifier_result", {}).get("margin", 0.0),
        }

    monkeypatch.setattr(
        "orchestrator.execution_runtime._resolve_classifier_result_for_request",
        fake_classifier_result,
    )
    monkeypatch.setattr("orchestrator.execution_runtime.decide_orchestration", fake_decide)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет",
                "runtime_mode": "auto",
                "history": [],
            },
            deps=deps,
        )
    )

    assert captured["query"] == "Привет"
    assert captured["assistant_mode"] == "general_chat"
    assert captured["classifier_result"]["intent"] == "general_chat"
    assert _strip_timing_footer(response["assistant_message"]) == "Привет!"


def test_execute_orchestration_adds_top_level_control_plane_fields():
    deps = _build_minimal_deps()

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что по загруженному документу?",
                "session_id": "session-3",
                "assistant_mode": "rag_qa",
                "rag_scope": "knowledge_base_rag",
                "knowledge_collection_id": "legal",
                "session_docs": {
                    "doc.txt": {"text": "штраф 10%"},
                },
                "active_doc_ids": ["doc.txt"],
                "history": [],
            },
            deps=deps,
        )
    )

    assert response["rag_scope"] == "knowledge_base_rag"
    assert response["knowledge_collection_id"] == "legal"
    assert response["source_scope_summary"] == "knowledge_base+session_overlay"


def test_execute_orchestration_exposes_top_level_control_plane_fields():
    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что указано в базе знаний?",
                "assistant_mode": "rag_qa",
                "rag_scope": "knowledge_base_rag",
                "knowledge_collection_id": "legal",
                "session_docs": {
                    "session-note.txt": {"text": "overlay"},
                },
                "has_session_docs": True,
                "active_doc_ids": ["session-note.txt"],
                "classifier_result": {
                    "intent": "general_chat",
                    "confidence": 0.2,
                    "margin": 0.1,
                    "needs_rag": True,
                },
            }
        )
    )

    assert response["rag_scope"] == "knowledge_base_rag"
    assert response["knowledge_collection_id"] == "legal"
    assert response["source_scope_summary"] == "knowledge_base+session_overlay"
    assert isinstance(response.get("model_profile"), (str, type(None)))


def test_execute_orchestration_marks_fresh_attachments_as_session_overlay():
    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что в свежем документе?",
                "assistant_mode": "rag_qa",
                "rag_scope": "knowledge_base_rag",
                "knowledge_collection_id": "legal",
                "attachments_meta": [{"name": "fresh.pdf", "path": "/tmp/fresh.pdf"}],
            }
        )
    )

    assert response["rag_scope"] == "knowledge_base_rag"
    assert response["source_scope_summary"] == "knowledge_base+session_overlay"


def test_execute_orchestration_doc_question_multihop_single_citation_falls_back(monkeypatch):
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {"document_id": "doc-a", "display_name": "a.pdf", "text": "Уведомление за 10 дней"},
        {"document_id": "doc-b", "display_name": "b.pdf", "text": "Штраф 10 процентов"},
    ]
    deps.get_active_docs = deps.get_all_docs
    deps.resolve_target_doc_name = lambda query, docs: None
    deps.get_rag_pipeline = lambda: SimpleNamespace(
        _indexed=True,
        top_k=5,
        retrieve=lambda query, top_k=None: SimpleNamespace(chunks=["chunk-a", "chunk-b"], metadata={"mode": "simple"}),
    )
    deps.build_sources_from_rag_result = lambda rag_result, rag_pipeline, max_sources=20: [
        {
            "source_id": 1,
            "document_id": "doc-a",
            "display_name": "a.pdf",
            "chunk_id": 0,
            "char_span": {"start_char": 0, "end_char": 100},
            "page": None,
            "quote": "Уведомление за 10 дней",
            "raw_score": 0.72,
            "normalized_score": 0.95,
            "grade": "excellent",
            "z_score": 0.8,
        },
        {
            "source_id": 2,
            "document_id": "doc-b",
            "display_name": "b.pdf",
            "chunk_id": 1,
            "char_span": {"start_char": 101, "end_char": 200},
            "page": None,
            "quote": "Штраф 10 процентов",
            "raw_score": 0.68,
            "normalized_score": 0.91,
            "grade": "excellent",
            "z_score": 0.7,
        },
    ]
    deps.infer_assistant_text = AsyncMock(return_value="Ответ [1]")
    deps.citations_are_valid = citations_are_valid
    deps.extract_citation_ids = extract_citation_ids
    deps.has_sufficient_evidence = has_sufficient_evidence_v1
    deps.compute_confidence_v1 = compute_confidence_v1
    deps.build_doc_question_deterministic_fallback = build_doc_question_deterministic_fallback
    deps.render_doc_question_markdown = lambda payload: payload["answer_text"]
    deps.build_doc_question_prompt_with_sources = lambda query, history, sources: "prompt"

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.execution_runtime.asyncio.to_thread", fake_to_thread)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Сравни условия уведомления и штрафа между документами",
                "runtime_mode": "specialized_tasks",
                "assistant_mode": "rag_qa",
                "rag_scope": "session_rag",
                "has_session_docs": True,
                "session_docs": {
                    "a.pdf": {"document_id": "doc-a", "text": "Уведомление за 10 дней"},
                    "b.pdf": {"document_id": "doc-b", "text": "Штраф 10 процентов"},
                },
                "active_doc_ids": ["doc-a", "doc-b"],
                "classifier_result": {
                    "intent": "document_question",
                    "confidence": 0.95,
                    "margin": 0.5,
                    "needs_rag": True,
                },
            },
            deps=deps,
        )
    )

    assert response["route"] == "document_question"
    assert "данных недостаточно" in response["assistant_message"].lower()


def test_execute_orchestration_doc_question_direct_single_source_evidence_avoids_fallback(monkeypatch):
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {"document_id": "doc-1", "display_name": "murka.txt", "text": "Мурка живет на пятом этаже"}
    ]
    deps.get_active_docs = deps.get_all_docs
    deps.resolve_target_doc_name = lambda query, docs: "murka.txt"
    deps.get_rag_pipeline = lambda: SimpleNamespace(
        _indexed=True,
        top_k=5,
        retrieve=lambda query, top_k=None: SimpleNamespace(
            chunks=["chunk-murka"],
            metadata={"mode": "simple"},
        ),
    )
    deps.build_sources_from_rag_result = lambda rag_result, rag_pipeline, max_sources=20: [
        {
            "source_id": 1,
            "document_id": "doc-1",
            "display_name": "murka.txt",
            "chunk_id": 0,
            "char_span": {"start_char": 0, "end_char": 100},
            "page": None,
            "quote": "Мурка живет на пятом этаже",
            "raw_score": 0.72,
            "normalized_score": 0.95,
            "grade": "excellent",
            "z_score": 0.8,
        },
    ]
    deps.infer_assistant_text = AsyncMock(
        side_effect=[
            "По документу недостаточно данных для короткого ответа, но есть указание на пятый этаж [1]",
            "5 [1]",
        ]
    )
    deps.citations_are_valid = citations_are_valid
    deps.extract_citation_ids = extract_citation_ids
    deps.has_sufficient_evidence = lambda **kwargs: False
    deps.compute_confidence_v1 = compute_confidence_v1
    deps.build_doc_question_deterministic_fallback = build_doc_question_deterministic_fallback
    deps.render_doc_question_markdown = lambda payload: payload["answer_text"]
    deps.build_doc_question_prompt_with_sources = lambda query, history, sources: "prompt"

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.execution_runtime.asyncio.to_thread", fake_to_thread)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "На каком этаже живет Мурка? Ответь только числом.",
                "runtime_mode": "specialized_tasks",
                "assistant_mode": "rag_qa",
                "rag_scope": "session_rag",
                "has_session_docs": True,
                "session_docs": {
                    "murka.txt": {"document_id": "doc-1", "text": "Мурка живет на пятом этаже"}
                },
                "active_doc_ids": ["doc-1"],
                "classifier_result": {
                    "intent": "document_question",
                    "confidence": 0.95,
                    "margin": 0.5,
                    "needs_rag": True,
                },
            },
            deps=deps,
        )
    )

    assert response["route"] == "document_question"
    assert _strip_timing_footer(response["assistant_message"]) == "5 [1]"
    assert "данных недостаточно" not in response["assistant_message"].lower()


def test_execute_orchestration_doc_question_single_source_regens_when_initial_citations_invalid(monkeypatch):
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {"document_id": "doc-1", "display_name": "murka.txt", "text": "Мурка живет на пятом этаже"}
    ]
    deps.get_active_docs = deps.get_all_docs
    deps.resolve_target_doc_name = lambda query, docs: "murka.txt"
    deps.get_rag_pipeline = lambda: SimpleNamespace(
        _indexed=True,
        top_k=5,
        retrieve=lambda query, top_k=None: SimpleNamespace(
            chunks=["chunk-murka"],
            metadata={"mode": "simple"},
        ),
    )
    deps.build_sources_from_rag_result = lambda rag_result, rag_pipeline, max_sources=20: [
        {
            "source_id": 1,
            "document_id": "doc-1",
            "display_name": "murka.txt",
            "chunk_id": 0,
            "char_span": {"start_char": 0, "end_char": 100},
            "page": None,
            "quote": "Мурка живет на пятом этаже",
            "raw_score": 0.05,
            "normalized_score": 0.5,
            "grade": None,
            "z_score": None,
        },
    ]
    deps.infer_assistant_text = AsyncMock(
        side_effect=[
            "Мурка живет на пятом этаже.",
            "Мурка живет на пятом этаже.",
            "5 [1]",
        ]
    )
    deps.citations_are_valid = citations_are_valid
    deps.extract_citation_ids = extract_citation_ids
    deps.has_sufficient_evidence = lambda **kwargs: False
    deps.compute_confidence_v1 = compute_confidence_v1
    deps.build_doc_question_deterministic_fallback = build_doc_question_deterministic_fallback
    deps.render_doc_question_markdown = lambda payload: payload["answer_text"]
    deps.build_doc_question_prompt_with_sources = lambda query, history, sources: "prompt"

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.execution_runtime.asyncio.to_thread", fake_to_thread)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "На каком этаже живет Мурка? Ответь только числом.",
                "runtime_mode": "specialized_tasks",
                "assistant_mode": "rag_qa",
                "rag_scope": "session_rag",
                "has_session_docs": True,
                "session_docs": {
                    "murka.txt": {"document_id": "doc-1", "text": "Мурка живет на пятом этаже"}
                },
                "active_doc_ids": ["doc-1"],
                "classifier_result": {
                    "intent": "document_question",
                    "confidence": 0.95,
                    "margin": 0.5,
                    "needs_rag": True,
                },
            },
            deps=deps,
        )
    )

    assert _strip_timing_footer(response["assistant_message"]) == "5 [1]"
    assert deps.infer_assistant_text.await_count == 3


def test_execute_orchestration_doc_question_single_source_builds_deterministic_grounded_answer_when_model_still_fails(monkeypatch):
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {"document_id": "doc-1", "display_name": "murka.txt", "text": "Мурка живет на пятом этаже"}
    ]
    deps.get_active_docs = deps.get_all_docs
    deps.resolve_target_doc_name = lambda query, docs: "murka.txt"
    deps.get_rag_pipeline = lambda: SimpleNamespace(
        _indexed=True,
        top_k=5,
        retrieve=lambda query, top_k=None: SimpleNamespace(
            chunks=["chunk-murka"],
            metadata={"mode": "simple"},
        ),
    )
    deps.build_sources_from_rag_result = lambda rag_result, rag_pipeline, max_sources=20: [
        {
            "source_id": 1,
            "document_id": "doc-1",
            "display_name": "murka.txt",
            "chunk_id": 0,
            "char_span": {"start_char": 0, "end_char": 100},
            "page": None,
            "quote": "Мурка живет на пятом этаже",
            "raw_score": 0.05,
            "normalized_score": 0.5,
            "grade": None,
            "z_score": None,
        },
    ]
    deps.infer_assistant_text = AsyncMock(
        side_effect=[
            "Мурка живет на пятом этаже.",
            "Мурка живет на пятом этаже.",
            "Не могу ответить с валидной ссылкой.",
        ]
    )
    deps.citations_are_valid = citations_are_valid
    deps.extract_citation_ids = extract_citation_ids
    deps.has_sufficient_evidence = lambda **kwargs: False
    deps.compute_confidence_v1 = compute_confidence_v1
    deps.build_doc_question_deterministic_fallback = build_doc_question_deterministic_fallback
    deps.render_doc_question_markdown = lambda payload: payload["answer_text"]
    deps.build_doc_question_prompt_with_sources = lambda query, history, sources: "prompt"

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.execution_runtime.asyncio.to_thread", fake_to_thread)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "На каком этаже живет Мурка? Ответь только числом.",
                "runtime_mode": "specialized_tasks",
                "assistant_mode": "rag_qa",
                "rag_scope": "session_rag",
                "has_session_docs": True,
                "session_docs": {
                    "murka.txt": {"document_id": "doc-1", "text": "Мурка живет на пятом этаже"}
                },
                "active_doc_ids": ["doc-1"],
                "classifier_result": {
                    "intent": "document_question",
                    "confidence": 0.95,
                    "margin": 0.5,
                    "needs_rag": True,
                },
            },
            deps=deps,
        )
    )

    assert _strip_timing_footer(response["assistant_message"]) == "5 [1]"


def test_execute_orchestration_doc_question_floor_query_does_not_take_first_unrelated_number(monkeypatch):
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {"document_id": "doc-1", "display_name": "murka.txt", "text": "п. 3: Мурка живет на 10 этаже 10 дней"}
    ]
    deps.get_active_docs = deps.get_all_docs
    deps.resolve_target_doc_name = lambda query, docs: "murka.txt"
    deps.get_rag_pipeline = lambda: SimpleNamespace(
        _indexed=True,
        top_k=5,
        retrieve=lambda query, top_k=None: SimpleNamespace(
            chunks=["chunk-murka"],
            metadata={"mode": "simple"},
        ),
    )
    deps.build_sources_from_rag_result = lambda rag_result, rag_pipeline, max_sources=20: [
        {
            "source_id": 1,
            "document_id": "doc-1",
            "display_name": "murka.txt",
            "chunk_id": 0,
            "char_span": {"start_char": 0, "end_char": 100},
            "page": None,
            "quote": "п. 3: Мурка живет на 10 этаже 10 дней",
            "raw_score": 0.05,
            "normalized_score": 0.5,
            "grade": None,
            "z_score": None,
        },
    ]
    deps.infer_assistant_text = AsyncMock(
        side_effect=[
            "Без валидной ссылки.",
            "Без валидной ссылки.",
            "Без валидной ссылки.",
        ]
    )
    deps.citations_are_valid = citations_are_valid
    deps.extract_citation_ids = extract_citation_ids
    deps.has_sufficient_evidence = lambda **kwargs: False
    deps.compute_confidence_v1 = compute_confidence_v1
    deps.build_doc_question_deterministic_fallback = build_doc_question_deterministic_fallback
    deps.render_doc_question_markdown = lambda payload: payload["answer_text"]
    deps.build_doc_question_prompt_with_sources = lambda query, history, sources: "prompt"

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.execution_runtime.asyncio.to_thread", fake_to_thread)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "На каком этаже живет Мурка? Ответь только числом.",
                "runtime_mode": "specialized_tasks",
                "assistant_mode": "rag_qa",
                "rag_scope": "session_rag",
                "has_session_docs": True,
                "session_docs": {
                    "murka.txt": {"document_id": "doc-1", "text": "п. 3: Мурка живет на 10 этаже 10 дней"}
                },
                "active_doc_ids": ["doc-1"],
                "classifier_result": {
                    "intent": "document_question",
                    "confidence": 0.95,
                    "margin": 0.5,
                    "needs_rag": True,
                },
            },
            deps=deps,
        )
    )

    assert _strip_timing_footer(response["assistant_message"]) == "10 [1]"


def test_run_graph_merges_errors_from_multiple_nodes():
    class _Workflow:
        async def astream(self, initial_state):
            yield {"node_a": {"errors": ["first"], "value": 1}}
            yield {"node_b": {"errors": ["second"], "other": 2}}

    final_state = asyncio.run(_run_graph(_Workflow(), {"errors": []}))

    assert final_state["errors"] == ["first", "second"]
    assert final_state["value"] == 1
    assert final_state["other"] == 2


def test_execute_orchestration_doc_question_records_rag_exception_fallback_metric(monkeypatch):
    deps = _build_minimal_deps()
    deps.has_retrieval_adapter = lambda: True
    deps.get_all_docs = lambda: [{"document_id": "doc-1", "display_name": "contract.pdf", "text": "Штраф 10 процентов"}]
    deps.get_active_docs = deps.get_all_docs
    deps.resolve_target_doc_name = lambda query, docs: None
    deps.ensure_rag_index_for_doc_ids = AsyncMock(return_value=True)
    deps.get_rag_pipeline = lambda: SimpleNamespace(
        _indexed=True,
        top_k=5,
        retrieve=lambda query, top_k=None: (_ for _ in ()).throw(RuntimeError("rag boom")),
    )

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.execution_runtime.asyncio.to_thread", fake_to_thread)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что сказано про штраф?",
                "runtime_mode": "specialized_tasks",
                "assistant_mode": "rag_qa",
                "rag_scope": "session_rag",
                "has_session_docs": True,
                "session_docs": {"contract.pdf": {"document_id": "doc-1", "text": "Штраф 10 процентов"}},
                "active_doc_ids": ["doc-1"],
                "classifier_result": {
                    "intent": "document_question",
                    "confidence": 0.95,
                    "margin": 0.5,
                    "needs_rag": True,
                },
            },
            deps=deps,
        )
    )

    assert "не удалось получить проверяемые источники" in response["assistant_message"].lower()
    metrics = render_metrics_text()
    assert "llm_tools_platform_fallback_events_total" in metrics
    assert 'component="doc_question"' in metrics
    assert 'fallback="rag_exception"' in metrics


def test_execute_orchestration_doc_question_handles_rag_result_without_metadata_and_filters_by_display_name(monkeypatch):
    deps = _build_minimal_deps()
    deps.has_retrieval_adapter = lambda: True
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.pdf",
            "text": "Штраф 10 процентов",
        }
    ]
    deps.get_active_docs = deps.get_all_docs
    deps.resolve_target_doc_name = lambda query, docs: "contract.pdf"
    deps.ensure_rag_index_for_doc_ids = AsyncMock(return_value=True)
    deps.get_rag_pipeline = lambda: SimpleNamespace(
        _indexed=True,
        top_k=5,
        retrieve=lambda query, top_k=None: SimpleNamespace(chunks=["chunk-1"]),
    )
    deps.build_sources_from_rag_result = lambda rag_result, rag_pipeline, max_sources=20: [
        {"document_id": "doc-1", "display_name": "contract.pdf", "source_id": 1},
        {"document_id": "doc-2", "display_name": "other.pdf", "source_id": 2},
    ]
    deps.reindex_sources = lambda sources: [
        {**source, "source_id": idx + 1} for idx, source in enumerate(sources)
    ]
    deps.build_doc_question_prompt_with_sources = lambda query, history, sources: "prompt with sources"
    deps.infer_assistant_text = AsyncMock(return_value="Ответ [1]")
    deps.extract_citation_ids = lambda answer_text: [1]
    deps.has_sufficient_evidence = lambda **kwargs: True
    deps.compute_confidence_v1 = lambda sources, cited_ids, answer_mode, **kwargs: (0.8, "high")
    deps.render_doc_question_markdown = lambda payload: payload["answer_text"]

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("orchestrator.execution_runtime.asyncio.to_thread", fake_to_thread)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что написано в contract.pdf про штраф?",
                "runtime_mode": "specialized_tasks",
                "session_docs": {
                    "contract.pdf": {
                        "document_id": "doc-1",
                        "text": "Штраф 10 процентов",
                        "path": "/tmp/contract.pdf",
                    }
                },
                "has_session_docs": True,
                "active_doc_ids": ["doc-1"],
                "classifier_result": {
                    "intent": "document_question",
                    "confidence": 0.95,
                    "margin": 0.7,
                    "needs_rag": True,
                },
            },
            deps=deps,
        )
    )

    assert _strip_timing_footer(response["assistant_message"]) == "Ответ [1]"
    assert response["sources"] == [
        {"document_id": "doc-1", "display_name": "contract.pdf", "source_id": 1}
    ]


def test_execute_orchestration_reuses_same_run_for_same_thread_and_persists_resume_snapshot():
    deps = _build_minimal_deps()

    first = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет",
                "thread_id": "thread-42",
                "history": [],
                "ui_state": {"control_plane_state": {"assistant_mode": "general_chat"}},
            },
            deps=deps,
        )
    )
    second = asyncio.run(
        execute_orchestration(
            {
                "message": "Еще вопрос",
                "thread_id": "thread-42",
                "history": [],
                "ui_state": {"control_plane_state": {"assistant_mode": "general_chat"}},
            },
            deps=deps,
        )
    )

    assert first["run_id"] == second["run_id"]
    assert first["state_ref"] == second["state_ref"]
    assert second["state_version"] > first["state_version"]

    stored = asyncio.run(get_orchestration_state_store().load_run(run_id=second["run_id"]))
    assert stored is not None
    assert "document_refs" not in (stored.resume_state_blob or {})


def test_execute_orchestration_reuses_same_run_for_same_idempotency_key():
    deps = _build_minimal_deps()

    first = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет",
                "session_id": "session-idem-1",
                "idempotency_key": "same-key",
                "history": [],
                "ui_state": {"control_plane_state": {"assistant_mode": "general_chat"}},
            },
            deps=deps,
        )
    )
    second = asyncio.run(
        execute_orchestration(
            {
                "message": "Повтор",
                "session_id": "session-idem-2",
                "idempotency_key": "same-key",
                "history": [],
                "ui_state": {"control_plane_state": {"assistant_mode": "general_chat"}},
            },
            deps=deps,
        )
    )

    assert first["run_id"] == second["run_id"]
    stored = asyncio.run(get_orchestration_state_store().load_run(run_id=second["run_id"]))
    assert stored is not None
    assert stored.idempotency_key == "same-key"


def test_execute_orchestration_persists_document_refs_not_inline_documents():
    deps = _build_minimal_deps()

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Что в документе?",
                "thread_id": "thread-doc-refs",
                "runtime_mode": "chat_only",
                "history": [],
                "ui_state": {
                    "documents_by_id": {
                        "doc-1": {
                            "document_id": "doc-1",
                            "display_name": "contract.pdf",
                            "version": 1,
                            "path": "/tmp/contract.pdf",
                            "text": "Штраф 10 процентов",
                            "uploaded_at": 1.0,
                            "source_message_id": None,
                        }
                    },
                    "active_doc_ids": ["doc-1"],
                    "control_plane_state": {"assistant_mode": "specific_tasks"},
                },
                "session_docs": {
                    "contract.pdf": {
                        "document_id": "doc-1",
                        "display_name": "contract.pdf",
                        "path": "/tmp/contract.pdf",
                        "text": "Штраф 10 процентов",
                        "version": 1,
                    }
                },
                "active_doc_ids": ["doc-1"],
            },
            deps=deps,
        )
    )

    stored = asyncio.run(get_orchestration_state_store().load_run(run_id=response["run_id"]))

    assert stored is not None
    assert "documents_by_id" not in (stored.resume_state_blob or {})
    assert "session_docs" not in (stored.resume_state_blob or {})
    assert stored.resume_state_blob["document_refs"] == [
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


def test_execute_orchestration_routes_kb_doc_question_through_unified_backend_core(monkeypatch):
    deps = _build_minimal_deps()
    deps.has_retrieval_adapter = lambda: True
    deps.get_retrieval_embed_fn = lambda: object()
    deps.get_knowledge_base_store = lambda: object()
    deps.infer_assistant_text = AsyncMock(return_value="Ответ по базе [1]")
    deps.citations_are_valid = lambda answer_text, source_count: True
    deps.extract_citation_ids = lambda answer_text: [1]
    deps.has_sufficient_evidence = lambda **kwargs: True
    deps.compute_confidence_v1 = lambda sources, cited_ids, answer_mode, **kwargs: (0.9, "high")
    deps.render_doc_question_markdown = lambda payload: payload["answer_text"]

    monkeypatch.setattr(
        "orchestrator.execution_runtime.retrieve_merged_chunks",
        lambda **kwargs: {
            "chunks": [
                {
                    "chunk_id": "kb-doc:0",
                    "document_id": "kb-doc",
                    "display_name": "kb_policy.txt",
                    "collection_id": "legal",
                    "source_origin": "knowledge_base",
                    "text": "Штраф за просрочку составляет 3 процента.",
                    "metadata_json": {"start_char": 0, "end_char": 42},
                    "raw_score": 0.91,
                    "normalized_score": 0.88,
                }
            ],
            "source_scope_summary": "knowledge_base",
        },
    )

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Какой штраф за просрочку?",
                "assistant_mode": "rag_qa",
                "runtime_mode": "specialized_tasks",
                "rag_scope": "knowledge_base_rag",
                "knowledge_collection_id": "legal",
                "history": [],
            },
            deps=deps,
        )
    )

    assert response["route"] == "document_question"
    assert response["executor"] == "document_question"
    assert _strip_timing_footer(response["assistant_message"]) == "Ответ по базе [1]"
    assert response["sources"][0]["source_origin"] == "knowledge_base"
    assert response["sources"][0]["collection_id"] == "legal"


def test_execute_documents_summary_updates_single_progress_box_per_chunk():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("Первый абзац. " * 180) + "\n\n" + ("Второй абзац. " * 180),
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=["chunk-1", "chunk-2", "merged", "global"])

    with patch("orchestrator.execution_runtime._format_elapsed_seconds", return_value="12.3 сек."):
        response = asyncio.run(
            _execute_documents_summary(
                query="Сделай сводку",
                history=[],
                ui_locale="ru-RU",
                deps=deps,
            )
        )

    assert "## Сводка по документам" in response["assistant_message"]
    assert "Время выполнения:** 12.3 сек." in response["assistant_message"]
    assert deps.update_progress_box.await_count == 5
    assert deps.update_progress_box.await_args_list[0].kwargs == {
        "key": "documents_summary_progress",
        "title": "Суммаризация фрагментов",
        "content": "1/2",
    }
    assert deps.update_progress_box.await_args_list[1].kwargs == {
        "key": "documents_summary_progress",
        "title": "Суммаризация фрагментов",
        "content": "2/2",
    }
    assert deps.update_progress_box.await_args_list[2].kwargs["title"] == "Промежуточная сводка"
    assert "Готов документ: contract.txt" in deps.update_progress_box.await_args_list[2].kwargs["content"]
    assert deps.update_progress_box.await_args_list[3].kwargs == {
        "key": "documents_summary_progress",
        "title": "Формирование итоговой сводки",
        "content": "Формирую общую сводку по уже собранным промежуточным результатам.",
    }
    assert deps.update_progress_box.await_args_list[4].kwargs == {
        "key": "documents_summary_progress",
        "title": "Суммаризация завершена",
        "content": "Готово: обработано 2/2 фрагментов.",
    }
    deps.clear_progress_box.assert_not_awaited()


def test_execute_documents_summary_localizes_progress_for_english():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("First paragraph. " * 180) + "\n\n" + ("Second paragraph. " * 180),
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=["chunk-1", "chunk-2", "merged", "global"])

    with patch("orchestrator.execution_runtime._format_elapsed_seconds", return_value="12.3 sec."):
        response = asyncio.run(
            _execute_documents_summary(
                query="Build a summary",
                history=[],
                ui_locale="en-US",
                deps=deps,
            )
        )

    assert "## Document summary" in response["assistant_message"]
    assert "**Execution time:** 12.3 sec." in response["assistant_message"]
    assert deps.update_progress_box.await_args_list[0].kwargs == {
        "key": "documents_summary_progress",
        "title": "Summarizing document chunks",
        "content": "1/2",
    }
    assert deps.update_progress_box.await_args_list[1].kwargs == {
        "key": "documents_summary_progress",
        "title": "Summarizing document chunks",
        "content": "2/2",
    }
    assert deps.update_progress_box.await_args_list[2].kwargs["title"] == "Intermediate summary"
    assert "Document ready: contract.txt" in deps.update_progress_box.await_args_list[2].kwargs["content"]
    assert deps.update_progress_box.await_args_list[3].kwargs == {
        "key": "documents_summary_progress",
        "title": "Building the final summary",
        "content": "Building the overall summary from the intermediate results.",
    }
    assert deps.update_progress_box.await_args_list[4].kwargs == {
        "key": "documents_summary_progress",
        "title": "Summarization completed",
        "content": "Done: processed 2/2 chunks.",
    }


def test_execute_documents_summary_uses_low_vram_stage_policy():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("Первый абзац. " * 180) + "\n\n" + ("Второй абзац. " * 180),
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=["chunk-1", "chunk-2", "merged", "global"])

    asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "low-vram"},
            deps=deps,
        )
    )

    first_call = deps.infer_assistant_text.await_args_list[0].kwargs
    merge_call = deps.infer_assistant_text.await_args_list[2].kwargs
    global_call = deps.infer_assistant_text.await_args_list[3].kwargs

    assert first_call["device_mode"] == "cpu"
    assert first_call["allow_sync_retry"] is False
    assert first_call["raise_on_error"] is True
    assert first_call["summary_stage"] == "chunk"
    assert merge_call["summary_stage"] == "merge"
    assert merge_call["enforced_overrides"]["max_tokens"] <= 320
    assert global_call["summary_stage"] == "global"
    assert global_call["enforced_overrides"]["max_tokens"] <= 384


def test_execute_documents_summary_returns_partial_response_when_global_summary_fails():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("Первый абзац. " * 180) + "\n\n" + ("Второй абзац. " * 180),
        }
    ]
    deps.infer_assistant_text = AsyncMock(
        side_effect=["chunk-1", "chunk-2", "merged-summary", RuntimeError("UMS timeout")]
    )

    response = asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "prefer-gpu"},
            deps=deps,
        )
    )

    assert "merged-summary" in response["assistant_message"]
    assert "The overall summary is unavailable" in response["assistant_message"]


def test_execute_documents_summary_reuses_cached_chunk_summaries():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("Первый абзац. " * 180) + "\n\n" + ("Второй абзац. " * 180),
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=["chunk-1", "chunk-2", "merged", "global", "merged-2", "global-2"])

    first = asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "low-vram", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )
    second = asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "low-vram", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )

    assert "merged" in first["assistant_message"]
    assert "merged-2" in second["assistant_message"]
    assert deps.infer_assistant_text.await_count == 6
    first_two = [call.kwargs["summary_stage"] for call in deps.infer_assistant_text.await_args_list[:2]]
    second_two = [call.kwargs["summary_stage"] for call in deps.infer_assistant_text.await_args_list[4:6]]
    assert first_two == ["chunk", "chunk"]
    assert second_two == ["merge", "global"]


def test_execute_documents_summary_applies_budget_guard_before_merge_and_global():
    deps = _build_minimal_deps()
    paragraphs = ["Абзац %s. %s" % (idx, "Текст " * 260) for idx in range(1, 7)]
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": "\n\n".join(paragraphs),
        }
    ]
    long_chunk_summary = "summary " * 220
    deps.infer_assistant_text = AsyncMock(
        side_effect=list(repeat(long_chunk_summary, 6)) + ["merge-1", "merge-2", "global-summary"]
    )

    asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={
                "device_mode": "prefer-gpu",
                "resolved_model_id": "test-model",
                "runtime_admission": {"documents_summary": {"global": "requires_degraded"}},
            },
            deps=deps,
        )
    )

    merge_calls = [call for call in deps.infer_assistant_text.await_args_list if call.kwargs["summary_stage"] == "merge"]
    global_calls = [call for call in deps.infer_assistant_text.await_args_list if call.kwargs["summary_stage"] == "global"]

    assert len(merge_calls) >= 2
    assert len(global_calls) == 1
    for call in merge_calls:
        assert len(call.args[0]) < 5000
    assert len(global_calls[0].args[0]) < 6000


def test_execute_documents_summary_uses_fast_final_merge_when_payload_is_safe():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("А" * 2000) + "\n\n" + ("Б" * 2000),
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=["chunk-1", "chunk-2", "doc-final", "global"])

    response = asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "prefer-gpu", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )

    stages = [call.kwargs["summary_stage"] for call in deps.infer_assistant_text.await_args_list]
    assert stages == ["chunk", "chunk", "merge", "global"]
    assert response["execution_metadata"]["reduce_strategy"] == "fast_final_merge"
    assert response["execution_metadata"]["reduce_reason"] == "fits_final_budget"
    assert response["execution_metadata"]["reduce_levels_used"] == 0
    assert response["execution_metadata"]["reduce_groups_total"] == 0


def test_execute_documents_summary_uses_hierarchical_merge_on_low_vram_even_if_payload_fits():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("А" * 2000) + "\n\n" + ("Б" * 2000),
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=["chunk-1", "chunk-2", "merge-1", "global"])

    response = asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "low-vram", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )

    stages = [call.kwargs["summary_stage"] for call in deps.infer_assistant_text.await_args_list]
    assert stages == ["chunk", "chunk", "merge", "global"]
    assert response["execution_metadata"]["reduce_strategy"] == "hierarchical_merge"
    assert response["execution_metadata"]["reduce_reason"] == "hardware_policy"
    assert response["execution_metadata"]["reduce_levels_used"] == 1
    assert response["execution_metadata"]["reduce_groups_total"] == 1


def test_execute_documents_summary_uses_hierarchy_when_margin_blocks_fast_path(monkeypatch):
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("А" * 2000) + "\n\n" + ("Б" * 2000),
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=["chunk-1", "chunk-2", "merge-1", "global"])

    def _estimate_tokens(text: str) -> int:
        if "СУММАРИЗАЦИИ ФРАГМЕНТОВ" in text or "CHUNK SUMMARIES" in text:
            return 600
        return 32

    with patch("orchestrator.execution_runtime._estimate_prompt_tokens", side_effect=_estimate_tokens):
        response = asyncio.run(
            _execute_documents_summary(
                query="Сделай сводку",
                history=[],
                effective_settings={"device_mode": "prefer-gpu", "resolved_model_id": "test-model"},
                deps=deps,
            )
        )

    assert response["execution_metadata"]["reduce_strategy"] == "hierarchical_merge"
    assert response["execution_metadata"]["reduce_reason"] == "budget_overflow"
    assert response["execution_metadata"]["final_admission_margin_tokens"] > 0
    assert response["execution_metadata"]["final_admission_estimated_tokens"] > 0
    assert response["execution_metadata"]["final_admission_budget_tokens"] >= response["execution_metadata"]["final_admission_margin_tokens"]


def test_execute_documents_summary_prefers_fast_path_when_chars_are_large_but_token_estimate_is_safe():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("А" * 2000) + "\n\n" + ("Б" * 2000),
        }
    ]
    long_summary = "X" * 2200
    deps.infer_assistant_text = AsyncMock(side_effect=[long_summary, long_summary, "doc-final", "global"])

    with patch("orchestrator.execution_runtime._estimate_prompt_tokens", return_value=48):
        response = asyncio.run(
            _execute_documents_summary(
                query="Сделай сводку",
                history=[],
                effective_settings={"device_mode": "prefer-gpu", "resolved_model_id": "test-model"},
                deps=deps,
            )
        )

    assert response["execution_metadata"]["reduce_strategy"] == "fast_final_merge"
    assert response["execution_metadata"]["reduce_reason"] == "fits_final_budget"
    assert response["execution_metadata"]["fast_path_eligible"] is True
    assert "final_prompt_tokens_est" in response["execution_metadata"]
    assert "group_prompt_tokens_est" in response["execution_metadata"]


def test_execute_documents_summary_shadow_mode_records_trace_and_diff_metric(monkeypatch):
    monkeypatch.setenv("SUMMARY_REDUCE_STRATEGY_SHADOW_MODE", "1")
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": "\n\n".join([("А" * 2000) for _ in range(5)]),
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=[
        "chunk-1",
        "chunk-2",
        "chunk-3",
        "chunk-4",
        "chunk-5",
        "doc-final",
        "global",
    ])

    with patch("orchestrator.execution_runtime._estimate_prompt_tokens", return_value=48):
        response = asyncio.run(
            _execute_documents_summary(
                query="Сделай сводку",
                history=[],
                effective_settings={"device_mode": "prefer-gpu", "resolved_model_id": "test-model"},
                deps=deps,
            )
        )

    metadata = response["execution_metadata"]
    assert metadata["executed_strategy"] == "fast_final_merge"
    assert metadata["recommended_strategy"] == "fast_final_merge"
    assert metadata["shadow_baseline_strategy"] == "hierarchical_merge"
    assert metadata["would_skip_levels"] == 1
    assert metadata["estimated_token_saving"] > 0
    assert metadata["reduce_decisions"]
    assert metadata["reduce_decisions"][0]["executed_strategy"] == "fast_final_merge"
    assert metadata["reduce_decisions"][0]["shadow_baseline_strategy"] == "hierarchical_merge"

    metrics = render_metrics_text()
    assert 'llm_tools_platform_summary_strategy_shadow_diff_total{component="documents_summary"' in metrics
    assert " 1.0" in metrics


def test_execute_documents_summary_shadow_mode_does_not_increment_metric_when_strategies_match(monkeypatch):
    monkeypatch.setenv("SUMMARY_REDUCE_STRATEGY_SHADOW_MODE", "1")
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": "Первый абзац.\n\nВторой абзац.",
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=["chunk-1", "global"])

    asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "prefer-gpu", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )

    metrics = render_metrics_text()
    assert "llm_tools_platform_summary_strategy_shadow_diff_total" not in metrics


def test_execute_documents_summary_updates_progress_with_partial_results():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("Первый абзац. " * 180) + "\n\n" + ("Второй абзац. " * 180),
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=["chunk-1", "chunk-2", "merged", "global"])

    asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "prefer-gpu", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )

    progress_titles = [call.kwargs["title"] for call in deps.update_progress_box.await_args_list]
    progress_contents = [call.kwargs["content"] for call in deps.update_progress_box.await_args_list]

    assert progress_titles[:2] == ["Summarizing document chunks", "Summarizing document chunks"]
    assert "Document ready: contract.txt" in progress_contents[2]
    assert "merged" in progress_contents[2]
    assert "Building the overall summary" in progress_contents[3]


def test_execute_documents_summary_emits_degraded_notice_when_stage_requires_reduced_context():
    deps = _build_minimal_deps()
    paragraphs = ["Абзац %s. %s" % (idx, "Текст " * 320) for idx in range(1, 7)]
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": "\n\n".join(paragraphs),
        }
    ]
    deps.infer_assistant_text = AsyncMock(
        side_effect=list(repeat("chunk-summary", 6)) + ["merge-1", "merge-2", "merge-3", "global-summary"]
    )

    response = asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={
                "device_mode": "prefer-gpu",
                "resolved_model_id": "test-model",
                "runtime_admission": {"documents_summary": {"global": "requires_degraded"}},
            },
            deps=deps,
        )
    )

    assert response["execution_metadata"]["degraded"] is True
    assert response["execution_metadata"]["policy"] == "reduced_context"
    assert "resource limits" in response["assistant_message"]


def test_execute_documents_summary_retries_global_only_after_policy_change():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("Первый абзац. " * 180) + "\n\n" + ("Второй абзац. " * 180),
        }
    ]
    deps.infer_assistant_text = AsyncMock(
        side_effect=["chunk-1", "chunk-2", "merged-summary", RuntimeError("OOM"), "global-summary-degraded"]
    )

    response = asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "prefer-gpu", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )

    global_calls = [call.kwargs for call in deps.infer_assistant_text.await_args_list if call.kwargs["summary_stage"] == "global"]

    assert len(global_calls) == 2
    assert global_calls[1]["enforced_overrides"]["max_tokens"] < global_calls[0]["enforced_overrides"]["max_tokens"]
    assert response["execution_metadata"]["degraded"] is True
    assert response["execution_metadata"]["stage"] == "global"
    assert "global-summary-degraded" in response["assistant_message"]


def test_execute_documents_summary_cache_entry_expires_by_ttl(monkeypatch):
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("Первый абзац. " * 180) + "\n\n" + ("Второй абзац. " * 180),
        }
    ]
    deps.infer_assistant_text = AsyncMock(
        side_effect=["chunk-1", "chunk-2", "merged", "global", "chunk-1b", "chunk-2b", "merged-2", "global-2"]
    )
    monkeypatch.setenv("DOCUMENTS_SUMMARY_CACHE_TTL_S", "1")

    base_time = 1000.0
    monkeypatch.setattr("orchestrator.execution_runtime.time.time", lambda: base_time)
    asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "low-vram", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )

    monkeypatch.setattr("orchestrator.execution_runtime.time.time", lambda: base_time + 2.0)
    asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "low-vram", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )

    stages = [call.kwargs["summary_stage"] for call in deps.infer_assistant_text.await_args_list]
    assert stages[:4] == ["chunk", "chunk", "merge", "global"]
    assert stages[4:8] == ["chunk", "chunk", "merge", "global"]


def test_execute_documents_summary_records_stage_metrics_for_cache_and_degraded_paths():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("Первый абзац. " * 180) + "\n\n" + ("Второй абзац. " * 180),
        }
    ]
    deps.infer_assistant_text = AsyncMock(
        side_effect=["chunk-1", "chunk-2", "merged-summary", RuntimeError("UMS timeout")]
    )

    asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "prefer-gpu", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )
    deps.infer_assistant_text = AsyncMock(side_effect=["merged-2", "global-2"])
    asyncio.run(
        _execute_documents_summary(
            query="Сделай сводку",
            history=[],
            effective_settings={"device_mode": "prefer-gpu", "resolved_model_id": "test-model"},
            deps=deps,
        )
    )

    metrics = render_metrics_text()
    assert 'fallback="documents_summary_global_degraded"' in metrics
    assert 'fallback="documents_summary_chunk_cache_hit"' in metrics


def test_execute_documents_summary_raises_cancelled_before_global_stage():
    deps = _build_minimal_deps()
    deps.get_all_docs = lambda: [
        {
            "document_id": "doc-1",
            "display_name": "contract.txt",
            "text": ("Первый абзац. " * 180) + "\n\n" + ("Второй абзац. " * 180),
        }
    ]
    deps.infer_assistant_text = AsyncMock(side_effect=["chunk-1", "chunk-2", "merged"])
    state = {"calls": 0}

    def _cancel_after_merge():
        state["calls"] += 1
        return state["calls"] >= 4

    deps.is_cancelled = _cancel_after_merge

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            _execute_documents_summary(
                query="Сделай сводку",
                history=[],
                deps=deps,
            )
        )


def test_execute_orchestration_returns_busy_status_for_ums_saturation(monkeypatch):
    deps = _build_minimal_deps()

    async def _busy_chat(**kwargs):
        raise UMSBusyError("429 saturated")

    monkeypatch.setattr("orchestrator.execution_runtime._execute_general_chat", _busy_chat)

    response = asyncio.run(
        execute_orchestration(
            {
                "message": "Привет",
                "runtime_mode": "chat_only",
                "assistant_mode": "general_chat",
                "history": [],
            },
            deps=deps,
        )
    )

    assert response["execution_metadata"]["status"] == "busy"
    assert "The model is busy" in response["assistant_message"]


def test_execute_document_analysis_surfaces_partial_summary_metadata():
    deps = _build_minimal_deps()

    with patch("orchestrator.workflows.document_analysis.create_analysis_graph", return_value=SimpleNamespace()), \
         patch("orchestrator.execution_runtime._run_graph", new=AsyncMock(return_value={
             "final_report": "# Report\n\npartial",
             "errors": [],
             "summary_metadata": {
                 "degraded": True,
                 "completed_stages": ["chunk_summary", "group_merge"],
                 "final_synthesis_status": "failed",
                 "degraded_reason": "retry_exhausted",
                 "degraded_stage": "final_synthesis",
             },
         })):
        result = asyncio.run(
            _execute_document_analysis(
                query="Сделай глубокий анализ документа.",
                new_files=[{"name": "big.pdf", "path": "/tmp/big.pdf"}],
                session_docs={},
                effective_settings={"runtime_budget_metadata": {"tier": 1}},
                deps=deps,
            )
        )

    assert result["execution_metadata"]["status"] == "degraded"
    assert result["execution_metadata"]["degraded"] is True
    assert result["execution_metadata"]["completed_stages"] == ["chunk_summary", "group_merge"]
    assert result["execution_metadata"]["final_synthesis_status"] == "failed"
    deps.attach_and_register_report.assert_awaited_once()


def test_execute_document_analysis_passes_prefetched_text_and_analysis_goal_to_workflow():
    deps = _build_minimal_deps()
    captured_state = {}

    async def _fake_run_graph(_workflow, state):
        captured_state.update(state)
        return {
            "final_report": "# Report\n\nok",
            "errors": [],
            "summary_metadata": {
                "degraded": False,
                "completed_stages": ["classify_and_load", "summarize"],
                "final_synthesis_status": "completed",
            },
        }

    with patch("orchestrator.workflows.document_analysis.create_analysis_graph", return_value=SimpleNamespace()), \
         patch("orchestrator.execution_runtime._run_graph", new=AsyncMock(side_effect=_fake_run_graph)):
        result = asyncio.run(
            _execute_document_analysis(
                query="Сфокусируйся на процессорах и памяти.",
                new_files=[{"name": "big.pdf", "path": "/tmp/big.pdf"}],
                session_docs={
                    "big.pdf": {
                        "path": "/tmp/big.pdf",
                        "text": "Требование: 2 процессора, 128 ГБ RAM",
                    }
                },
                effective_settings={"runtime_budget_metadata": {"tier": 1}},
                deps=deps,
                ui_locale="en",
            )
        )

    assert captured_state["analysis_goal"] == "Сфокусируйся на процессорах и памяти."
    assert captured_state["prefetched_full_text"] == "Требование: 2 процессора, 128 ГБ RAM"
    assert captured_state["ui_locale"] == "en"
    assert captured_state["runtime_context"]["ui_locale"] == "en"
    assert result["execution_metadata"]["status"] == "completed"


def test_execute_document_analysis_summarizes_model_execution_event_list():
    deps = _build_minimal_deps()

    with patch("orchestrator.workflows.document_analysis.create_analysis_graph", return_value=SimpleNamespace()), \
         patch("orchestrator.execution_runtime._run_graph", new=AsyncMock(return_value={
             "final_report": "# Report\n\nok",
             "errors": [],
             "summary_metadata": {
                 "degraded": False,
                 "completed_stages": ["classify_and_load", "extract", "summarize", "report"],
                 "final_synthesis_status": "completed",
             },
             "model_execution": [
                 {
                     "role_key": "llm.document_analysis",
                     "primary_model_id": "qwen-14b-llm",
                     "fallback_model_id": "qwen-7b",
                     "used_model_id": "qwen-14b-llm",
                     "fallback_used": False,
                     "attempt_count": 1,
                     "status": "completed",
                 },
                 {
                     "role_key": "llm.document_analysis",
                     "primary_model_id": "qwen-14b-llm",
                     "fallback_model_id": "qwen-7b",
                     "used_model_id": "qwen-7b",
                     "fallback_used": True,
                     "attempt_count": 2,
                     "status": "fallback_completed",
                 },
             ],
         })):
        result = asyncio.run(
            _execute_document_analysis(
                query="Сделай глубокий анализ документа.",
                new_files=[{"name": "big.pdf", "path": "/tmp/big.pdf"}],
                session_docs={},
                effective_settings={"runtime_budget_metadata": {"tier": 1}},
                deps=deps,
            )
        )

    assert isinstance(result["model_execution"], dict)
    assert result["model_execution"]["fallback_used"] is True
    assert result["model_execution"]["attempt_count"] == 3
    assert result["model_execution"]["events"][0]["role_key"] == "llm.document_analysis"


def test_execute_equipment_tolerates_non_dict_summary_metadata():
    deps = _build_minimal_deps()

    with patch("orchestrator.workflows.equipment.create_equipment_graph", return_value=SimpleNamespace()), \
         patch("orchestrator.execution_runtime._run_graph", new=AsyncMock(return_value={
             "final_report": "# Equipment Report\n\nok",
             "errors": [],
             "analysis_results": [{"result": "PASS"}],
             "items_1": [{"name": "ТЗ"}],
             "items_2": [{"name": "КП"}],
             "summary_metadata": [],
         })):
        result = asyncio.run(
            _execute_equipment(
                query="Сравни ТЗ и КП",
                new_files=[
                    {"name": "req.pdf", "path": "/tmp/req.pdf"},
                    {"name": "quote.pdf", "path": "/tmp/quote.pdf"},
                ],
                session_docs={
                    "req.pdf": {"path": "/tmp/req.pdf", "text": "техническое задание"},
                    "quote.pdf": {"path": "/tmp/quote.pdf", "text": "коммерческое предложение"},
                },
                history=[],
                effective_settings={},
                deps=deps,
            )
        )

    assert result["execution_metadata"]["status"] == "completed"
    assert result["execution_metadata"]["degraded"] is False
    assert result["execution_metadata"]["completed_stages"] == []
    assert result["execution_metadata"]["final_synthesis_status"] == "unknown"
    deps.attach_and_register_report.assert_awaited_once()


def test_execute_equipment_explicit_tool_rejects_single_document_instead_of_fallback_chat():
    deps = _build_minimal_deps()

    with patch("orchestrator.execution_runtime._execute_general_chat", new=AsyncMock()) as fallback_chat:
        result = asyncio.run(
            _execute_equipment(
                query="Разбери один загруженный документ по оборудованию.",
                new_files=[
                    {"name": "Requirements.pdf", "path": "/tmp/Requirements.pdf"},
                ],
                session_docs={
                    "Requirements.pdf": {"path": "/tmp/Requirements.pdf", "text": "Техническое задание"},
                },
                history=[],
                effective_settings={},
                deps=deps,
                strict_tool_contract=True,
                ui_locale="en-US",
            )
        )

    fallback_chat.assert_not_awaited()
    assert result["execution_metadata"]["status"] == "failed"
    assert result["execution_metadata"]["reason"] == "equipment_requires_two_documents"
    assert "Use the document analysis tool" in result["assistant_message"]


@pytest.mark.integration
@pytest.mark.skipif(not (os.path.exists(_REQ_PDF) and os.path.exists(_QUOTE_PDF)), reason="equipment PDF fixtures not found")
def test_execute_equipment_real_pdfs_requirements_vs_quotation():
    deps = _build_minimal_deps()

    result = asyncio.run(
        _execute_equipment(
            query="Сравни требования из Requirements.pdf с предложением из Quotation_12.pdf. Сфокусируйся на процессорах, памяти, накопителях и пропущенных обязательных характеристиках.",
            new_files=[
                {"name": "Requirements.pdf", "path": _REQ_PDF},
                {"name": "Quotation_12.pdf", "path": _QUOTE_PDF},
            ],
            session_docs={
                "Requirements.pdf": {"path": _REQ_PDF, "text": ""},
                "Quotation_12.pdf": {"path": _QUOTE_PDF, "text": ""},
            },
            history=[],
            effective_settings={},
            deps=deps,
        )
    )

    assert result["execution_metadata"]["status"] in {"completed", "degraded"}
    assert "assistant_message" in result
    deps.attach_and_register_report.assert_awaited_once()
