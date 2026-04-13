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
from typing import Any, Callable, Dict, List, Optional

from services.observability import inc_metric_counter
from orchestrator.utils import parse_json_garbage

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
INTENT_LABELS = tuple(INTENT_NEEDS_RAG.keys())
UNSURE_INTENT = "__unsure__"


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


class LLMIntentClassifier:
    """LLM-based intent classifier with strict JSON contract."""

    def __init__(
        self,
        infer_text_fn: Callable[[str], str],
        *,
        confidence_floor: float = 0.0,
    ):
        self.infer_text_fn = infer_text_fn
        self.confidence_floor = confidence_floor

    def _build_prompt(self, query: str) -> str:
        categories = "\n".join(f"- {intent}" for intent in INTENT_LABELS)
        return (
            "Ты классификатор интентов для оркестратора документов.\n"
            "Выбери ровно одну категорию из списка.\n\n"
            f"Категории:\n{categories}\n\n"
            "Правила:\n"
            "- greeting: приветствие, благодарность, короткий social reply\n"
            "- compare_documents: сравнение двух документов, различия, версии\n"
            "- equipment_analysis: соответствие ТЗ и КП, смета, оборудование\n"
            "- document_analysis: обзор/суммарный анализ одного документа\n"
            "- document_question: вопрос по содержимому документа(ов)\n"
            "- general_chat: общий разговор без document workflow\n\n"
            "Верни ТОЛЬКО JSON без markdown:\n"
            "{\"intent\":\"...\",\"confidence\":0.0-1.0,\"needs_rag\":true|false}\n\n"
            f"Запрос пользователя: {query}"
        )

    def _normalize_result(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        intent = payload.get("intent")
        if intent not in INTENT_LABELS:
            return None

        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(self.confidence_floor, min(confidence, 1.0))

        needs_rag_raw = payload.get("needs_rag")
        if isinstance(needs_rag_raw, bool):
            needs_rag = needs_rag_raw
        else:
            needs_rag = INTENT_NEEDS_RAG.get(intent, False)

        return {
            "intent": intent,
            "confidence": confidence,
            "margin": payload.get("margin", confidence),
            "needs_rag": needs_rag,
            "scores": payload.get("scores", {}),
            "source": "llm",
            "raw_response": payload,
        }

    def classify(self, query: str) -> Optional[Dict[str, Any]]:
        raw_text = self.infer_text_fn(self._build_prompt(query))
        parsed = parse_json_garbage(raw_text)
        if not isinstance(parsed, dict):
            logger.warning("LLM intent classifier returned non-JSON payload: %s", raw_text)
            inc_metric_counter(
                "llm_tools_platform_fallback_events_total",
                labels={"component": "intent_classifier", "fallback": "llm_non_json", "source": "classifier"},
            )
            return None
        normalized = self._normalize_result(parsed)
        if normalized is None:
            logger.warning("LLM intent classifier returned unsupported intent: %s", parsed)
            inc_metric_counter(
                "llm_tools_platform_fallback_events_total",
                labels={"component": "intent_classifier", "fallback": "llm_unsupported_intent", "source": "classifier"},
            )
        return normalized


def _build_unsure_result(
    predicted_result: Optional[Dict[str, Any]],
    *,
    source: str,
    reason: str,
    confidence_threshold: float,
    margin_threshold: float,
) -> Dict[str, Any]:
    predicted_result = dict(predicted_result or {})
    predicted_intent = predicted_result.get("intent") or "general_chat"
    return {
        "intent": UNSURE_INTENT,
        "predicted_intent": predicted_intent,
        "confidence": float(predicted_result.get("confidence", 0.0) or 0.0),
        "margin": float(predicted_result.get("margin", 0.0) or 0.0),
        "needs_rag": False,
        "scores": predicted_result.get("scores", {}),
        "abstained": True,
        "abstain_reason": reason,
        "thresholds": {
            "confidence": confidence_threshold,
            "margin": margin_threshold,
        },
        "source": source,
    }


def _passes_embedder_thresholds(
    embedder_result: Optional[Dict[str, Any]],
    *,
    confidence_threshold: float,
    margin_threshold: float,
) -> bool:
    if not embedder_result:
        return False
    confidence = float(embedder_result.get("confidence", 0.0) or 0.0)
    margin = embedder_result.get("margin")
    margin_value = confidence if margin is None else float(margin or 0.0)
    return (
        confidence >= confidence_threshold
        and margin_value >= margin_threshold
    )


def _resolve_embedder_result(
    embedder_result: Optional[Dict[str, Any]],
    *,
    confidence_threshold: float,
    margin_threshold: float,
    source: str,
    reason: str,
) -> Optional[Dict[str, Any]]:
    if _passes_embedder_thresholds(
        embedder_result,
        confidence_threshold=confidence_threshold,
        margin_threshold=margin_threshold,
    ):
        merged = dict(embedder_result or {})
        merged["source"] = source
        return merged
    if embedder_result:
        return _build_unsure_result(
            embedder_result,
            source=f"{source}_abstain" if source == "embedder" else source,
            reason=reason,
            confidence_threshold=confidence_threshold,
            margin_threshold=margin_threshold,
        )
    return None


def select_classifier_result(
    mode: str,
    *,
    embedder_result: Optional[Dict[str, Any]],
    llm_result: Optional[Dict[str, Any]],
    llm_confidence_threshold: float = 0.75,
    embedder_confidence_threshold: float = 0.60,
    embedder_margin_threshold: float = 0.10,
) -> Optional[Dict[str, Any]]:
    """Selects the final classifier result based on env-driven mode."""
    normalized_mode = (mode or "embedder").strip().lower()

    if normalized_mode == "embedder":
        return _resolve_embedder_result(
            embedder_result,
            confidence_threshold=embedder_confidence_threshold,
            margin_threshold=embedder_margin_threshold,
            source="embedder",
            reason="embedder_low_confidence",
        )

    if normalized_mode == "llm":
        if llm_result:
            return llm_result
        result = _resolve_embedder_result(
            embedder_result,
            confidence_threshold=embedder_confidence_threshold,
            margin_threshold=embedder_margin_threshold,
            source="embedder",
            reason="llm_fallback_embedder_low_confidence",
        )
        if result is not None:
            inc_metric_counter(
                "llm_tools_platform_fallback_events_total",
                labels={"component": "intent_classifier", "fallback": "llm_missing_embedder_fallback", "source": "classifier"},
            )
        return result

    if normalized_mode == "hybrid":
        if llm_result and llm_result.get("confidence", 0.0) >= llm_confidence_threshold:
            merged = dict(llm_result)
            merged["source"] = "llm"
            return merged
        embedder_fallback = _resolve_embedder_result(
            embedder_result,
            confidence_threshold=embedder_confidence_threshold,
            margin_threshold=embedder_margin_threshold,
            source="embedder_fallback",
            reason="hybrid_low_confidence",
        )
        if embedder_fallback and embedder_fallback.get("intent") != UNSURE_INTENT:
            merged = dict(embedder_fallback)
            if llm_result:
                merged["llm_fallback"] = {
                    "intent": llm_result.get("intent"),
                    "confidence": llm_result.get("confidence"),
                }
            return merged
        if embedder_result:
            inc_metric_counter(
                "llm_tools_platform_fallback_events_total",
                labels={"component": "intent_classifier", "fallback": "hybrid_low_confidence_unsure", "source": "classifier"},
            )
            result = _build_unsure_result(
                embedder_result,
                source="hybrid_unsure",
                reason="hybrid_low_confidence",
                confidence_threshold=embedder_confidence_threshold,
                margin_threshold=embedder_margin_threshold,
            )
            if llm_result:
                result["llm_fallback"] = {
                    "intent": llm_result.get("intent"),
                    "confidence": llm_result.get("confidence"),
                }
            return result
        return llm_result

    logger.warning("Unknown INTENT_CLASSIFIER_MODE=%s, falling back to embedder", mode)
    return _resolve_embedder_result(
        embedder_result,
        confidence_threshold=embedder_confidence_threshold,
        margin_threshold=embedder_margin_threshold,
        source="embedder",
        reason="embedder_low_confidence",
    ) or llm_result
