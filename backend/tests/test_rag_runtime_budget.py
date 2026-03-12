import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.rag.pipeline import AdaptiveRAGPipeline
from orchestrator.rag.retriever import RetrievalResult


def test_pipeline_uses_effective_context_tokens_for_char_budget():
    pipeline = AdaptiveRAGPipeline(
        embed_fn=None,
        rag_mode="simple",
        effective_context_tokens=1000,
        retrieved_context_ratio=0.60,
        chars_per_token=4.0,
    )

    assert pipeline.max_context_chars == 2400
    assert pipeline.get_runtime_budget_metadata() == {
        "effective_context_tokens": 1000,
        "retrieved_context_ratio": 0.60,
        "retrieved_context_tokens_budget": 600,
        "max_context_chars": 2400,
    }


def test_pipeline_build_context_respects_runtime_budget():
    pipeline = AdaptiveRAGPipeline(
        embed_fn=None,
        rag_mode="simple",
        effective_context_tokens=100,
        retrieved_context_ratio=0.60,
        chars_per_token=4.0,
    )
    results = [
        RetrievalResult(text="A" * 300, score=1.0, index=0),
        RetrievalResult(text="B" * 300, score=0.9, index=1),
        RetrievalResult(text="C" * 300, score=0.8, index=2),
    ]

    context = pipeline._build_context(results)

    assert len(context) <= pipeline.max_context_chars + 50
    assert "[Чанк 1]" in context
    assert "[Чанк 2]" not in context
    assert "[Чанк 3]" not in context


def test_pipeline_can_reconfigure_runtime_budget():
    pipeline = AdaptiveRAGPipeline(embed_fn=None, rag_mode="simple", max_context_chars=16000)

    pipeline.configure_runtime_budget(
        effective_context_tokens=2000,
        retrieved_context_ratio=0.55,
    )

    assert pipeline.effective_context_tokens == 2000
    assert pipeline.max_context_chars == 4400
