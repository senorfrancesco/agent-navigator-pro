"""
AdaptiveRAGPipeline — tiered RAG pipeline, адаптирующийся к железу.

Tier 1 (basic retrieval): BM25+Dense -> RRF -> top-5 -> Generate
Tier 2 (corrective retrieval): IntentClassifier -> Hybrid Search -> grade -> Generate
Tier 3 (iterative retrieval): internal compat key `agentic`, фактически iterative retrieval loop
Tier 4 (planned multi-agent): internal compat key `multi-agent`, пока fallback в iterative retrieval
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .retriever import HybridRetriever, RetrievalResult
from .classifier import EmbeddingIntentClassifier
from .chunker import LegalDocumentChunker, Chunk
from services.hardware.tier_selector import describe_rag_mode

logger = logging.getLogger("RAG")
DEFAULT_CHARS_PER_TOKEN = 4.0
DEFAULT_RETRIEVED_CONTEXT_RATIO = 0.60


@dataclass
class RAGResult:
    """Результат RAG pipeline."""
    chunks: List[RetrievalResult]
    intent: Optional[Dict[str, Any]] = None
    needs_generation: bool = True
    context_text: str = ""
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class AdaptiveRAGPipeline:
    """
    Tiered RAG Pipeline — автоматически выбирает retrieval-стратегию по tier.

    Usage:
        pipeline = AdaptiveRAGPipeline(embed_fn=my_embed, rag_mode="corrective")
        pipeline.index_documents(["doc1 text", "doc2 text"])
        result = pipeline.retrieve("Какие условия договора?")
        # result.chunks — найденные чанки
        # result.context_text — готовый контекст для LLM
    """

    def __init__(
        self,
        embed_fn: Optional[Callable] = None,
        rag_mode: str = "simple",
        top_k: int = 5,
        use_bm25: bool = True,
        z_score_threshold: float = -0.5,
        max_context_chars: Optional[int] = 16000,
        effective_context_tokens: Optional[int] = None,
        retrieved_context_ratio: float = DEFAULT_RETRIEVED_CONTEXT_RATIO,
        chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    ):
        """
        Args:
            embed_fn: Функция embeddings (List[str]) -> np.ndarray
            rag_mode: internal compat key:
                - "simple" -> basic retrieval
                - "corrective" -> corrective retrieval
                - "agentic" -> iterative retrieval
                - "multi-agent" -> planned multi-agent (currently falls back)
            top_k: Количество чанков для retrieval
            use_bm25: Использовать BM25 в hybrid search
            z_score_threshold: Порог Z-score для grading (ниже = poor)
            max_context_chars: Максимум символов контекста для LLM
            effective_context_tokens: Effective context window runtime profile
            retrieved_context_ratio: Доля окна под retrieved context
            chars_per_token: Эвристика token->chars
        """
        self.embed_fn = embed_fn
        self.rag_mode = rag_mode
        self.top_k = top_k
        self.z_score_threshold = z_score_threshold
        self.effective_context_tokens = effective_context_tokens
        self.retrieved_context_ratio = max(0.1, min(0.95, retrieved_context_ratio))
        self.chars_per_token = max(1.0, chars_per_token)
        self.max_context_chars = self._resolve_max_context_chars(max_context_chars)

        self.retriever = HybridRetriever(
            embed_fn=embed_fn,
            use_bm25=use_bm25,
        )
        self.classifier = EmbeddingIntentClassifier(embed_fn=embed_fn)
        self.chunker = LegalDocumentChunker()

        self._classifier_initialized = False
        self._indexed = False

    def _resolve_max_context_chars(self, max_context_chars: Optional[int]) -> int:
        if self.effective_context_tokens:
            retrieved_tokens_budget = int(self.effective_context_tokens * self.retrieved_context_ratio)
            return max(256, int(retrieved_tokens_budget * self.chars_per_token))
        if max_context_chars is None:
            return 16000
        return int(max_context_chars)

    def configure_runtime_budget(
        self,
        *,
        effective_context_tokens: Optional[int],
        retrieved_context_ratio: Optional[float] = None,
        max_context_chars: Optional[int] = None,
    ) -> None:
        self.effective_context_tokens = effective_context_tokens
        if retrieved_context_ratio is not None:
            self.retrieved_context_ratio = max(0.1, min(0.95, retrieved_context_ratio))
        self.max_context_chars = self._resolve_max_context_chars(max_context_chars)

    def get_runtime_budget_metadata(self) -> Dict[str, Any]:
        retrieved_tokens_budget = None
        if self.effective_context_tokens is not None:
            retrieved_tokens_budget = int(self.effective_context_tokens * self.retrieved_context_ratio)
        return {
            "effective_context_tokens": self.effective_context_tokens,
            "retrieved_context_ratio": self.retrieved_context_ratio,
            "retrieved_context_tokens_budget": retrieved_tokens_budget,
            "max_context_chars": self.max_context_chars,
        }

    def index_documents(
        self,
        documents: List[str],
        chunk: bool = True,
        doc_names: Optional[List[str]] = None,
    ) -> List[Chunk]:
        """
        Индексирует документы для поиска.

        Args:
            documents: Список текстов документов
            chunk: Разбивать на чанки (True) или использовать как есть (False)
            doc_names: Имена документов для метаданных

        Returns:
            Список чанков (если chunk=True)
        """
        all_chunks = []

        if chunk:
            for i, doc_text in enumerate(documents):
                name = doc_names[i] if doc_names and i < len(doc_names) else f"doc_{i}"
                chunks = self.chunker.chunk(doc_text, doc_name=name)
                all_chunks.extend(chunks)
            texts = [c.text for c in all_chunks]
        else:
            texts = documents
            all_chunks = [
                Chunk(text=t, index=i, start_char=0, end_char=len(t))
                for i, t in enumerate(texts)
            ]

        self.retriever.index(texts)
        self._indexed = True

        # Инициализируем classifier если ещё не готов
        if self.embed_fn and not self._classifier_initialized:
            try:
                self.classifier.initialize()
                self._classifier_initialized = True
            except Exception as e:
                logger.warning(f"Classifier init failed: {e}")

        self._chunks = all_chunks
        return all_chunks

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> RAGResult:
        """
        Выполняет retrieval по выбранной стратегии.

        Args:
            query: Запрос пользователя
            top_k: Override количества результатов

        Returns:
            RAGResult с найденными чанками и метаданными
        """
        k = top_k or self.top_k

        if self.rag_mode == "simple":
            return self._retrieve_simple(query, k)
        elif self.rag_mode == "corrective":
            return self._retrieve_corrective(query, k)
        elif self.rag_mode == "agentic":
            return self._retrieve_agentic(query, k)
        else:
            # multi-agent fallback to agentic
            return self._retrieve_agentic(query, k)

    def _retrieve_simple(self, query: str, top_k: int) -> RAGResult:
        """
        Tier 1 — Simple RAG.
        BM25+Dense → RRF → top-K → контекст.
        0 дополнительных LLM-вызовов.
        """
        if not self._indexed:
            return RAGResult(
                chunks=[],
                needs_generation=True,
                metadata={"mode": "simple", "mode_label": describe_rag_mode("simple"), "error": "not_indexed"},
            )

        results = self.retriever.search(query, top_k=top_k)
        context = self._build_context(results)

        return RAGResult(
            chunks=results,
            needs_generation=True,
            context_text=context,
            metadata={
                "mode": "simple",
                "mode_label": describe_rag_mode("simple"),
                "chunks_found": len(results),
                "runtime_budget": self.get_runtime_budget_metadata(),
            },
        )

    def _retrieve_corrective(self, query: str, top_k: int) -> RAGResult:
        """
        Tier 2 — Corrective RAG.
        1. EmbeddingIntentClassifier (~5ms) → нужен ли RAG?
        2. Если нет → direct generate
        3. Если да → BM25+Dense → Z-score grading → [score > threshold?]
           - ДА → generate с контекстом
           - НЕТ → Rocchio expansion → re-retrieve → generate
        """
        # Classify intent
        intent = None
        if self._classifier_initialized:
            intent = self.classifier.classify(query)
            # Пропускаем RAG только если classifier уверен (margin > порога)
            # При низком margin (почти случайный выбор) — делаем поиск на всякий случай
            LOW_MARGIN_THRESHOLD = 0.05
            if not intent.get("needs_rag", True) and intent.get("margin", 0) > LOW_MARGIN_THRESHOLD:
                return RAGResult(
                    chunks=[],
                    intent=intent,
                    needs_generation=True,
                    context_text="",
                    metadata={
                        "mode": "corrective",
                        "mode_label": describe_rag_mode("corrective"),
                        "skipped_rag": True,
                        "intent": intent["intent"],
                    },
                )

        if not self._indexed:
            return RAGResult(
                chunks=[],
                intent=intent,
                needs_generation=True,
                metadata={"mode": "corrective", "mode_label": describe_rag_mode("corrective"), "error": "not_indexed"},
            )

        # Hybrid search
        results = self.retriever.search(query, top_k=top_k)

        # Z-score grading
        results = self.retriever.grade_results(results)

        # Проверяем качество
        good_results = [r for r in results if r.metadata.get("z_score", 0) > self.z_score_threshold]

        if good_results:
            context = self._build_context(good_results)
            return RAGResult(
                chunks=good_results,
                intent=intent,
                needs_generation=True,
                context_text=context,
                metadata={
                    "mode": "corrective",
                    "mode_label": describe_rag_mode("corrective"),
                    "quality": "good",
                    "chunks_found": len(good_results),
                    "runtime_budget": self.get_runtime_budget_metadata(),
                },
            )

        # Rocchio expansion
        expanded_results = self.retriever.rocchio_expand(query, results[:3], top_k=top_k)
        context = self._build_context(expanded_results)

        return RAGResult(
            chunks=expanded_results,
            intent=intent,
            needs_generation=True,
            context_text=context,
            metadata={
                "mode": "corrective",
                "mode_label": describe_rag_mode("corrective"),
                "quality": "expanded",
                "chunks_found": len(expanded_results),
                "runtime_budget": self.get_runtime_budget_metadata(),
            },
        )

    def _retrieve_agentic(self, query: str, top_k: int) -> RAGResult:
        """
        Tier 3 — iterative retrieval.

        Internal compat key остаётся `agentic`, но фактически это
        iterative corrective path без полноценного planner/tool-use runtime.
        """
        # Classify
        intent = None
        if self._classifier_initialized:
            intent = self.classifier.classify(query)

        if not self._indexed:
            return RAGResult(
                chunks=[],
                intent=intent,
                needs_generation=True,
                metadata={"mode": "agentic", "mode_label": describe_rag_mode("agentic"), "error": "not_indexed"},
            )

        max_iterations = 3
        all_results = []

        for iteration in range(max_iterations):
            results = self.retriever.search(query, top_k=top_k)
            results = self.retriever.grade_results(results)

            good = [r for r in results if r.metadata.get("grade") in ("excellent", "good")]

            if good:
                all_results = good
                break

            # Rocchio expansion для следующей итерации
            if iteration < max_iterations - 1:
                expanded = self.retriever.rocchio_expand(query, results[:3], top_k=top_k)
                if expanded and expanded[0].score > (results[0].score if results else 0):
                    all_results = expanded
                    break
                # Используем expanded results как fallback
                all_results = expanded if expanded else results
            else:
                all_results = results

        context = self._build_context(all_results)

        return RAGResult(
            chunks=all_results,
            intent=intent,
            needs_generation=True,
            context_text=context,
            metadata={
                "mode": "agentic",
                "mode_label": describe_rag_mode("agentic"),
                "iterations": iteration + 1,
                "chunks_found": len(all_results),
                "runtime_budget": self.get_runtime_budget_metadata(),
            },
        )

    def _build_context(self, results: List[RetrievalResult]) -> str:
        """Собирает контекст из результатов поиска с учётом лимита."""
        if not results:
            return ""

        parts = []
        total = 0
        for i, r in enumerate(results):
            text = r.text.strip()
            if total + len(text) > self.max_context_chars:
                remaining = self.max_context_chars - total
                if remaining > 100:
                    text = text[:remaining] + "..."
                else:
                    break
            parts.append(f"[Чанк {i+1}] {text}")
            total += len(text)

        return "\n\n".join(parts)

    def classify_intent(self, query: str) -> Optional[Dict[str, Any]]:
        """Классифицирует интент запроса (без retrieval)."""
        if self._classifier_initialized:
            return self.classifier.classify(query)
        return None
