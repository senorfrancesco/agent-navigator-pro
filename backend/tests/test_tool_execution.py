from __future__ import annotations

import asyncio

import pytest

from orchestrator.execution_runtime import ExecutionDependencies
from orchestrator.tool_execution import (
    apply_tool_contract_to_payload,
    build_accepted_tool_job_response,
    build_tool_job_status_response,
    get_tool_job_result,
    submit_async_tool_job,
)
from orchestrator.tool_job_store import get_tool_job_store


def _stub_execution_dependencies() -> ExecutionDependencies:
    async def _async_none(*args, **kwargs):
        return None

    def _sync_none(*args, **kwargs):
        return None

    return ExecutionDependencies(
        infer_assistant_text=_async_none,
        build_prompt=_sync_none,
        get_profile_system_prompt=_sync_none,
        has_retrieval_adapter=lambda: False,
        get_retrieval_embed_fn=_sync_none,
        get_knowledge_base_store=_sync_none,
        get_active_doc_ids=lambda: [],
        get_all_docs=lambda: [],
        get_active_docs=lambda: [],
        get_report_docs=lambda: [],
        resolve_target_doc_name=_sync_none,
        is_report_query=lambda query: False,
        ensure_rag_index_for_doc_ids=_async_none,
        get_rag_pipeline=_sync_none,
        build_sources_from_rag_result=lambda rag_result, rag_pipeline, max_sources=5: [],
        reindex_sources=lambda sources: sources,
        build_doc_question_deterministic_fallback=lambda **kwargs: {},
        render_doc_question_markdown=lambda payload: "",
        build_doc_question_prompt_with_sources=lambda query, history, sources: "",
        citations_are_valid=lambda answer, sources: False,
        needs_doc_question_regen=lambda answer_text, has_session_docs: False,
        extract_citation_ids=lambda answer: [],
        has_sufficient_evidence=lambda **kwargs: False,
        compute_confidence_v1=lambda sources, cited_ids, answer_mode, **kwargs: (0.0, "low"),
        strip_model_source_sections=lambda answer_text: answer_text,
        to_host_path=lambda path: path,
        active_set_status_line=lambda: "",
        attach_and_register_report=_async_none,
        update_progress_box=_async_none,
        clear_progress_box=_async_none,
        is_cancelled=lambda: False,
    )


def test_apply_tool_contract_routes_single_document_equipment_deep_to_document_question():
    payload = {
        "requested_tool": "analyze_equipment_deep",
        "message": "Сфокусируйся на процессорах и памяти.",
        "routing_mode": "explicit",
        "file_count": 1,
        "has_session_docs": True,
        "active_doc_ids": ["file:req-1"],
        "session_docs": {
            "Requirements.pdf": {
                "document_id": "file:req-1",
                "text": "Процессор 8 ядер, память 32 ГБ",
            }
        },
    }

    tool_definition = apply_tool_contract_to_payload(payload)

    assert tool_definition is not None
    assert payload["requested_tool"] == "analyze_equipment_deep"
    assert payload["forced_route"] == "document_question"
    assert payload["rag_scope"] == "session_rag"
    assert payload["tool_execution_mode"] == "async"
    assert payload["runtime_mode"] == "specialized_tasks"


def test_apply_tool_contract_keeps_two_document_equipment_deep_on_equipment_route():
    payload = {
        "requested_tool": "analyze_equipment_deep",
        "message": "Сравни ТЗ и КП по CPU и памяти.",
        "routing_mode": "explicit",
        "file_count": 2,
        "has_session_docs": True,
        "active_doc_ids": ["file:req-1", "file:quote-1"],
        "session_docs": {
            "Requirements.pdf": {"document_id": "file:req-1", "text": "ТЗ"},
            "Quotation_12.pdf": {"document_id": "file:quote-1", "text": "КП"},
        },
    }

    tool_definition = apply_tool_contract_to_payload(payload)

    assert tool_definition is not None
    assert payload["forced_route"] == "equipment_analysis"
    assert payload.get("rag_scope") is None


