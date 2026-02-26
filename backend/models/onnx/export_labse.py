"""
Экспорт LaBSE в ONNX FP32.

Pipeline: BertModel → CLS Pooling → Dense(768→768, Tanh) → L2 Normalize

Примечание: INT8 dynamic quantization ломает embedding space LaBSE
(все вектора коллапсируют, cosine ≈ 1.0 между любыми текстами).
Для INT8 нужна static quantization с calibration dataset — TODO.

Usage:
    cd backend
    python -m models.onnx.export_labse

Output:
    models/onnx/labse.onnx — FP32 (~490MB)
"""

import shutil
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn


LABSE_DIR = Path(__file__).resolve().parent.parent / "st" / "LaBSE"
OUTPUT_DIR = Path(__file__).resolve().parent
ONNX_FP32 = OUTPUT_DIR / "labse.onnx"


class LaBSEOnnxWrapper(nn.Module):
    """Обёртка LaBSE для ONNX: Transformer + CLS pooling + Dense + Normalize."""

    def __init__(self, labse_dir: Path):
        super().__init__()
        from transformers import AutoModel
        from safetensors.torch import load_file

        self.bert = AutoModel.from_pretrained(str(labse_dir))

        # Dense layer (2_Dense)
        dense_weights = load_file(str(labse_dir / "2_Dense" / "model.safetensors"))
        self.dense = nn.Linear(768, 768)
        self.dense.weight.data = dense_weights["linear.weight"]
        self.dense.bias.data = dense_weights["linear.bias"]
        self.activation = nn.Tanh()

    def forward(self, input_ids, attention_mask, token_type_ids):
        outputs = self.bert(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )
        # CLS pooling
        cls_output = outputs.last_hidden_state[:, 0, :]
        # Dense + Tanh
        dense_output = self.activation(self.dense(cls_output))
        # L2 Normalize
        norms = torch.norm(dense_output, p=2, dim=1, keepdim=True)
        norms = torch.clamp(norms, min=1e-12)
        normalized = dense_output / norms
        return normalized


def export_onnx():
    """Экспорт LaBSE в ONNX FP32."""
    print(f"Loading LaBSE from {LABSE_DIR}...")
    model = LaBSEOnnxWrapper(LABSE_DIR)
    model.eval()

    # Dummy inputs
    batch_size = 1
    seq_len = 128
    dummy_input_ids = torch.ones(batch_size, seq_len, dtype=torch.long)
    dummy_attention_mask = torch.ones(batch_size, seq_len, dtype=torch.long)
    dummy_token_type_ids = torch.zeros(batch_size, seq_len, dtype=torch.long)

    print(f"Exporting to {ONNX_FP32}...")
    # dynamo=False — использовать legacy TorchScript exporter,
    # который корректно встраивает веса и поддерживает dynamic_axes
    torch.onnx.export(
        model,
        (dummy_input_ids, dummy_attention_mask, dummy_token_type_ids),
        str(ONNX_FP32),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["embeddings"],
        dynamic_axes={
            "input_ids": {0: "batch_size", 1: "sequence_length"},
            "attention_mask": {0: "batch_size", 1: "sequence_length"},
            "token_type_ids": {0: "batch_size", 1: "sequence_length"},
            "embeddings": {0: "batch_size"},
        },
        opset_version=14,
        do_constant_folding=True,
        dynamo=False,
    )

    size_mb = ONNX_FP32.stat().st_size / (1024 ** 2)
    print(f"FP32 ONNX exported: {size_mb:.1f} MB")
    return ONNX_FP32


def copy_tokenizer():
    """Копирует tokenizer файлы в ONNX директорию."""
    tokenizer_files = [
        "tokenizer.json", "tokenizer_config.json",
        "vocab.txt", "special_tokens_map.json",
    ]
    copied = 0
    for fname in tokenizer_files:
        src = LABSE_DIR / fname
        dst = OUTPUT_DIR / fname
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            copied += 1
    print(f"Copied {copied} tokenizer files to {OUTPUT_DIR}")


def verify(onnx_path: Path):
    """Быстрая верификация ONNX модели."""
    import onnxruntime as ort
    from transformers import AutoTokenizer

    print(f"\nVerifying {onnx_path.name}...")
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    tokenizer = AutoTokenizer.from_pretrained(str(LABSE_DIR))

    texts = ["Договор купли-продажи", "Purchase agreement", "テスト"]
    encoded = tokenizer(texts, padding=True, truncation=True, max_length=128, return_tensors="np")

    feeds = {
        "input_ids": encoded["input_ids"].astype(np.int64),
        "attention_mask": encoded["attention_mask"].astype(np.int64),
        "token_type_ids": encoded.get("token_type_ids", np.zeros_like(encoded["input_ids"])).astype(np.int64),
    }

    outputs = sess.run(None, feeds)
    embeddings = outputs[0]

    print(f"  Shape: {embeddings.shape}")
    print(f"  Norm (should be ~1.0): {np.linalg.norm(embeddings, axis=1)}")
    print(f"  Cosine sim [0,1] (ru-en): {np.dot(embeddings[0], embeddings[1]):.4f}")
    assert embeddings.shape == (3, 768), f"Wrong shape: {embeddings.shape}"
    norms = np.linalg.norm(embeddings, axis=1)
    assert all(abs(n - 1.0) < 0.01 for n in norms), f"Bad norms: {norms}"
    print("  PASS")


if __name__ == "__main__":
    if not LABSE_DIR.exists():
        print(f"ERROR: LaBSE not found at {LABSE_DIR}")
        sys.exit(1)

    export_onnx()
    copy_tokenizer()
    verify(ONNX_FP32)

    print(f"\nDone! FP32: {ONNX_FP32} ({ONNX_FP32.stat().st_size / 1024**2:.1f} MB)")
