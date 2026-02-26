"""
Тесты для HybridRetriever — BM25 + Dense + RRF.
"""

import numpy as np
import pytest
from orchestrator.rag.retriever import (
    HybridRetriever, BM25, tokenize_russian, RetrievalResult
)


# Тестовый корпус юридических документов
TEST_DOCS = [
    "Договор купли-продажи недвижимого имущества заключается в письменной форме.",
    "Штрафные санкции за нарушение сроков поставки составляют 0.1% за каждый день просрочки.",
    "Гарантийный срок на оборудование составляет 24 месяца с даты ввода в эксплуатацию.",
    "Стороны несут ответственность за неисполнение обязательств в соответствии с законодательством РФ.",
    "Оплата производится в рублях путём безналичного перечисления на расчётный счёт поставщика.",
    "Приёмка товара осуществляется по количеству и качеству в соответствии с ГОСТ.",
    "Расторжение договора возможно по соглашению сторон или в судебном порядке.",
    "Форс-мажорные обстоятельства освобождают стороны от ответственности за неисполнение.",
    "Цена договора включает стоимость оборудования, доставку и монтаж.",
    "Арбитражный суд города Москвы рассматривает споры, вытекающие из настоящего договора.",
]


def _mock_embed_fn(texts):
    """Mock embedding для тестов."""
    results = []
    for text in texts:
        np.random.seed(hash(text.lower().strip()[:50]) % (2**31))
        vec = np.random.randn(768).astype(np.float32)
        vec /= np.linalg.norm(vec)
        results.append(vec)
    return np.array(results)


class TestTokenizeRussian:
    """Тесты токенизации."""

    def test_basic(self):
        tokens = tokenize_russian("Договор купли-продажи")
        assert "договор" in tokens
        assert len(tokens) >= 1

    def test_stopwords_removed(self):
        tokens = tokenize_russian("и в на с по о")
        assert len(tokens) == 0

    def test_short_words_removed(self):
        tokens = tokenize_russian("я ты он мы документ")
        assert "документ" in tokens


class TestBM25:
    """Тесты BM25."""

    def setup_method(self):
        self.bm25 = BM25()
        self.bm25.fit(TEST_DOCS)

    def test_fit(self):
        """Индексация работает."""
        assert self.bm25.corpus_size == len(TEST_DOCS)
        assert self.bm25.avgdl > 0

    def test_search_returns_results(self):
        """Поиск возвращает результаты."""
        results = self.bm25.search("штрафные санкции")
        assert len(results) > 0
        assert results[0][1] > 0  # score > 0

    def test_search_relevance(self):
        """Релевантный документ имеет высший score."""
        results = self.bm25.search("штрафные санкции просрочка")
        top_idx = results[0][0]
        assert "штрафн" in TEST_DOCS[top_idx].lower()

    def test_search_top_k(self):
        """top_k ограничивает результаты."""
        results = self.bm25.search("договор", top_k=3)
        assert len(results) <= 3

    def test_empty_query(self):
        """Пустой запрос возвращает нули."""
        results = self.bm25.search("")
        # Все scores должны быть 0
        assert all(score == 0 for _, score in results)


class TestHybridRetriever:
    """Тесты Hybrid Retriever."""

    def setup_method(self):
        self.retriever = HybridRetriever(
            embed_fn=_mock_embed_fn,
            use_bm25=True,
        )
        self.retriever.index(TEST_DOCS)

    def test_index(self):
        """Индексация работает."""
        assert self.retriever.indexed
        assert len(self.retriever.documents) == len(TEST_DOCS)

    def test_search_hybrid(self):
        """Hybrid search возвращает результаты."""
        results = self.retriever.search("штрафные санкции", top_k=5)
        assert len(results) > 0
        assert all(isinstance(r, RetrievalResult) for r in results)

    def test_search_bm25_only(self):
        """BM25-only search."""
        results = self.retriever.search("оплата рублей", top_k=3, mode="bm25")
        assert len(results) > 0
        assert all(r.source == "bm25" for r in results)

    def test_search_dense_only(self):
        """Dense-only search."""
        results = self.retriever.search("гарантия оборудование", top_k=3, mode="dense")
        assert len(results) > 0
        assert all(r.source == "dense" for r in results)

    def test_hybrid_has_source(self):
        """Hybrid results имеют source="hybrid"."""
        results = self.retriever.search("договор", top_k=3, mode="hybrid")
        if results:
            assert results[0].source == "hybrid"

    def test_grade_results(self):
        """Z-score grading добавляет метаданные."""
        results = self.retriever.search("договор", top_k=5)
        graded = self.retriever.grade_results(results)
        for r in graded:
            assert "z_score" in r.metadata
            assert "grade" in r.metadata
            assert r.metadata["grade"] in ("excellent", "good", "marginal", "poor")

    def test_rocchio_expand(self):
        """Rocchio query expansion возвращает результаты."""
        initial = self.retriever.search("договор", top_k=3)
        expanded = self.retriever.rocchio_expand("договор", initial, top_k=5)
        assert len(expanded) > 0
        assert all(r.source == "rocchio" for r in expanded)

    def test_not_indexed_raises(self):
        """Поиск до индексации бросает RuntimeError."""
        retriever = HybridRetriever(embed_fn=_mock_embed_fn)
        with pytest.raises(RuntimeError):
            retriever.search("тест")

    def test_bm25_disabled(self):
        """Retriever без BM25 работает."""
        retriever = HybridRetriever(embed_fn=_mock_embed_fn, use_bm25=False)
        retriever.index(TEST_DOCS)
        results = retriever.search("договор", top_k=3)
        assert len(results) > 0

    def test_no_embed_fn(self):
        """Retriever без embed_fn использует только BM25."""
        retriever = HybridRetriever(embed_fn=None, use_bm25=True)
        retriever.index(TEST_DOCS)
        results = retriever.search("штрафные санкции", top_k=3)
        assert len(results) > 0
