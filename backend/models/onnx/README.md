# LaBSE ONNX

ONNX Runtime inference для LaBSE — замена SentenceTransformers на CPU (Tier 1-2).

## Файлы

| Файл | Описание |
|------|----------|
| `labse.onnx` | FP32 модель (~490 MB) |
| `labse_onnx.py` | Inference wrapper (LaBSEOnnx) |
| `export_labse.py` | Скрипт экспорта из SentenceTransformers |
| `tokenizer.*` | Токенизатор (копия из models/st/LaBSE/) |

## Бенчмарк: ONNX FP32 vs SentenceTransformers (CPU)

Тест на Intel CPU, batch encoding русских юридических текстов:

| Batch | ONNX FP32 (CPU) | SentenceTransformers (CPU) | Speedup |
|-------|-----------------|---------------------------|---------|
| 10 текстов | 0.043s | 0.057s | **1.3x** |
| 50 текстов | 0.176s | 0.240s | **1.4x** |
| 200 текстов | 0.694s | 0.814s | **1.2x** |

**Точность:** cosine similarity между ONNX и PyTorch > 0.99 на всех текстах.

**Преимущества ONNX:**
- Не требует PyTorch/CUDA — меньше зависимостей для CPU-only серверов
- Стабильное ускорение 1.2-1.4x на CPU
- Фиксированный формат — не зависит от версии PyTorch/transformers

**INT8 квантизация:** динамическая квантизация ломает embedding space LaBSE (все вектора коллапсируют). Для корректной INT8 нужна static quantization с calibration dataset (TODO).

## Использование

```python
from models.onnx.labse_onnx import LaBSEOnnx

model = LaBSEOnnx("models/onnx/labse.onnx")
embeddings = model.encode(["тестовый текст"])  # shape: (1, 768)
```

## Экспорт

```bash
cd backend
python -m models.onnx.export_labse
```

Требования: `torch`, `transformers`, `onnxruntime`, `onnxscript`, `safetensors`.
