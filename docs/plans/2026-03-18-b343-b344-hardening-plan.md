# B3.43-B3.44 Hardening Plan

Дата: 2026-03-18

## Цель

Убрать format-specific эвристики из fallback extraction и перевести `equipment` на более устойчивую архитектуру:

1. strategy-based parsing вместо прямых веток "КП" / "ТЗ";
2. role normalization для документов (`offer_like`, `specification_like`, `generic_list_like`);
3. двухступенчатый matching (`embedder -> reranker`);
4. отчёт, который строится по нормализованным ролям, а не по жёсткой mode-логике.

## Что показал ресёрч

### 1. Извлечение структуры должно идти через элементы документа, а не через общий text chunking

Практика из Unstructured и Azure Document Intelligence:

- сначала получать элементы документа: `Table`, `Title`, `ListItem`, `NarrativeText`, `Header`, `Footer`;
- таблицы обрабатывать как отдельные структурные объекты;
- списки и линейризованные row-like блоки обрабатывать отдельно от narrative текста;
- хранить metadata по элементам, а не только итоговый plain text.

Практический вывод для нас:

- fallback parser должен работать не по "всему документу строкой", а по нормализованному списку `DocumentElement`;
- для `.docx` допустим временный local extractor из `word/document.xml`, но он должен заполнять общую intermediate schema, а не содержать бизнес-логику сравнения.

### 2. Лучший retrieval stack — bi-encoder + reranker

Практика из SentenceTransformers и Qwen:

- bi-encoder нужен только для first-stage candidate generation;
- reranker нужен как second-stage precision layer для top-k кандидатов;
- cross-encoder / reranker особенно полезен для semi-structured позиций, где similarity по названию и similarity по требованиям расходятся.

Практический вывод для нас:

- `Qwen3-Embedding-0.6B` использовать как dense candidate retriever;
- добавить `Qwen3-Reranker-0.6B` на top-k кандидатов;
- matching threshold принимать не по embedding cosine alone, а по composite score:
  - dense similarity,
  - reranker score,
  - lexical overlap,
  - quantity / numeric compatibility.

### 3. Роль документа должна определяться не query-only эвристикой, а структурными признаками

Практика из Azure / Google Document AI classifier flow:

- classification и splitting рассматриваются как отдельный stage;
- классификация документа использует layout/content features, а не только имена файлов;
- downstream extraction зависит от роли документа.

Практический вывод для нас:

- нужно вводить normalized role:
  - `specification`
  - `offer`
  - `estimate`
  - `unknown`
- решение о роли должно принимать feature scorer, а не ветка `if "коммерческое предложение" in head`.

## Рекомендуемая целевая архитектура

### Stage A. Intermediate Representation

Ввести промежуточные сущности:

- `DocumentElement`
  - `type`: `table|list_item|paragraph|title|row_like|unknown`
  - `text`
  - `page`
  - `metadata`
- `DocumentRoleDetection`
  - `role`
  - `confidence`
  - `feature_scores`
- `NormalizedItem`
  - `name`
  - `specs`
  - `quantity`
  - `price`
  - `currency`
  - `source_kind`
  - `source_ref`
  - `confidence`

### Stage B. Parser Strategies

Вынести fallback extraction в стратегии:

- `offer_like`
  - row-like коммерческие позиции
  - quantity + unit price + total
  - допускает compact rows без реальной таблицы
- `specification_like`
  - нумерованные item blocks
  - имя позиции + хвост характеристик
  - quantity может стоять отдельно
- `generic_list_like`
  - списки материалов/оборудования без строгой табличности
  - weaker confidence

Каждая стратегия:

- получает `List[DocumentElement]`
- возвращает `List[NormalizedItem]`
- возвращает `strategy_metadata`
  - `strategy_name`
  - `matched_blocks`
  - `confidence`

### Stage C. Strategy Selection

Вместо прямых keyword branches использовать feature scoring.

Пример feature groups:

- `offer_features`
  - доля строк с 2 monetary values
  - наличие quantity+price+total pattern
  - наличие предложенческой лексики
- `specification_features`
  - повторяющиеся numbered blocks
  - длинные хвосты характеристик после item name
  - наличие колонкообразных заголовков / спецификационной лексики
- `generic_list_features`
  - списки item-like сущностей без price/total
  - высокая доля list_item / row_like элементов

Решение:

- если top strategy confidence < threshold -> multi-strategy fallback
- хранить feature scores в debug metadata

### Stage D. Matching

Для `specification -> offer`:

1. normalizer строит `match_text` для item:
   - canonical name
   - extracted model/vendor tokens
   - critical specs
   - quantity bucket
