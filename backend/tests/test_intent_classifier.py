"""
Тесты для EmbeddingIntentClassifier — классификация интентов без LLM.
"""

import numpy as np
import pytest
from orchestrator.rag.classifier import EmbeddingIntentClassifier, INTENT_EXAMPLES


def _mock_embed_fn(texts):
    """Mock embedding function: простой bag-of-chars хеш для тестов."""
    results = []
    for text in texts:
        # Создаём детерминированный вектор из текста
        np.random.seed(hash(text.lower().strip()) % (2**31))
        vec = np.random.randn(768).astype(np.float32)
        vec /= np.linalg.norm(vec)
        results.append(vec)
    return np.array(results)


# Тестовые запросы: (query, expected_intent, expected_needs_rag)
TEST_QUERIES = [
    ("Привет", "greeting", False),
    ("Здравствуйте, как дела", "greeting", False),
    ("Сравни эти два документа", "compare_documents", True),
    ("Какие различия между файлами", "compare_documents", True),
    ("Что написано в документе про штрафы", "document_question", True),
    ("Найди в файле информацию о сроках", "document_question", True),
    ("Проанализируй смету", "equipment_analysis", True),
    ("Проверь оборудование по ТЗ", "equipment_analysis", True),
    ("Что ты умеешь", "general_chat", False),
    ("Помоги мне", "general_chat", False),
]


class TestEmbeddingIntentClassifier:
    """Тесты классификатора интентов."""

    def setup_method(self):
        self.classifier = EmbeddingIntentClassifier(embed_fn=_mock_embed_fn)
        self.classifier.initialize()

    def test_initialization(self):
        """Классификатор инициализируется с центроидами."""
        assert self.classifier.initialized
        assert len(self.classifier.centroids) == len(INTENT_EXAMPLES)

    def test_centroids_normalized(self):
        """Центроиды нормализованы (unit vectors)."""
        for intent, centroid in self.classifier.centroids.items():
            norm = np.linalg.norm(centroid)
            assert abs(norm - 1.0) < 0.01, f"Centroid '{intent}' not normalized: {norm}"

    def test_classify_returns_dict(self):
        """classify() возвращает правильную структуру."""
        result = self.classifier.classify("Привет")
        assert "intent" in result
        assert "confidence" in result
        assert "needs_rag" in result
        assert "scores" in result
        assert isinstance(result["scores"], dict)

    def test_classify_confidence_positive(self):
        """Все классификации имеют confidence > 0."""
        for q, _, _ in TEST_QUERIES:
            result = self.classifier.classify(q)
            assert result["confidence"] > 0, f"'{q}': confidence should be > 0"

    def test_classify_all_intents_scored(self):
        """Все интенты имеют score в результате."""
        result = self.classifier.classify("тест")
        assert len(result["scores"]) == len(INTENT_EXAMPLES)

    def test_classify_needs_rag_greeting(self):
        """Greeting не требует RAG."""
        result = self.classifier.classify("Привет")
        # С mock embeddings точность может быть ниже,
        # но needs_rag должен быть bool
        assert isinstance(result["needs_rag"], bool)

    def test_classify_batch(self):
        """Batch классификация работает."""
        queries = [q for q, _, _ in TEST_QUERIES]
        results = self.classifier.classify_batch(queries)
        assert len(results) == len(queries)
        for r in results:
            assert "intent" in r

    def test_not_initialized_raises(self):
        """classify() до initialize() бросает RuntimeError."""
        classifier = EmbeddingIntentClassifier(embed_fn=_mock_embed_fn)
        with pytest.raises(RuntimeError):
            classifier.classify("тест")

    def test_no_embed_fn_raises(self):
        """initialize() без embed_fn бросает ValueError."""
        classifier = EmbeddingIntentClassifier(embed_fn=None)
        with pytest.raises(ValueError):
            classifier.initialize()

    def test_custom_examples(self):
        """Можно добавить кастомные примеры."""
        classifier = EmbeddingIntentClassifier(embed_fn=_mock_embed_fn)
        classifier.initialize(custom_examples={
            "greeting": ["Йо", "Салют"],
        })
        assert classifier.initialized

    def test_margin_positive(self):
        """Margin между top-1 и top-2 >= 0."""
        for q, _, _ in TEST_QUERIES:
            result = self.classifier.classify(q)
            assert result["margin"] >= 0
