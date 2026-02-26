"""
Тесты для HardwareProfiler — определение GPU, CPU, RAM.
"""

import platform
import pytest
from services.hardware.profiler import (
    HardwareProfiler, SystemProfile, GPUInfo, CPUInfo, MemoryInfo
)


class TestHardwareProfiler:
    """Тесты определения оборудования."""

    def setup_method(self):
        self.profiler = HardwareProfiler()

    def test_detect_returns_system_profile(self):
        """detect() возвращает SystemProfile."""
        profile = self.profiler.detect()
        assert isinstance(profile, SystemProfile)

    def test_detect_cpu(self):
        """Проверяет CPU cores, architecture."""
        profile = self.profiler.detect()
        assert profile.cpu.physical_cores >= 1
        assert profile.cpu.logical_cores >= profile.cpu.physical_cores
        assert profile.cpu.architecture != ""

    def test_detect_cpu_avx2(self):
        """Проверяет AVX2 flag (обязателен для llama.cpp на x86_64)."""
        profile = self.profiler.detect()
        if platform.machine() in ("x86_64", "AMD64"):
            assert profile.cpu.has_avx2 == True, "AVX2 required for llama.cpp"

    def test_detect_memory(self):
        """Проверяет RAM detection."""
        profile = self.profiler.detect()
        assert profile.memory.total_ram_gb >= 4
        assert profile.memory.available_ram_gb > 0
        assert profile.memory.available_ram_gb <= profile.memory.total_ram_gb

    def test_detect_memory_usage(self):
        """Проверяет вычисляемые свойства памяти."""
        profile = self.profiler.detect()
        assert profile.memory.used_ram_gb >= 0
        assert 0 <= profile.memory.usage_percent <= 100

    def test_system_profile_properties(self):
        """Проверяет вычисляемые свойства SystemProfile."""
        profile = self.profiler.detect()
        assert isinstance(profile.has_gpu, bool)
        assert isinstance(profile.gpu_count, int)
        assert profile.total_vram_gb >= 0

    def test_detect_gpus(self):
        """Проверяет GPU detection (если есть GPU)."""
        profile = self.profiler.detect()
        if profile.has_gpu:
            gpu = profile.gpus[0]
            assert gpu.total_vram_gb > 0
            assert gpu.name != ""
            assert gpu.index >= 0
            assert gpu.compute_capability[0] >= 0

    def test_best_gpu(self):
        """Проверяет best_gpu property."""
        profile = self.profiler.detect()
        if profile.has_gpu:
            best = profile.best_gpu
            assert best is not None
            assert best.total_vram_gb == max(g.total_vram_gb for g in profile.gpus)
        else:
            assert profile.best_gpu is None

    def test_str_representation(self):
        """Проверяет строковое представление."""
        profile = self.profiler.detect()
        s = str(profile)
        assert "SystemProfile" in s
        assert "CPU" in s
        assert "RAM" in s

    def test_platform_detection(self):
        """Проверяет определение платформы."""
        profile = self.profiler.detect()
        assert profile.platform_name in ("Linux", "Darwin", "Windows")


class TestGPUInfo:
    """Тесты для GPUInfo dataclass."""

    def test_used_vram(self):
        gpu = GPUInfo(index=0, name="Test GPU", total_vram_gb=24.0, free_vram_gb=20.0)
        assert gpu.used_vram_gb == 4.0

    def test_zero_free_vram(self):
        gpu = GPUInfo(index=0, name="Busy GPU", total_vram_gb=8.0, free_vram_gb=0.0)
        assert gpu.used_vram_gb == 8.0


class TestMemoryInfo:
    """Тесты для MemoryInfo dataclass."""

    def test_usage_percent(self):
        mem = MemoryInfo(total_ram_gb=64.0, available_ram_gb=32.0)
        assert mem.usage_percent == 50.0

    def test_zero_total(self):
        mem = MemoryInfo(total_ram_gb=0.0, available_ram_gb=0.0)
        assert mem.usage_percent == 0.0
