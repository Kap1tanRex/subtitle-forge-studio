"""Какие вычислители есть на этой машине и что из них умеет программа.

Разделение, без которого разговор про видеокарты превращается в кашу:

* **устройство** — то, что физически стоит в компьютере: GeForce, Radeon,
  встроенная графика Intel;
* **способ счёта** — программный путь к этому устройству: CUDA, Vulkan,
  DirectML;
* **движок** — то, что этим путём пользуется: faster-whisper умеет только
  CUDA, whisper.cpp собирается с Vulkan и работает на любой карте.

Путать их нельзя. Видеокарта AMD в системе есть, но у CTranslate2 для неё
пути нет — и честный ответ «карта найдена, но этот движок с ней не работает,
нужен другой» полезнее, чем и молчание, и обещание.

Устройства ищутся **в реестре**, а не запуском ``nvidia-smi`` или WMI:
подпроцесс стоит секунду, а список нужен при каждом открытии окна
распознавания. В реестре Windows все видеоадаптеры перечислены с названиями,
и читается это мгновенно.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "Accelerator",
    "GpuInfo",
    "Vendor",
    "detect_accelerators",
    "detect_gpus",
    "vendor_of",
]

#: Ветка реестра со списком видеоадаптеров. Класс устройств «Display adapters»
#: у Windows один и тот же во всех версиях начиная с XP.
_DISPLAY_CLASS = (
    r"SYSTEM\CurrentControlSet\Control\Class"
    r"\{4d36e968-e325-11ce-bfc1-08002be10318}"
)


class Vendor(StrEnum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    OTHER = "other"

    @property
    def title(self) -> str:
        return {
            Vendor.NVIDIA: "NVIDIA",
            Vendor.AMD: "AMD",
            Vendor.INTEL: "Intel",
            Vendor.OTHER: "видеокарта",
        }[self]


def vendor_of(name: str) -> Vendor:
    """Производитель по названию адаптера."""
    text = name.casefold()
    if "nvidia" in text or "geforce" in text or "quadro" in text or "rtx" in text:
        return Vendor.NVIDIA
    if "amd" in text or "radeon" in text or "firepro" in text:
        return Vendor.AMD
    if "intel" in text or "arc " in text or "iris" in text:
        return Vendor.INTEL
    return Vendor.OTHER


@dataclass(frozen=True, slots=True)
class GpuInfo:
    """Видеоадаптер, как его называет драйвер."""

    name: str
    vendor: Vendor

    @property
    def title(self) -> str:
        return self.name


def detect_gpus() -> list[GpuInfo]:
    """Видеоадаптеры системы. Пустой список — не ошибка, а «не смогли узнать».

    Список используется только для подсказок, поэтому любая неудача чтения
    реестра гасится: без названия карты программа работает, а падать из-за
    справочной строки нельзя.
    """
    if os.name != "nt":
        return _detect_gpus_posix()

    import winreg

    found: list[GpuInfo] = []
    seen: set[str] = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS) as root:
            index = 0
            while True:
                try:
                    subkey = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                # Подключи вида «0000», «0001»; рядом лежат Configuration и
                # Properties, которые адаптерами не являются.
                if not subkey.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, subkey) as entry:
                        name, _type = winreg.QueryValueEx(entry, "DriverDesc")
                except OSError:
                    continue
                name = str(name).strip()
                if name and name not in seen:
                    seen.add(name)
                    found.append(GpuInfo(name, vendor_of(name)))
    except OSError:
        return []
    return found


def _detect_gpus_posix() -> list[GpuInfo]:
    """То же для Linux: имена адаптеров лежат в sysfs.

    Читаем только идентификатор производителя — модель там записана кодом
    PCI, а таблицу кодов держать в программе ради подписи не стоит.
    """
    from pathlib import Path

    vendors = {"0x10de": Vendor.NVIDIA, "0x1002": Vendor.AMD, "0x8086": Vendor.INTEL}
    found: list[GpuInfo] = []
    root = Path("/sys/class/drm")
    if not root.is_dir():
        return []
    for card in sorted(root.glob("card[0-9]")):
        try:
            code = (card / "device" / "vendor").read_text().strip().lower()
        except OSError:
            continue
        vendor = vendors.get(code, Vendor.OTHER)
        found.append(GpuInfo(f"{vendor.title} ({card.name})", vendor))
    return found


@dataclass(frozen=True, slots=True)
class Accelerator:
    """Способ счёта: чем считать и что для этого нужно."""

    key: str
    title: str
    #: Готов к работе прямо сейчас.
    available: bool
    #: Что происходит или чего не хватает — текстом для человека.
    note: str = ""
    #: Устройства, к которым этот способ ведёт.
    devices: tuple[str, ...] = ()
    #: Движки распознавания, умеющие этим способом пользоваться.
    engines: tuple[str, ...] = ()

    @property
    def caption(self) -> str:
        if self.devices:
            return f"{self.title} — {self.devices[0]}"
        return self.title


def detect_accelerators(*, libraries_dir=None) -> list[Accelerator]:
    """Способы счёта в порядке предпочтения: процессор, затем видеокарты.

    Процессор первым не потому, что он лучше, а потому, что он есть всегда:
    список начинается с того, что заведомо работает.

    Недоступные способы **не выбрасываются из списка**. Владельцу Radeon
    полезнее увидеть «карта найдена, нужен движок whisper.cpp», чем не
    увидеть ничего и решить, что программа его карту не заметила.
    """
    gpus = detect_gpus()
    result = [
        Accelerator(
            key="cpu",
            title="Процессор",
            available=True,
            note="Работает всегда. Медленнее видеокарты в 2–5 раз.",
            engines=("faster-whisper", "whisper-cpp", "vosk"),
        )
    ]

    nvidia = [g.name for g in gpus if g.vendor is Vendor.NVIDIA]
    if nvidia or not gpus:
        result.append(_cuda_accelerator(tuple(nvidia), libraries_dir))

    others = tuple(g.name for g in gpus if g.vendor in (Vendor.AMD, Vendor.INTEL))
    if others:
        result.append(
            Accelerator(
                key="vulkan",
                title="Видеокарта (Vulkan)",
                available=False,
                note=(
                    "Карта найдена, но готового пути к ней у программы нет. "
                    "Оба встроенных движка считают на видеокарте только через "
                    "CUDA, то есть на NVIDIA. whisper.cpp умеет Vulkan и "
                    "работает с любой картой, но собранных с Vulkan файлов "
                    "проект не выкладывает — проверены двенадцать последних "
                    "выпусков. Такой файл можно собрать самому и указать путь "
                    "к нему; иначе счёт идёт на процессоре."
                ),
                devices=others,
                engines=("whisper-cpp",),
            )
        )
    return result


def _cuda_accelerator(devices: tuple[str, ...], libraries_dir) -> Accelerator:
    """Состояние счёта на видеокарте NVIDIA — с причиной, если он не готов."""
    from sfstudio.services.asr.engines.faster_whisper import cuda_status

    ready, note = cuda_status(libraries_dir)
    return Accelerator(
        key="cuda",
        title="Видеокарта (CUDA)",
        available=ready,
        note=note,
        devices=devices,
        engines=("faster-whisper", "whisper-cpp"),
    )

