import os
import time
import glob
import logging
import re
import textwrap
from pathlib import Path
from typing import Optional

logger = logging.getLogger("report_utils")


def _get_pdf_font_path() -> Optional[str]:
    candidates = [
        os.getenv("REPORT_PDF_FONT_PATH", ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _render_markdown_html(report_text: str) -> str:
    try:
        import markdown
    except Exception as e:
        raise RuntimeError(
            "Python-Markdown не установлен. Добавьте зависимость Markdown в окружение."
        ) from e

    return markdown.markdown(
        report_text or "",
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html5",
    )


def _build_pdf_html_document(report_text: str) -> str:
    body_html = _render_markdown_html(report_text)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <style>
    @page {{
      size: A4;
      margin: 18mm 16mm 18mm 16mm;
    }}
    body {{
      font-family: "DejaVu Sans", "Noto Sans", sans-serif;
      font-size: 11pt;
      line-height: 1.45;
      color: #1f2937;
    }}
    h1, h2, h3, h4 {{
      color: #111827;
      font-weight: 700;
      line-height: 1.2;
      margin: 1.1em 0 0.45em;
    }}
    h1 {{
      font-size: 22pt;
      border-bottom: 2px solid #d1d5db;
      padding-bottom: 8px;
      margin-top: 0;
    }}
    h2 {{ font-size: 16pt; }}
    h3 {{ font-size: 13pt; }}
    p, ul, ol, blockquote, table, pre {{
      margin: 0.45em 0 0.85em;
    }}
    ul, ol {{
      padding-left: 1.4em;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 9.5pt;
    }}
    th, td {{
      border: 1px solid #cbd5e1;
      padding: 6px 8px;
      vertical-align: top;
      word-wrap: break-word;
      overflow-wrap: anywhere;
    }}
    th {{
      background: #f3f4f6;
      font-weight: 700;
    }}
    code {{
      font-family: "DejaVu Sans Mono", monospace;
      font-size: 9.5pt;
      background: #f3f4f6;
      padding: 1px 4px;
      border-radius: 3px;
    }}
    pre {{
      background: #111827;
      color: #f9fafb;
      padding: 10px 12px;
      border-radius: 6px;
      white-space: pre-wrap;
      word-wrap: break-word;
    }}
    pre code {{
      background: transparent;
      color: inherit;
      padding: 0;
    }}
    blockquote {{
      margin-left: 0;
      padding: 10px 14px;
      border-left: 4px solid #60a5fa;
      background: #eff6ff;
      color: #1e3a8a;
    }}
    hr {{
      border: 0;
      border-top: 1px solid #d1d5db;
      margin: 1.2em 0;
    }}
    strong {{
      color: #111827;
    }}
  </style>
</head>
<body>
{body_html}
</body>
</html>
"""


def _write_weasyprint_pdf_report(report_text: str, filepath: str) -> None:
    try:
        from weasyprint import HTML
    except Exception as e:
        raise RuntimeError(
            "WeasyPrint не установлен. Добавьте зависимость WeasyPrint и системные библиотеки cairo/pango/gdk-pixbuf."
        ) from e

    document_html = _build_pdf_html_document(report_text)
    HTML(string=document_html, base_url=os.getcwd()).write_pdf(filepath)


def _write_plain_text_pdf_report(report_text: str, filepath: str) -> None:
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        raise RuntimeError(
            "PyMuPDF (fitz) не установлен. Добавьте зависимость pymupdf в окружение."
        ) from e

    doc = fitz.open()
    page = doc.new_page()  # A4 by default
    margin = 44
    line_height = 13
    font_size = 10
    font_path = _get_pdf_font_path()
    font_name = "helv"
    if font_path:
        font_name = "dejavu"

    y = margin
    max_y = page.rect.height - margin
    max_width_chars = 110

    for raw_line in (report_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.expandtabs(4)
        wrapped = textwrap.wrap(
            line,
            width=max_width_chars,
            replace_whitespace=False,
            drop_whitespace=False,
        ) or [""]
        for out_line in wrapped:
            if y + line_height > max_y:
                page = doc.new_page()
                y = margin
                max_y = page.rect.height - margin
            page.insert_text(
                (margin, y),
                out_line,
                fontsize=font_size,
                fontname=font_name,
                fontfile=font_path,
            )
            y += line_height

    doc.save(filepath, deflate=True, garbage=4)
    doc.close()


def _write_pdf_report(report_text: str, filepath: str) -> None:
    try:
        _write_weasyprint_pdf_report(report_text, filepath)
        logger.info("report-renderer:weasyprint path=%s", filepath)
    except Exception as exc:
        logger.warning("report-renderer:pymupdf-fallback reason=%s", exc)
        _write_plain_text_pdf_report(report_text, filepath)


def _write_markdown_report(report_text: str, filepath: str) -> None:
    Path(filepath).write_text(report_text, encoding="utf-8")


def _read_report_text(filepath: str) -> str:
    if filepath.lower().endswith(".pdf"):
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(filepath)
            parts = []
            for page in doc:
                parts.append(page.get_text("text"))
            doc.close()
            return "\n".join(parts)
        except Exception as e:
            logger.warning("Failed to read PDF report text from %s: %s", filepath, e)
            return ""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()

def get_uploads_dir() -> str:
    """Возвращает путь к директории загрузок, общую для контейнера и хоста."""
    # По умолчанию для Docker
    default_dir = "/app/uploads"
    if not os.path.exists(default_dir) and not os.getenv("UPLOADS_DIR"):
        # Фолбэк для запуска на хосте вне Docker
        default_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'backend', 'open_webui_uploads'
        )
    return os.getenv("UPLOADS_DIR", default_dir)

def save_report_with_dedup(
    report_text: str,
    prefix: str,
    input_names: list[str],
    current_metric: int,
    metric_marker: str
) -> str:
    """
    Сохраняет отчет на диск с проверкой на дубликаты за последние 60 секунд.
    Если найден недавний отчет для тех же файлов с лучшей или равной метрикой (например, найдено больше позиций),
    новый отчет отбрасывается. Если новый лучше — старый удаляется.
    
    Args:
        report_text: Текст отчета Markdown.
        prefix: Префикс файла (например, "Report_Compare", "Report_Equipment").
        input_names: Список имен исходных файлов для проверки совпадения.
        current_metric: Количественная метрика качества (найдено изменений, извлечено позиций).
        metric_marker: Строка-маркер для поиска метрики в старом файле (например, "Найдено изменений:**").
        
    Returns:
        Текст для вывода пользователю (сам отчет или сообщение о дубликате) или путь.
    """
    try:
        uploads_dir = get_uploads_dir()
        if not os.path.exists(uploads_dir):
            os.makedirs(uploads_dir, exist_ok=True)

        now = time.time()
        existing_markdown_reports = glob.glob(os.path.join(uploads_dir, f"{prefix}_*.md"))
        existing_reports = list(existing_markdown_reports)
        for pdf_path in glob.glob(os.path.join(uploads_dir, f"{prefix}_*.pdf")):
            markdown_pair = f"{os.path.splitext(pdf_path)[0]}.md"
            if markdown_pair not in existing_reports and not os.path.exists(markdown_pair):
                existing_reports.append(pdf_path)
        
        # Проверяем недавние отчеты
        for existing in existing_reports:
            if now - os.path.getmtime(existing) < 60:
                try:
                    content = _read_report_text(existing)
                    # Проверяем, что отчет относится к тем же файлам
                    if all(name in content for name in input_names if name):
                        if metric_marker in content:
                            old_count_str = content.split(metric_marker)[1].split()[0]
                            # Очищаем от знаков препинания (например, "1," -> "1")
                            clean_count = re.sub(r'[^\d]', '', old_count_str)
                            if clean_count.isdigit() and int(clean_count) >= current_metric:
                                logger.info(f"Duplicate report skipped (existing is better/equal). Existing: {existing}")
                                existing_pdf = f"{os.path.splitext(existing)[0]}.pdf"
                                return report_text + f"\n---\n**Отчет уже сохранен:** `{os.path.basename(existing_pdf if os.path.exists(existing_pdf) else existing)}`"

                        # Если новый отчет лучше (metric больше), удаляем старый
                        existing_base = os.path.splitext(existing)[0]
                        for artifact in (f"{existing_base}.md", f"{existing_base}.pdf"):
                            if os.path.exists(artifact):
                                os.remove(artifact)
                        logger.info(f"Removed older/worse report duplicate: {existing_base}")
                except Exception as e:
                    logger.warning(f"Error reading existing report {existing}: {e}")

        # Сохраняем новый
        basename = f"{prefix}_{int(now)}"
        pdf_filename = f"{basename}.pdf"
        pdf_filepath = os.path.join(uploads_dir, pdf_filename)
        markdown_filepath = os.path.join(uploads_dir, f"{basename}.md")
        _write_markdown_report(report_text, markdown_filepath)
        _write_pdf_report(report_text, pdf_filepath)
            
        logger.info(f"Saved new report: pdf=%s markdown=%s", pdf_filepath, markdown_filepath)
        return report_text + f"\n---\n**Отчет сохранен:** `{pdf_filename}`"

    except Exception as e:
        logger.error(f"Failed to save report: {e}")
        # Возвращаем текст отчета даже если не удалось сохранить на диск (чтобы UI не упал)
        return report_text
