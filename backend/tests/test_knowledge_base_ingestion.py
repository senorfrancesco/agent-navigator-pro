import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.knowledge_base_ingestion import ingest_text_source_sync
from orchestrator.knowledge_base_store import SQLiteKnowledgeBaseStore


def test_ingest_text_source_persists_source_and_chunks(tmp_path):
    store = SQLiteKnowledgeBaseStore(db_url=f"sqlite:///{tmp_path}/kb_ingestion.db")

    record = ingest_text_source_sync(
        collection_id="legal",
        display_name="contract.txt",
        text=(
            "Статья 1. Уведомление направляется не позднее чем за 10 календарных дней.\n"
            "Статья 2. За просрочку поставки применяется штраф 3 процента."
        ),
        store=store,
        mime_type="text/plain",
        index_version="v1",
        embedding_model_id="labse",
        chunking_version="legal_v1",
    )

    sources = store.list_sources_sync("legal")
    chunks = store.list_chunks_sync("legal")

    assert record.source.source_id == sources[0].source_id
    assert record.source.content_hash
    assert len(chunks) >= 1
    assert all(chunk.source_origin == "knowledge_base" for chunk in chunks)
