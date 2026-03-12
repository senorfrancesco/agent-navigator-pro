"""
Tier Selector — маппинг SystemProfile → TierConfig.

4 уровня адаптации:
  Tier 1: CPU only, 32-64GB RAM -> basic retrieval
  Tier 2: CPU 128GB или слабый GPU -> corrective retrieval
  Tier 3: GPU 24GB+ -> iterative retrieval
  Tier 4: Multi-GPU / H100 -> planned multi-agent
"""

import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

from .profiler import SystemProfile
from .vram_calculator import VRAMCalculator


RAG_MODE_PUBLIC_LABELS = {
    "simple": "basic retrieval",
    "corrective": "corrective retrieval",
    "agentic": "iterative retrieval",
    "multi-agent": "planned multi-agent",
}


def describe_rag_mode(rag_mode: str) -> str:
    normalized = str(rag_mode or "simple").strip().lower()
    return RAG_MODE_PUBLIC_LABELS.get(normalized, normalized or "basic retrieval")


class Tier(IntEnum):
    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
    TIER_4 = 4


@dataclass
class TierConfig:
    """Конфигурация системы для выбранного tier'а."""
    tier: Tier

    # LLM config
    llm_model_id: str = "qwen-14b-llm"
    llm_quant: str = "Q4_K_M"
    llm_ctx_size: int = 8192
    llm_gpu_layers: int = -1
    llm_batch_size: int = 512

    # Embedding config
    embedding_backend: str = "pytorch"  # "pytorch" | "onnx" | "onnx-int8"
    embedding_device: str = "cuda"       # "cuda" | "cpu"
    embedding_model: str = "LaBSE"

    # RAG config
    rag_mode: str = "simple"  # "simple" | "corrective" | "agentic" | "multi-agent"
    retrieval_top_k: int = 5
    use_reranker: bool = False
    use_bm25: bool = True

    # Workflow config
    workflow_batch_size: int = 5  # Пар для параллельного анализа
    max_concurrent_llm: int = 1

    def __str__(self) -> str:
        return (
            f"TierConfig(tier={self.tier.name}, "
            f"llm={self.llm_model_id} {self.llm_quant} ctx={self.llm_ctx_size} "
            f"gpu_layers={self.llm_gpu_layers}, "
            f"embedding={self.embedding_backend}/{self.embedding_device}, "
            f"rag={self.rag_mode} [{describe_rag_mode(self.rag_mode)}])"
        )


