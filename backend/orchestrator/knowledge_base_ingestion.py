from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Optional

from orchestrator.knowledge_base_store import (
    KnowledgeBaseChunkRecord,
    KnowledgeBaseSourceRecord,
    SQLiteKnowledgeBaseStore,
    get_knowledge_base_store,
)
from orchestrator.rag.chunker import LegalDocumentChunker


@dataclass
class IngestedKnowledgeBaseSource:
    source: KnowledgeBaseSourceRecord
    chunks: List[KnowledgeBaseChunkRecord]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ingest_text_source_sync(
    *,
    collection_id: str,
    display_name: str,
    text: str,
    store: Optional[SQLiteKnowledgeBaseStore] = None,
    mime_type: str = "text/plain",
    index_version: str = "v1",
    embedding_model_id: str = "labse",
    chunking_version: str = "legal_v1",
    chunker: Optional[LegalDocumentChunker] = None,
) -> IngestedKnowledgeBaseSource:
    if not text or not text.strip():
        raise ValueError("Knowledge base source text must be non-empty")

    kb_store = store or get_knowledge_base_store()
    chunker = chunker or LegalDocumentChunker()
    content_hash = _sha256_text(text)
    source = kb_store.register_source_sync(
        collection_id=collection_id,
        display_name=display_name,
        content_hash=content_hash,
        mime_type=mime_type,
        index_version=index_version,
        embedding_model_id=embedding_model_id,
        chunking_version=chunking_version,
    )

    chunk_objects = chunker.chunk(text, doc_name=display_name)
    chunk_rows = []
    for chunk in chunk_objects:
        metadata = dict(chunk.metadata or {})
        metadata.update(
            {
                "section": chunk.section,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
                "display_name": display_name,
                "document_id": source.source_id,
            }
        )
        chunk_rows.append(
            {
                "chunk_id": f"{source.source_id}:{chunk.index}",
                "chunk_index": int(chunk.index),
                "text": chunk.text,
                "metadata_json": metadata,
                "source_origin": "knowledge_base",
            }
        )

    kb_store.replace_chunks_sync(source_id=source.source_id, chunks=chunk_rows)
    chunks = kb_store.list_chunks_sync(collection_id, source_ids=[source.source_id])
    return IngestedKnowledgeBaseSource(source=source, chunks=chunks)
