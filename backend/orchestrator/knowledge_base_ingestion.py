from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, List, Optional

import numpy as np

from orchestrator.knowledge_base_store import (
    KnowledgeBaseChunkRecord,
    KnowledgeBaseSourceRecord,
    KnowledgeBaseStoreProtocol,
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
    store: Optional[KnowledgeBaseStoreProtocol] = None,
    mime_type: str = "text/plain",
    index_version: str = "v1",
    embedding_model_id: str = "labse",
    chunking_version: str = "legal_v1",
    chunker: Optional[LegalDocumentChunker] = None,
    embed_fn: Optional[Callable] = None,
    source_origin: str = "knowledge_base",
    content_hash_override: Optional[str] = None,
    base_metadata: Optional[dict] = None,
) -> IngestedKnowledgeBaseSource:
    if not text or not text.strip():
        raise ValueError("Knowledge base source text must be non-empty")

    kb_store = store or get_knowledge_base_store()
    chunker = chunker or LegalDocumentChunker()
    content_hash = str(content_hash_override or _sha256_text(text))
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
    chunk_embeddings = None
    if embed_fn is not None and chunk_objects:
        chunk_embeddings = np.asarray(embed_fn([chunk.text for chunk in chunk_objects]), dtype=np.float32)
        if chunk_embeddings.ndim != 2 or chunk_embeddings.shape[0] != len(chunk_objects):
            raise ValueError("Knowledge base embed_fn must return a 2D array aligned with chunks")
    chunk_rows = []
    for idx, chunk in enumerate(chunk_objects):
        metadata = dict(chunk.metadata or {})
        metadata.update(
            {
                "section": chunk.section,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
                "display_name": display_name,
                "source_id": source.source_id,
            }
        )
        if base_metadata:
            metadata.update(dict(base_metadata))
        metadata.setdefault("document_id", source.source_id)
        chunk_rows.append(
            {
                "chunk_id": f"{source.source_id}:{chunk.index}",
                "chunk_index": int(chunk.index),
                "text": chunk.text,
                "metadata_json": metadata,
                "source_origin": source_origin,
                "embedding": chunk_embeddings[idx] if chunk_embeddings is not None else None,
                "embedding_model_id": embedding_model_id if chunk_embeddings is not None else None,
            }
        )

    kb_store.replace_chunks_sync(source_id=source.source_id, chunks=chunk_rows)
    chunks = kb_store.list_chunks_sync(collection_id, source_ids=[source.source_id])
    return IngestedKnowledgeBaseSource(source=source, chunks=chunks)
