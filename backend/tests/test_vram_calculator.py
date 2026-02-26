"""
Тесты для VRAMCalculator — расчёт gpu_layers и ctx_size.
"""

import pytest
from services.hardware.vram_calculator import VRAMCalculator


class TestVRAMCalculator:
    """Тесты расчёта VRAM."""

    def setup_method(self):
        self.calc = VRAMCalculator()

    def test_qwen14b_q4_8gb(self):
        """Qwen-14B Q4 + 8GB VRAM → partial offload (>0 but not all)."""
        layers = self.calc.calc_gpu_layers(
            model_params_b=14, quant="Q4_K_M", vram_gb=8.0, ctx_size=8192
        )
        assert layers > 0, "Should offload some layers"
        assert layers < 40, "Should not fit all 40 layers"

    def test_qwen14b_q4_16gb(self):
        """Qwen-14B Q4 + 16GB VRAM → all layers (-1)."""
        layers = self.calc.calc_gpu_layers(
            model_params_b=14, quant="Q4_K_M", vram_gb=16.0, ctx_size=8192
        )
        assert layers == -1, "Should fit all layers on 16GB"

    def test_qwen7b_q4_4gb(self):
        """Qwen-7B Q4 + 4GB VRAM → partial offload."""
        layers = self.calc.calc_gpu_layers(
            model_params_b=7, quant="Q4_K_M", vram_gb=4.0, ctx_size=4096
        )
        assert layers > 0

    def test_qwen72b_q4_24gb(self):
        """Qwen-72B Q4 + 24GB VRAM → partial offload."""
        layers = self.calc.calc_gpu_layers(
            model_params_b=72, quant="Q4_K_M", vram_gb=24.0, ctx_size=8192
        )
        assert 0 < layers < 80

    def test_zero_vram(self):
        """0 VRAM → 0 layers."""
        layers = self.calc.calc_gpu_layers(
            model_params_b=14, quant="Q4_K_M", vram_gb=0.0
        )
        assert layers == 0

    def test_huge_vram(self):
        """200GB VRAM → all layers (-1) for any model."""
        layers = self.calc.calc_gpu_layers(
            model_params_b=72, quant="Q4_K_M", vram_gb=200.0
        )
        assert layers == -1

    def test_optimal_ctx_16gb(self):
        """Qwen-14B Q4, 16GB, all layers → ctx >= 8192."""
        ctx = self.calc.calc_optimal_ctx(
            model_params_b=14, quant="Q4_K_M", vram_gb=16.0, gpu_layers=-1
        )
        assert ctx >= 8192

    def test_optimal_ctx_small_vram(self):
        """Small VRAM → minimum ctx 2048."""
        ctx = self.calc.calc_optimal_ctx(
            model_params_b=14, quant="Q4_K_M", vram_gb=0.5, gpu_layers=0
        )
        assert ctx == 2048

    def test_estimate_total_vram(self):
        """Qwen-14B Q4, 8192 ctx, all layers → ~10-12 GB."""
        total = self.calc.estimate_total_vram(
            model_params_b=14, quant="Q4_K_M", ctx_size=8192
        )
        assert 5 < total < 20, f"Expected 5-20 GB, got {total:.1f}"

    def test_kv_cache_increases_with_ctx(self):
        """KV-cache должен расти с ctx_size."""
        small = self.calc.estimate_total_vram(14, "Q4_K_M", ctx_size=2048)
        large = self.calc.estimate_total_vram(14, "Q4_K_M", ctx_size=16384)
        assert large > small

    def test_quant_affects_size(self):
        """Q8 модель больше Q4."""
        q4 = self.calc.estimate_total_vram(14, "Q4_K_M", ctx_size=8192)
        q8 = self.calc.estimate_total_vram(14, "Q8_0", ctx_size=8192)
        assert q8 > q4

    def test_model_arch_override(self):
        """Явное указание архитектуры работает."""
        layers = self.calc.calc_gpu_layers(
            model_params_b=14, quant="Q4_K_M", vram_gb=16.0,
            model_arch="qwen-14b"
        )
        assert layers == -1
