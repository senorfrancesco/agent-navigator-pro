"""
Hybrid Retriever — BM25 + Dense (LaBSE) + RRF fusion.

Компоненты:
1. BM25 для русского текста (встроенная реализация)
2. Dense retrieval через LaBSE embeddings
3. Reciprocal Rank Fusion (RRF) для объединения результатов
4. Z-score grading (без LLM) для оценки качества retrieval
5. Rocchio query expansion (без LLM) для улучшения recall
"""

import math
import re
import numpy as np
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


@dataclass
class RetrievalResult:
    """Результат поиска с метаданными."""
    text: str
    score: float
    index: int  # Индекс в исходном списке чанков
    source: str = ""  # "bm25" | "dense" | "hybrid"
    metadata: dict = field(default_factory=dict)


# Русские стоп-слова
RUSSIAN_STOPWORDS = {
    "и", "в", "во", "на", "с", "со", "к", "по", "о", "об", "от", "из", "за", "до",
    "для", "при", "без", "не", "ни", "но", "а", "или", "да", "то", "же", "ли",
    "бы", "что", "как", "это", "этот", "эта", "эти", "тот", "та", "те",
    "он", "она", "оно", "они", "мы", "вы", "я", "ты", "его", "её", "их",
    "который", "которая", "которые", "которого", "которой", "которых",
    "быть", "был", "была", "были", "будет", "будут", "есть", "нет",
    "очень", "уже", "ещё", "еще", "также", "тоже", "только", "всего",
    "все", "всё", "весь", "вся", "каждый", "другой", "такой", "самый",
    "если", "когда", "так", "тогда", "потом", "здесь", "там", "где",
}


