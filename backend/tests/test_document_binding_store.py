import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.agent_api import (
    OrchestrationRequest,
    _build_api_execution_dependencies,
    _materialize_request_document_context,
)
from orchestrator.document_binding_store import SQLiteDocumentBindingStore
from orchestrator.ui_control_plane import resolve_effective_settings


def test_document_binding_store_materializes_binding_and_thread_scope(tmp_path):
    store = SQLiteDocumentBindingStore(db_url=f"sqlite:///{tmp_path}/document_bindings.db")

    binding = store.register_document_binding_sync(
        thread_id="thread-1",
        binding={
            "label": "Contract.pdf",
            "session_file_ref": "session-contract",
            "file_path": "/tmp/contract.pdf",
        },
        source_scope="session",
    )

    assert binding.document_id.startswith("doc:")
    assert binding.version_id.startswith("ver:")
    assert binding.display_name == "Contract.pdf"
    assert binding.storage_path == "/tmp/contract.pdf"
    assert binding.source_scope == "session"
    assert binding.thread_id == "thread-1"
    assert binding.expires_at is not None
    assert store.list_thread_bindings_sync("thread-1")[0].version_id == binding.version_id


def test_document_binding_store_reuses_existing_thread_binding(tmp_path):
    store = SQLiteDocumentBindingStore(db_url=f"sqlite:///{tmp_path}/document_bindings.db")

    first = store.register_document_binding_sync(
        thread_id="thread-1",
        binding={
            "label": "Contract.pdf",
            "file_id": "file-1",
            "file_path": "/tmp/contract.pdf",
        },
        source_scope="session",
    )
    second = store.register_document_binding_sync(
        thread_id="thread-1",
        binding={
            "label": "Contract.pdf",
            "file_id": "file-1",
            "file_path": "/tmp/contract.pdf",
        },
        source_scope="session",
    )

    assert second.document_id == first.document_id
    assert second.version_id == first.version_id
    assert len(store.list_thread_bindings_sync("thread-1")) == 1


def test_document_binding_store_lists_expired_bindings_before_prune(tmp_path):
    store = SQLiteDocumentBindingStore(db_url=f"sqlite:///{tmp_path}/document_bindings.db")

    binding = store.register_document_binding_sync(
        thread_id="thread-1",
        binding={
            "label": "Contract.pdf",
            "file_id": "file-1",
            "file_path": "/tmp/contract.pdf",
        },
        source_scope="session",
    )

    expired = store.list_expired_bindings_sync(now=float(binding.expires_at or 0.0) + 1.0)

    assert [item.version_id for item in expired] == [binding.version_id]
    assert store.prune_expired_bindings_sync(now=float(binding.expires_at or 0.0) + 1.0) == 1
    assert store.list_thread_bindings_sync("thread-1") == []


def test_agent_api_materializes_document_bindings_from_tool_payload(monkeypatch):
    request = OrchestrationRequest(
        message="Что сказано про штраф?",
        thread_id="thread-42",
        has_session_docs=True,
        session_docs={
            "Contract.pdf": {
                "document_id": "session-contract",
                "path": "/tmp/contract.pdf",
                "text": "Штраф 10 процентов",
            }
        },
        ui_state={
            "document_ref_bindings": [
                {
                    "label": "Contract.pdf",
                    "session_file_ref": "session-contract",
                    "file_path": "/tmp/contract.pdf",
                    "resolved_identity": "session-contract",
                    "resolved_identity_kind": "session_file_ref",
                    "has_canonical_identity": False,
                }
            ]
        },
    )

    _materialize_request_document_context(request)
    deps = _build_api_execution_dependencies(request, resolve_effective_settings({}))
    docs = deps.get_all_docs()

    assert request.document_bindings is not None
    assert request.document_bindings[0]["document_id"].startswith("doc:")
    assert request.document_bindings[0]["version_id"].startswith("ver:")
    assert request.active_doc_ids == [request.document_bindings[0]["document_id"]]
    assert docs[0]["document_id"] == request.document_bindings[0]["document_id"]
    assert docs[0]["version_id"] == request.document_bindings[0]["version_id"]
    assert docs[0]["display_name"] == "Contract.pdf"
    assert docs[0]["path"] == "/tmp/contract.pdf"
    assert docs[0]["text"] == "Штраф 10 процентов"
    assert docs[0]["report_generated"] is False
    assert docs[0]["thread_id"] == "thread-42"
    assert docs[0]["expires_at"] is not None
    assert docs[0]["source_scope"] == "session"
    assert docs[0]["ingestion_status"] == "registered"
    assert docs[0]["order_index"] == 1
