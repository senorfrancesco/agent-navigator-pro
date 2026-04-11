# Documents Test Fixtures

Рабочая памятка по содержимому `documents/`, чтобы не перечитывать каждый файл заново перед прогоном тестов и smoke-сценариев.

## Классы тестов

- `single-doc` — `ask_document`, `analyze_document_fast`, `analyze_document_deep`
- `compare` — `compare_documents_fast`, `compare_documents_deep`
- `equipment` — equipment/spec-offer role detection, extraction, compliance
- `legal` — legal RAG, legal QA, legal compare
- `ocr` — OCR / parser-robustness / graceful-failure

## Канонические пары и сценарии

- `equipment compare (PDF)`: `Requirements.pdf` + `Quotation_12.pdf`
- `equipment compare (DOCX)`: `2._KSU_1_4_24_tz-V2.docx` + `КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ.docx`
- `legal compare`: `H12100110_1621890000.pdf` + `H12300274_1688590800.pdf`
- `real equipment E2E extraction`: `f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf`
- `OCR / negative control`: `qw23.pdf`

## Матрица по файлам

| Файл | Тип документа | Рекомендуемые классы тестов | Комментарий |
|---|---|---|---|
| `Requirements.pdf` | ТЗ на поставку вычислительной техники и периферии | `single-doc`, `compare`, `equipment` | Чистый цифровой PDF, хороший baseline для extraction требований |
| `Quotation_12.pdf` | Коммерческое предложение на серверное и компьютерное оборудование | `single-doc`, `compare`, `equipment` | Чистый PDF-offer, подходит как вторая сторона для `Requirements.pdf` |
| `2._KSU_1_4_24_tz-V2.docx` | Большое DOCX-ТЗ на сетевое и серверное оборудование | `single-doc`, `compare`, `equipment` | Хороший fixture для DOCX parsing и specification-like extraction |
| `КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ.docx` | DOCX-коммерческое предложение | `single-doc`, `compare`, `equipment` | Хороший fixture для DOCX offer-like parsing |
| `f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf` | Реальное ТЗ на поставку оборудования с таблицами | `single-doc`, `equipment`, `ocr` | Лучший кандидат на integration/E2E equipment extraction |
| `H12100110_1621890000.pdf` | Закон РБ 2021, изменения в закон о СМИ | `single-doc`, `compare`, `legal` | Каноническая legal compare pair с `H123...` |
| `H12300274_1688590800.pdf` | Закон РБ 2023, изменения в закон о СМИ | `single-doc`, `compare`, `legal` | Каноническая legal compare pair с `H121...` |
| `455-z.pdf` | Закон РБ "Об информации, информатизации и защите информации" | `single-doc`, `legal` | Базовый legal QA / RAG документ |
| `2003-86(043-067).pdf` | Нормативный правовой акт МО РБ | `single-doc`, `legal` | Хороший legal single-doc fixture, не compare-first |
| `51-55 (1) (1).pdf` | Научная статья по криминологии / праву | `single-doc`, `legal`, `ocr` | Не закон и не ТЗ; полезен как negative/control и parser-robustness case |
| `qw23.pdf` | PDF с практически пустым text extraction | `ocr` | `pdftotext` даёт почти ноль текста; использовать для OCR-needed / graceful-failure |

## Что уже считается каноническим в репозитории

- `f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf` уже используется как реальный E2E fixture для equipment workflow.
- `H12100110_1621890000.pdf` + `H12300274_1688590800.pdf` уже используются как canonical legal amendment pair.
- `Requirements.pdf` уже фигурирует в benchmark/scenario inventory как requirements fixture.

## Известные несостыковки

- В `scripts/benchmark.py` ожидается имя `Quotation.pdf`, но в папке лежит `Quotation_12.pdf`.
- В `backend/tests/test_e2e_equipment.py` зашит старый абсолютный путь к `f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf`; перед переносом/перезапуском на другой машине лучше использовать repo-relative path.
