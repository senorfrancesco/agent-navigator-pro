from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np
import yaml
from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.rag.classifier import EmbeddingIntentClassifier, LLMIntentClassifier
from services.model_manager.ums_client import UMSClient

INTENT_LABELS = (
    "greeting",
    "compare_documents",
    "equipment_analysis",
    "document_analysis",
    "document_question",
    "general_chat",
)
TRUE_INTENT_WEIGHTS = {
    "compare_documents": 3.0,
    "equipment_analysis": 3.0,
    "document_question": 3.0,
    "document_analysis": 2.0,
    "general_chat": 1.0,
    "greeting": 1.0,
}


@dataclass(frozen=True)
class EvalCase:
    text: str
    label: str
    difficulty: str = "medium"
    cost_sensitive: bool = False
    language: str = "ru"


def load_eval_cases(dataset_path: str) -> List[EvalCase]:
    with open(dataset_path, "r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or []

    cases: List[EvalCase] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        text = str(item.get("text", "")).strip()
        if not text or label not in INTENT_LABELS:
            continue
        cases.append(
            EvalCase(
                text=text,
                label=label,
                difficulty=str(item.get("difficulty", "medium")),
                cost_sensitive=bool(item.get("cost_sensitive", False)),
                language=str(item.get("language", "ru")),
            )
        )
    return cases


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def evaluate_predictions(cases: Sequence[EvalCase], predictions: Sequence[str]) -> Dict[str, object]:
    if len(cases) != len(predictions):
        raise ValueError("cases and predictions must have the same length")

    total = len(cases)
    correct = sum(1 for case, pred in zip(cases, predictions) if case.label == pred)
    accuracy = round(_safe_div(correct, total), 4)
    unsure_count = sum(1 for pred in predictions if pred == "__unsure__")

    per_intent: Dict[str, Dict[str, float]] = {}
    macro_f1_values: List[float] = []
    fp_by_predicted_intent = Counter()

    for intent in INTENT_LABELS:
        tp = sum(1 for case, pred in zip(cases, predictions) if case.label == intent and pred == intent)
        fp = sum(1 for case, pred in zip(cases, predictions) if case.label != intent and pred == intent)
        fn = sum(1 for case, pred in zip(cases, predictions) if case.label == intent and pred != intent)
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        f1 = _safe_div(2 * precision * recall, precision + recall)
        per_intent[intent] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
        macro_f1_values.append(f1)

    for case, pred in zip(cases, predictions):
        if pred != case.label and pred != "__unsure__":
            fp_by_predicted_intent[pred] += 1

    weighted_total = sum(TRUE_INTENT_WEIGHTS[case.label] for case in cases)
    weighted_correct = sum(
        TRUE_INTENT_WEIGHTS[case.label]
        for case, pred in zip(cases, predictions)
        if case.label == pred
    )
    cost_weighted_accuracy = round(_safe_div(weighted_correct, weighted_total), 4)

    return {
        "total_cases": total,
        "accuracy": accuracy,
        "unsure_count": unsure_count,
        "unsure_rate": round(_safe_div(unsure_count, total), 4),
        "macro_f1": round(_safe_div(sum(macro_f1_values), len(INTENT_LABELS)), 4),
        "cost_weighted_accuracy": cost_weighted_accuracy,
        "per_intent": per_intent,
        "false_positive_by_predicted_intent": dict(sorted(fp_by_predicted_intent.items())),
    }


def _build_embed_fn(model_path: str, device: str = "cpu"):
    model = SentenceTransformer(model_path, device=device)

    def embed_fn(texts: Sequence[str]) -> np.ndarray:
        embeddings = model.encode(list(texts), normalize_embeddings=True)
        return np.array(embeddings)

    return embed_fn


def _latency_stats(samples_ms: Sequence[float]) -> Dict[str, float]:
    if not samples_ms:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "avg_ms": 0.0}
    ordered = sorted(samples_ms)
    p95_index = max(0, int(len(ordered) * 0.95) - 1)
    return {
        "p50_ms": round(median(ordered), 2),
        "p95_ms": round(ordered[p95_index], 2),
        "avg_ms": round(sum(ordered) / len(ordered), 2),
    }


def select_hybrid_prediction(
    *,
    llm_result: Optional[Dict[str, Any]],
    embedder_result: Optional[Dict[str, Any]],
    llm_conf_threshold: float,
    embedder_conf_threshold: float,
    embedder_margin_threshold: float,
) -> str:
    if llm_result and llm_result.get("confidence", 0.0) >= llm_conf_threshold:
        return str(llm_result["intent"])
    if embedder_result:
        if (
            embedder_result.get("confidence", 0.0) >= embedder_conf_threshold
            and embedder_result.get("margin", 0.0) >= embedder_margin_threshold
        ):
            return str(embedder_result["intent"])
    return "__unsure__"


