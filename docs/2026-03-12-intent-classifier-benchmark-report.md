# Отчёт по бенчмарку intent-classifier

Дата: 2026-03-12

## Контекст

Цель: сравнить варианты intent-routing для текущего orchestration-сценария и понять, что безопаснее ставить по умолчанию после phase 1.

В этот бенчмарк вошли:
- текущие интенты маршрутизации:
  - `greeting`
  - `compare_documents`
  - `equipment_analysis`
  - `document_analysis`
  - `document_question`
  - `general_chat`
- режимы:
  - чистый `embedder`
  - чистый `llm`
  - `hybrid`
- dataset:
  - [intent_eval_dataset.yaml](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/data/intent_eval_dataset.yaml)
- eval runner:
  - [intent_embedder_eval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/evals/intent_embedder_eval.py)

Важно:
- benchmark для embedders запускался на `CPU`
- benchmark для `llm` и `hybrid` использовал живой `UMS` + `qwen-14b-llm`
- цифры относятся к текущему dataset и текущим prompt/parser/policy

## Как запускалось

Подъём стека:

```bash
./scripts/run_native.sh --no-attach
```

Проверка embedder-моделей:

```bash
cd backend && python evals/intent_embedder_eval.py \
  --mode embedder \
  --model LaBSE=/home/seral/HDD/proj/agent-navigator-pro/backend/models/st/LaBSE \
  --model multilingual-e5-large-instruct=/home/seral/HDD/proj/agent-navigator-pro/backend/models/st/multilingual-e5-large-instruct \
  --model Qwen3-Embedding-0.6B=/home/seral/HDD/proj/agent-navigator-pro/backend/models/st/Qwen3-Embedding-0.6B \
  --model bge-m3=/home/seral/HDD/proj/agent-navigator-pro/backend/models/st/bge-m3 \
  --json-output /tmp/intent_embedder_eval_embedder.json
```

Проверка pure `llm`:

```bash
cd backend && python evals/intent_embedder_eval.py \
  --mode llm \
  --llm-model-id qwen-14b-llm \
  --json-output /tmp/intent_embedder_eval_llm.json
```

Проверка `hybrid`:

```bash
cd backend && python evals/intent_embedder_eval.py \
  --mode hybrid \
  --llm-model-id qwen-14b-llm \
  --llm-conf-threshold 0.8 \
  --embedder-conf-threshold 0.6 \
  --embedder-margin-threshold 0.1 \
  --embedder-device cpu \
  --model Qwen3-Embedding-0.6B=/home/seral/HDD/proj/agent-navigator-pro/backend/models/st/Qwen3-Embedding-0.6B \
  --model bge-m3=/home/seral/HDD/proj/agent-navigator-pro/backend/models/st/bge-m3 \
  --json-output /tmp/intent_embedder_eval_hybrid.json
```

## Результаты

### Общая таблица

| Модель | Режим | Accuracy | Macro F1 | Cost-Weighted Accuracy | Unsure Rate | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| Qwen3-Embedding-0.6B | embedder | 0.9800 | 0.9804 | 0.9907 | 0.0000 | 125.39 | 131.28 |
| bge-m3 | embedder | 0.9400 | 0.9383 | 0.9722 | 0.0000 | 78.81 | 81.50 |
| LaBSE | embedder | 0.9200 | 0.9179 | 0.9537 | 0.0000 | 23.44 | 24.89 |
| multilingual-e5-large-instruct | embedder | 0.9000 | 0.8983 | 0.9444 | 0.0000 | 75.22 | 79.55 |
| bge-m3 | hybrid | 0.6600 | 0.7352 | 0.7130 | 0.2400 | 1150.04 | 1716.57 |
| Qwen3-Embedding-0.6B | hybrid | 0.6000 | 0.6849 | 0.6481 | 0.3000 | 1187.68 | 1740.43 |
| qwen-14b-llm | llm | 0.3600 | 0.4820 | 0.3611 | 0.5400 | 1057.61 | 1626.31 |

### Рейтинг для текущего сценария

1. `Qwen3-Embedding-0.6B` в режиме `embedder`
2. `bge-m3` в режиме `embedder`
3. `LaBSE`
4. `multilingual-e5-large-instruct`
5. текущие варианты `hybrid`
6. чистый `qwen-14b-llm`

## Интерпретация

### Лучший текущий вариант

Для текущего routing-сценария лучший вариант сейчас:

`Qwen3-Embedding-0.6B` + `INTENT_CLASSIFIER_MODE=embedder`

Почему:
- лучшая `accuracy`
- лучшая `macro_f1`
- лучшая `cost_weighted_accuracy`
- нулевой `unsure_rate`
- нет зависимости от хрупкого JSON-ответа `Qwen`

### Почему проиграл pure LLM

`qwen-14b-llm` сработал слабо именно как JSON-router в текущем setup:
- часто добавлял лишний текст до JSON
- иногда дублировал JSON-блоки
- из-за этого получился очень высокий `unsure_rate`
- итоговое качество на дорогих интентах сильно просело

Числа:
- `accuracy = 0.36`
- `cost_weighted_accuracy = 0.3611`
- `unsure_rate = 0.54`

Это означает, что pure `llm` сейчас нельзя ставить default.

### Почему проиграл hybrid

Текущий `hybrid` тоже проиграл:
- качество хуже, чем у лучшего pure embedder
- latency намного выше
- `unsure_rate` всё ещё заметный

В текущей реализации `hybrid` наследует нестабильность LLM-шага и одновременно платит полную цену по latency за оба пути.

## Характерные ошибки

### Pure LLM

Типичный паттерн ошибки:
- дорогие интенты уходили в `__unsure__`
- JSON-парсинг ломался из-за лишнего текста вокруг ответа

### Лучший pure embedder

У `Qwen3-Embedding-0.6B` ошибок мало.

Наблюдаемая характерная ошибка:
- `Сравни видеокарты для локальных LLM`
  - expected: `general_chat`
  - predicted: `compare_documents`

Это пограничная ошибка, но она намного дешевле и понятнее, чем провал pure `llm`.

## Operational Note

Во время hybrid-бенчмарка embedder inference пришлось принудительно уводить на `CPU`, потому что иначе `SentenceTransformer` пытался использовать `CUDA` и конфликтовал с уже загруженной `Qwen` в `UMS`.

Это дополнительный аргумент не делать `hybrid` default до стабилизации runtime/resource policy.

## Итог

Текущее инженерное решение:
- рекомендуемый default сейчас: `embedder`
- рекомендуемая модель сейчас: `Qwen3-Embedding-0.6B`
- для юридических документов и semantic matching оставляем `LaBSE`
- `hybrid` оставить экспериментальным
- pure `llm` оставить только как экспериментальный режим

## Что делать дальше

1. Переключить repo default на `Qwen3-Embedding-0.6B` для intent-routing, не трогая `LaBSE`
   в legal/document similarity контуре.
2. Продолжать расширять eval dataset за счёт hard negatives и ambiguity-cases.
3. Если возвращаться к `llm`, то сначала отдельно усиливать:
   - prompt
   - JSON parser
   - abstain policy
   - threshold calibration
