"""
LaBSE ONNX Wrapper — inference через ONNX Runtime.

Замена SentenceTransformers для CPU: 2-4x ускорение, меньше памяти.

Usage:
    from models.onnx.labse_onnx import LaBSEOnnx

    model = LaBSEOnnx("models/onnx/labse.onnx")
    embeddings = model.encode(["тестовый текст"])  # shape: (1, 768)
"""

import os
import numpy as np
from pathlib import Path
from typing import List, Optional, Union

import onnxruntime as ort


class LaBSEOnnx:
    """ONNX Runtime inference для LaBSE."""

    def __init__(
        self,
        model_path: str,
        tokenizer_path: Optional[str] = None,
        max_length: int = 128,
        num_threads: Optional[int] = None,
    ):
        """
        Args:
            model_path: Путь к .onnx файлу (FP32 или INT8)
            tokenizer_path: Путь к директории с tokenizer файлами.
                           По умолчанию — та же директория что и model_path.
            max_length: Максимальная длина последовательности
            num_threads: Количество потоков для inference (None = auto)
        """
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(f"ONNX model not found: {model_path}")

        self.max_length = max_length

        # Session options
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if num_threads is not None:
            sess_options.intra_op_num_threads = num_threads
            sess_options.inter_op_num_threads = num_threads

        self.session = ort.InferenceSession(
            str(self.model_path),
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )

        # Tokenizer
        tok_path = tokenizer_path or str(self.model_path.parent)
        from transformers import AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(tok_path)

        # Output shape
        self.embedding_dim = self.session.get_outputs()[0].shape[-1]

    def encode(
        self,
        texts: Union[str, List[str]],
        batch_size: int = 32,
        normalize: bool = True,
    ) -> np.ndarray:
        """
        Генерирует embeddings для текстов.

        Args:
            texts: Строка или список строк
            batch_size: Размер батча
            normalize: L2 нормализация (True по умолчанию)

        Returns:
            np.ndarray shape (N, embedding_dim)
        """
        if isinstance(texts, str):
            texts = [texts]

        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            encoded = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="np",
            )

            feeds = {
                "input_ids": encoded["input_ids"].astype(np.int64),
                "attention_mask": encoded["attention_mask"].astype(np.int64),
            }
            # token_type_ids может отсутствовать
            if "token_type_ids" in encoded:
                feeds["token_type_ids"] = encoded["token_type_ids"].astype(np.int64)
            else:
                feeds["token_type_ids"] = np.zeros_like(feeds["input_ids"])

            outputs = self.session.run(None, feeds)
            embeddings = outputs[0]

            if normalize:
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                norms = np.where(norms == 0, 1, norms)
                embeddings = embeddings / norms

            all_embeddings.append(embeddings)

        return np.vstack(all_embeddings) if len(all_embeddings) > 1 else all_embeddings[0]

    @property
    def dimension(self) -> int:
        """Размерность embedding вектора."""
        return self.embedding_dim

    def __repr__(self) -> str:
        model_name = self.model_path.name
        size_mb = self.model_path.stat().st_size / (1024 ** 2)
        return f"LaBSEOnnx(model={model_name}, dim={self.embedding_dim}, size={size_mb:.1f}MB)"
