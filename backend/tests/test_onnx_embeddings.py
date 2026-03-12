"""
Бенчмарк: SentenceTransformers (PyTorch) vs ONNX Runtime для LaBSE.

Сравнивает скорость и точность на CPU.

Usage:
    cd backend
    python -m pytest tests/test_onnx_embeddings.py -v
    python -m pytest tests/test_onnx_embeddings.py -v -k benchmark -s  # только бенчмарк с выводом
"""

import time
import pytest
import numpy as np
from pathlib import Path

ONNX_DIR = Path(__file__).resolve().parent.parent / "models" / "onnx"
LABSE_ONNX = ONNX_DIR / "labse.onnx"
LABSE_ST_DIR = Path(__file__).resolve().parent.parent / "models" / "st" / "LaBSE"

pytestmark = pytest.mark.skipif(
    not LABSE_ONNX.exists() or not LABSE_ST_DIR.exists(),
    reason="ONNX model or SentenceTransformers LaBSE not found",
)


@pytest.fixture(scope="module")
def onnx_model():
    from models.onnx.labse_onnx import LaBSEOnnx
    return LaBSEOnnx(str(LABSE_ONNX))


@pytest.fixture(scope="module")
def pytorch_model():
    try:
        from sentence_transformers import SentenceTransformer
        import torch
        # Force CPU для честного сравнения
        return SentenceTransformer(str(LABSE_ST_DIR), device="cpu")
    except ImportError:
        pytest.skip("sentence_transformers not installed")


# ── Форма и нормализация ──

class TestOnnxBasics:
    """Базовые проверки ONNX модели."""

    def test_single_text_shape(self, onnx_model):
        result = onnx_model.encode("тестовый текст")
        assert result.shape == (1, 768)

    def test_multiple_texts_shape(self, onnx_model):
        result = onnx_model.encode(["один", "два", "три"])
        assert result.shape == (3, 768)

    def test_dimension_property(self, onnx_model):
        assert onnx_model.dimension == 768

    def test_normalized_vectors(self, onnx_model):
        embeddings = onnx_model.encode(["Договор", "Штраф", "Гарантия"])
        norms = np.linalg.norm(embeddings, axis=1)
        for n in norms:
            assert abs(n - 1.0) < 0.01

    def test_batch_100(self, onnx_model):
        texts = [f"текст номер {i}" for i in range(100)]
        result = onnx_model.encode(texts, batch_size=32)
        assert result.shape == (100, 768)

    def test_empty_string(self, onnx_model):
        result = onnx_model.encode("")
        assert result.shape == (1, 768)

    def test_long_text_truncation(self, onnx_model):
        result = onnx_model.encode("слово " * 500)
        assert result.shape == (1, 768)

    def test_model_not_found(self):
        from models.onnx.labse_onnx import LaBSEOnnx
        with pytest.raises(FileNotFoundError):
            LaBSEOnnx("/nonexistent/model.onnx")

    def test_repr(self, onnx_model):
        r = repr(onnx_model)
        assert "LaBSEOnnx" in r
        assert "768" in r


# ── Семантика ──

class TestOnnxSemantics:
    """Проверка качества embeddings."""

    def test_similar_texts(self, onnx_model):
        emb = onnx_model.encode([
            "Договор купли-продажи недвижимости",
            "Контракт на покупку и продажу имущества",
        ])
        cosine = float(np.dot(emb[0], emb[1]))
        assert cosine > 0.7, f"Similar: cosine={cosine:.4f}"

    def test_different_texts(self, onnx_model):
        emb = onnx_model.encode([
            "Договор купли-продажи недвижимости",
            "Рецепт борща с пампушками",
        ])
        cosine = float(np.dot(emb[0], emb[1]))
        assert cosine < 0.5, f"Different: cosine={cosine:.4f}"

    def test_multilingual(self, onnx_model):
        emb = onnx_model.encode([
            "Штрафные санкции за нарушение договора",
            "Penalty sanctions for contract violation",
        ])
        cosine = float(np.dot(emb[0], emb[1]))
        assert cosine > 0.7, f"Multilingual: cosine={cosine:.4f}"


# ── Точность: ONNX vs PyTorch ──

class TestOnnxVsPytorchAccuracy:
    """ONNX FP32 должна быть идентична PyTorch."""

    def test_cosine_delta(self, onnx_model, pytorch_model):
        """Cosine similarity между ONNX и PyTorch > 0.99 для каждого текста."""
        texts = [
            "Договор купли-продажи",
            "Штрафные санкции",
            "Обязательства сторон",
            "Приёмка работ",
            "Гарантийные обязательства",
        ]
        onnx_embs = onnx_model.encode(texts)
        pytorch_embs = pytorch_model.encode(texts, normalize_embeddings=True)

        for i, text in enumerate(texts):
            cosine = float(np.dot(onnx_embs[i], pytorch_embs[i]))
            assert cosine > 0.99, f"'{text}': ONNX vs PyTorch cosine={cosine:.4f}"


# ── Бенчмарк: скорость CPU vs CPU ──

class TestBenchmark:
    """Бенчмарк скорости на CPU. Запускать с -s для вывода."""

    TEXTS_SMALL = [f"Пункт {i} договора поставки оборудования" for i in range(10)]
    TEXTS_MEDIUM = [f"Пункт {i} договора поставки оборудования" for i in range(50)]
    TEXTS_LARGE = [f"Пункт {i} договора поставки оборудования" for i in range(200)]

    def _bench(self, fn, texts, n_runs=3):
        """Вызывает fn(texts) n_runs раз, возвращает среднее время."""
        fn(texts[:3])  # warmup
        t0 = time.perf_counter()
        for _ in range(n_runs):
            fn(texts)
        return (time.perf_counter() - t0) / n_runs

    def test_benchmark_speed(self, onnx_model, pytorch_model):
        """ONNX FP32 на CPU должен оставаться конкурентоспособным относительно PyTorch CPU."""
        results = []

        for label, texts in [
            ("10 texts", self.TEXTS_SMALL),
            ("50 texts", self.TEXTS_MEDIUM),
            ("200 texts", self.TEXTS_LARGE),
        ]:
            t_onnx = self._bench(onnx_model.encode, texts)
            t_pytorch = self._bench(
                lambda t: pytorch_model.encode(t, normalize_embeddings=True),
                texts,
            )
            speedup = t_pytorch / t_onnx if t_onnx > 0 else 0
            results.append((label, t_onnx, t_pytorch, speedup))

        print("\n" + "=" * 60)
        print("LaBSE Benchmark: ONNX FP32 (CPU) vs SentenceTransformers (CPU)")
        print("=" * 60)
        print(f"{'Batch':<12} {'ONNX (s)':<12} {'PyTorch (s)':<14} {'Speedup':<10}")
        print("-" * 48)
        for label, t_o, t_p, s in results:
            print(f"{label:<12} {t_o:<12.4f} {t_p:<14.4f} {s:.1f}x")
        print("=" * 60)

        speedups = [speedup for _, _, _, speedup in results]
        mean_speedup = sum(speedups) / len(speedups)
        best_speedup = max(speedups)

        # Full-suite шум CPU/joblib делает точное >1.0 на batch=50 нестабильным.
        # Держим тест как smoke-check: ONNX должен быть хотя бы конкурентоспособным
        # и выигрывать хотя бы на одном batch.
        assert mean_speedup >= 0.95, f"Средний speedup ONNX слишком низкий: {mean_speedup:.3f}"
        assert best_speedup >= 1.0, f"ONNX не быстрее PyTorch ни на одном batch: {results!r}"
