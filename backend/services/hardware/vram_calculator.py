"""
VRAM Calculator — расчёт gpu_layers и ctx_size под доступную VRAM.

Формула для GGUF моделей:
  model_size = params_B * bytes_per_param(quant)
  per_layer = model_size / total_layers
  kv_cache = 2 * n_layers * d_model * ctx_size * 2bytes / 1024^3  (FP16)
  total = layers_on_gpu * per_layer + kv_cache + overhead(~0.5GB)
"""

from dataclasses import dataclass
from typing import Tuple


# Размер в байтах на параметр для разных квантизаций
QUANT_BPP = {
    "F16": 2.0,
    "Q8_0": 1.1,
    "Q6_K": 0.85,
    "Q5_K_M": 0.72,
    "Q5_K_S": 0.70,
    "Q4_K_M": 0.63,
    "Q4_K_S": 0.60,
    "Q4_0": 0.55,
    "Q3_K_M": 0.50,
    "Q3_K_S": 0.45,
    "Q2_K": 0.38,
    "IQ4_XS": 0.52,
    "IQ3_XXS": 0.38,
}

# Архитектуры моделей: (total_layers, d_model)
MODEL_ARCH = {
    "qwen-7b": (32, 4096),
    "qwen-14b": (40, 5120),
    "qwen-32b": (64, 5120),
    "qwen-72b": (80, 8192),
}


class VRAMCalculator:
    """Расчёт оптимального количества GPU layers и ctx_size."""

    OVERHEAD_GB = 0.5  # CUDA context + buffers

    def calc_gpu_layers(
        self,
        model_params_b: float,
        quant: str = "Q4_K_M",
        vram_gb: float = 8.0,
        ctx_size: int = 8192,
        model_arch: str = "",
    ) -> int:
        """
        Рассчитывает количество слоёв для загрузки на GPU.

        Args:
            model_params_b: Количество параметров в миллиардах (7, 14, 32, 72)
            quant: Квантизация (Q4_K_M, Q8_0, F16, ...)
            vram_gb: Доступная VRAM в GB
            ctx_size: Размер контекста
            model_arch: Ключ архитектуры (qwen-7b, qwen-14b, ...)

        Returns:
            Количество слоёв для GPU (-1 = все)
        """
        bpp = QUANT_BPP.get(quant, 0.63)  # Default Q4_K_M
        total_layers, d_model = self._get_arch(model_params_b, model_arch)

        # Размер всей модели
        model_size_gb = model_params_b * bpp

        # Размер одного слоя
        per_layer_gb = model_size_gb / total_layers

        # KV-cache (FP16)
        kv_cache_gb = self._estimate_kv_cache(total_layers, d_model, ctx_size)

        # Доступная VRAM за вычетом overhead и KV-cache
        available = vram_gb - self.OVERHEAD_GB - kv_cache_gb

        if available <= 0:
            return 0

        # Сколько слоёв влезет
        max_layers = int(available / per_layer_gb)

        if max_layers >= total_layers:
            return -1  # Все слои на GPU

        return min(max_layers, total_layers)

    def calc_optimal_ctx(
        self,
        model_params_b: float,
        quant: str = "Q4_K_M",
        vram_gb: float = 8.0,
        gpu_layers: int = -1,
        model_arch: str = "",
    ) -> int:
        """
        Рассчитывает оптимальный ctx_size для заданных gpu_layers.

        Returns:
            ctx_size (степень двойки: 2048, 4096, 8192, 16384, 32768)
        """
        bpp = QUANT_BPP.get(quant, 0.63)
        total_layers, d_model = self._get_arch(model_params_b, model_arch)

        model_size_gb = model_params_b * bpp
        per_layer_gb = model_size_gb / total_layers

        if gpu_layers == -1:
            gpu_layers = total_layers

        model_on_gpu_gb = gpu_layers * per_layer_gb
        remaining = vram_gb - self.OVERHEAD_GB - model_on_gpu_gb

        if remaining <= 0:
            return 2048

        # Найти максимальный ctx_size, KV-cache которого влезает в remaining
        for ctx in [32768, 16384, 8192, 4096, 2048]:
            kv = self._estimate_kv_cache(total_layers, d_model, ctx)
            if kv <= remaining:
                return ctx

        return 2048

    def estimate_total_vram(
        self,
        model_params_b: float,
        quant: str = "Q4_K_M",
        ctx_size: int = 8192,
        gpu_layers: int = -1,
        model_arch: str = "",
    ) -> float:
        """Оценка полного потребления VRAM в GB."""
        bpp = QUANT_BPP.get(quant, 0.63)
        total_layers, d_model = self._get_arch(model_params_b, model_arch)

        model_size_gb = model_params_b * bpp
        per_layer_gb = model_size_gb / total_layers

        if gpu_layers == -1:
            gpu_layers = total_layers

        layers_gb = gpu_layers * per_layer_gb
        kv_gb = self._estimate_kv_cache(total_layers, d_model, ctx_size)

        return layers_gb + kv_gb + self.OVERHEAD_GB

    def _get_arch(self, params_b: float, model_arch: str = "") -> Tuple[int, int]:
        """Возвращает (total_layers, d_model) для модели."""
        if model_arch and model_arch in MODEL_ARCH:
            return MODEL_ARCH[model_arch]

        # Автоопределение по размеру
        if params_b <= 8:
            return MODEL_ARCH["qwen-7b"]
        elif params_b <= 16:
            return MODEL_ARCH["qwen-14b"]
        elif params_b <= 40:
            return MODEL_ARCH["qwen-32b"]
        else:
            return MODEL_ARCH["qwen-72b"]

    @staticmethod
    def _estimate_kv_cache(n_layers: int, d_model: int, ctx_size: int) -> float:
        """
        Оценка KV-cache в GB.
        KV-cache = 2 * n_layers * d_model * ctx_size * 2 (FP16 bytes) / 1024^3
        """
        return (2 * n_layers * d_model * ctx_size * 2) / (1024 ** 3)
