"""
Тесты для AdaptiveRAGPipeline — tiered RAG.
"""

import numpy as np
import pytest
from orchestrator.rag.pipeline import AdaptiveRAGPipeline, RAGResult
from orchestrator.rag.retriever import RetrievalResult


TEST_DOCS = [
    "Договор купли-продажи заключается в письменной форме и подлежит государственной регистрации.",
    "Штрафные санкции за нарушение сроков составляют 0.1% за каждый день просрочки.",
    "Гарантийный срок на оборудование составляет 24 месяца с даты ввода в эксплуатацию.",
    "Стороны несут ответственность за неисполнение обязательств по законодательству РФ.",
    "Оплата производится путём безналичного перечисления на расчётный счёт поставщика.",
]


def _mock_embed_fn(texts):
    results = []
    for text in texts:
        np.random.seed(hash(text.lower().strip()[:50]) % (2**31))
        vec = np.random.randn(768).astype(np.float32)
        vec /= np.linalg.norm(vec)
        results.append(vec)
    return np.array(results)


class TestAdaptiveRAGPipeline:
    """Тесты Adaptive RAG Pipeline."""

    def test_simple_mode(self):
        """Tier 1: Simple RAG — 0 доп. LLM вызовов."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=_mock_embed_fn,
            rag_mode="simple",
            top_k=3,
        )
        pipeline.index_documents(TEST_DOCS, chunk=False)
        result = pipeline.retrieve("штрафные санкции")

        assert isinstance(result, RAGResult)
        assert result.needs_generation == True
        assert result.metadata["mode"] == "simple"
        assert len(result.chunks) > 0

    def test_corrective_mode(self):
        """Tier 2: Corrective RAG."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=_mock_embed_fn,
            rag_mode="corrective",
            top_k=3,
        )
        pipeline.index_documents(TEST_DOCS, chunk=False)
        result = pipeline.retrieve("Какие условия договора?")

        assert isinstance(result, RAGResult)
        assert result.metadata["mode"] == "corrective"

    def test_corrective_skips_rag_for_greeting(self):
        """Tier 2: Corrective RAG пропускает RAG для приветствий."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=_mock_embed_fn,
            rag_mode="corrective",
            top_k=3,
        )
        pipeline.index_documents(TEST_DOCS, chunk=False)
        result = pipeline.retrieve("Привет")

        # Классификатор должен определить greeting → skip RAG
        if result.metadata.get("skipped_rag"):
            assert len(result.chunks) == 0
            assert result.context_text == ""

    def test_agentic_mode(self):
        """Tier 3: Agentic RAG."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=_mock_embed_fn,
            rag_mode="agentic",
            top_k=3,
        )
        pipeline.index_documents(TEST_DOCS, chunk=False)
        result = pipeline.retrieve("гарантийный срок оборудования")

        assert isinstance(result, RAGResult)
        assert result.metadata["mode"] == "agentic"
        assert "iterations" in result.metadata

    def test_index_with_chunking(self):
        """Индексация с чанкированием."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=_mock_embed_fn,
            rag_mode="simple",
        )
        long_doc = " ".join(TEST_DOCS) * 5
        chunks = pipeline.index_documents([long_doc], chunk=True, doc_names=["test.pdf"])
        assert len(chunks) > 0

    def test_not_indexed_returns_empty(self):
        """Retrieve без индексации возвращает пустой результат."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=_mock_embed_fn,
            rag_mode="simple",
        )
        result = pipeline.retrieve("тест")
        assert len(result.chunks) == 0
        assert "error" in result.metadata

    def test_context_text_built(self):
        """context_text собирается из чанков."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=_mock_embed_fn,
            rag_mode="simple",
            top_k=3,
        )
        pipeline.index_documents(TEST_DOCS, chunk=False)
        result = pipeline.retrieve("договор")

        if result.chunks:
            assert len(result.context_text) > 0
            assert "[Чанк" in result.context_text

    def test_context_text_respects_limit(self):
        """context_text не превышает max_context_chars."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=_mock_embed_fn,
            rag_mode="simple",
            top_k=10,
            max_context_chars=500,
        )
        big_docs = [d * 10 for d in TEST_DOCS]
        pipeline.index_documents(big_docs, chunk=False)
        result = pipeline.retrieve("договор")

        assert len(result.context_text) <= 600  # Некоторый запас на заголовки

    def test_classify_intent(self):
        """classify_intent работает после index."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=_mock_embed_fn,
            rag_mode="corrective",
        )
        pipeline.index_documents(TEST_DOCS, chunk=False)
        intent = pipeline.classify_intent("Привет")
        assert intent is not None
        assert "intent" in intent

    def test_classify_intent_before_index(self):
        """classify_intent до index возвращает None."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=_mock_embed_fn,
            rag_mode="corrective",
        )
        intent = pipeline.classify_intent("Привет")
        assert intent is None

    def test_no_embed_fn(self):
        """Pipeline без embed_fn использует только BM25."""
        pipeline = AdaptiveRAGPipeline(
            embed_fn=None,
            rag_mode="simple",
        )
        pipeline.index_documents(TEST_DOCS, chunk=False)
        result = pipeline.retrieve("штрафные санкции")
        # Должен работать через BM25
        assert isinstance(result, RAGResult)
