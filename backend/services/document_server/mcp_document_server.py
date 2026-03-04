"""
MCP Document Server - FastAPI приложение для работы с документами.

Функции:
- Загрузка документов (PDF, DOCX, TXT)
- Извлечение текста (через OCR для изображений, если нужно)
- Извлечение таблиц
- Семантическое разбиение текста на чанки с поддержкой перекрытия

Интегрирует UMS для использования Vision-модели (Qwen-VL) для OCR.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import json
import os
from pathlib import Path
import sys

# Добавляем путь к model_manager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'model_manager'))

from ums_client import process_vision_via_ums, get_embeddings_via_ums, ums_client

app = FastAPI(title="MCP Document Server", version="1.0.0")

# ============================================================================
# Request/Response Models
# ============================================================================

class LoadDocumentRequest(BaseModel):
    path: str
    extract_tables: bool = False
    use_ocr: bool = False

class SmartChunkRequest(BaseModel):
    text: str
    max_tokens: int = 8000  # Для llama.cpp с n_ctx=8192
    overlap: int = 100      # Перекрытие в токенах

class ExtractTablesRequest(BaseModel):
    path: str
    pages: Optional[List[int]] = None

class ExtractTablesDocxRequest(BaseModel):
    path: str

class ExtractExcelRequest(BaseModel):
    path: str
    sheets: Optional[List[str]] = None

# ============================================================================
# Document Loading Functions
# ============================================================================

def load_pdf(path: str) -> str:
    """Извлечение текста из PDF."""
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                text += f"\n--- Page {page_num} ---\n"
                text += page.extract_text() or ""
        return text
    except ImportError:
        raise RuntimeError("pdfplumber not installed. Install with: pip install pdfplumber")
    except Exception as e:
        raise RuntimeError(f"Error reading PDF: {e}")

def load_docx(path: str) -> str:
    """Извлечение текста из DOCX."""
    try:
        from docx import Document
        doc = Document(path)
        text = "\n".join([para.text for para in doc.paragraphs])
        return text
    except ImportError:
        raise RuntimeError("python-docx not installed. Install with: pip install python-docx")
    except Exception as e:
        raise RuntimeError(f"Error reading DOCX: {e}")

def load_txt(path: str) -> str:
    """Загрузка текста из TXT."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        raise RuntimeError(f"Error reading TXT: {e}")