def _build_llm_classifier(model_id: str) -> LLMIntentClassifier:
    ums_client = UMSClient()

    def infer_text(prompt: str) -> str:
        response = ums_client.infer(
            model_id,
            {
                "prompt": prompt,
                "temperature": 0.0,
                "top_p": 0.1,
                "max_tokens": 96,
            },
        )
        return response.get("choices", [{}])[0].get("text", str(response))

    return LLMIntentClassifier(infer_text_fn=infer_text)


def evaluate_embedder_model(model_path: str, dataset_path: str, *, device: str = "cpu") -> Dict[str, object]:
    cases = load_eval_cases(dataset_path)
    if not cases:
        raise ValueError(f"No valid eval cases found in {dataset_path}")

    classifier = EmbeddingIntentClassifier(embed_fn=_build_embed_fn(model_path, device=device))
    classifier.initialize()
    predictions = [classifier.classify(case.text)["intent"] for case in cases]
    metrics = evaluate_predictions(cases, predictions)
    metrics["model_path"] = model_path
    metrics["dataset_path"] = dataset_path
    metrics["sample_errors"] = [
        {
            "text": case.text,
            "expected": case.label,
            "predicted": pred,
            "difficulty": case.difficulty,
        }
        for case, pred in zip(cases, predictions)
        if pred != case.label
    ][:10]
    return metrics


def evaluate_embedder_model_with_latency(model_path: str, dataset_path: str, *, device: str = "cpu") -> Dict[str, object]:
    cases = load_eval_cases(dataset_path)
    classifier = EmbeddingIntentClassifier(embed_fn=_build_embed_fn(model_path, device=device))
    classifier.initialize()
    predictions: List[str] = []
    latencies_ms: List[float] = []
    for case in cases:
        started = time.perf_counter()
        result = classifier.classify(case.text)
        latencies_ms.append((time.perf_counter() - started) * 1000)
        predictions.append(result["intent"])
    metrics = evaluate_predictions(cases, predictions)
    metrics.update(_latency_stats(latencies_ms))
    metrics["model_path"] = model_path
    metrics["dataset_path"] = dataset_path
    metrics["mode"] = "embedder"
    metrics["device"] = device
    metrics["sample_errors"] = [
        {
            "text": case.text,
            "expected": case.label,
            "predicted": pred,
            "difficulty": case.difficulty,
            "language": case.language,
        }
        for case, pred in zip(cases, predictions)
        if pred != case.label
    ][:10]
    return metrics


def evaluate_llm_model(model_id: str, dataset_path: str) -> Dict[str, object]:
    cases = load_eval_cases(dataset_path)
    classifier = _build_llm_classifier(model_id)
    predictions: List[str] = []
    latencies_ms: List[float] = []
    for case in cases:
        started = time.perf_counter()
        result = classifier.classify(case.text)
        latencies_ms.append((time.perf_counter() - started) * 1000)
        predictions.append(result["intent"] if result else "__unsure__")
    metrics = evaluate_predictions(cases, predictions)
    metrics.update(_latency_stats(latencies_ms))
    metrics["model_id"] = model_id
    metrics["dataset_path"] = dataset_path
    metrics["mode"] = "llm"
    metrics["sample_errors"] = [
        {
            "text": case.text,
            "expected": case.label,
            "predicted": pred,
            "difficulty": case.difficulty,
            "language": case.language,
        }
        for case, pred in zip(cases, predictions)
        if pred != case.label
    ][:10]
    return metrics


