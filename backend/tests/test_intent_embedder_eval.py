import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evals.intent_embedder_eval import (
    EvalCase,
    evaluate_predictions,
    load_eval_cases,
    select_hybrid_prediction,
)


def test_load_eval_cases_reads_yaml_dataset():
    dataset_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "orchestrator",
        "data",
        "intent_eval_dataset.yaml",
    )

    cases = load_eval_cases(dataset_path)

    assert cases
    assert all(isinstance(case, EvalCase) for case in cases)
    assert {"compare_documents", "equipment_analysis", "document_question"} <= {
        case.label for case in cases
    }


def test_evaluate_predictions_computes_core_metrics():
    cases = [
        EvalCase(text="сравни документы", label="compare_documents"),
        EvalCase(text="что в документе", label="document_question"),
        EvalCase(text="привет", label="greeting"),
        EvalCase(text="что ты умеешь", label="general_chat"),
    ]
    predictions = [
        "compare_documents",
        "general_chat",
        "greeting",
        "general_chat",
    ]

    metrics = evaluate_predictions(cases, predictions)

    assert metrics["accuracy"] == 0.75
    assert metrics["per_intent"]["compare_documents"]["tp"] == 1
    assert metrics["per_intent"]["document_question"]["fn"] == 1
    assert metrics["false_positive_by_predicted_intent"]["general_chat"] == 1
    assert 0.0 <= metrics["cost_weighted_accuracy"] <= 1.0
    assert 0.0 <= metrics["macro_f1"] <= 1.0


def test_evaluate_predictions_tracks_unsure_rate():
    cases = [
        EvalCase(text="сравни документы", label="compare_documents"),
        EvalCase(text="привет", label="greeting"),
    ]
    predictions = ["__unsure__", "greeting"]

    metrics = evaluate_predictions(cases, predictions)

    assert metrics["unsure_rate"] == 0.5
    assert metrics["unsure_count"] == 1


def test_select_hybrid_prediction_prefers_confident_llm_and_can_abstain():
    assert (
        select_hybrid_prediction(
            llm_result={"intent": "compare_documents", "confidence": 0.91},
            embedder_result={"intent": "general_chat", "confidence": 0.61, "margin": 0.08},
            llm_conf_threshold=0.8,
            embedder_conf_threshold=0.6,
            embedder_margin_threshold=0.1,
        )
        == "compare_documents"
    )

    assert (
        select_hybrid_prediction(
            llm_result={"intent": "general_chat", "confidence": 0.41},
            embedder_result={"intent": "general_chat", "confidence": 0.58, "margin": 0.05},
            llm_conf_threshold=0.8,
            embedder_conf_threshold=0.6,
            embedder_margin_threshold=0.1,
        )
        == "__unsure__"
    )