@pytest.mark.asyncio
async def test_submit_async_tool_job_reports_progress_for_generic_deep_job(monkeypatch):
    monkeypatch.setenv("OPENWEBUI_GENERIC_DEEP_JOB_ENABLED", "1")
    monkeypatch.setenv("OPENWEBUI_EXPLICIT_TOOL_JOB_START_DELAY_S", "0")
    monkeypatch.delenv("OPENWEBUI_GENERIC_DEEP_JOB_ENABLED_TOOLS", raising=False)

    async def fake_execute(payload, deps=None):
        await deps.update_progress_box(
            key="stage:indexing",
            title="Индексация",
            content="Подготавливаем фрагменты документа.",
        )
        await deps.update_progress_box(
            key="stage:analysis",
            title="Анализ",
            content="Собираем итоговый вывод.",
        )
        await deps.clear_progress_box(key="stage:indexing")
        return {
            "assistant_message": "Глубокий анализ готов.\n---\n**Отчет сохранен:** `Report_Test_123.pdf`",
            "structured_result": {"summary": "Готово"},
            "sources": [{"source_id": "src-1", "title": "Requirements.pdf"}],
            "generated_report": "Глубокий анализ готов.\n---\n**Отчет сохранен:** `Report_Test_123.pdf`",
            "embeds": [{"kind": "status", "title": "Готово"}],
        }

    job = submit_async_tool_job(
        request_payload={
            "requested_tool": "analyze_document_deep",
            "tool_execution_mode": "async",
            "execution_surface": "explicit_tool",
        },
        deps=_stub_execution_dependencies(),
        execute_fn=fake_execute,
        route_prefix="/tool-server",
    )

    for _ in range(100):
        await asyncio.sleep(0.01)
        stored = get_tool_job_store().get(job.job_id)
        if stored is not None and stored.status == "completed":
            break

    stored = get_tool_job_store().get(job.job_id)
    assert stored is not None
    assert stored.status == "completed"
    accepted = build_accepted_tool_job_response(stored, {"requested_tool": "analyze_document_deep"})
    status_payload = build_tool_job_status_response(stored)

    assert accepted["job_status"] == "completed"
    assert accepted["status_text"] == "deep-job завершён."
    assert accepted["poll_after_ms"] == 1500
    assert status_payload["status"] == "completed"
    assert status_payload["status_text"] == "deep-job завершён."
    assert status_payload["progress"]["phase"] == "stage:analysis"
    assert status_payload["status_history"][0]["key"] == "stage:indexing"
    assert status_payload["status_history"][1]["key"] == "stage:analysis"
    assert status_payload["embeds"][0]["kind"] == "status"
    assert status_payload["sources"][0]["source_id"] == "src-1"
    assert status_payload["artifacts"][0]["artifact_id"] == "Report_Test_123.pdf"
    assert status_payload["artifacts"][0]["url"] == "/tool-server/tool-reports/Report_Test_123.pdf"
    result_payload = get_tool_job_result(job.job_id)
    assert result_payload["artifacts"][0]["artifact_id"] == "Report_Test_123.pdf"


@pytest.mark.asyncio
async def test_submit_async_tool_job_returns_terminal_result_for_failed_job(monkeypatch):
    monkeypatch.setenv("OPENWEBUI_GENERIC_DEEP_JOB_ENABLED", "1")
    monkeypatch.setenv("OPENWEBUI_EXPLICIT_TOOL_JOB_START_DELAY_S", "0")

    async def fake_execute(payload, deps=None):
        raise RuntimeError("модель занята")

    job = submit_async_tool_job(
        request_payload={
            "requested_tool": "analyze_equipment_deep",
            "tool_execution_mode": "async",
            "execution_surface": "explicit_tool",
        },
        deps=_stub_execution_dependencies(),
        execute_fn=fake_execute,
        route_prefix="/tool-server",
    )

    for _ in range(100):
        await asyncio.sleep(0.01)
        stored = get_tool_job_store().get(job.job_id)
        if stored is not None and stored.status == "failed":
            break

    stored = get_tool_job_store().get(job.job_id)
    assert stored is not None
    assert stored.status == "failed"
    assert build_tool_job_status_response(stored)["result_ref"].endswith("/result")
    result_payload = get_tool_job_result(job.job_id)
    assert "модель занята" in result_payload["assistant_message"]
    assert result_payload["execution_metadata"]["status"] == "failed"
