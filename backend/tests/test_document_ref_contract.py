import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.openapi_tools_api import _build_orchestration_payload
from orchestrator.tool_schemas import AskDocumentRequest, DocumentRef


def test_document_ref_prefers_canonical_identity_over_compatibility_fields():
    document_ref = DocumentRef(
        document_id="doc-1",
        upload_id="upload-1",
        session_file_ref="session-1",
        file_path="/tmp/doc.txt",
    )

    assert document_ref.canonical_identity() == "doc-1"
    assert document_ref.compatibility_identity() == "session-1"
    assert document_ref.resolved_identity() == "doc-1"
    assert document_ref.resolved_identity_kind() == "document_id"


def test_document_ref_uses_compatibility_identity_when_canonical_missing():
    document_ref = DocumentRef(
        session_file_ref="session-1",
        file_path="/tmp/doc.txt",
    )

    assert document_ref.canonical_identity() is None
    assert document_ref.compatibility_identity() == "session-1"
    assert document_ref.resolved_identity() == "session-1"
    assert document_ref.resolved_identity_kind() == "session_file_ref"


def test_tool_payload_exposes_document_ref_bindings_and_canonical_ids():
    request = AskDocumentRequest(
        question="Что сказано про штраф?",
        document_refs=[
            DocumentRef(document_id="doc-1", file_path="/tmp/doc-1.txt", label="Doc 1"),
            DocumentRef(session_file_ref="session-2", file_path="/tmp/doc-2.txt", label="Doc 2"),
        ],
        ui_hints={"surface": "openwebui"},
    )

    payload = _build_orchestration_payload(request)

    assert payload["active_doc_ids"] == ["doc-1", "session-2"]
    assert payload["ui_state"]["surface"] == "openwebui"
    assert payload["ui_state"]["active_canonical_doc_ids"] == ["doc-1"]
    assert payload["ui_state"]["has_canonical_document_refs"] is True
    assert payload["ui_state"]["document_ref_bindings"] == [
        {
            "label": "Doc 1",
            "document_id": "doc-1",
            "upload_id": None,
            "file_id": None,
            "version_id": None,
            "session_file_ref": None,
            "file_path": "/tmp/doc-1.txt",
            "resolved_identity": "doc-1",
            "resolved_identity_kind": "document_id",
            "has_canonical_identity": True,
        },
        {
            "label": "Doc 2",
            "document_id": None,
            "upload_id": None,
            "file_id": None,
            "version_id": None,
            "session_file_ref": "session-2",
            "file_path": "/tmp/doc-2.txt",
            "resolved_identity": "session-2",
            "resolved_identity_kind": "session_file_ref",
            "has_canonical_identity": False,
        },
    ]
