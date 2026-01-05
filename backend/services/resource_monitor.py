"""
System Resources Monitor - Мониторинг системных ресурсов.

Использует:
- psutil для RAM и CPU
- pynvml для GPU (NVIDIA)

Проверяет поддержку CUDA.
"""

import os
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("[Warning] psutil not installed, RAM monitoring disabled")

try:
    import pynvml
    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    print("[Warning] pynvml not installed, GPU monitoring disabled")


@dataclass
class GPUInfo:
    """Информация о GPU."""
    index: int
    name: str
    vram_total_gb: float
    vram_used_gb: float
    vram_free_gb: float
    temperature: Optional[int]  # Celsius
    utilization: Optional[int]  # Percent
    cuda_version: Optional[str]
    driver_version: Optional[str]


@dataclass
class SystemResources:
    """Полная информация о системных ресурсах."""
    # RAM
    ram_total_gb: float
    ram_used_gb: float
    ram_free_gb: float
    ram_percent: float
    
    # CPU
    cpu_percent: float
    cpu_count: int
    
    # GPU
    cuda_available: bool
    cuda_version: Optional[str]
    driver_version: Optional[str]
    gpus: List[GPUInfo]
    
    # Суммарные значения GPU (для удобства)
    vram_total_gb: float
    vram_used_gb: float
    vram_free_gb: float
    
    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result['gpus'] = [asdict(gpu) for gpu in self.gpus]
        return result


class ResourceMonitor:
    """Класс для мониторинга системных ресурсов."""
    
    def __init__(self):
        self._nvml_initialized = False
        self._cuda_available = False
        self._cuda_version: Optional[str] = None
        self._driver_version: Optional[str] = None
        
        self._init_nvml()
    
    def _init_nvml(self):
        """Инициализация NVML для работы с GPU."""
        if not PYNVML_AVAILABLE:
            return
        
        try:
            pynvml.nvmlInit()
            self._nvml_initialized = True
            self._cuda_available = True
            
            # Получаем версии драйвера и CUDA
            try:
                self._driver_version = pynvml.nvmlSystemGetDriverVersion()
            except Exception:
                pass
            
            try:
                self._cuda_version = pynvml.nvmlSystemGetCudaDriverVersion_v2()
                # Конвертируем в читаемый формат (например, 12010 -> "12.1")
                if self._cuda_version:
                    major = self._cuda_version // 1000
                    minor = (self._cuda_version % 1000) // 10
                    self._cuda_version = f"{major}.{minor}"
            except Exception:
                pass
                
            print(f"[ResourceMonitor] NVIDIA GPU detected")
            print(f"[ResourceMonitor] Driver: {self._driver_version}")
            print(f"[ResourceMonitor] CUDA: {self._cuda_version}")
            
        except pynvml.NVMLError as e:
            print(f"[ResourceMonitor] NVML init failed: {e}")
            self._cuda_available = False
    
    def _shutdown_nvml(self):
        """Завершение работы с NVML."""
        if self._nvml_initialized:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            self._nvml_initialized = False
    
    def get_ram_info(self) -> Dict[str, float]:
        """Получение информации о RAM."""
        if not PSUTIL_AVAILABLE:
            return {
                "total_gb": 0.0,
                "used_gb": 0.0,
                "free_gb": 0.0,
                "percent": 0.0
            }
        
        try:
            mem = psutil.virtual_memory()
            return {
                "total_gb": mem.total / (1024 ** 3),
                "used_gb": mem.used / (1024 ** 3),
                "free_gb": mem.available / (1024 ** 3),
                "percent": mem.percent
            }
        except Exception as e:
            print(f"[ResourceMonitor] RAM info error: {e}")
            return {
                "total_gb": 0.0,
                "used_gb": 0.0,
                "free_gb": 0.0,
                "percent": 0.0
            }
    
    def get_cpu_info(self) -> Dict[str, Any]:
        """Получение информации о CPU."""
        if not PSUTIL_AVAILABLE:
            return {
                "percent": 0.0,
                "count": 0
            }
        
        try:
            return {
                "percent": psutil.cpu_percent(interval=0.1),
                "count": psutil.cpu_count()
            }
        except Exception as e:
            print(f"[ResourceMonitor] CPU info error: {e}")
            return {
                "percent": 0.0,
                "count": 0
            }
    
    def get_gpu_info(self) -> List[GPUInfo]:
        """Получение информации о всех GPU."""
        if not PYNVML_AVAILABLE or not self._nvml_initialized:
            return []
        
        gpus = []
        
        try:
            device_count = pynvml.nvmlDeviceGetCount()
            
            for i in range(device_count):
                try:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    
                    # Имя GPU
                    name = pynvml.nvmlDeviceGetName(handle)
                    if isinstance(name, bytes):
                        name = name.decode('utf-8')
                    
                    # Память
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    vram_total = mem_info.total / (1024 ** 3)
                    vram_used = mem_info.used / (1024 ** 3)
                    vram_free = mem_info.free / (1024 ** 3)
                    
                    # Температура
                    try:
                        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                    except Exception:
                        temp = None
                    
                    # Загрузка
                    try:
                        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                        utilization = util.gpu
                    except Exception:
                        utilization = None
                    
                    gpus.append(GPUInfo(
                        index=i,
                        name=name,
                        vram_total_gb=round(vram_total, 2),
                        vram_used_gb=round(vram_used, 2),
                        vram_free_gb=round(vram_free, 2),
                        temperature=temp,
                        utilization=utilization,
                        cuda_version=self._cuda_version,
                        driver_version=self._driver_version
                    ))
                    
                except pynvml.NVMLError as e:
                    print(f"[ResourceMonitor] Error reading GPU {i}: {e}")
                    
        except pynvml.NVMLError as e:
            print(f"[ResourceMonitor] Error getting GPU count: {e}")
        
        return gpus
    
    def get_all_resources(self) -> SystemResources:
        """Получение полной информации о ресурсах."""
        ram = self.get_ram_info()
        cpu = self.get_cpu_info()
        gpus = self.get_gpu_info()
        
        # Суммируем VRAM по всем GPU
        vram_total = sum(g.vram_total_gb for g in gpus)
        vram_used = sum(g.vram_used_gb for g in gpus)
        vram_free = sum(g.vram_free_gb for g in gpus)
        
        return SystemResources(
            ram_total_gb=round(ram["total_gb"], 2),
            ram_used_gb=round(ram["used_gb"], 2),
            ram_free_gb=round(ram["free_gb"], 2),
            ram_percent=round(ram["percent"], 1),
            cpu_percent=round(cpu["percent"], 1),
            cpu_count=cpu["count"],
            cuda_available=self._cuda_available,
            cuda_version=self._cuda_version,
            driver_version=self._driver_version,
            gpus=gpus,
            vram_total_gb=round(vram_total, 2),
            vram_used_gb=round(vram_used, 2),
            vram_free_gb=round(vram_free, 2)
        )
    
    def check_cuda_support(self) -> Dict[str, Any]:
        """Проверка поддержки CUDA."""
        result = {
            "cuda_available": self._cuda_available,
            "cuda_version": self._cuda_version,
            "driver_version": self._driver_version,
            "gpu_count": 0,
            "gpus": []
        }
        
        if self._cuda_available and self._nvml_initialized:
            try:
                result["gpu_count"] = pynvml.nvmlDeviceGetCount()
                
                for i in range(result["gpu_count"]):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    name = pynvml.nvmlDeviceGetName(handle)
                    if isinstance(name, bytes):
                        name = name.decode('utf-8')
                    
                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    
                    result["gpus"].append({
                        "index": i,
                        "name": name,
                        "vram_gb": round(mem.total / (1024 ** 3), 2)
                    })
            except Exception as e:
                print(f"[ResourceMonitor] Error checking CUDA: {e}")
        
        return result


