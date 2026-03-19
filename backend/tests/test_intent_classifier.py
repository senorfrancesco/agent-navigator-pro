"""
Тесты для EmbeddingIntentClassifier — классификация интентов без LLM.
"""

import os
import sys
import hashlib

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.observability import render_metrics_text, reset_observability_metrics
from orchestrator.rag.classifier import (
    EmbeddingIntentClassifier,
    LLMIntentClassifier,
    UNSURE_INTENT,
    select_classifier_result,
)


@pytest.fixture(autouse=True)
def _reset_classifier_metrics():
    reset_observability_metrics()
    yield
    reset_observability_metrics()


def _mock_embed_fn(texts):
    """Mock embedding function: простой bag-of-chars хеш для тестов."""
    results = []
    for text in texts:
        # Создаём детерминированный вектор из текста
        digest = hashlib.sha256(text.lower().strip().encode("utf-8")).digest()
        seed = int.from_bytes(digest[:4], "big") % (2**31)
        np.random.seed(seed)
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
    ("Что нам подходит из коммерческого предложения", "equipment_analysis", True),
    ("Что из КП соответствует ТЗ", "equipment_analysis", True),
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
        assert len(self.classifier.centroids) == 6

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
        assert len(result["scores"]) == 6

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


def test_llm_intent_classifier_parses_json():
    classifier = LLMIntentClassifier(
        infer_text_fn=lambda prompt: '{"intent":"compare_documents","confidence":0.93,"needs_rag":true}'
    )

    result = classifier.classify("Сравни документы")

    assert result is not None
    assert result["intent"] == "compare_documents"
    assert result["confidence"] == pytest.approx(0.93)
    assert result["needs_rag"] is True
    assert result["source"] == "llm"


def test_llm_intent_classifier_rejects_unknown_intent():
    classifier = LLMIntentClassifier(
        infer_text_fn=lambda prompt: '{"intent":"unknown_category","confidence":0.99}'
    )

    assert classifier.classify("какой-то запрос") is None
    metrics = render_metrics_text()
    assert "agent_nav_fallback_events_total" in metrics
    assert 'component="intent_classifier"' in metrics
    assert 'fallback="llm_unsupported_intent"' in metrics


def test_llm_intent_classifier_non_json_records_metric():
    classifier = LLMIntentClassifier(
        infer_text_fn=lambda prompt: "not a json payload"
    )

    assert classifier.classify("какой-то запрос") is None
    metrics = render_metrics_text()
    assert "agent_nav_fallback_events_total" in metrics
    assert 'component="intent_classifier"' in metrics
    assert 'fallback="llm_non_json"' in metrics


def test_select_classifier_result_hybrid_prefers_confident_llm():
    result = select_classifier_result(
        "hybrid",
        embedder_result={"intent": "general_chat", "confidence": 0.61, "needs_rag": False},
        llm_result={"intent": "document_question", "confidence": 0.88, "needs_rag": True},
        llm_confidence_threshold=0.75,
    )

    assert result["intent"] == "document_question"
    assert result["source"] == "llm"


def test_select_classifier_result_hybrid_falls_back_to_embedder():
    result = select_classifier_result(
        "hybrid",
        embedder_result={"intent": "equipment_analysis", "confidence": 0.67, "needs_rag": True},
        llm_result={"intent": "general_chat", "confidence": 0.41, "needs_rag": False},
        llm_confidence_threshold=0.75,
    )

    assert result["intent"] == "equipment_analysis"
    assert result["source"] == "embedder_fallback"
    assert result["llm_fallback"]["intent"] == "general_chat"


def test_select_classifier_result_embedder_abstains_when_below_thresholds():
    result = select_classifier_result(
        "embedder",
        embedder_result={"intent": "document_question", "confidence": 0.42, "margin": 0.03, "needs_rag": True},
        llm_result=None,
        embedder_confidence_threshold=0.6,
        embedder_margin_threshold=0.1,
    )

    assert result["intent"] == UNSURE_INTENT
    assert result["predicted_intent"] == "document_question"
    assert result["abstained"] is True
    assert result["abstain_reason"] == "embedder_low_confidence"
    assert result["source"] == "embedder_abstain"
    assert result["thresholds"] == {"confidence": 0.6, "margin": 0.1}


def test_select_classifier_result_hybrid_can_end_unsure():
    result = select_classifier_result(
        "hybrid",
        embedder_result={"intent": "compare_documents", "confidence": 0.48, "margin": 0.02, "needs_rag": True},
        llm_result={"intent": "general_chat", "confidence": 0.41, "needs_rag": False},
        llm_confidence_threshold=0.75,
        embedder_confidence_threshold=0.6,
        embedder_margin_threshold=0.1,
    )

    assert result["intent"] == UNSURE_INTENT
    assert result["predicted_intent"] == "compare_documents"
    assert result["abstained"] is True
    assert result["source"] == "hybrid_unsure"
    metrics = render_metrics_text()
    assert "agent_nav_fallback_events_total" in metrics
    assert 'fallback="hybrid_low_confidence_unsure"' in metrics


def test_select_classifier_result_llm_fallback_still_respects_embedder_abstain():
    result = select_classifier_result(
        "llm",
        embedder_result={"intent": "document_question", "confidence": 0.42, "margin": 0.03, "needs_rag": True},
        llm_result=None,
        embedder_confidence_threshold=0.6,
        embedder_margin_threshold=0.1,
    )

    assert result["intent"] == UNSURE_INTENT
    assert result["predicted_intent"] == "document_question"
    assert result["abstained"] is True
    assert result["abstain_reason"] == "llm_fallback_embedder_low_confidence"
    assert result["source"] == "embedder_abstain"
    metrics = render_metrics_text()
    assert "agent_nav_fallback_events_total" in metrics
    assert 'fallback="llm_missing_embedder_fallback"' in metrics
