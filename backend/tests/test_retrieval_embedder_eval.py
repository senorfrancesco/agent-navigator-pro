import os
import sys
import json

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import evals.retrieval_embedder_eval as retrieval_eval
from evals.retrieval_embedder_eval import (
    SCENARIOS,
    compute_answer_faithfulness,
    evaluate_retrieval_cases,
    evaluate_embedder_model,
    load_retrieval_eval_cases,
)


def _stub_embed_fn(texts):
    vectors = []
    for text in texts:
        q = text.lower()
        vec = np.array(
            [
                1.0 if "уведом" in q or "дней" in q else 0.0,
                1.0 if "штраф" in q or "просроч" in q else 0.0,
                1.0 if "гарант" in q or "24 месяц" in q else 0.0,
                1.0 if "сервис" in q or "обслуж" in q else 0.0,
                1.0 if "аванс" in q or "оплат" in q else 0.0,
                1.0 if "оборудован" in q else 0.0,
                1.0 if "претенз" in q or "качеств" in q else 0.0,
                1.0 if "расторж" in q or "существен" in q or "нарушен" in q else 0.0,
                1.0 if "ответствен" in q or "огранич" in q or "10 процент" in q else 0.0,
                1.0 if "валют" in q or "курс" in q or "рубл" in q else 0.0,
            ],
            dtype=np.float32,
        )
        if not np.any(vec):
            vec = np.ones(10, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        vectors.append(vec)
    return np.array(vectors)


def _dataset_path():
    return os.path.join(
        os.path.dirname(__file__),
        "..",
        "evals",
        "data",
        "retrieval_eval_dataset.yaml",
    )


def test_load_retrieval_eval_cases_reads_curated_dataset():
    cases = load_retrieval_eval_cases(_dataset_path())

    assert len(cases) == 9
    assert {case.scenario for case in cases} == set(SCENARIOS)
    assert all(case.chunks for case in cases)


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (
            """
- id: broken_case
  scenario: wrong
  query: "test"
  answerable: true
  reference_answer: "answer"
  expected_chunk_ids: [chunk-1]
  expected_source_origins: [session]
  chunks:
    - chunk_id: chunk-1
      document_id: doc-1
      source_origin: session
      text: "text"
""",
            "invalid scenario",
        ),
        (
            """
- id: broken_case
  scenario: session_only
  query: "test"
  answerable: true
  reference_answer: ""
  expected_chunk_ids: [chunk-1]
  expected_source_origins: [session]
  chunks:
    - chunk_id: chunk-1
      document_id: doc-1
      source_origin: session
      text: "text"
""",
            "reference_answer",
        ),
        (
            """
- id: broken_case
  scenario: session_only
  query: "test"
  answerable: true
  reference_answer: "answer"
  expected_chunk_ids: []
  expected_source_origins: [session]
  chunks:
    - chunk_id: chunk-1
      document_id: doc-1
      source_origin: session
      text: "text"
""",
            "expected_chunk_ids",
        ),
        (
            """
- id: broken_case
  scenario: session_only
  query: "test"
  answerable: true
  reference_answer: "answer"
  expected_chunk_ids: [chunk-1]
  expected_source_origins: [session]
  chunks:
    - chunk_id: chunk-1
      document_id: doc-1
      source_origin: wrong
      text: "text"
""",
            "source_origin",
        ),
    ],
)
def test_load_retrieval_eval_cases_rejects_invalid_dataset(tmp_path, payload, expected_error):
    dataset = tmp_path / "broken.yaml"
    dataset.write_text(payload.strip() + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=expected_error):
        load_retrieval_eval_cases(str(dataset))


def test_compute_answer_faithfulness_scores_supported_answer():
    score = compute_answer_faithfulness(
        "Гарантийный срок оборудования составляет 24 месяца.",
        ["Гарантийный срок оборудования составляет 24 месяца с даты ввода в эксплуатацию."],
    )

    assert score == 1.0


def test_compute_answer_faithfulness_drops_without_support():
    score = compute_answer_faithfulness(
        "Гарантийный срок оборудования составляет 24 месяца.",
        ["Поставка выполняется партиями по согласованному графику."],
    )

    assert score < 0.5


def test_evaluate_retrieval_cases_reports_core_metrics():
    cases = load_retrieval_eval_cases(_dataset_path())

    metrics = evaluate_retrieval_cases(
        cases,
        embed_fn=_stub_embed_fn,
        mode="hybrid",
        top_k=3,
    )

    assert metrics["total_cases"] == 9
    assert metrics["recall_at_k"] >= 0.75
    assert metrics["evidence_hit_rate"] >= 0.75
    assert metrics["source_origin_accuracy"] >= 0.5
    assert metrics["answer_faithfulness"] >= 0.75
    assert metrics["unanswerable_rejection_rate"] == 1.0
    assert "mixed" in metrics["per_scenario"]