# Глобальный экземпляр монитора
_resource_monitor: Optional[ResourceMonitor] = None


def get_resource_monitor() -> ResourceMonitor:
    """Получение глобального экземпляра монитора ресурсов."""
    global _resource_monitor
    if _resource_monitor is None:
        _resource_monitor = ResourceMonitor()
    return _resource_monitor


def get_system_resources() -> Dict[str, Any]:
    """Быстрый способ получить ресурсы как словарь."""
    monitor = get_resource_monitor()
    return monitor.get_all_resources().to_dict()


def check_cuda() -> Dict[str, Any]:
    """Быстрый способ проверить CUDA."""
    monitor = get_resource_monitor()
    return monitor.check_cuda_support()


# Тестирование при запуске напрямую
if __name__ == "__main__":
    print("=" * 50)
    print("System Resources Monitor Test")
    print("=" * 50)
    
    monitor = ResourceMonitor()
    resources = monitor.get_all_resources()
    
    print(f"\nRAM: {resources.ram_used_gb:.1f}/{resources.ram_total_gb:.1f} GB ({resources.ram_percent}%)")
    print(f"CPU: {resources.cpu_percent}% ({resources.cpu_count} cores)")
    
    if resources.cuda_available:
        print(f"\nCUDA: {resources.cuda_version}")
        print(f"Driver: {resources.driver_version}")
        
        for gpu in resources.gpus:
            print(f"\nGPU {gpu.index}: {gpu.name}")
            print(f"  VRAM: {gpu.vram_used_gb:.1f}/{gpu.vram_total_gb:.1f} GB")
            if gpu.temperature:
                print(f"  Temp: {gpu.temperature}°C")
            if gpu.utilization is not None:
                print(f"  Load: {gpu.utilization}%")
    else:
        print("\nCUDA: Not available")
