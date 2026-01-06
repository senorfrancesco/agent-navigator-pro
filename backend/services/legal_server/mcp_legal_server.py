"""
MCP Legal Server - FastAPI приложение для анализа юридических документов.

Функции:
- Сравнение двух документов (выявление различий)
- Анализ юридической значимости различий с выделением критических изменений
- Генерация детальных отчётов

Интегрирует UMS для использования:
- Embedding-модели (LaBSE) для сравнения текстов
- LLM-модели (Qwen-14B) для анализа значимости
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import json
import sys
import os

# Добавляем путь к model_manager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'model_manager'))

from ums_client import get_embeddings_via_ums, generate_text_via_ums, ums_client

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
    error: Optional[str] = None

class MatchBatchesRequest(BaseModel):
    list_old: List[str]
    list_new: List[str]
    threshold: float = 0.72

class MatchBatchesResponse(BaseModel):
    status: str
    matches: List[Dict[str, Any]]
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

@app.post("/compare_chunks", response_model=CompareChunksResponse)
async def compare_chunks(request: CompareChunksRequest):
    """
    Сравнивает два текста и выявляет различия.
    
    Использует эмбеддинги через UMS для определения семантического сходства.
    """
    print(f"[LEGAL_SERVER] Comparing chunks (threshold={request.threshold})")
    
    try:
        # Получаем эмбеддинги через UMS
        old_embedding = get_embeddings_via_ums(request.old_text)
        new_embedding = get_embeddings_via_ums(request.new_text)
        
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
            differences=differences
        )
    
    except Exception as e:
        return CompareChunksResponse(
            status="error",
            error=str(e)
        )

@app.post("/match_batches", response_model=MatchBatchesResponse)
async def match_batches(request: MatchBatchesRequest):
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
        
        def get_batch_embeddings(texts: List[str], batch_size: int = 32) -> List[List[float]]:
            all_embeddings = []
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                payload = {"input": batch, "normalize": True}
                try:
                    # Используем cpu для эмбеддингов, чтобы не занимать VRAM LLM-модели
                    emb_res = ums_client.infer("labse-embedding", payload, device_mode="cpu")
                    
                    if "data" in emb_res:
                        batch_embs = [item["embedding"] for item in emb_res["data"]]
                    elif "embedding" in emb_res:
                        # Обработка случая, если вернулся один эмбеддинг (хотя отправляли список)
                        e = emb_res["embedding"]
                        if isinstance(e[0], list): batch_embs = e
                        else: batch_embs = [e]
                    else:
                        batch_embs = []
                        print(f"[LEGAL_SERVER] Warning: No embeddings returned for batch {i}")
                    
                    all_embeddings.extend(batch_embs)
                except Exception as e:
                    print(f"[LEGAL_SERVER] Error in embedding batch {i}: {e}")
                    # В случае ошибки заполняем нулями, чтобы не ломать индексы
                    all_embeddings.extend([[0.0]*768] * len(batch))
            return all_embeddings

        embs_old = get_batch_embeddings(request.list_old)
        embs_new = get_batch_embeddings(request.list_new)
        
        if not embs_old or not embs_new:
            raise ValueError("Failed to get embeddings from UMS")

        # 2. Вычисляем сходство и сопоставляем (Greedy approach для скорости)
        matches = []
        matched_new_indices = set()
        
        # Статистика для отладки
        stats = {"UNCHANGED": 0, "MODIFIED": 0, "DELETED": 0, "ADDED": 0}
        
        for i, v_old in enumerate(embs_old):
            best_score = -1.0
            best_idx = -1
            
            for j, v_new in enumerate(embs_new):
                if j in matched_new_indices: continue
                
                score = _cosine_similarity(v_old, v_new)
                if score > best_score:
                    best_score = score
                    best_idx = j
            
            # Порог сходства
            if best_idx != -1 and best_score >= request.threshold:
                match_type = "MODIFIED" if best_score < 0.99 else "UNCHANGED"
                matches.append({
                    "type": match_type,
                    "old_text": request.list_old[i],
                    "new_text": request.list_new[best_idx],
                    "similarity_score": best_score
                })
                matched_new_indices.add(best_idx)
                stats[match_type] += 1
            else:
                matches.append({
                    "type": "DELETED",
                    "old_text": request.list_old[i],
                    "new_text": None,
                    "similarity_score": 0.0
                })
                stats["DELETED"] += 1
        
        # Добавляем новые чанки, которые не нашли пару
        for j, text in enumerate(request.list_new):
            if j not in matched_new_indices:
                matches.append({
                    "type": "ADDED",
                    "old_text": None,
                    "new_text": text,
                    "similarity_score": 0.0
                })
                stats["ADDED"] += 1

        print(f"[LEGAL_SERVER] Match stats: {stats}")
        return MatchBatchesResponse(status="success", matches=matches)
    
    except Exception as e:
        print(f"[LEGAL_SERVER] Error in match_batches: {e}")
        return MatchBatchesResponse(status="error", error=str(e), matches=[])

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
                    impact_desc = generate_text_via_ums(prompt, max_tokens=200)
                except:
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
            summary=summary
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
