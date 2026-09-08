"""Ускорители: какие вычислители есть и как получить для них библиотеки."""

from sfstudio.services.accel.devices import (
    Accelerator,
    GpuInfo,
    Vendor,
    detect_accelerators,
    detect_gpus,
    vendor_of,
)

__all__ = [
    "Accelerator",
    "GpuInfo",
    "Vendor",
    "detect_accelerators",
    "detect_gpus",
    "vendor_of",
]