def tokenize_russian(text: str) -> List[str]:
    """Простая токенизация для русского текста."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    tokens = text.split()
    return [t for t in tokens if t not in RUSSIAN_STOPWORDS and len(t) > 1]


class BM25:
    """BM25 для русского текста."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = 0
        self.avgdl = 0.0
        self.doc_freqs: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.doc_lens: List[int] = []
        self.tf: List[Dict[str, int]] = []

    def fit(self, documents: List[str]):
        """Индексирует корпус документов."""
        self.corpus_size = len(documents)
        self.doc_lens = []
        self.tf = []
        self.doc_freqs = Counter()

        for doc in documents:
            tokens = tokenize_russian(doc)
            self.doc_lens.append(len(tokens))
            tf = Counter(tokens)
            self.tf.append(tf)
            for token in set(tokens):
                self.doc_freqs[token] += 1

        self.avgdl = sum(self.doc_lens) / max(self.corpus_size, 1)

        # IDF
        self.idf = {}
        for word, freq in self.doc_freqs.items():
            self.idf[word] = math.log(
                (self.corpus_size - freq + 0.5) / (freq + 0.5) + 1
            )

    def search(self, query: str, top_k: int = 10) -> List[Tuple[int, float]]:
        """
        Поиск по запросу.

        Returns:
            [(doc_index, score), ...] отсортированный по убыванию score
        """
        query_tokens = tokenize_russian(query)
        scores = []

        for i in range(self.corpus_size):
            score = 0.0
            dl = self.doc_lens[i]
            for token in query_tokens:
                if token not in self.idf:
                    continue
                tf = self.tf[i].get(token, 0)
                idf = self.idf[token]
                tf_norm = (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1))
                )
                score += idf * tf_norm
            scores.append((i, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


class HybridRetriever:
    """
    Hybrid search: BM25 + Dense (LaBSE) + RRF fusion.

    Поддерживает:
    - BM25 для лексического поиска (русский текст)
    - Dense retrieval через embedding function
    - RRF (Reciprocal Rank Fusion) для объединения
    - Z-score grading для оценки качества
    - Rocchio query expansion для улучшения recall
    """

    def __init__(
        self,
        embed_fn: Optional[Callable] = None,
        rrf_k: int = 60,
        use_bm25: bool = True,
    ):
        """
        Args:
            embed_fn: Функция embeddings: (List[str]) -> np.ndarray (N, dim)
            rrf_k: Параметр RRF (default=60, стандарт)
            use_bm25: Использовать BM25 в дополнение к dense
        """
        self.embed_fn = embed_fn
        self.rrf_k = rrf_k
        self.use_bm25 = use_bm25

        self.bm25 = BM25() if use_bm25 else None
        self.documents: List[str] = []
        self.doc_embeddings: Optional[np.ndarray] = None
        self.indexed = False

    def index(self, documents: List[str]):
        """
        Индексирует документы для поиска.

        Args:
            documents: Список текстов чанков
        """
        self.documents = documents

        if self.use_bm25 and self.bm25:
            self.bm25.fit(documents)

        if self.embed_fn is not None:
            embeddings = self.embed_fn(documents)
            if isinstance(embeddings, list):
                embeddings = np.array(embeddings)
            # L2-нормализация
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            self.doc_embeddings = embeddings / norms

        self.indexed = True

    def search(
        self,
        query: str,
        top_k: int = 5,
        mode: str = "hybrid",
    ) -> List[RetrievalResult]:
        """
        Поиск по запросу.

        Args:
            query: Текст запроса
            top_k: Количество результатов
            mode: "hybrid" | "bm25" | "dense"

        Returns:
            Список RetrievalResult отсортированный по score
        """
        if not self.indexed:
            raise RuntimeError("Retriever not indexed. Call index() first.")

        if mode == "bm25":
            return self._search_bm25(query, top_k)
        elif mode == "dense":
            return self._search_dense(query, top_k)
        else:
            return self._search_hybrid(query, top_k)

    def _search_bm25(self, query: str, top_k: int) -> List[RetrievalResult]:
        """Лексический поиск через BM25."""
        if not self.bm25:
            return []
        results = self.bm25.search(query, top_k)
        return [
            RetrievalResult(
                text=self.documents[idx],
                score=score,
                index=idx,
                source="bm25",
            )
            for idx, score in results if score > 0
        ]

    def _search_dense(self, query: str, top_k: int) -> List[RetrievalResult]:
        """Семантический поиск через embeddings."""
        if self.embed_fn is None or self.doc_embeddings is None:
            return []

        query_emb = self.embed_fn([query])
        if isinstance(query_emb, list):
            query_emb = np.array(query_emb)
        query_vec = query_emb[0]
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        # Cosine similarity
        scores = self.doc_embeddings @ query_vec
        top_indices = np.argsort(scores)[::-1][:top_k]

        return [
            RetrievalResult(
                text=self.documents[idx],
                score=float(scores[idx]),
                index=idx,
                source="dense",
            )
            for idx in top_indices
        ]

    def _search_hybrid(self, query: str, top_k: int) -> List[RetrievalResult]:
        """Hybrid search: BM25 + Dense + RRF fusion."""
        # Получаем больше кандидатов для fusion
        fetch_k = top_k * 3

        bm25_results = self._search_bm25(query, fetch_k) if self.use_bm25 else []
        dense_results = self._search_dense(query, fetch_k)

        if not bm25_results:
            return dense_results[:top_k]
        if not dense_results:
            return bm25_results[:top_k]

        # RRF fusion
        rrf_scores: Dict[int, float] = {}

        for rank, result in enumerate(bm25_results):
            rrf_scores[result.index] = rrf_scores.get(result.index, 0) + 1.0 / (self.rrf_k + rank + 1)

        for rank, result in enumerate(dense_results):
            rrf_scores[result.index] = rrf_scores.get(result.index, 0) + 1.0 / (self.rrf_k + rank + 1)

        # Сортировка по RRF score
        sorted_indices = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        return [
            RetrievalResult(
                text=self.documents[idx],
                score=score,
                index=idx,
                source="hybrid",
            )
            for idx, score in sorted_indices[:top_k]
        ]

    def grade_results(self, results: List[RetrievalResult]) -> List[RetrievalResult]:
        """
        Z-score grading — оценка качества без LLM.

        Добавляет z_score и grade в metadata каждого результата.
        grade: "excellent" | "good" | "marginal" | "poor"
        """
        if not results:
            return results

        scores = [r.score for r in results]
        mean = np.mean(scores)
        std = np.std(scores) if len(scores) > 1 else 1.0
        if std == 0:
            std = 1.0

        for r in results:
            z = (r.score - mean) / std
            r.metadata["z_score"] = float(z)
            if z > 1.0:
                r.metadata["grade"] = "excellent"
            elif z > 0.0:
                r.metadata["grade"] = "good"
            elif z > -0.5:
                r.metadata["grade"] = "marginal"
            else:
                r.metadata["grade"] = "poor"

        return results

    def rocchio_expand(
        self,
        query: str,
        relevant_results: List[RetrievalResult],
        alpha: float = 1.0,
        beta: float = 0.75,
        top_k: int = 5,
    ) -> List[RetrievalResult]:
        """
        Rocchio query expansion в embedding space — без LLM.

        Создаёт модифицированный query vector как:
            q' = alpha * q + beta * mean(relevant_docs)

        Затем re-retrieves с новым вектором.
        """
        if self.embed_fn is None or self.doc_embeddings is None:
            return self.search(query, top_k)

        # Оригинальный query embedding
        query_emb = self.embed_fn([query])
        if isinstance(query_emb, list):
            query_emb = np.array(query_emb)
        query_vec = query_emb[0]

        # Средний вектор релевантных документов
        if relevant_results:
            rel_indices = [r.index for r in relevant_results if r.index < len(self.doc_embeddings)]
            if rel_indices:
                rel_embs = self.doc_embeddings[rel_indices]
                rel_centroid = np.mean(rel_embs, axis=0)
            else:
                rel_centroid = np.zeros_like(query_vec)
        else:
            rel_centroid = np.zeros_like(query_vec)

        # Rocchio expansion
        expanded = alpha * query_vec + beta * rel_centroid
        norm = np.linalg.norm(expanded)
        if norm > 0:
            expanded = expanded / norm

        # Re-retrieve с expanded vector
        scores = self.doc_embeddings @ expanded
        top_indices = np.argsort(scores)[::-1][:top_k]

        return [
            RetrievalResult(
                text=self.documents[idx],
                score=float(scores[idx]),
                index=idx,
                source="rocchio",
            )
            for idx in top_indices if scores[idx] > 0
        ]
