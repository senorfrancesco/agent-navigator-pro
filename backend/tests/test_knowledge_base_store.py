import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.knowledge_base_store import SQLiteKnowledgeBaseStore


def test_kb_store_registers_source_and_deduplicates_by_content_hash(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_store.db")

    first = store.register_source_sync(
        collection_id="legal",
        display_name="contract_v1.pdf",
        content_hash="hash-1",
        mime_type="application/pdf",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )
    second = store.register_source_sync(
        collection_id="legal",
        display_name="contract_v1_copy.pdf",
        content_hash="hash-1",
        mime_type="application/pdf",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )

    assert first.source_id == second.source_id
    assert len(store.list_sources_sync("legal")) == 1


def test_kb_store_replaces_chunks_for_source(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_store.db")
    source = store.register_source_sync(
        collection_id="legal",
        display_name="policy.txt",
        content_hash="hash-2",
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )

    store.replace_chunks_sync(
        source_id=source.source_id,
        chunks=[
            {
                "chunk_id": "chunk-1",
                "chunk_index": 0,
                "text": "Первый фрагмент",
                "metadata_json": {"section": "1.1"},
            },
            {
                "chunk_id": "chunk-2",
                "chunk_index": 1,
                "text": "Второй фрагмент",
                "metadata_json": {"section": "1.2"},
            },
        ],
    )
    store.replace_chunks_sync(
        source_id=source.source_id,
        chunks=[
            {
                "chunk_id": "chunk-3",
                "chunk_index": 0,
                "text": "Обновлённый фрагмент",
                "metadata_json": {"section": "2.1"},
            }
        ],
    )

    chunks = store.list_chunks_sync(collection_id="legal")

    assert [chunk.chunk_id for chunk in chunks] == ["chunk-3"]
    assert chunks[0].metadata_json["section"] == "2.1"