2. dense retrieval (`Qwen3-Embedding-0.6B`) выбирает top-k кандидатов из правого документа;
3. reranker (`Qwen3-Reranker-0.6B`) пересчитывает top-k;
4. final score:
   - `0.45 * reranker`
   - `0.30 * dense`
   - `0.15 * token overlap`
   - `0.10 * numeric/spec compatibility`
5. assignment:
   - greedy with one-to-one lock или Hungarian по final score;
   - low-score pairs уходят в unmatched.

### Stage E. Reporting

Отчёт строить от роли документов:

- если есть `specification` и `offer`:
  - основная таблица по `specification`
  - extras из `offer` отдельным блоком
- если `estimate` vs `estimate`:
  - symmetric diff mode
- если роли uncertain:
  - explicit warning
  - обе стороны как отдельные extracted lists

## Конкретный план внедрения

### Phase 1. Stabilize Parsing

1. Вынести текущий DOCX fallback в отдельный модуль, например `backend/orchestrator/equipment_parsing.py`.
2. Ввести dataclass / TypedDict для `DocumentElement`, `NormalizedItem`, `RoleDetection`.
3. Переписать текущий fallback в три strategy parser-а:
   - `parse_offer_like_elements`
   - `parse_specification_like_elements`
   - `parse_generic_list_like_elements`
4. Добавить `score_document_role(elements)`.
5. Обновить `load_and_extract_node` так, чтобы:
   - сначала получались structured tables;
   - затем elements;
   - затем strategy selection;
   - затем только при low confidence подключался LLM extraction.

### Phase 2. Improve Matching

1. Добавить reranker client path для `Qwen3-Reranker-0.6B`.
2. Вынести matching в отдельный service layer:
   - `build_match_candidates`
   - `rerank_match_candidates`
   - `assign_matches`
3. Перевести `match_items_node` с single-score cosine на two-stage ranking.
4. В telemetry/logs писать:
   - top strategy
   - parser confidence
   - top-k candidate scores
   - final accepted / rejected pairs

### Phase 3. Normalize Report Logic

1. Вынести report assembly из текущей mode-ветки в role-aware builder.
2. Ввести role-pair matrix:
   - `specification -> offer`
   - `estimate -> estimate`
   - `unknown -> unknown`
3. Для uncertain role добавить fallback report section:
   - `Не удалось надёжно определить роли документов`
   - `Извлечённые позиции документа A`
   - `Извлечённые позиции документа B`

### Phase 4. Evaluation Harness

1. Собрать локальный gold set:
   - минимум 10 пар `ТЗ -> КП`
   - минимум 5 пар `смета -> смета`
   - минимум 5 слабоструктурных `.docx`
2. Для каждого кейса сохранять:
   - expected roles
   - expected extracted item count
   - expected key matches
   - expected unmatched extras
3. Метрики:
   - role accuracy
   - extraction recall / precision on item count
   - top-1 match accuracy
   - unmatched extras recall

## Что делать в следующем implementation slice

### Slice 1

Scope:

- parser strategies
- role scoring
- removal of direct format branches from current fallback

Files:

- new: `backend/orchestrator/equipment_parsing.py`
- update: `backend/orchestrator/workflows/equipment.py`
- tests: `backend/tests/test_equipment_workflow.py`

Acceptance:

- no direct `if "коммерческое предложение" in ...` in workflow orchestration layer
- strategy decision stored in metadata
- existing `kp_tz_equip` smoke still extracts non-zero items

### Slice 2

Scope:

- reranker integration
- score fusion
- better assignment

Files:

- `backend/services/model_manager/*`
- `backend/services/legal_server/mcp_legal_server.py`
- `backend/orchestrator/workflows/equipment.py`
- tests for ranking and assignment

Acceptance:

- matching no longer depends on embedding cosine alone
- top-k + rerank visible in logs
- false matches reduce on eval set

## Recommended non-goals for first hardening slice

- не делать сразу полноценную ML classification training pipeline;
- не вводить внешние SaaS зависимости в критический path;
- не переписывать document server;
- не пытаться решить OCR/noisy scan cases до стабилизации born-digital DOCX/PDF path.

## Sources

- Unstructured document elements:
  - https://docs.unstructured.io/api-reference/partition/document-elements
  - https://docs.unstructured.io/platform/document-elements
- Azure layout/document structure:
  - https://azure.microsoft.com/en-us/products/ai-foundry/tools/document-intelligence
  - https://ai.azure.com/catalog/models/Azure-Content-Understanding-Layout
- Retrieve + rerank pattern:
  - https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html
  - https://www.sbert.net/docs/quickstart.html
  - https://www.sbert.net/docs/cross_encoder/training_overview.html
- Qwen embedding + reranker:
  - https://huggingface.co/Qwen/Qwen3-Reranker-0.6B
  - https://qwenlm.github.io/blog/qwen3-embedding/
