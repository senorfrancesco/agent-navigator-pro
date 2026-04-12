from pydantic import ValidationError

from orchestrator.tool_catalog import get_tool_definition, is_known_tool, list_tool_definitions
from orchestrator.tool_schemas import (
    AcceptedToolResult,
    AnalyzeDocumentFastRequest,
    AnalyzeDocumentDeepRequest,
    AskDocumentRequest,
    CompareDocumentsFastRequest,
    CompletedToolResult,
    DocumentRef,
    ExecutionMetadata,
    ToolJobStatus,
)


def test_tool_catalog_contains_expected_contract_names():
    definitions = {definition.name: definition for definition in list_tool_definitions()}

    assert set(definitions) == {
        "ask_document",
        "analyze_document_fast",
        "analyze_document_deep",
        "compare_documents_fast",
        "compare_documents_deep",
        "analyze_equipment_fast",
        "analyze_equipment_deep",
    }
    assert definitions["ask_document"].execution_mode == "sync"
    assert definitions["compare_documents_deep"].execution_mode == "async"
    assert definitions["analyze_equipment_fast"].label == "Быстрый анализ оборудования"
    assert definitions["analyze_equipment_fast"].openwebui_status == "enabled"
    assert definitions["compare_documents_fast"].openwebui_status == "deferred"
    assert definitions["compare_documents_fast"].requires_min_documents == 2
    assert definitions["ask_document"].requires_document_context is True
    assert definitions["compare_documents_fast"].availability_note


def test_tool_catalog_resolves_legacy_executor_mapping():
    assert is_known_tool("ask_document") is True
    assert is_known_tool("unknown_tool") is False
    assert get_tool_definition("analyze_document_deep").legacy_executor == "document_analysis"
    assert "когда использовать" in get_tool_definition("analyze_document_deep").openapi_description.lower()
    assert "document_ref" in get_tool_definition("ask_document").openapi_description
    assert "analysis_goal" in get_tool_definition("analyze_document_deep").openapi_description


def test_tool_request_normalizes_requested_tool_to_tool_name():
    request = AskDocumentRequest(
        question="Что написано про штраф?",
        routing_mode="explicit",
        document_refs=[DocumentRef(document_id="doc-1")],
    )

    assert request.tool_name == "ask_document"
    assert request.requested_tool == "ask_document"


def test_tool_request_rejects_requested_tool_mismatch():
    try:
        AskDocumentRequest(
            question="Что написано про штраф?",
            document_refs=[DocumentRef(document_id="doc-1")],
            requested_tool="compare_documents_fast",
        )
    except ValidationError as exc:
        assert "requested_tool must match tool_name" in str(exc)
    else:
        raise AssertionError("expected validation error")


def test_document_tools_require_document_ref():
    for request_type, payload, expected_error in (
        (AskDocumentRequest, {"question": "Что написано про штраф?"}, "ask_document requires at least one document_ref"),
        (
            AnalyzeDocumentFastRequest,
            {"analysis_goal": "Выдели риски"},
            "analyze_document_fast requires at least one document_ref",
        ),
        (
            AnalyzeDocumentDeepRequest,
            {"analysis_goal": "Найди риски и скрытые обязательства"},
            "analyze_document_deep requires at least one document_ref",
        ),
    ):
        try:
            request_type(**payload)
        except ValidationError as exc:
            assert expected_error in str(exc)
        else:
            raise AssertionError("expected validation error")


def test_compare_request_requires_two_document_refs():
    try:
        CompareDocumentsFastRequest(
            document_refs=[DocumentRef(document_id="doc-1")],
        )
    except ValidationError as exc:
        assert "requires at least two document refs" in str(exc)
    else:
        raise AssertionError("expected validation error")


def test_document_ref_requires_identity_field():
    try:
        DocumentRef()
    except ValidationError as exc:
        assert "document ref requires one of" in str(exc)
    else:
        raise AssertionError("expected validation error")


def test_deep_analysis_request_keeps_assisted_routing_contract():
    request = AnalyzeDocumentDeepRequest(
        document_refs=[DocumentRef(document_id="doc-1")],
        routing_mode="assisted",
        analysis_goal="Найди риски и скрытые обязательства",
    )

    assert request.requested_tool == "analyze_document_deep"
    assert request.routing_mode == "assisted"
    assert request.include_report is True


def test_document_tool_catalog_distinguishes_question_from_analysis_run():
    ask_definition = get_tool_definition("ask_document")
    deep_definition = get_tool_definition("analyze_document_deep")

    assert "текущему question" in ask_definition.output_summary
    assert "конкретному запуску анализа" in deep_definition.output_summary


def test_completed_tool_result_matches_mvp_contract_shape():
    result = CompletedToolResult(
        tool_name="ask_document",
        assistant_message="Ответ с цитатами",
        structured_result={"answer": "Штраф указан в разделе 4"},
        execution_metadata=ExecutionMetadata(
            requested_tool="ask_document",
            routing_mode="explicit",
            execution_mode="sync",
        ),
    )

    dumped = result.model_dump()
    assert dumped["status"] == "completed"
    assert dumped["tool_name"] == "ask_document"
    assert dumped["execution_metadata"]["execution_mode"] == "sync"


def test_accepted_tool_result_matches_job_contract_shape():
    result = AcceptedToolResult(
        tool_name="compare_documents_deep",
        job_id="job-123",
        status_url="/tools/jobs/job-123",
        submitted_at="2026-04-07T12:00:00Z",
        execution_metadata=ExecutionMetadata(
            requested_tool="compare_documents_deep",
            routing_mode="explicit",
            execution_mode="async",
        ),
    )
    status = ToolJobStatus(
        job_id="job-123",
        status="queued",
        submitted_at="2026-04-07T12:00:00Z",
    )

    assert result.model_dump()["status"] == "accepted"
    assert result.execution_metadata.execution_mode == "async"
    assert status.model_dump()["status"] == "queued"


def test_tool_job_status_accepts_cancelling_state():
    status = ToolJobStatus(
        job_id="job-456",
        status="cancelling",
        submitted_at="2026-04-08T09:00:00Z",
    )

    assert status.model_dump()["status"] == "cancelling"
