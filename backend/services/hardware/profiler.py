"""
Hardware Profiler — определение GPU, CPU, RAM для автоматического выбора Tier.

Использует:
- pynvml (primary) для GPU detection
- torch.cuda (fallback) если pynvml недоступен
- psutil для CPU и RAM
- /proc/cpuinfo для AVX2 detection (Linux)
"""

import os
import platform
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import psutil


@dataclass
class GPUInfo:
    """Информация об одном GPU."""
    index: int
    name: str
    total_vram_gb: float
    free_vram_gb: float
    compute_capability: Tuple[int, int] = (0, 0)
    driver_version: str = ""

    @property
    def used_vram_gb(self) -> float:
        return self.total_vram_gb - self.free_vram_gb


@dataclass
class CPUInfo:
    """Информация о CPU."""
    name: str = ""
    physical_cores: int = 0
    logical_cores: int = 0
    frequency_mhz: float = 0.0
    has_avx2: bool = False
    has_avx512: bool = False
    architecture: str = ""


@dataclass
class MemoryInfo:
    """Информация о системной памяти."""
    total_ram_gb: float = 0.0
    available_ram_gb: float = 0.0

    @property
    def used_ram_gb(self) -> float:
        return self.total_ram_gb - self.available_ram_gb

    @property
    def usage_percent(self) -> float:
        if self.total_ram_gb == 0:
            return 0.0
        return (self.used_ram_gb / self.total_ram_gb) * 100


@dataclass
class SystemProfile:
    """Полный профиль системы."""
    gpus: List[GPUInfo] = field(default_factory=list)
    cpu: CPUInfo = field(default_factory=CPUInfo)
    memory: MemoryInfo = field(default_factory=MemoryInfo)
    platform_name: str = ""

    @property
    def has_gpu(self) -> bool:
        return len(self.gpus) > 0

    @property
    def gpu_count(self) -> int:
        return len(self.gpus)

    @property
    def total_vram_gb(self) -> float:
        return sum(g.total_vram_gb for g in self.gpus)

    @property
    def free_vram_gb(self) -> float:
        return sum(g.free_vram_gb for g in self.gpus)

    @property
    def best_gpu(self) -> Optional[GPUInfo]:
        if not self.gpus:
            return None
        return max(self.gpus, key=lambda g: g.total_vram_gb)

    def __str__(self) -> str:
        lines = [f"SystemProfile (platform={self.platform_name})"]
        lines.append(f"  CPU: {self.cpu.name} ({self.cpu.physical_cores}C/{self.cpu.logical_cores}T, AVX2={self.cpu.has_avx2})")
        lines.append(f"  RAM: {self.memory.total_ram_gb:.1f}GB total, {self.memory.available_ram_gb:.1f}GB free")
        if self.gpus:
            for g in self.gpus:
                lines.append(f"  GPU[{g.index}]: {g.name} — {g.total_vram_gb:.1f}GB VRAM ({g.free_vram_gb:.1f}GB free, cc={g.compute_capability})")
        else:
            lines.append("  GPU: none")
        return "\n".join(lines)


class HardwareProfiler:
    """Определяет аппаратный профиль системы."""

    def detect(self) -> SystemProfile:
        """Полное сканирование оборудования."""
        profile = SystemProfile(
            gpus=self._detect_gpus(),
            cpu=self._detect_cpu(),
            memory=self._detect_memory(),
            platform_name=platform.system(),
        )
        return profile

    def _detect_gpus(self) -> List[GPUInfo]:
        """Определение GPU через pynvml (primary) или torch.cuda (fallback)."""
        gpus = self._detect_gpus_pynvml()
        if not gpus:
            gpus = self._detect_gpus_torch()
        return gpus

    def _detect_gpus_pynvml(self) -> List[GPUInfo]:
        """GPU detection через pynvml."""
        try:
            import pynvml
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
            gpus = []
            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8")

                # Compute capability
                try:
                    cc_major = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
                    if isinstance(cc_major, tuple):
                        compute_capability = cc_major
                    else:
                        compute_capability = (cc_major, 0)
                except Exception:
                    compute_capability = (0, 0)

                # Driver version
                try:
                    driver = pynvml.nvmlSystemGetDriverVersion()
                    if isinstance(driver, bytes):
                        driver = driver.decode("utf-8")
                except Exception:
                    driver = ""

                gpus.append(GPUInfo(
                    index=i,
                    name=name,
                    total_vram_gb=mem_info.total / (1024 ** 3),
                    free_vram_gb=mem_info.free / (1024 ** 3),
                    compute_capability=compute_capability,
                    driver_version=driver,
                ))
            return gpus
        except Exception:
            return []

    def _detect_gpus_torch(self) -> List[GPUInfo]:
        """Fallback GPU detection через torch.cuda."""
        try:
            import torch
            if not torch.cuda.is_available():
                return []
            gpus = []
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                mem_total = props.total_mem / (1024 ** 3)
                # torch не даёт free memory без аллокации
                try:
                    free_mem = torch.cuda.mem_get_info(i)[0] / (1024 ** 3)
                except Exception:
                    free_mem = mem_total * 0.9  # Estimate
                gpus.append(GPUInfo(
                    index=i,
                    name=props.name,
                    total_vram_gb=mem_total,
                    free_vram_gb=free_mem,
                    compute_capability=(props.major, props.minor),
                ))
            return gpus
        except Exception:
            return []

    def _detect_cpu(self) -> CPUInfo:
        """Определение CPU через psutil и /proc/cpuinfo."""
        info = CPUInfo(
            physical_cores=psutil.cpu_count(logical=False) or 1,
            logical_cores=psutil.cpu_count(logical=True) or 1,
            architecture=platform.machine(),
        )

        # Частота
        freq = psutil.cpu_freq()
        if freq:
            info.frequency_mhz = freq.current or freq.max or 0.0

        # Имя CPU и флаги (Linux)
        if platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo", "r") as f:
                    cpuinfo = f.read()
                # Имя
                for line in cpuinfo.split("\n"):
                    if line.startswith("model name"):
                        info.name = line.split(":", 1)[1].strip()
                        break
                # Флаги
                for line in cpuinfo.split("\n"):
                    if line.startswith("flags"):
                        flags = line.split(":", 1)[1].strip()
                        info.has_avx2 = "avx2" in flags
                        info.has_avx512 = "avx512f" in flags
                        break
            except Exception:
                pass
        elif platform.system() == "Darwin":
            try:
                info.name = subprocess.check_output(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    text=True, timeout=5
                ).strip()
                features = subprocess.check_output(
                    ["sysctl", "-n", "machdep.cpu.leaf7_features"],
                    text=True, timeout=5
                ).strip()
                info.has_avx2 = "AVX2" in features
            except Exception:
                pass
        elif platform.system() == "Windows":
            info.name = platform.processor() or "Unknown"
            # AVX2 detection on Windows: проверяем через environment
            # llama.cpp сам определит, просто ставим True для x86_64
            info.has_avx2 = platform.machine() in ("AMD64", "x86_64")

        return info

    def _detect_memory(self) -> MemoryInfo:
        """Определение RAM через psutil."""
        vm = psutil.virtual_memory()
        return MemoryInfo(
            total_ram_gb=vm.total / (1024 ** 3),
            available_ram_gb=vm.available / (1024 ** 3),
        )
