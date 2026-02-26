"""
Тесты для TierSelector — маппинг SystemProfile → TierConfig.
"""

import os
import pytest
from services.hardware.profiler import SystemProfile, GPUInfo, CPUInfo, MemoryInfo
from services.hardware.tier_selector import TierSelector, TierConfig, Tier


def _make_gpu(vram_gb: float, name: str = "Test GPU") -> GPUInfo:
    return GPUInfo(
        index=0, name=name,
        total_vram_gb=vram_gb, free_vram_gb=vram_gb * 0.95,
        compute_capability=(8, 6),
    )


def _make_profile(
    gpus: list = None,
    ram_gb: float = 32.0,
    cpu_cores: int = 8,
) -> SystemProfile:
    return SystemProfile(
        gpus=gpus or [],
        cpu=CPUInfo(
            name="Test CPU", physical_cores=cpu_cores,
            logical_cores=cpu_cores * 2, has_avx2=True,
        ),
        memory=MemoryInfo(total_ram_gb=ram_gb, available_ram_gb=ram_gb * 0.7),
        platform_name="Linux",
    )


class TestTierSelector:
    """Тесты выбора tier'а."""

    def setup_method(self):
        self.selector = TierSelector()
        # Убираем override если остался от предыдущих тестов
        os.environ.pop("TIER_OVERRIDE", None)
        os.environ.pop("N_GPU_LAYERS_OVERRIDE", None)

    def teardown_method(self):
        os.environ.pop("TIER_OVERRIDE", None)
        os.environ.pop("N_GPU_LAYERS_OVERRIDE", None)

    def test_cpu_only_32gb(self):
        """CPU + 32GB RAM → Tier 1."""
        profile = _make_profile(gpus=[], ram_gb=32)
        config = self.selector.select(profile)
        assert config.tier == Tier.TIER_1
        assert config.llm_gpu_layers == 0
        assert config.rag_mode == "simple"
        assert config.embedding_backend == "onnx-int8"

    def test_cpu_only_64gb(self):
        """CPU + 64GB RAM → Tier 1 (< 48GB available)."""
        profile = _make_profile(gpus=[], ram_gb=64)
        config = self.selector.select(profile)
        assert config.tier == Tier.TIER_1

    def test_cpu_128gb(self):
        """CPU + 128GB RAM → Tier 2."""
        profile = _make_profile(gpus=[], ram_gb=128)
        config = self.selector.select(profile)
        assert config.tier == Tier.TIER_2
        assert config.rag_mode == "corrective"

    def test_weak_gpu_4gb(self):
        """GPU 4GB → Tier 2 (CPU inference — KV-cache для Qwen-14B не влезает в 4GB)."""
        profile = _make_profile(gpus=[_make_gpu(4)], ram_gb=32)
        config = self.selector.select(profile)
        assert config.tier == Tier.TIER_2
        assert config.llm_gpu_layers >= 0  # 4GB не хватает для partial offload Qwen-14B

    def test_rtx2070_dual(self):
        """2x RTX 2070 (16GB total) → Tier 3 (usable 14.4GB >= 12)."""
        profile = _make_profile(gpus=[_make_gpu(8), _make_gpu(8)], ram_gb=32)
        config = self.selector.select(profile)
        assert config.tier == Tier.TIER_3

    def test_rtx3090(self):
        """RTX 3090 (24GB) → Tier 3."""
        profile = _make_profile(gpus=[_make_gpu(24, "NVIDIA GeForce RTX 3090")], ram_gb=64)
        config = self.selector.select(profile)
        assert config.tier == Tier.TIER_3
        assert config.rag_mode == "agentic"
        assert config.use_reranker == True
        assert config.llm_gpu_layers == -1

    def test_a100_40gb(self):
        """A100 (40GB) → Tier 3."""
        profile = _make_profile(gpus=[_make_gpu(40, "NVIDIA A100")], ram_gb=256)
        config = self.selector.select(profile)
        assert config.tier == Tier.TIER_3

    def test_h100_cluster(self):
        """8x H100 (640GB total) → Tier 4."""
        profile = _make_profile(
            gpus=[_make_gpu(80, f"NVIDIA H100 #{i}") for i in range(8)],
            ram_gb=1024,
        )
        config = self.selector.select(profile)
        assert config.tier == Tier.TIER_4
        assert config.rag_mode == "multi-agent"
        assert config.max_concurrent_llm == 4

    def test_multi_gpu_4x24(self):
        """4x RTX 4090 (96GB total, >=4 GPUs) → Tier 4."""
        profile = _make_profile(
            gpus=[_make_gpu(24, f"RTX 4090 #{i}") for i in range(4)],
            ram_gb=128,
        )
        config = self.selector.select(profile)
        assert config.tier == Tier.TIER_4

    def test_env_override_tier(self):
        """TIER_OVERRIDE=1 forces Tier 1 even with powerful GPU."""
        os.environ["TIER_OVERRIDE"] = "1"
        profile = _make_profile(gpus=[_make_gpu(24)], ram_gb=64)
        config = self.selector.select(profile)
        assert config.tier == Tier.TIER_1

    def test_env_override_gpu_layers(self):
        """N_GPU_LAYERS_OVERRIDE=25 sets specific gpu layers."""
        os.environ["N_GPU_LAYERS_OVERRIDE"] = "25"
        profile = _make_profile(gpus=[_make_gpu(8)], ram_gb=32)
        config = self.selector.select(profile)
        assert config.llm_gpu_layers == 25

    def test_config_str(self):
        """TierConfig имеет читаемое представление."""
        profile = _make_profile(gpus=[_make_gpu(24)], ram_gb=64)
        config = self.selector.select(profile)
        s = str(config)
        assert "TierConfig" in s
        assert "tier=" in s

    def test_all_tiers_have_bm25(self):
        """BM25 включён на всех tier'ах."""
        for gpus, ram in [([], 32), ([], 128), ([_make_gpu(24)], 64), ([_make_gpu(80)] * 4, 512)]:
            profile = _make_profile(gpus=gpus, ram_gb=ram)
            config = self.selector.select(profile)
            assert config.use_bm25 == True, f"BM25 should be enabled for tier {config.tier}"
