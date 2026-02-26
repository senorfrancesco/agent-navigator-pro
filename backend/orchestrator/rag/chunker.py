"""
Legal Document Chunker — section-aware чанкирование юридических документов.

Разбивка по:
- «Статья N», «Раздел N», «Глава N»
- Нумерация: «1.2.3.», «п. а)», «б)»
- target=800 символов, max=1500, overlap=100
- Никогда не разрезает внутри нумерованного пункта
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Chunk:
    """Чанк документа с метаданными."""
    text: str
    index: int
    start_char: int
    end_char: int
    section: str = ""  # Заголовок секции (Статья 5, Раздел 2, ...)
    metadata: dict = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)


# Паттерны начала секций юридических документов
SECTION_PATTERNS = [
    # Статья 1. / СТАТЬЯ 1.
    re.compile(r'^(?:СТАТЬЯ|Статья|статья)\s+\d+[\.\s]', re.MULTILINE),
    # Раздел 1. / РАЗДЕЛ 1.
    re.compile(r'^(?:РАЗДЕЛ|Раздел|раздел)\s+\d+[\.\s]', re.MULTILINE),
    # Глава 1. / ГЛАВА 1.
    re.compile(r'^(?:ГЛАВА|Глава|глава)\s+\d+[\.\s]', re.MULTILINE),
    # 1. / 1.1. / 1.1.1. (нумерация верхнего уровня)
    re.compile(r'^\d+\.\s', re.MULTILINE),
    # Приложение N
    re.compile(r'^(?:ПРИЛОЖЕНИЕ|Приложение)\s+[№N\d]', re.MULTILINE),
]

# Паттерн нумерованного пункта (не разрезаем внутри)
NUMBERED_ITEM_PATTERN = re.compile(
    r'^\s*(?:\d+[\.\)]\s|[а-яa-z][\.\)]\s|[-–—•]\s)',
    re.MULTILINE,
)

# Паттерн конца предложения
SENTENCE_END = re.compile(r'[.!?;]\s+')


class LegalDocumentChunker:
    """Section-aware чанкирование юридических документов."""

    def __init__(
        self,
        target_size: int = 800,
        max_size: int = 1500,
        overlap: int = 100,
        respect_sections: bool = True,
    ):
        self.target_size = target_size
        self.max_size = max_size
        self.overlap = overlap
        self.respect_sections = respect_sections

    def chunk(self, text: str, doc_name: str = "") -> List[Chunk]:
        """
        Разбивает документ на чанки с учётом структуры.

        Args:
            text: Полный текст документа
            doc_name: Имя документа (для метаданных)

        Returns:
            Список чанков
        """
        if not text or not text.strip():
            return []

        if self.respect_sections:
            sections = self._split_by_sections(text)
        else:
            sections = [("", text)]

        chunks = []
        global_idx = 0
        global_offset = 0

        for section_title, section_text in sections:
            section_chunks = self._chunk_section(section_text, section_title)
            for chunk_text in section_chunks:
                start = text.find(chunk_text, global_offset)
                if start == -1:
                    start = global_offset
                end = start + len(chunk_text)

                chunks.append(Chunk(
                    text=chunk_text.strip(),
                    index=global_idx,
                    start_char=start,
                    end_char=end,
                    section=section_title,
                    metadata={"doc_name": doc_name} if doc_name else {},
                ))
                global_idx += 1
                global_offset = max(global_offset, start + len(chunk_text) - self.overlap)

        return chunks

    def _split_by_sections(self, text: str) -> List[tuple]:
        """Разбивает текст на секции по паттернам заголовков."""
        # Находим все позиции заголовков секций
        split_points = []
        for pattern in SECTION_PATTERNS:
            for match in pattern.finditer(text):
                split_points.append((match.start(), match.group().strip()))

        if not split_points:
            return [("", text)]

        # Сортируем по позиции
        split_points.sort(key=lambda x: x[0])

        # Удаляем дубликаты (разные паттерны могут найти одно место)
        filtered = []
        for pos, title in split_points:
            if not filtered or pos - filtered[-1][0] > 10:
                filtered.append((pos, title))
        split_points = filtered

        sections = []

        # Текст до первой секции
        if split_points[0][0] > 50:  # Есть значимый текст до первой секции
            sections.append(("Преамбула", text[:split_points[0][0]].strip()))

        # Секции
        for i, (pos, title) in enumerate(split_points):
            end = split_points[i + 1][0] if i + 1 < len(split_points) else len(text)
            section_text = text[pos:end].strip()
            if section_text:
                sections.append((title, section_text))

        return sections if sections else [("", text)]

    def _chunk_section(self, text: str, section_title: str) -> List[str]:
        """Разбивает секцию на чанки с учётом размеров."""
        if len(text) <= self.target_size:
            return [text] if text.strip() else []

        chunks = []
        remaining = text

        while remaining:
            remaining = remaining.strip()
            if not remaining:
                break

            if len(remaining) <= self.max_size:
                chunks.append(remaining)
                break

            # Ищем точку разреза
            cut_point = self._find_cut_point(remaining)
            chunk = remaining[:cut_point].strip()
            if chunk:
                chunks.append(chunk)

            # Overlap
            overlap_start = max(0, cut_point - self.overlap)
            remaining = remaining[overlap_start:]

            # Защита от бесконечного цикла
            if cut_point == 0:
                # Принудительный разрез
                chunks.append(remaining[:self.max_size])
                remaining = remaining[self.max_size - self.overlap:]

        return chunks

    def _find_cut_point(self, text: str) -> int:
        """
        Находит оптимальную точку разреза.

        Приоритет:
        1. Конец нумерованного пункта перед target_size
        2. Конец предложения перед target_size
        3. Конец строки перед target_size
        4. target_size (force)
        """
        search_window = text[:self.max_size]

        # 1. Ищем конец нумерованного пункта
        # Находим начало следующего пункта после target_size/2
        item_starts = list(NUMBERED_ITEM_PATTERN.finditer(search_window))
        best_item_cut = 0
        for match in item_starts:
            pos = match.start()
            if self.target_size * 0.5 <= pos <= self.max_size:
                best_item_cut = pos
                break
            elif pos > 0 and pos <= self.target_size:
                best_item_cut = pos  # Запоминаем последний перед target

        if best_item_cut > self.target_size * 0.5:
            return best_item_cut

        # 2. Конец предложения
        best_sentence_cut = 0
        for match in SENTENCE_END.finditer(search_window):
            pos = match.end()
            if pos <= self.target_size:
                best_sentence_cut = pos
            elif pos <= self.max_size and best_sentence_cut == 0:
                best_sentence_cut = pos
                break

        if best_sentence_cut > self.target_size * 0.3:
            return best_sentence_cut

        # 3. Конец строки
        best_line_cut = 0
        for i, ch in enumerate(search_window):
            if ch == '\n' and i <= self.target_size:
                best_line_cut = i + 1

        if best_line_cut > self.target_size * 0.3:
            return best_line_cut

        # 4. Force cut at target
        return self.target_size
