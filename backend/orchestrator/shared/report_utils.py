import os
import time
import glob
import logging
import re

logger = logging.getLogger("report_utils")

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
        existing_reports = glob.glob(os.path.join(uploads_dir, f"{prefix}_*.md"))
        
        # Проверяем недавние отчеты
        for existing in existing_reports:
            if now - os.path.getmtime(existing) < 60:
                try:
                    with open(existing, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                        # Проверяем, что отчет относится к тем же файлам
                        if all(name in content for name in input_names if name):
                            if metric_marker in content:
                                old_count_str = content.split(metric_marker)[1].split()[0]
                                # Очищаем от знаков препинания (например, "1," -> "1")
                                clean_count = re.sub(r'[^\d]', '', old_count_str)
                                if clean_count.isdigit() and int(clean_count) >= current_metric:
                                    logger.info(f"Duplicate report skipped (existing is better/equal). Existing: {existing}")
                                    return report_text + f"\n---\n**Отчет уже сохранен:** `{os.path.basename(existing)}`"
                            
                            # Если новый отчет лучше (metric больше), удаляем старый
                            os.remove(existing)
                            logger.info(f"Removed older/worse report duplicate: {existing}")
                except Exception as e:
                    logger.warning(f"Error reading existing report {existing}: {e}")

        # Сохраняем новый
        filename = f"{prefix}_{int(now)}.md"
        filepath = os.path.join(uploads_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report_text)
            
        logger.info(f"Saved new report: {filepath}")
        return report_text + f"\n---\n**Отчет сохранен:** `{filename}`"

    except Exception as e:
        logger.error(f"Failed to save report: {e}")
        # Возвращаем текст отчета даже если не удалось сохранить на диск (чтобы UI не упал)
        return report_text