def test_evaluate_retrieval_cases_dense_mode_keeps_mixed_source_accuracy():
    cases = load_retrieval_eval_cases(_dataset_path())

    metrics = evaluate_retrieval_cases(
        cases,
        embed_fn=_stub_embed_fn,
        mode="dense",
        top_k=3,
    )

    assert metrics["source_origin_accuracy"] >= 0.5
    assert metrics["per_scenario"]["mixed"]["source_origin_accuracy"] == 1.0


def test_expanded_dataset_contains_hard_negative_and_legal_wording_cases():
    cases = {case.case_id: case for case in load_retrieval_eval_cases(_dataset_path())}

    assert "session_claim_deadline" in cases
    assert "kb_unilateral_termination_material_breach" in cases
    assert "mixed_service_and_liability_cap" in cases
    assert "unanswerable_currency_rate" in cases


def test_hard_negative_claim_deadline_prefers_claim_chunk_over_generic_day_distractors():
    cases = load_retrieval_eval_cases(_dataset_path())
    case = next(item for item in cases if item.case_id == "session_claim_deadline")

    metrics = evaluate_retrieval_cases([case], embed_fn=_stub_embed_fn, mode="dense", top_k=2)

    assert metrics["recall_at_k"] == 1.0
    assert metrics["mrr"] == 1.0


def test_answer_faithfulness_uses_retrieved_hits_not_all_gold_evidence():
    cases = load_retrieval_eval_cases(_dataset_path())
    mixed_case = next(case for case in cases if case.case_id == "mixed_penalty_and_service")

    def partial_embed_fn(texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vec = np.array(
                [
                    1.0 if "штраф" in lowered or "просроч" in lowered else 0.0,
                    1.0 if "сервис" in lowered or "обслуж" in lowered else 0.0,
                    0.1,
                ],
                dtype=np.float32,
            )
            if "какая ответственность" in lowered:
                vec = np.array([1.0, 0.0, 0.1], dtype=np.float32)
            vec /= np.linalg.norm(vec)
            vectors.append(vec)
        return np.array(vectors)

    metrics = evaluate_retrieval_cases([mixed_case], embed_fn=partial_embed_fn, mode="dense", top_k=1)

    assert metrics["recall_at_k"] == 0.5
    assert metrics["answer_faithfulness"] < 1.0
    assert metrics["source_origin_accuracy"] == 0.0


def test_cli_json_output_contract(tmp_path, monkeypatch, capsys):
    output_path = tmp_path / "retrieval_eval.json"

    def fake_evaluate(**kwargs):
        return {
            "total_cases": 5,
            "mode": "hybrid",
            "top_k": 3,
            "recall_at_k": 0.8,
            "mrr": 0.7,
            "ndcg_at_k": 0.75,
            "evidence_hit_rate": 0.8,
            "source_origin_accuracy": 0.6,
            "answer_faithfulness": 0.9,
            "unanswerable_rejection_rate": 1.0,
            "per_scenario": {},
            "sample_failures": [],
            "model_path": "local-model",
            "dataset_path": "dataset.yaml",
            "device": "cpu",
        }

    monkeypatch.setattr(retrieval_eval, "evaluate_embedder_model", fake_evaluate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "retrieval_embedder_eval.py",
            "--dataset",
            _dataset_path(),
            "--model",
            "local-model",
            "--mode",
            "hybrid",
            "--top-k",
            "3",
            "--json-output",
            str(output_path),
        ],
    )

    retrieval_eval.main()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    stdout_payload = json.loads(capsys.readouterr().out)
    required_keys = {
        "total_cases",
        "mode",
        "top_k",
        "recall_at_k",
        "mrr",
        "ndcg_at_k",
        "evidence_hit_rate",
        "source_origin_accuracy",
        "answer_faithfulness",
        "unanswerable_rejection_rate",
        "per_scenario",
        "sample_failures",
        "model_path",
        "dataset_path",
        "device",
    }

    assert required_keys.issubset(payload.keys())
    assert payload == stdout_payload


def test_evaluate_embedder_model_includes_report_metadata(monkeypatch):
    monkeypatch.setattr(retrieval_eval, "_build_embed_fn", lambda model_path, device="cpu": _stub_embed_fn)

    metrics = evaluate_embedder_model(
        model_path="local-model",
        dataset_path=_dataset_path(),
        mode="hybrid",
        top_k=3,
        device="cpu",
    )

    assert metrics["model_path"] == "local-model"
    assert metrics["dataset_path"].endswith("retrieval_eval_dataset.yaml")
    assert metrics["device"] == "cpu"