class TierSelector:
    """Выбирает tier на основе SystemProfile."""

    def __init__(self):
        self.vram_calc = VRAMCalculator()

    def select(self, profile: SystemProfile) -> TierConfig:
        """
        Определяет оптимальный tier для данного железа.

        Приоритет:
        1. TIER_OVERRIDE из .env
        2. Автоматический выбор по GPU VRAM / RAM
        """
        # Проверяем override
        override = os.environ.get("TIER_OVERRIDE")
        if override:
            tier = Tier(int(override))
            config = self._build_config(tier, profile)
            return config

        # Автоматический выбор
        usable_vram = profile.total_vram_gb * 0.90  # 10% overhead

        if usable_vram >= 60 or profile.gpu_count >= 4:
            return self._tier4(profile, usable_vram)
        if usable_vram >= 12:
            return self._tier3(profile, usable_vram)
        if usable_vram >= 3.5 or profile.memory.available_ram_gb >= 48:
            return self._tier2(profile, usable_vram)
        return self._tier1(profile)

    def _build_config(self, tier: Tier, profile: SystemProfile) -> TierConfig:
        """Строит конфиг для заданного tier (при override)."""
        builders = {
            Tier.TIER_1: self._tier1,
            Tier.TIER_2: self._tier2,
            Tier.TIER_3: self._tier3,
            Tier.TIER_4: self._tier4,
        }
        usable_vram = profile.total_vram_gb * 0.90
        return builders[tier](profile, usable_vram)

    def _tier1(self, profile: SystemProfile, usable_vram: float = 0) -> TierConfig:
        """CPU + 32-64GB RAM: Qwen-7B Q4, basic retrieval."""
        gpu_layers = self._get_gpu_layers_override()
        if gpu_layers is None:
            gpu_layers = 0  # CPU only

        return TierConfig(
            tier=Tier.TIER_1,
            llm_model_id="qwen-7b-llm",
            llm_quant="Q4_K_M",
            llm_ctx_size=4096,
            llm_gpu_layers=gpu_layers,
            llm_batch_size=256,
            embedding_backend="onnx",
            embedding_device="cpu",
            rag_mode="simple",
            retrieval_top_k=5,
            use_reranker=False,
            use_bm25=True,
            workflow_batch_size=2,
            max_concurrent_llm=1,
        )

    def _tier2(self, profile: SystemProfile, usable_vram: float = 0) -> TierConfig:
        """CPU 128GB или слабый GPU: Qwen-14B Q4, corrective retrieval."""
        gpu_layers = self._get_gpu_layers_override()
        if gpu_layers is None:
            if usable_vram >= 4:
                gpu_layers = self.vram_calc.calc_gpu_layers(
                    model_params_b=14, quant="Q4_K_M", vram_gb=usable_vram
                )
            else:
                gpu_layers = 0

        # Embedding: GPU если есть хотя бы 2GB свободного после модели
        embedding_device = "cpu"
        embedding_backend = "onnx"
        if usable_vram > 6:  # Хватит и на модель и на LaBSE (~0.5GB)
            embedding_device = "cuda"
            embedding_backend = "pytorch"

        return TierConfig(
            tier=Tier.TIER_2,
            llm_model_id="qwen-14b-llm",
            llm_quant="Q4_K_M",
            llm_ctx_size=8192,
            llm_gpu_layers=gpu_layers,
            llm_batch_size=512,
            embedding_backend=embedding_backend,
            embedding_device=embedding_device,
            rag_mode="corrective",
            retrieval_top_k=7,
            use_reranker=False,
            use_bm25=True,
            workflow_batch_size=5,
            max_concurrent_llm=1,
        )

    def _tier3(self, profile: SystemProfile, usable_vram: float = 0) -> TierConfig:
        """GPU 24GB+: Qwen-14B/Qwen-32B, iterative retrieval (compat key: agentic)."""
        gpu_layers = self._get_gpu_layers_override()

        # Выбор модели по VRAM
        if usable_vram >= 20:
            # Хватит на Qwen-14B Q8 (~16GB) или Qwen-32B Q4 (~20GB)
            if usable_vram >= 24:
                model_id = "qwen-32b-llm"
                quant = "Q4_K_M"
                ctx = 16384
            else:
                model_id = "qwen-14b-llm"
                quant = "Q8_0"
                ctx = 16384
        else:
            model_id = "qwen-14b-llm"
            quant = "Q4_K_M"
            ctx = 16384

        if gpu_layers is None:
            gpu_layers = -1  # Все на GPU

        return TierConfig(
            tier=Tier.TIER_3,
            llm_model_id=model_id,
            llm_quant=quant,
            llm_ctx_size=ctx,
            llm_gpu_layers=gpu_layers,
            llm_batch_size=1024,
            embedding_backend="pytorch",
            embedding_device="cuda",
            rag_mode="agentic",
            retrieval_top_k=10,
            use_reranker=True,
            use_bm25=True,
            workflow_batch_size=10,
            max_concurrent_llm=1,
        )

    def _tier4(self, profile: SystemProfile, usable_vram: float = 0) -> TierConfig:
        """Multi-GPU / H100: large-model path, planned multi-agent (compat key: multi-agent)."""
        gpu_layers = self._get_gpu_layers_override()
        if gpu_layers is None:
            gpu_layers = -1

        return TierConfig(
            tier=Tier.TIER_4,
            llm_model_id="qwen-72b-llm",
            llm_quant="Q4_K_M",
            llm_ctx_size=32768,
            llm_gpu_layers=gpu_layers,
            llm_batch_size=2048,
            embedding_backend="pytorch",
            embedding_device="cuda",
            embedding_model="BGE-M3",
            rag_mode="multi-agent",
            retrieval_top_k=15,
            use_reranker=True,
            use_bm25=True,
            workflow_batch_size=20,
            max_concurrent_llm=4,
        )

    @staticmethod
    def _get_gpu_layers_override() -> Optional[int]:
        """Проверяет N_GPU_LAYERS_OVERRIDE из окружения."""
        val = os.environ.get("N_GPU_LAYERS_OVERRIDE")
        if val is not None:
            try:
                return int(val)
            except ValueError:
                pass
        return None
