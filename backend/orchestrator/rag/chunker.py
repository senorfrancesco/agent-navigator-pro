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

# Паттерн позиции спецификации: "N. Название\n   - Характеристика: значение"
# Детектируем структуру: ^\d+\. <текст> \n (пробелы/таб) - <атрибут>
SPEC_ITEM_PATTERN = re.compile(
    r'^(\d+)\.\s+(.+?)(?=^\d+\.\s|\Z)',
    re.MULTILINE | re.DOTALL,
)
SPEC_BULLET_PATTERN = re.compile(r'^\s+[-–—]\s+\S', re.MULTILINE)

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

        # Детектируем спецификацию: пронумерованные позиции с bullet-атрибутами
        if self._is_specification(text):
            return self._chunk_specification(text, doc_name)

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

    def _is_specification(self, text: str) -> bool:
        """
        Определяет, является ли документ спецификацией/списком позиций.

        Критерий: >=3 нумерованных позиций верхнего уровня (^\d+\. Текст)
        И хотя бы половина из них содержат bullet-атрибуты (- Характеристика:).
        """
        items = SPEC_ITEM_PATTERN.findall(text)
        if len(items) < 3:
            return False
        bullets = len(SPEC_BULLET_PATTERN.findall(text))
        return bullets >= len(items) // 2

    def _chunk_specification(self, text: str, doc_name: str) -> List[Chunk]:
        """
        Разбивает спецификацию: каждая нумерованная позиция → отдельный чанк.
        Заголовок/итоги документа идут в отдельный чанк.
        """
        chunks = []
        idx = 0

        # Находим все позиции и их границы
        matches = list(SPEC_ITEM_PATTERN.finditer(text))

        if not matches:
            return self._chunk_section(text, "") and [Chunk(
                text=text.strip(), index=0, start_char=0, end_char=len(text),
                metadata={"doc_name": doc_name}
            )]

        # Текст до первой позиции (заголовок документа)
        header = text[:matches[0].start()].strip()
        if header:
            chunks.append(Chunk(
                text=header,
                index=idx,
                start_char=0,
                end_char=matches[0].start(),
                section="Заголовок",
                metadata={"doc_name": doc_name, "chunk_type": "header"},
            ))
            idx += 1

        # Каждая позиция — отдельный чанк
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            item_text = text[start:end].strip()

            if not item_text:
                continue

            # Если позиция слишком большая — разбиваем дополнительно
            if len(item_text) > self.max_size:
                sub_chunks = self._chunk_section(item_text, f"Позиция {match.group(1)}")
                for sub in sub_chunks:
                    chunks.append(Chunk(
                        text=sub.strip(),
                        index=idx,
                        start_char=start,
                        end_char=end,
                        section=f"Позиция {match.group(1)}",
                        metadata={"doc_name": doc_name, "chunk_type": "spec_item",
                                  "item_num": match.group(1)},
                    ))
                    idx += 1
            else:
                chunks.append(Chunk(
                    text=item_text,
                    index=idx,
                    start_char=start,
                    end_char=end,
                    section=f"Позиция {match.group(1)}",
                    metadata={"doc_name": doc_name, "chunk_type": "spec_item",
                              "item_num": match.group(1)},
                ))
                idx += 1

        # Текст после последней позиции (итоги)
        footer = text[matches[-1].end():].strip()
        if footer and len(footer) > 50:
            chunks.append(Chunk(
                text=footer,
                index=idx,
                start_char=matches[-1].end(),
                end_char=len(text),
                section="Итоги",
                metadata={"doc_name": doc_name, "chunk_type": "footer"},
            ))

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