def evaluate_hybrid_model(
    model_path: str,
    dataset_path: str,
    *,
    llm_model_id: str,
    llm_conf_threshold: float,
    embedder_conf_threshold: float,
    embedder_margin_threshold: float,
    embedder_device: str = "cpu",
) -> Dict[str, object]:
    cases = load_eval_cases(dataset_path)
    llm_classifier = _build_llm_classifier(llm_model_id)
    embedder_classifier = EmbeddingIntentClassifier(
        embed_fn=_build_embed_fn(model_path, device=embedder_device)
    )
    embedder_classifier.initialize()
    predictions: List[str] = []
    latencies_ms: List[float] = []
    routed_by_llm = 0
    fallback_to_embedder = 0
    ended_unsure = 0

    for case in cases:
        started = time.perf_counter()
        llm_result = llm_classifier.classify(case.text)
        embedder_result = embedder_classifier.classify(case.text)
        prediction = select_hybrid_prediction(
            llm_result=llm_result,
            embedder_result=embedder_result,
            llm_conf_threshold=llm_conf_threshold,
            embedder_conf_threshold=embedder_conf_threshold,
            embedder_margin_threshold=embedder_margin_threshold,
        )
        latencies_ms.append((time.perf_counter() - started) * 1000)
        predictions.append(prediction)

        if llm_result and llm_result.get("confidence", 0.0) >= llm_conf_threshold:
            routed_by_llm += 1
        elif prediction == "__unsure__":
            ended_unsure += 1
        else:
            fallback_to_embedder += 1

    metrics = evaluate_predictions(cases, predictions)
    metrics.update(_latency_stats(latencies_ms))
    metrics["mode"] = "hybrid"
    metrics["model_path"] = model_path
    metrics["llm_model_id"] = llm_model_id
    metrics["dataset_path"] = dataset_path
    metrics["device"] = embedder_device
    metrics["routed_by_llm"] = routed_by_llm
    metrics["fallback_to_embedder"] = fallback_to_embedder
    metrics["ended_unsure"] = ended_unsure
    metrics["sample_errors"] = [
        {
            "text": case.text,
            "expected": case.label,
            "predicted": pred,
            "difficulty": case.difficulty,
            "language": case.language,
        }
        for case, pred in zip(cases, predictions)
        if pred != case.label
    ][:10]
    return metrics


def _parse_model_args(model_args: Iterable[str]) -> List[tuple[str, str]]:
    parsed: List[tuple[str, str]] = []
    for raw in model_args:
        if "=" in raw:
            name, path = raw.split("=", 1)
        else:
            path = raw
            name = os.path.basename(path.rstrip("/"))
        parsed.append((name.strip(), path.strip()))
    return parsed


def _default_dataset_path() -> str:
    return os.path.join(
        os.path.dirname(__file__),
        "..",
        "orchestrator",
        "data",
        "intent_eval_dataset.yaml",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate embedding models for intent routing.")
    parser.add_argument(
        "--mode",
        choices=("embedder", "llm", "hybrid"),
        default="embedder",
        help="Evaluation mode.",
    )
    parser.add_argument(
        "--dataset",
        default=_default_dataset_path(),
        help="Path to YAML eval dataset.",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="Model spec NAME=/abs/or/rel/path or just /path.",
    )
    parser.add_argument(
        "--llm-model-id",
        default="qwen-14b-llm",
        help="UMS model id for LLM evaluation.",
    )
    parser.add_argument("--llm-conf-threshold", type=float, default=0.8)
    parser.add_argument("--embedder-conf-threshold", type=float, default=0.6)
    parser.add_argument("--embedder-margin-threshold", type=float, default=0.1)
    parser.add_argument("--embedder-device", default="cpu", help="Device for SentenceTransformer evals.")
    parser.add_argument(
        "--json-output",
        default="",
        help="Optional path to save raw JSON results.",
    )
    args = parser.parse_args()

    results = []
    if args.mode == "llm":
        metrics = evaluate_llm_model(args.llm_model_id, args.dataset)
        metrics["model_name"] = args.llm_model_id
        results.append(metrics)
    else:
        if not args.model:
            parser.error("--model is required for embedder and hybrid modes")
        for name, path in _parse_model_args(args.model):
            if args.mode == "embedder":
                metrics = evaluate_embedder_model_with_latency(
                    path,
                    args.dataset,
                    device=args.embedder_device,
                )
            else:
                metrics = evaluate_hybrid_model(
                    path,
                    args.dataset,
                    llm_model_id=args.llm_model_id,
                    llm_conf_threshold=args.llm_conf_threshold,
                    embedder_conf_threshold=args.embedder_conf_threshold,
                    embedder_margin_threshold=args.embedder_margin_threshold,
                    embedder_device=args.embedder_device,
                )
            metrics["model_name"] = name
            results.append(metrics)

    results.sort(key=lambda item: item["cost_weighted_accuracy"], reverse=True)

    print("model\tmode\taccuracy\tmacro_f1\tcost_weighted_accuracy\tunsure_rate\tp50_ms\tp95_ms")
    for item in results:
        print(
            f"{item['model_name']}\t{item['mode']}\t{item['accuracy']:.4f}\t"
            f"{item['macro_f1']:.4f}\t{item['cost_weighted_accuracy']:.4f}\t"
            f"{item['unsure_rate']:.4f}\t{item['p50_ms']:.2f}\t{item['p95_ms']:.2f}"
        )

    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
