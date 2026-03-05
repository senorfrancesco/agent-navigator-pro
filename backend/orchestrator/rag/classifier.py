"""
EmbeddingIntentClassifier — классификация интентов без LLM.

5 интентов:
- greeting: приветствие
- compare_documents: сравнение документов
- equipment_analysis: анализ сметы/оборудования
- document_question: вопрос по документу
- general_chat: общий чат

Метод: centroid-based classification по cosine similarity.
Обучается на эталонных фразах, затем классифицирует новые запросы
по близости к центроидам кластеров.
"""

import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import yaml
from dataclasses import dataclass

logger = logging.getLogger("classifier")

@dataclass
class ClassificationResult:
    """Результат классификации."""
    intent: str
    confidence: float
    needs_rag: bool
    scores: Dict[str, float]


class IntentExamplesConfigError(RuntimeError):
    """Ошибка конфигурации intent-примеров."""

# Какие интенты требуют RAG
INTENT_NEEDS_RAG = {
    "greeting": False,
    "compare_documents": True,
    "equipment_analysis": True,
    "document_analysis": True,
    "document_question": True,
    "general_chat": False,
}


class EmbeddingIntentClassifier:
    """
    Классификация интентов на основе embedding similarity.
    
    TD-2 Fix: Примеры загружаются из внешнего YAML-файла.
    В будущем может быть заменено на поиск в полноценной Vector DB (Chroma/FAISS).
    """

    def __init__(self, embed_fn: Optional[Callable] = None):
        self.embed_fn = embed_fn
        self.centroids: Dict[str, np.ndarray] = {}
        self.initialized = False
        self.examples_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "intent_examples.yaml"
        )

    def _validate_examples(self, examples: Any) -> Dict[str, List[str]]:
        """Проверяет, что YAML содержит словарь intent -> non-empty list[str]."""
        if not isinstance(examples, dict) or not examples:
            raise IntentExamplesConfigError("YAML должен быть непустым словарём intent -> list[str]")

        validated_examples: Dict[str, List[str]] = {}
        for intent, phrases in examples.items():
            if not isinstance(intent, str) or not intent.strip():
                raise IntentExamplesConfigError("Ключ intent должен быть непустой строкой")

            if not isinstance(phrases, list) or not phrases:
                raise IntentExamplesConfigError(
                    f"Intent '{intent}' должен содержать непустой список примеров"
                )

            if not all(isinstance(phrase, str) and phrase.strip() for phrase in phrases):
                raise IntentExamplesConfigError(
                    f"Intent '{intent}' должен содержать только непустые строки"
                )

            validated_examples[intent] = phrases

        return validated_examples

    def _load_examples(self) -> Dict[str, List[str]]:
        """Загружает примеры из YAML-файла и валидирует контракт структуры."""
        path = Path(self.examples_path)
        try:
            with path.open("r", encoding="utf-8") as f:
                raw_examples = yaml.safe_load(f)
            examples = self._validate_examples(raw_examples)
            logger.info("Loaded intent examples from %s", path)
            return examples
        except FileNotFoundError as exc:
            logger.error("Intent examples config error: path=%s reason=%s", path, exc)
            raise IntentExamplesConfigError(
                f"Intent examples file not found: {path}"
            ) from exc
        except (yaml.YAMLError, IntentExamplesConfigError) as exc:
            logger.error("Intent examples config error: path=%s reason=%s", path, exc)
            raise IntentExamplesConfigError(
                f"Invalid intent examples config at {path}: {exc}"
            ) from exc

    def initialize(self, custom_examples: Optional[Dict[str, List[str]]] = None):
        """
        Инициализирует центроиды для каждого интента.
        """
        if self.embed_fn is None:
            raise ValueError("embed_fn must be set before initialization")

        # Загружаем из файла
        examples = self._load_examples()
        
        if custom_examples:
            for intent, texts in custom_examples.items():
                if intent in examples:
                    examples[intent].extend(texts)
                else:
                    examples[intent] = texts

        # Вычисляем центроиды
        for intent, texts in examples.items():
            if not texts:
                continue
            embeddings = self.embed_fn(texts)
            if isinstance(embeddings, list):
                embeddings = np.array(embeddings)
            
            # Центроид = среднее нормализованных эмбеддингов
            # Это простая альтернатива Vector DB (прототипирование по методу центроидов)
            centroid = np.mean(embeddings, axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            self.centroids[intent] = centroid

        self.initialized = True

    def classify(self, query: str) -> Dict[str, any]:
        """
        Классифицирует запрос пользователя.

        Args:
            query: Текст запроса

        Returns:
            {"intent": str, "confidence": float, "needs_rag": bool, "scores": {...}}
        """
        if not self.initialized:
            raise RuntimeError("Classifier not initialized. Call initialize() first.")

        # Получаем embedding запроса
        query_emb = self.embed_fn([query])
        if isinstance(query_emb, list):
            query_emb = np.array(query_emb)
        query_vec = query_emb[0]
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        # Cosine similarity с каждым центроидом
        scores = {}
        for intent, centroid in self.centroids.items():
            scores[intent] = float(np.dot(query_vec, centroid))

        # Выбираем лучший
        best_intent = max(scores, key=scores.get)
        best_score = scores[best_intent]

        # Confidence: разница между лучшим и вторым лучшим
        sorted_scores = sorted(scores.values(), reverse=True)
        margin = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]

        return {
            "intent": best_intent,
            "confidence": max(0.0, best_score),
            "margin": margin,
            "needs_rag": INTENT_NEEDS_RAG.get(best_intent, False),
            "scores": scores,
        }

    def classify_batch(self, queries: List[str]) -> List[Dict[str, any]]:
        """Классифицирует список запросов."""
        return [self.classify(q) for q in queries]