def load_excel_as_text(path: str) -> str:
    """Извлечение текста из Excel (xlsx) — ячейки через ' | '."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            parts.append(f"\n--- Sheet: {sheet_name} ---\n")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                if any(c.strip() for c in cells):
                    parts.append(" | ".join(cells))
        wb.close()
        return "\n".join(parts)
    except ImportError:
        raise RuntimeError("openpyxl not installed. Install with: pip install openpyxl")
    except Exception as e:
        raise RuntimeError(f"Error reading Excel: {e}")

# ============================================================================
# Chunking Functions
# ============================================================================

def smart_chunk(text: str, max_tokens: int = 8000, overlap: int = 100) -> List[str]:
    """
    Умное разбиение текста на чанки с поддержкой перекрытия.
    
    Стратегия:
    1. Разбиение по логическим границам (абзацы, главы)
    2. Учет размера контекста модели (max_tokens)
    3. Перекрытие между чанками для сохранения контекста
    
    Args:
        text: Исходный текст
        max_tokens: Максимальное количество токенов в чанке (примерно)
        overlap: Перекрытие в токенах между соседними чанками
    
    Returns:
        Список чанков
    """
    # Разбиваем по абзацам (логические границы)
    paragraphs = text.split('\n\n')
    
    chunks = []
    current_chunk = ""
    current_token_count = 0
    
    # Примерный подсчет: 1 слово ≈ 1.3 токена
    for para in paragraphs:
        para_tokens = len(para.split()) * 1.3
        
        # Если добавление абзаца превышает лимит, сохраняем текущий чанк
        if current_token_count + para_tokens > max_tokens and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = para + "\n\n"
            current_token_count = para_tokens
        else:
            current_chunk += para + "\n\n"
            current_token_count += para_tokens
    
    # Добавляем последний чанк
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    # Добавляем перекрытие между чанками
    if overlap > 0 and len(chunks) > 1:
        overlapped_chunks = []
        overlap_lines_count = max(1, int(overlap / 50))  # Примерно 50 токенов на строку
        
        for i, chunk in enumerate(chunks):
            if i > 0:
                # Берем последние overlap_lines из предыдущего чанка
                prev_lines = chunks[i-1].split('\n')
                overlap_lines = prev_lines[-overlap_lines_count:]
                overlapped_chunks.append('\n'.join(overlap_lines) + '\n\n' + chunk)
            else:
                overlapped_chunks.append(chunk)
        
        chunks = overlapped_chunks
    
    return chunks

def load_pdf_pages(path: str) -> List[str]:
    """Извлечение текста из PDF постранично."""
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
        return pages
    except Exception as e:
        raise RuntimeError(f"Error reading PDF pages: {e}")

# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/health")
async def health():
    """Health check."""
    return {"status": "healthy", "service": "document_server"}

@app.post("/load_document")
async def load_document(request: LoadDocumentRequest):
    """
    Загружает документ и извлекает текст целиком.
    """
    try:
        path = request.path
        
        # Проверяем существование файла
        if not os.path.exists(path):
            return {"status": "error", "error": f"File not found: {path}"}
        
        # Определяем формат файла
        file_ext = Path(path).suffix.lower()
        
        if file_ext == ".pdf":
            text = load_pdf(path)
            format_type = "pdf"
        elif file_ext == ".docx":
            text = load_docx(path)
            format_type = "docx"
        elif file_ext == ".txt":
            text = load_txt(path)
            format_type = "txt"
        elif file_ext in (".xlsx", ".xls"):
            text = load_excel_as_text(path)
            format_type = "excel"
        else:
            return {"status": "error", "error": f"Unsupported format: {file_ext}"}
        
        return {
            "status": "success",
            "text": text,
            "path": path,
            "format": format_type
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/load_pages")
async def load_pages(request: LoadDocumentRequest):
    """
    Загружает документ и возвращает список страниц (текст каждой страницы).
    Используется для Map-Reduce анализа.
    """
    try:
        path = request.path
        if not os.path.exists(path):
            return {"status": "error", "error": f"File not found: {path}"}

        file_ext = Path(path).suffix.lower()
        if file_ext == ".pdf":
            pages = load_pdf_pages(path)
        else:
            # Для не-PDF просто возвращаем весь текст как одну страницу
            full_text = load_txt(path) if file_ext == ".txt" else load_docx(path)
            pages = [full_text]
            
        return {
            "status": "success",
            "pages": pages,
            "total_pages": len(pages),
            "path": path
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/smart_chunk")
async def smart_chunk_endpoint(request: SmartChunkRequest):
    """
    Разбивает текст на чанки с учетом контекста модели.
    
    Использует семантическое разбиение по абзацам и добавляет перекрытие.
    """
    try:
        chunks = smart_chunk(
            request.text,
            max_tokens=request.max_tokens,
            overlap=request.overlap
        )
        
        return {
            "status": "success",
            "chunks": chunks,
            "chunk_count": len(chunks),
            "strategy": "semantic_with_overlap",
            "max_tokens": request.max_tokens,
            "overlap": request.overlap
        }
    
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

@app.post("/extract_tables")
async def extract_tables(request: ExtractTablesRequest):
    """
    Извлекает таблицы из PDF.
    """
    try:
        import pdfplumber
        
        if not os.path.exists(request.path):
            return {
                "status": "error",
                "error": f"File not found: {request.path}"
            }
        
        tables = []
        with pdfplumber.open(request.path) as pdf:
            pages_to_process = request.pages or range(len(pdf.pages))
            
            for page_num in pages_to_process:
                if page_num < len(pdf.pages):
                    page = pdf.pages[page_num]
                    page_tables = page.extract_tables()
                    
                    if page_tables:
                        for table in page_tables:
                            tables.append({
                                "page": page_num + 1,
                                "data": table
                            })
        
        return {
            "status": "success",
            "tables": tables,
            "table_count": len(tables)
        }
    
    except ImportError:
        return {
            "status": "error",
            "error": "pdfplumber not installed. Install with: pip install pdfplumber"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

@app.post("/extract_tables_docx")
async def extract_tables_docx(request: ExtractTablesDocxRequest):
    """Извлекает таблицы из DOCX через python-docx."""
    try:
        from docx import Document

        if not os.path.exists(request.path):
            return {"status": "error", "error": f"File not found: {request.path}"}

        doc = Document(request.path)
        tables = []
        for idx, table in enumerate(doc.tables):
            rows_data = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows_data.append(cells)
            if rows_data:
                tables.append({"page": idx + 1, "data": rows_data})

        return {
            "status": "success",
            "tables": tables,
            "table_count": len(tables),
        }
    except ImportError:
        return {"status": "error", "error": "python-docx not installed"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/extract_tables_excel")
async def extract_tables_excel(request: ExtractExcelRequest):
    """Извлекает таблицы из Excel (xlsx) через openpyxl."""
    try:
        from openpyxl import load_workbook

        if not os.path.exists(request.path):
            return {"status": "error", "error": f"File not found: {request.path}"}

        wb = load_workbook(request.path, read_only=True, data_only=True)
        tables = []
        sheet_names = request.sheets or wb.sheetnames

        for sheet_name in sheet_names:
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                if any(c.strip() for c in cells):
                    rows.append(cells)
            if rows:
                tables.append({"page": sheet_name, "data": rows})

        wb.close()
        return {
            "status": "success",
            "tables": tables,
            "table_count": len(tables),
        }
    except ImportError:
        return {"status": "error", "error": "openpyxl not installed"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
