"""
MCP Legal Server - FastAPI приложение для анализа юридических документов.

Функции:
- Сравнение двух документов (выявление различий)
- Анализ юридической значимости различий с выделением критических изменений
- Генерация детальных отчётов

Интегрирует UMS для использования:
- Embedding-модели (Qwen3 Embedding по умолчанию) для сравнения текстов
- LLM-модели (Qwen-14B) для анализа значимости
"""

import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import json
import sys
import os
import re
import numpy as np
from scipy.optimize import linear_sum_assignment
from functools import lru_cache

# Добавляем путь к model_manager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'model_manager'))

from ums_client import UMSBusyError, ums_client
from services.model_manager.model_selection import resolve_model_selection

LEGAL_RERANKER_MODEL_PATH = os.getenv("MODEL_PATH_RERANKER", "").strip()
LEGAL_MATCH_TOP_K = max(1, int(os.getenv("LEGAL_MATCH_TOP_K", "3")))
LEGAL_ENABLE_RERANK = os.getenv("LEGAL_ENABLE_RERANK", "1").strip().lower() not in {"0", "false", "no"}

app = FastAPI(title="MCP Legal Server", version="1.0.0")

# ============================================================================
# Request/Response Models
# ============================================================================

class CompareChunksRequest(BaseModel):
    old_text: str
    new_text: str
    threshold: float = 0.72

class DifferenceItem(BaseModel):
    type: str  # "ADDED", "DELETED", "MODIFIED"
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    similarity_score: Optional[float] = None

class CompareChunksResponse(BaseModel):
    status: str
    differences: Optional[List[DifferenceItem]] = None
    model_execution: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None

class MatchBatchesRequest(BaseModel):
    list_old: List[str]
    list_new: List[str]
    threshold: float = 0.72

class MatchBatchesResponse(BaseModel):
    status: str
    matches: List[Dict[str, Any]]
    model_execution: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None

class AnalyzeImpactRequest(BaseModel):
    differences: List[DifferenceItem]

class ImpactAnalysis(BaseModel):
    difference: str
    is_critical: bool
    severity: str  # "CRITICAL", "MODERATE", "MINOR"
    impact_description: str
    recommendation: str
    similarity_score: Optional[float] = None

class AnalyzeImpactResponse(BaseModel):
    status: str
    analysis: Optional[List[ImpactAnalysis]] = None
    summary: Optional[str] = None
    model_execution: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None

class GenerateReportRequest(BaseModel):
    analysis: List[ImpactAnalysis]
    format: str = "markdown"  # "markdown" или "json"

class GenerateReportResponse(BaseModel):
    status: str
    report: Optional[str] = None
    error: Optional[str] = None

# ============================================================================
# Endpoints
# ============================================================================

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "mcp-legal-server"}


def _is_model_failover_blocked(exc: Exception) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return True
    if isinstance(exc, UMSBusyError):
        return True
    message = str(exc).lower()
    return "429" in message or "cancel" in message or "busy" in message


