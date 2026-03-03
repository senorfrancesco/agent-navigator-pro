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

import os
import yaml
import logging
import numpy as np
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("classifier")

@dataclass
class ClassificationResult:
    """Результат классификации."""
    intent: str
    confidence: float
    needs_rag: bool
    scores: Dict[str, float]


# Резервные эталонные фразы (если YAML не найден)
DEFAULT_INTENT_EXAMPLES = {
    "greeting": ["Привет", "Здравствуйте", "Добрый день"],
    "compare_documents": ["Сравни документы", "Какие различия"],
    "equipment_analysis": ["Проанализируй смету", "Проверь оборудование"],
    "document_analysis": ["Что в этом документе", "Сделай обзор"],
    "document_question": ["Найди информацию о", "Что написано про"],
    "general_chat": ["Что ты умеешь", "Помоги мне"],
}

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

    def _load_examples(self) -> Dict[str, List[str]]:
        """Загружает примеры из YAML-файла."""
        if os.path.exists(self.examples_path):
            try:
                with open(self.examples_path, 'r', encoding='utf-8') as f:
                    examples = yaml.safe_load(f)
                    if isinstance(examples, dict) and len(examples) > 0:
                        logger.info(f"Loaded intent examples from {self.examples_path}")
                        return examples
            except Exception as e:
                logger.error(f"Error loading intent examples from {self.examples_path}: {e}")
        
        logger.warning("Using default intent examples (hardcoded fallback)")
        return DEFAULT_INTENT_EXAMPLES

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
