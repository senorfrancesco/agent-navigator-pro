from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional

from orchestrator.knowledge_base_store import SQLiteKnowledgeBaseStore
from orchestrator.rag.chunker import LegalDocumentChunker
from orchestrator.rag.retriever import HybridRetriever


def _normalize_text_hash(text: str) -> str:
    normalized = " ".join((text or "").lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _build_session_entries(
    session_docs: Dict[str, Any],
    active_doc_ids: List[str],
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    chunker = LegalDocumentChunker()
    for display_name, info in (session_docs or {}).items():
        document_id = str(info.get("document_id") or display_name)
        if active_doc_ids and document_id not in active_doc_ids:
            continue
        text = str(info.get("text") or "").strip()
        if not text:
            continue
        for chunk in chunker.chunk(text, doc_name=display_name):
            entries.append(
                {
                    "chunk_id": f"session:{document_id}:{chunk.index}",
                    "document_id": document_id,
                    "display_name": display_name,
                    "collection_id": None,
                    "source_origin": "session",
                    "text": chunk.text,
                    "metadata_json": {
                        **dict(chunk.metadata or {}),
                        "section": chunk.section,
                        "start_char": chunk.start_char,
                        "end_char": chunk.end_char,
                        "display_name": display_name,
                    },
                }
            )
    return entries


def _build_kb_entries(
    kb_store: SQLiteKnowledgeBaseStore,
    collection_id: Optional[str],
) -> List[Dict[str, Any]]:
    if not collection_id:
        return []
    entries: List[Dict[str, Any]] = []
    for chunk in kb_store.list_chunks_sync(collection_id):
        entries.append(
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.source_id,
                "display_name": chunk.display_name,
                "collection_id": chunk.collection_id,
                "source_origin": chunk.source_origin,
                "text": chunk.text,
                "metadata_json": dict(chunk.metadata_json or {}),
            }
        )
    return entries


def _search_entries(
    *,
    entries: List[Dict[str, Any]],
    query: str,
    embed_fn: Callable,
    mode: str,
    top_k: int,
) -> List[Dict[str, Any]]:
    if not entries:
        return []
    retriever = HybridRetriever(embed_fn=embed_fn, use_bm25=True)
    retriever.index([entry["text"] for entry in entries])
    results = retriever.search(query, top_k=min(top_k, len(entries)), mode=mode)
    ranked: List[Dict[str, Any]] = []
    for rank, result in enumerate(results, start=1):
        entry = dict(entries[result.index])
        entry["raw_score"] = float(result.score)
        entry["scope_rank"] = rank
        ranked.append(entry)
    return ranked


def _annotate_scope_merge_scores(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not chunks:
        return []
    scores = [float(chunk.get("raw_score", 0.0)) for chunk in chunks]
    min_score = min(scores)
    max_score = max(scores)
    spread = max_score - min_score
    annotated: List[Dict[str, Any]] = []
    for chunk in chunks:
        raw_score = float(chunk.get("raw_score", 0.0))
        rank = int(chunk.get("scope_rank", 1) or 1)
        norm_score = 0.5 if spread <= 1e-9 else max(0.0, min(1.0, (raw_score - min_score) / spread))
        rank_bonus = 1.0 / rank
        merged = 0.80 * norm_score + 0.20 * rank_bonus
        annotated.append({**chunk, "scope_score": norm_score, "merge_score": merged})
    return annotated


def _prefer_chunk(existing: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    existing_merge = float(existing.get("merge_score", 0.0))
    candidate_merge = float(candidate.get("merge_score", 0.0))
    if abs(candidate_merge - existing_merge) <= 0.05:
        existing_origin = str(existing.get("source_origin") or "")
        candidate_origin = str(candidate.get("source_origin") or "")
        if existing_origin != candidate_origin:
            if candidate_origin == "session":
                return candidate
            if existing_origin == "session":
                return existing
    if candidate_merge > existing_merge:
        return candidate
    return existing


def _dedup_and_normalize(chunks: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    if not chunks:
        return []
    grouped: Dict[str, Dict[str, Any]] = {}
    for chunk in sorted(chunks, key=lambda item: float(item.get("merge_score", 0.0)), reverse=True):
        text_hash = _normalize_text_hash(chunk.get("text", ""))
        existing = grouped.get(text_hash)
        grouped[text_hash] = chunk if existing is None else _prefer_chunk(existing, chunk)
    deduped = sorted(grouped.values(), key=lambda item: float(item.get("merge_score", 0.0)), reverse=True)[:top_k]
    if not deduped:
        return []
    scores = [float(chunk.get("merge_score", 0.0)) for chunk in deduped] or [0.0]
    min_score = min(scores)
    max_score = max(scores)
    spread = max_score - min_score
    for chunk in deduped:
        raw_score = float(chunk.get("merge_score", 0.0))
        normalized = 0.5 if spread <= 1e-9 else max(0.0, min(1.0, (raw_score - min_score) / spread))
        chunk["normalized_score"] = normalized
    return deduped


def retrieve_merged_chunks(
    *,
    query: str,
    rag_scope: str,
    knowledge_collection_id: Optional[str],
    session_docs: Dict[str, Any],
    active_doc_ids: List[str],
    embed_fn: Optional[Callable],
    kb_store: Optional[SQLiteKnowledgeBaseStore],
    top_k: int = 5,
    candidate_budget_per_scope: int = 12,
    mode: str = "hybrid",
) -> Dict[str, Any]:
    if embed_fn is None:
        return {"chunks": [], "source_scope_summary": "off"}

    session_entries = _build_session_entries(session_docs, active_doc_ids)
    kb_entries = _build_kb_entries(kb_store, knowledge_collection_id) if kb_store is not None else []

    if rag_scope == "session_rag":
        session_ranked = _annotate_scope_merge_scores(_search_entries(
            entries=session_entries,
            query=query,
            embed_fn=embed_fn,
            mode=mode,
            top_k=candidate_budget_per_scope,
        ))
        return {
            "chunks": _dedup_and_normalize(session_ranked, top_k),
            "source_scope_summary": "session",
        }

    if rag_scope == "knowledge_base_rag":
        kb_ranked = _annotate_scope_merge_scores(_search_entries(
            entries=kb_entries,
            query=query,
            embed_fn=embed_fn,
            mode=mode,
            top_k=candidate_budget_per_scope,
        ))
        session_ranked = _annotate_scope_merge_scores(_search_entries(
            entries=session_entries,
            query=query,
            embed_fn=embed_fn,
            mode=mode,
            top_k=candidate_budget_per_scope,
        ))
        merged = _dedup_and_normalize(kb_ranked + session_ranked, top_k)
        return {
            "chunks": merged,
            "source_scope_summary": "knowledge_base+session_overlay" if session_entries else "knowledge_base",
        }

    return {"chunks": [], "source_scope_summary": "off"}