def _build_model_execution_event(
    *,
    selection: Any,
    used_model_id: str,
    fallback_used: bool,
    fallback_reason: Optional[str],
    attempt_count: int,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    payload = dict(selection.to_dict() if hasattr(selection, "to_dict") else selection or {})
    payload.update(
        {
            "role_key": payload.get("role_key"),
            "primary_model_id": payload.get("resolved_model_id") or payload.get("primary_model_id"),
            "fallback_model_id": payload.get("fallback_model_id") if payload.get("fallback_available") else None,
            "used_model_id": used_model_id,
            "fallback_used": bool(fallback_used),
            "fallback_stage": stage,
            "fallback_reason": fallback_reason,
            "attempt_count": max(1, int(attempt_count)),
            "status": "fallback_completed" if fallback_used else "completed",
        }
    )
    return payload


def _extract_embedding_rows(response: Dict[str, Any]) -> List[List[float]]:
    if "data" in response and isinstance(response["data"], list):
        return [list(item["embedding"]) for item in response["data"] if isinstance(item, dict) and item.get("embedding") is not None]
    if "embedding" in response:
        embedding = response["embedding"]
        if isinstance(embedding, list) and embedding and isinstance(embedding[0], list):
            return [list(row) for row in embedding]
        if isinstance(embedding, list):
            return [list(embedding)]
    raise RuntimeError("Embedding response missing data")


async def _infer_with_model_failover(
    *,
    role_key: str,
    payload: Dict[str, Any],
    device_mode: Optional[str] = None,
    stage: Optional[str] = None,
    model_execution_events: Optional[List[Dict[str, Any]]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    selection = resolve_model_selection(role_key)
    attempts = [selection.resolved_model_id]
    if selection.fallback_available and selection.fallback_model_id != selection.resolved_model_id:
        attempts.append(selection.fallback_model_id)
    last_error: Optional[Exception] = None
    for attempt_idx, model_id in enumerate(attempts, start=1):
        try:
            request_payload = dict(payload)
            if device_mode is None:
                response = await asyncio.to_thread(ums_client.infer, model_id, request_payload)
            else:
                response = await asyncio.to_thread(ums_client.infer, model_id, request_payload, device_mode)
            event = _build_model_execution_event(
                selection=selection,
                used_model_id=model_id,
                fallback_used=attempt_idx > 1,
                fallback_reason=str(last_error) if attempt_idx > 1 and last_error is not None else None,
                attempt_count=attempt_idx,
                stage=stage,
            )
            if model_execution_events is not None:
                model_execution_events.append(dict(event))
            return response, event
        except Exception as exc:
            if _is_model_failover_blocked(exc):
                raise
            last_error = exc
            if attempt_idx < len(attempts):
                logger.warning(
                    "Legal server model failover retry role=%s stage=%s primary=%s fallback=%s error=%s",
                    role_key,
                    stage or "default",
                    attempts[0],
                    attempts[1] if len(attempts) > 1 else None,
                    exc,
                    exc_info=True,
                )
                continue
            raise RuntimeError(f"Model execution failed for role {role_key} after fallback: {exc}") from exc

@app.post("/compare_chunks", response_model=CompareChunksResponse)
async def compare_chunks(request: CompareChunksRequest):
    """
    Сравнивает два текста и выявляет различия.
    
    Использует эмбеддинги через UMS для определения семантического сходства.
    """
    print(f"[LEGAL_SERVER] Comparing chunks (threshold={request.threshold})")
    
    model_execution_events: List[Dict[str, Any]] = []
    try:
        # Получаем эмбеддинги через UMS с универсальным failover
        old_response, old_event = await _infer_with_model_failover(
            role_key="legal.embedder",
            payload={"input": [request.old_text], "normalize": True},
            device_mode="cpu",
            stage="compare_chunks_old",
            model_execution_events=model_execution_events,
        )
        new_response, new_event = await _infer_with_model_failover(
            role_key="legal.embedder",
            payload={"input": [request.new_text], "normalize": True},
            device_mode="cpu",
            stage="compare_chunks_new",
            model_execution_events=model_execution_events,
        )

        old_embedding = _extract_embedding_rows(old_response)[0]
        new_embedding = _extract_embedding_rows(new_response)[0]
        
        # Вычисляем косинусное сходство
        similarity = _cosine_similarity(old_embedding, new_embedding)
        
        # Определяем тип различия
        if similarity < request.threshold:
            differences = [
                DifferenceItem(
                    type="MODIFIED",
                    old_text=request.old_text[:100],
                    new_text=request.new_text[:100],
                    similarity_score=similarity
                )
            ]
        else:
            differences = []
        
        # Также используем простой текстовый анализ для выявления конкретных изменений
        text_diffs = _extract_text_differences(request.old_text, request.new_text)
        differences.extend(text_diffs)
        
        return CompareChunksResponse(
            status="success",
            differences=differences,
            model_execution=model_execution_events,
        )
    
    except Exception as e:
        return CompareChunksResponse(
            status="error",
            error=str(e)
        )

@lru_cache(maxsize=1)
def _get_optional_reranker():
    if not LEGAL_ENABLE_RERANK or not LEGAL_RERANKER_MODEL_PATH:
        return None
    if not os.path.exists(LEGAL_RERANKER_MODEL_PATH):
        return None
    try:
        from sentence_transformers import CrossEncoder
        return CrossEncoder(LEGAL_RERANKER_MODEL_PATH)
    except Exception:
        return None


def _normalize_similarity_score(score: float) -> float:
    return max(0.0, min(1.0, (float(score) + 1.0) / 2.0))


def _normalize_rerank_score(score: float) -> float:
    score = float(score)
    if 0.0 <= score <= 1.0:
        return score
    # CrossEncoder outputs are often logits rather than probabilities.
    return 1.0 / (1.0 + np.exp(-score))


def _tokenize_match_text(text: str) -> List[str]:
    return re.findall(r"[a-zA-Zа-яА-Я0-9\+\.-]+", str(text or "").lower())


def _lexical_overlap_score(left: str, right: str) -> float:
    left_tokens = set(_tokenize_match_text(left))
    right_tokens = set(_tokenize_match_text(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / max(1, union)


def _numeric_compatibility_score(left: str, right: str) -> float:
    number_pattern = re.compile(r"\d+(?:[.,]\d+)?")
    left_numbers = set(number_pattern.findall(str(left or "")))
    right_numbers = set(number_pattern.findall(str(right or "")))
    if not left_numbers or not right_numbers:
        return 0.5
    overlap = len(left_numbers & right_numbers)
    return overlap / max(1, len(left_numbers))


def _heuristic_rerank_score(left: str, right: str) -> float:
    lexical = _lexical_overlap_score(left, right)
    numeric = _numeric_compatibility_score(left, right)
    left_lower = str(left or "").lower()
    right_lower = str(right or "").lower()
    exact_model_bonus = 0.0
    for token in _tokenize_match_text(left):
        if any(char.isdigit() for char in token) and token in right_lower:
            exact_model_bonus = max(exact_model_bonus, 0.15)
    return max(0.0, min(1.0, 0.65 * lexical + 0.25 * numeric + exact_model_bonus))


def _rerank_pair_scores(pairs: List[tuple[str, str]]) -> List[float]:
    if not pairs:
        return []
    reranker = _get_optional_reranker()
    if reranker is not None:
        try:
            scores = reranker.predict(pairs)
            return [_normalize_rerank_score(float(score)) for score in scores]
        except Exception:
            pass
    return [_heuristic_rerank_score(left, right) for left, right in pairs]


def _build_candidate_matrix(scores: np.ndarray, top_k: int) -> Dict[int, List[int]]:
    candidates: Dict[int, List[int]] = {}
    if scores.size == 0:
        return candidates
    for row_idx in range(scores.shape[0]):
        row = scores[row_idx]
        top_indices = np.argsort(row)[::-1][:top_k]
        candidates[row_idx] = [int(idx) for idx in top_indices]
    return candidates


def _build_final_score_matrix(
    list_old: List[str],
    list_new: List[str],
    dense_scores: np.ndarray,
    candidate_map: Dict[int, List[int]],
) -> tuple[np.ndarray, Dict[tuple[int, int], Dict[str, float]]]:
    final_scores = np.zeros_like(dense_scores, dtype=float)
    pair_metadata: Dict[tuple[int, int], Dict[str, float]] = {}
    rerank_pairs: List[tuple[str, str]] = []
    rerank_keys: List[tuple[int, int]] = []

    for row_idx, col_indices in candidate_map.items():
        for col_idx in col_indices:
            rerank_pairs.append((list_old[row_idx], list_new[col_idx]))
            rerank_keys.append((row_idx, col_idx))

    rerank_scores = _rerank_pair_scores(rerank_pairs)
    for (row_idx, col_idx), rerank_score in zip(rerank_keys, rerank_scores):
        dense_normalized = _normalize_similarity_score(float(dense_scores[row_idx, col_idx]))
        lexical = _lexical_overlap_score(list_old[row_idx], list_new[col_idx])
        numeric = _numeric_compatibility_score(list_old[row_idx], list_new[col_idx])
        compatibility = (lexical + numeric) / 2.0
        final_score = 0.45 * rerank_score + 0.35 * dense_normalized + 0.20 * compatibility
        final_scores[row_idx, col_idx] = final_score
        pair_metadata[(row_idx, col_idx)] = {
            "dense_score": float(dense_scores[row_idx, col_idx]),
            "dense_normalized_score": dense_normalized,
            "rerank_score": float(rerank_score),
            "lexical_overlap_score": lexical,
            "numeric_compatibility_score": numeric,
            "final_score": float(final_score),
        }

    return final_scores, pair_metadata


def _build_match_entry(
    *,
    match_type: str,
    old_text: Optional[str],
    new_text: Optional[str],
    similarity_score: float,
    meta: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": match_type,
        "old_text": old_text,
        "new_text": new_text,
        "similarity_score": float(similarity_score),
    }
    if meta:
        payload.update(meta)
    else:
        payload.setdefault("final_score", 0.0)
    return payload


async def _match_batches_impl(request: MatchBatchesRequest) -> MatchBatchesResponse:
    """
    Эффективно сопоставляет два списка чанков.
    
    1. Получает эмбеддинги для всех чанков батчем.
    2. Вычисляет матрицу сходства.
    3. Находит лучшие пары.
    """
    print(f"[LEGAL_SERVER] Matching batches: {len(request.list_old)} vs {len(request.list_new)}")
    
    try:
        if not request.list_old or not request.list_new:
            return MatchBatchesResponse(status="success", matches=[])

        # 1. Получаем эмбеддинги батчем с разбивкой на мини-батчи
        # UMS/Llama-server имеет лимит на размер контекста, поэтому разбиваем большие списки
        model_execution_events: List[Dict[str, Any]] = []

        async def get_batch_embeddings(texts: List[str], batch_size: int = 32) -> List[List[float]]:
            all_embeddings = []
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                payload = {"input": batch, "normalize": True}
                try:
                    emb_res, _event = await _infer_with_model_failover(
                        role_key="legal.embedder",
                        payload=payload,
                        device_mode="cpu",
                        stage="match_batches_embeddings",
                        model_execution_events=model_execution_events,
                    )
                    batch_embs = _extract_embedding_rows(emb_res)
                    if len(batch_embs) != len(batch):
                        raise RuntimeError(
                            f"Embedding batch size mismatch: expected {len(batch)}, got {len(batch_embs)}"
                        )
                    all_embeddings.extend(batch_embs)
                except Exception as e:
                    print(f"[LEGAL_SERVER] Error in embedding batch {i}: {e}")
                    raise
            return all_embeddings

        embs_old = np.array(await get_batch_embeddings(request.list_old))
        embs_new = np.array(await get_batch_embeddings(request.list_new))

        if embs_old.size == 0 or embs_new.size == 0:
            raise ValueError("Failed to get embeddings from UMS")

        # Строим полную матрицу косинусного сходства.
        norms_old = np.linalg.norm(embs_old, axis=1, keepdims=True)
        norms_new = np.linalg.norm(embs_new, axis=1, keepdims=True)
        embs_old_n = embs_old / np.maximum(norms_old, 1e-10)
        embs_new_n = embs_new / np.maximum(norms_new, 1e-10)
        dense_scores = embs_old_n @ embs_new_n.T  # shape: (N_old, N_new)

        candidate_map = _build_candidate_matrix(dense_scores, LEGAL_MATCH_TOP_K)
        final_scores, pair_metadata = _build_final_score_matrix(
            request.list_old,
            request.list_new,
            dense_scores,
            candidate_map,
        )

        # Венгерский алгоритм теперь работает по fused final score.
        row_ind, col_ind = linear_sum_assignment(1 - final_scores)

        matches = []
        matched_old_indices = set()
        matched_new_indices = set()
        stats = {"UNCHANGED": 0, "MODIFIED": 0, "DELETED": 0, "ADDED": 0}

        for r, c in zip(row_ind, col_ind):
            dense_similarity = float(dense_scores[r, c])
            meta = pair_metadata.get((r, c), {})
            final_score = float(meta.get("final_score", 0.0))
            if final_score >= 0.98 and dense_similarity >= 0.995:
                # Идентичные чанки — не репортим как изменение
                matches.append(_build_match_entry(
                    match_type="UNCHANGED",
                    old_text=request.list_old[r],
                    new_text=request.list_new[c],
                    similarity_score=dense_similarity,
                    meta=meta,
                ))
                matched_old_indices.add(r)
                matched_new_indices.add(c)
                stats["UNCHANGED"] += 1
            elif final_score >= request.threshold:
                matches.append(_build_match_entry(
                    match_type="MODIFIED",
                    old_text=request.list_old[r],
                    new_text=request.list_new[c],
                    similarity_score=dense_similarity,
                    meta=meta,
                ))
                matched_old_indices.add(r)
                matched_new_indices.add(c)
                stats["MODIFIED"] += 1
            else:
                matches.append(_build_match_entry(
                    match_type="DELETED",
                    old_text=request.list_old[r],
                    new_text=None,
                    similarity_score=0.0,
                    meta=meta,
                ))
                matched_old_indices.add(r)
                stats["DELETED"] += 1

        for i, text in enumerate(request.list_old):
            if i not in matched_old_indices:
                matches.append(_build_match_entry(
                    match_type="DELETED",
                    old_text=text,
                    new_text=None,
                    similarity_score=0.0,
                ))
                stats["DELETED"] += 1

        # Чанки из нового документа без пары — добавленные
        for j, text in enumerate(request.list_new):
            if j not in matched_new_indices:
                matches.append(_build_match_entry(
                    match_type="ADDED",
                    old_text=None,
                    new_text=text,
                    similarity_score=0.0,
                ))
                stats["ADDED"] += 1

        print(f"[LEGAL_SERVER] Match stats: {stats}")
        return MatchBatchesResponse(status="success", matches=matches, model_execution=model_execution_events)
    
    except Exception as e:
        print(f"[LEGAL_SERVER] Error in match_batches: {e}")
        return MatchBatchesResponse(status="error", error=str(e), matches=[])


@app.post("/match_batches", response_model=MatchBatchesResponse)
async def match_batches(request: MatchBatchesRequest):
    return await _match_batches_impl(request)


@app.post("/batch_match", response_model=MatchBatchesResponse)
async def batch_match(request: MatchBatchesRequest):
    """Backward-compatible alias for older workflow clients."""
    return await _match_batches_impl(request)

@app.post("/analyze_impact", response_model=AnalyzeImpactResponse)
async def analyze_impact(request: AnalyzeImpactRequest):
    """
    Анализирует юридическую и семантическую значимость различий.
    
    Использует LLM через UMS для генерации детального анализа.
    Выделяет критические изменения, которые влияют на смысл документа.
    """
    print(f"[LEGAL_SERVER] Analyzing impact of {len(request.differences)} differences")
    
    try:
        analysis = []
        model_execution_events: List[Dict[str, Any]] = []
        
        # Критические ключевые слова, указывающие на важные изменения
        critical_keywords = [
            "price", "payment", "cost", "fee", "liability", "indemnity",
            "termination", "breach", "penalty", "damages", "warranty",
            "confidentiality", "intellectual property", "ownership",
            "delivery", "deadline", "obligation", "responsibility"
        ]
        
        for diff in request.differences:
            # Объединяем старый и новый текст для анализа
            combined_text = f"{diff.old_text or ''} {diff.new_text or ''}".lower()
            
            # Проверяем наличие критических ключевых слов
            is_critical = any(keyword in combined_text for keyword in critical_keywords)
            
            # Определяем серьезность
            if is_critical:
                severity = "CRITICAL"
                # Используем LLM для детального анализа критических изменений
                prompt = f"""Проанализируй это изменение в юридическом документе и определи его влияние:

Тип: {diff.type}
Было: {diff.old_text}
Стало: {diff.new_text}

Ответь кратко, но полно о:
1. Как изменился смысл
2. Какие риски это создает
3. Какие действия нужны"""
                
                try:
                    impact_response, _event = await _infer_with_model_failover(
                        role_key="llm.legal_compare",
                        payload={"prompt": prompt, "max_tokens": 200, "temperature": 0.2, "top_p": 0.9},
                        stage="analyze_impact",
                        model_execution_events=model_execution_events,
                    )
                    impact_desc = (
                        impact_response.get("content")
                        or (impact_response.get("choices", [{}])[0].get("text", "") if isinstance(impact_response.get("choices"), list) else "")
                        or str(impact_response)
                    ).strip()
                except Exception:
                    impact_desc = "Critical change affecting contract terms and obligations. Manual review required."
            else:
                severity = "MINOR"
                impact_desc = "Minor change in document formatting or non-critical terms"
            
            analysis.append(ImpactAnalysis(
                difference=f"{diff.type}: '{diff.old_text}' -> '{diff.new_text}'",
                is_critical=is_critical,
                severity=severity,
                impact_description=impact_desc,
                recommendation="Immediate review required" if is_critical else "Review recommended",
                similarity_score=diff.similarity_score
            ))
        
        # Генерируем резюме
        critical_count = sum(1 for a in analysis if a.is_critical)
        summary = f"Found {critical_count} critical changes out of {len(analysis)} total differences"
        
        return AnalyzeImpactResponse(
            status="success",
            analysis=analysis,
            summary=summary,
            model_execution=model_execution_events,
        )
    
    except Exception as e:
        return AnalyzeImpactResponse(
            status="error",
            error=str(e)
        )

@app.post("/generate_report", response_model=GenerateReportResponse)
async def generate_report(request: GenerateReportRequest):
    """Генерирует детальный отчёт на основе анализа различий."""
    print(f"[LEGAL_SERVER] Generating report (format={request.format})")
    
    try:
        if request.format == "markdown":
            report = _generate_markdown_report(request.analysis)
        else:
            report = json.dumps([a.dict() for a in request.analysis], indent=2, ensure_ascii=False)
        
        return GenerateReportResponse(
            status="success",
            report=report
        )
    
    except Exception as e:
        return GenerateReportResponse(
            status="error",
            error=str(e)
        )

# ============================================================================
# Helper Functions
# ============================================================================

def _cosine_similarity(vec1: list, vec2: list) -> float:
    """Вычисляет косинусное сходство между двумя векторами."""
    if len(vec1) != len(vec2):
        return 0.0
    
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a ** 2 for a in vec1) ** 0.5
    norm2 = sum(b ** 2 for b in vec2) ** 0.5
    
    if norm1 == 0 or norm2 == 0:
        return 0.0
    
    return dot_product / (norm1 * norm2)

def _extract_text_differences(old_text: str, new_text: str) -> List[DifferenceItem]:
    """
    Извлекает конкретные текстовые различия.
    
    Анализирует:
    1. Добавленные и удаленные слова
    2. Изменения в числах (цены, сроки)
    3. Изменения в структуре предложений
    """
    differences = []
    
    # Разбиваем на слова
    old_words = old_text.split()
    new_words = new_text.split()
    
    # Простой анализ: ищем изменения в ключевых словах и числах
    old_set = set(old_words)
    new_set = set(new_words)
    
    added = new_set - old_set
    deleted = old_set - new_set
    
    # Выделяем значимые изменения (числа, валюта, сроки)
    for word in deleted:
        if _is_significant_word(word):
            differences.append(DifferenceItem(
                type="DELETED",
                old_text=word,
                new_text=None
            ))
    
    for word in added:
        if _is_significant_word(word):
            differences.append(DifferenceItem(
                type="ADDED",
                old_text=None,
                new_text=word
            ))
    
    return differences

def _is_significant_word(word: str) -> bool:
    """Определяет, является ли слово значимым для анализа."""
    # Числа, валюта, даты
    if word.replace("$", "").replace("€", "").replace(",", "").isdigit():
        return True
    
    # Слова с числами (например, "30-day", "2023-01-01")
    if any(char.isdigit() for char in word):
        return True
    
    # Ключевые слова
    important_words = ["not", "no", "yes", "must", "shall", "may", "cannot", "required"]
    if word.lower() in important_words:
        return True
    
    return False

def _generate_markdown_report(analysis: List[ImpactAnalysis]) -> str:
    """Генерирует детальный отчёт в формате Markdown."""
    report = "# Анализ различий в юридическом документе\n\n"
    
    critical_count = sum(1 for a in analysis if a.is_critical)
    moderate_count = sum(1 for a in analysis if a.severity == "MODERATE")
    minor_count = sum(1 for a in analysis if a.severity == "MINOR")
    
    report += "## Резюме\n\n"
    report += f"| Метрика | Значение |\n"
    report += f"|---------|----------|\n"
    report += f"| **Всего различий** | {len(analysis)} |\n"
    report += f"| **Критических** | {critical_count} |\n"
    report += f"| **Умеренных** | {moderate_count} |\n"
    report += f"| **Незначительных** | {minor_count} |\n\n"
    
    report += "## Детальный анализ\n\n"
    
    # Сортируем по критичности
    sorted_analysis = sorted(analysis, key=lambda x: (not x.is_critical, x.severity))
    
    for i, item in enumerate(sorted_analysis, 1):
        if item.severity == "CRITICAL":
            badge = "🔴 КРИТИЧНО"
        elif item.severity == "MODERATE":
            badge = "🟠 УМЕРЕННО"
        else:
            badge = "🟡 НЕЗНАЧИТЕЛЬНО"
        
        report += f"### {i}. {badge}\n\n"
        report += f"**Различие:** `{item.difference}`\n\n"
        report += f"**Влияние:**\n{item.impact_description}\n\n"
        report += f"**Рекомендация:** {item.recommendation}\n\n"
        
        if item.similarity_score is not None:
            report += f"*Сходство: {item.similarity_score:.2%}*\n\n"
    
    report += "---\n\n"
    report += "**Примечание:** Этот отчёт был автоматически сгенерирован. Рекомендуется провести дополнительный ручной анализ критических изменений.\n"
    
    return report

# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
