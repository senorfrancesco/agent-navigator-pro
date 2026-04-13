from __future__ import annotations

from orchestrator.tool_execution import apply_tool_contract_to_payload


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
