"""Реестр форматов: определение, чтение, запись.

Определение идёт в два шага — расширение файла даёт подсказку, содержимое
выносит решение. Расширению нельзя верить: ``.txt`` с SRT внутри и ``.srt``
с ASS внутри встречаются постоянно.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sfstudio.core.document import SubtitleDocument
from sfstudio.io.charset import decode_bytes
from sfstudio.io.formats import ass as ass_fmt
from sfstudio.io.formats import srt as srt_fmt
from sfstudio.io.formats import vtt as vtt_fmt

__all__ = [
    "FormatSpec",
    "detect_format",
    "load",
    "register_format",
    "save",
    "supported_read",
    "supported_write",
    "unregister_format",
]


@dataclass(frozen=True, slots=True)
class FormatSpec:
    fid: str
    title: str
    extensions: tuple[str, ...]
    reader: Callable[[str], SubtitleDocument] | None
    writer: Callable[..., str] | None

    @property
    def can_read(self) -> bool:
        return self.reader is not None

    @property
    def can_write(self) -> bool:
        return self.writer is not None


FORMATS: dict[str, FormatSpec] = {
    "ass": FormatSpec(
        "ass", "Advanced SubStation Alpha", (".ass", ".ssa"), ass_fmt.read_ass, ass_fmt.write_ass
    ),
    "srt": FormatSpec("srt", "SubRip", (".srt",), srt_fmt.read_srt, srt_fmt.write_srt),
    "vtt": FormatSpec("vtt", "WebVTT", (".vtt",), vtt_fmt.read_vtt, vtt_fmt.write_vtt),
}


def register_format(spec: FormatSpec) -> None:
    """Добавляет формат в реестр. Повторная регистрация замещает прежний.

    Нужно плагинам: форматов субтитров десятки, и держать в основной
    программе хвост из редких незачем — а человеку, которому нужен именно
    его, без этого не помочь никак.

    Встроенные форматы плагин заменить может — и это намеренно: если чей-то
    читатель ASS лучше нашего, запрещать его подстановку было бы упрямством.
    """
    if not spec.fid:
        raise ValueError("формат без идентификатора")
    FORMATS[spec.fid] = spec


def unregister_format(fid: str, restore: FormatSpec | None = None) -> bool:
    """Убирает формат из реестра. ``restore`` возвращает прежний на его место.

    Возврат прежнего обязателен: плагин вправе заменить встроенный формат
    своим, и при выключении такого плагина программа должна снова уметь
    читать ASS — а не остаться без формата вовсе.
    """
    if fid not in FORMATS:
        return False
    if restore is not None:
        FORMATS[fid] = restore
    else:
        del FORMATS[fid]
    return True


def supported_read() -> list[FormatSpec]:
    return [f for f in FORMATS.values() if f.can_read]


def supported_write() -> list[FormatSpec]:
    return [f for f in FORMATS.values() if f.can_write]


def detect_format(text: str, hint: str | None = None) -> str:
    """Определяет формат по содержимому. ``hint`` — расширение как подсказка.

    Голосование, а не первое совпадение: файл с секцией ``[Events]`` — точно ASS,
    даже если где-то ниже встретилась строка со стрелкой ``-->``.
    """
    head = text[:8192]
    low = head.lower()

    if "[script info]" in low or "[v4+ styles]" in low or "[v4 styles]" in low:
        return "ass"
    if "dialogue:" in low and "," in head:
        return "ass"
    if "-->" in head:
        return "vtt" if low.lstrip("﻿").startswith("webvtt") else "srt"

    if hint:
        ext = hint.lower() if hint.startswith(".") else f".{hint.lower()}"
        for spec in FORMATS.values():
            if ext in spec.extensions:
                return spec.fid
    return "srt"


def load(path: Path, *, encoding: str | None = None) -> SubtitleDocument:
    """Читает файл субтитров, определяя формат и кодировку."""
    raw = path.read_bytes()
    text, detected = decode_bytes(raw, forced=encoding)
    fid = detect_format(text, hint=path.suffix)

    spec = FORMATS.get(fid)
    if spec is None or spec.reader is None:
        raise ValueError(f"формат {fid!r} не поддерживается для чтения")

    doc = spec.reader(text)
    doc.source_path = path
    doc.source_format = fid
    doc.source_encoding = detected
    return doc


def save(
    doc: SubtitleDocument,
    path: Path,
    *,
    fid: str | None = None,
    encoding: str = "utf-8",
    newline: str = "\n",
    **options: object,
) -> None:
    """Записывает документ.

    Запись атомарная: сначала во временный файл рядом, потом ``replace``.
    Если процесс упадёт посередине, исходник останется целым — для файла,
    над которым человек работал час, это не роскошь.
    """
    fid = fid or _format_for_suffix(path.suffix) or doc.source_format
    spec = FORMATS.get(fid)
    if spec is None or spec.writer is None:
        raise ValueError(f"формат {fid!r} не поддерживается для записи")

    text = spec.writer(doc, newline=newline, **options)

    tmp = path.with_name(path.name + f".tmp-{id(doc):x}")
    try:
        tmp.write_bytes(text.encode(encoding))
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    doc.source_path = path
    doc.source_format = fid
    doc.source_encoding = encoding


def _format_for_suffix(suffix: str) -> str | None:
    suffix = suffix.lower()
    for spec in FORMATS.values():
        if suffix in spec.extensions:
            return spec.fid
    return None
