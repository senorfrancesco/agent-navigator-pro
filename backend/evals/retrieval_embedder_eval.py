from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np
import yaml
from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.rag.retriever import HybridRetriever, tokenize_russian

SCENARIOS = (
    "session_only",
    "knowledge_base_only",
    "mixed",
    "unanswerable",
    "duplicate-heavy",
)
SOURCE_ORIGINS = {"session", "knowledge_base"}


@dataclass(frozen=True)
class RetrievalChunkCase:
    chunk_id: str
    document_id: str
    source_origin: str
    text: str


@dataclass(frozen=True)
class RetrievalEvalCase:
    case_id: str
    scenario: str
    query: str
    answerable: bool
    reference_answer: str
    expected_chunk_ids: List[str]
    expected_source_origins: List[str]
    chunks: List[RetrievalChunkCase]


def _validation_error(case_id: str, message: str) -> ValueError:
    return ValueError(f"Dataset case '{case_id}': {message}")


def _require_non_empty_str(case_id: str, field_name: str, value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise _validation_error(case_id, f"{field_name} must be a non-empty string")
    return normalized


def _normalize_string_list(case_id: str, field_name: str, values: Any) -> List[str]:
    if not isinstance(values, list):
        raise _validation_error(case_id, f"{field_name} must be a list")
    normalized = [str(value).strip() for value in values if str(value).strip()]
    return normalized


def load_retrieval_eval_cases(dataset_path: str) -> List[RetrievalEvalCase]:
    with open(dataset_path, "r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or []

    if not isinstance(payload, list):
        raise ValueError(f"Retrieval eval dataset must be a list: {dataset_path}")

    cases: List[RetrievalEvalCase] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"Dataset item must be a mapping: {item!r}")
        case_id = _require_non_empty_str("<unknown>", "id", item.get("id"))
        scenario = _require_non_empty_str(case_id, "scenario", item.get("scenario"))
        if scenario not in SCENARIOS:
            raise _validation_error(case_id, f"invalid scenario '{scenario}'")
        query = _require_non_empty_str(case_id, "query", item.get("query"))
        answerable = bool(item.get("answerable", True))
        reference_answer = str(item.get("reference_answer", "")).strip()
        expected_chunk_ids = _normalize_string_list(case_id, "expected_chunk_ids", item.get("expected_chunk_ids", []))
        expected_source_origins = _normalize_string_list(
            case_id,
            "expected_source_origins",
            item.get("expected_source_origins", []),
        )
        for source_origin in expected_source_origins:
            if source_origin not in SOURCE_ORIGINS:
                raise _validation_error(case_id, f"expected_source_origins contains invalid source_origin '{source_origin}'")
        chunks_payload = item.get("chunks")
        if not isinstance(chunks_payload, list) or not chunks_payload:
            raise _validation_error(case_id, "chunks must be a non-empty list")
        if answerable and not reference_answer:
            raise _validation_error(case_id, "reference_answer is required for answerable case")
        if answerable and not expected_chunk_ids:
            raise _validation_error(case_id, "expected_chunk_ids must be non-empty for answerable case")
        chunks: List[RetrievalChunkCase] = []
        for chunk in chunks_payload:
            if not isinstance(chunk, dict):
                raise _validation_error(case_id, f"chunk must be a mapping: {chunk!r}")
            chunk_id = _require_non_empty_str(case_id, "chunk_id", chunk.get("chunk_id"))
            document_id = _require_non_empty_str(case_id, "document_id", chunk.get("document_id"))
            source_origin = _require_non_empty_str(case_id, "source_origin", chunk.get("source_origin"))
            if source_origin not in SOURCE_ORIGINS:
                raise _validation_error(case_id, f"source_origin '{source_origin}' is invalid")
            text = _require_non_empty_str(case_id, "text", chunk.get("text"))
            chunks.append(
                RetrievalChunkCase(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    source_origin=source_origin,
                    text=text,
                )
            )
        cases.append(
            RetrievalEvalCase(
                case_id=case_id,
                scenario=scenario,
                query=query,
                answerable=answerable,
                reference_answer=reference_answer,
                expected_chunk_ids=expected_chunk_ids,
                expected_source_origins=expected_source_origins,
                chunks=chunks,
            )
        )
    return cases


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _split_sentences(text: str) -> List[str]:
    parts = [part.strip() for part in text.replace("!", ".").replace("?", ".").split(".")]
    return [part for part in parts if part]


def _sentence_support_score(sentence: str, context_text: str) -> float:
    sentence_tokens = set(tokenize_russian(sentence))
    context_tokens = set(tokenize_russian(context_text))
    if not sentence_tokens:
        return 1.0
    return _safe_div(len(sentence_tokens & context_tokens), len(sentence_tokens))


def compute_answer_faithfulness(reference_answer: str, supporting_texts: Sequence[str]) -> float:
    if not reference_answer.strip():
        return 0.0
    context_text = " ".join(text for text in supporting_texts if text)
    if not context_text.strip():
        return 0.0
    sentence_scores = [
        _sentence_support_score(sentence, context_text)
        for sentence in _split_sentences(reference_answer)
    ]
    if not sentence_scores:
        return 0.0
    return round(sum(sentence_scores) / len(sentence_scores), 4)


def _dcg_at_k(relevances: Sequence[int], top_k: int) -> float:
    dcg = 0.0
    for rank, rel in enumerate(relevances[:top_k], start=1):
        if rel:
            dcg += rel / np.log2(rank + 1)
    return float(dcg)


def evaluate_retrieval_cases(
    cases: Sequence[RetrievalEvalCase],
    *,
    embed_fn: Callable[[Sequence[str]], np.ndarray],
    mode: str = "hybrid",
    top_k: int = 5,
) -> Dict[str, Any]:
    if mode not in {"dense", "hybrid", "bm25"}:
        raise ValueError(f"Unsupported mode: {mode}")

    answerable_cases = 0
    recall_sum = 0.0
    mrr_sum = 0.0
    ndcg_sum = 0.0
    evidence_hits = 0
    source_origin_total = 0
    source_origin_hits = 0
    faithfulness_total = 0.0
    faithfulness_cases = 0
    unanswerable_total = 0
    unanswerable_rejections = 0
    per_scenario: Dict[str, Dict[str, float]] = {}
    sample_failures: List[Dict[str, Any]] = []

    for case in cases:
        retriever = HybridRetriever(embed_fn=embed_fn, use_bm25=True)
        chunk_texts = [chunk.text for chunk in case.chunks]
        chunk_by_id = {chunk.chunk_id: chunk for chunk in case.chunks}
        chunk_by_index = {idx: chunk for idx, chunk in enumerate(case.chunks)}
        retriever.index(chunk_texts)
        results = retriever.search(case.query, top_k=top_k, mode=mode)
        retrieved_chunk_ids = [chunk_by_index[result.index].chunk_id for result in results]
        relevances = [1 if chunk_id in case.expected_chunk_ids else 0 for chunk_id in retrieved_chunk_ids]

        scenario_metrics = per_scenario.setdefault(
            case.scenario,
            {
                "cases": 0,
                "recall_sum": 0.0,
                "evidence_hits": 0.0,
                "source_origin_hits": 0.0,
                "source_origin_total": 0.0,
            },
        )
        scenario_metrics["cases"] += 1

        if case.answerable and case.expected_chunk_ids:
            answerable_cases += 1
            hit_chunk_ids = [chunk_id for chunk_id in retrieved_chunk_ids if chunk_id in case.expected_chunk_ids]
            hit_count = len(set(hit_chunk_ids))
            recall = _safe_div(hit_count, len(case.expected_chunk_ids))
            recall_sum += recall
            scenario_metrics["recall_sum"] += recall

            if hit_count > 0:
                evidence_hits += 1
                scenario_metrics["evidence_hits"] += 1

            first_hit_rank = next((idx + 1 for idx, chunk_id in enumerate(retrieved_chunk_ids) if chunk_id in case.expected_chunk_ids), None)
            if first_hit_rank is not None:
                mrr_sum += 1.0 / first_hit_rank

            dcg = _dcg_at_k(relevances, top_k)
            ideal_rels = [1] * min(len(case.expected_chunk_ids), top_k)
            idcg = _dcg_at_k(ideal_rels, top_k)
            ndcg_sum += _safe_div(dcg, idcg)

            supporting_texts = [chunk_by_id[chunk_id].text for chunk_id in hit_chunk_ids if chunk_id in chunk_by_id]
            faithfulness_total += compute_answer_faithfulness(case.reference_answer, supporting_texts)
            faithfulness_cases += 1
        else:
            unanswerable_total += 1
            if not any(relevances):
                unanswerable_rejections += 1

        if case.expected_source_origins:
            source_origin_total += 1
            scenario_metrics["source_origin_total"] += 1
            retrieved_hit_origins = {
                chunk_by_index[result.index].source_origin
                for result in results
                if chunk_by_index[result.index].chunk_id in case.expected_chunk_ids
            }
            if retrieved_hit_origins == set(case.expected_source_origins):
                source_origin_hits += 1
                scenario_metrics["source_origin_hits"] += 1

        if case.answerable and case.expected_chunk_ids and not any(relevances):
            sample_failures.append(
                {
                    "case_id": case.case_id,
                    "scenario": case.scenario,
                    "query": case.query,
                    "expected_chunk_ids": case.expected_chunk_ids,
                    "retrieved_chunk_ids": retrieved_chunk_ids,
                }
            )

    scenario_summary: Dict[str, Dict[str, float]] = {}
    for scenario, metrics in per_scenario.items():
        cases_count = int(metrics["cases"])
        scenario_summary[scenario] = {
            "cases": cases_count,
            "recall_at_k": round(_safe_div(metrics["recall_sum"], cases_count), 4),
            "evidence_hit_rate": round(_safe_div(metrics["evidence_hits"], cases_count), 4),
            "source_origin_accuracy": round(
                _safe_div(metrics["source_origin_hits"], metrics["source_origin_total"]), 4
            ),
        }

    return {
        "total_cases": len(cases),
        "mode": mode,
        "top_k": top_k,
        "recall_at_k": round(_safe_div(recall_sum, answerable_cases), 4),
        "mrr": round(_safe_div(mrr_sum, answerable_cases), 4),
        "ndcg_at_k": round(_safe_div(ndcg_sum, answerable_cases), 4),
        "evidence_hit_rate": round(_safe_div(evidence_hits, answerable_cases), 4),
        "source_origin_accuracy": round(_safe_div(source_origin_hits, source_origin_total), 4),
        "answer_faithfulness": round(_safe_div(faithfulness_total, faithfulness_cases), 4),
        "unanswerable_rejection_rate": round(_safe_div(unanswerable_rejections, unanswerable_total), 4),
        "per_scenario": scenario_summary,
        "sample_failures": sample_failures[:10],
    }


def _build_embed_fn(model_path: str, device: str = "cpu") -> Callable[[Sequence[str]], np.ndarray]:
    model = SentenceTransformer(model_path, device=device)

    def embed_fn(texts: Sequence[str]) -> np.ndarray:
        embeddings = model.encode(list(texts), normalize_embeddings=True)
        return np.array(embeddings)

    return embed_fn


def evaluate_embedder_model(
    *,
    model_path: str,
    dataset_path: str,
    mode: str = "hybrid",
    top_k: int = 5,
    device: str = "cpu",
) -> Dict[str, Any]:
    cases = load_retrieval_eval_cases(dataset_path)
    if not cases:
        raise ValueError(f"No valid retrieval eval cases found in {dataset_path}")
    metrics = evaluate_retrieval_cases(
        cases,
        embed_fn=_build_embed_fn(model_path, device=device),
        mode=mode,
        top_k=top_k,
    )
    metrics["model_path"] = model_path
    metrics["dataset_path"] = dataset_path
    metrics["device"] = device
    return metrics


def main() -> None:
    default_dataset = os.path.join(
        os.path.dirname(__file__),
        "data",
        "retrieval_eval_dataset.yaml",
    )
    parser = argparse.ArgumentParser(description="Evaluate dense embedder choices for retrieval.")
    parser.add_argument("--dataset", default=default_dataset, help="Path to retrieval eval YAML dataset.")
    parser.add_argument("--model", required=True, help="SentenceTransformer model path.")
    parser.add_argument("--mode", choices=("dense", "hybrid", "bm25"), default="hybrid")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--json-output", help="Optional path to write JSON metrics.")
    args = parser.parse_args()

    metrics = evaluate_embedder_model(
        model_path=args.model,
        dataset_path=args.dataset,
        mode=args.mode,
        top_k=args.top_k,
        device=args.device,
    )
    output = json.dumps(metrics, ensure_ascii=False, indent=2)
    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as fh:
            fh.write(output)
            fh.write("\n")
    print(output)


if __name__ == "__main__":
    main()
