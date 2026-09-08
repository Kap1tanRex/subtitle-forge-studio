"""Шаблоны оформления субтитров.

Стиль в ASS живёт внутри документа: настроил вид в одном файле — в следующем
настраивай заново. Шаблон решает именно это: набор параметров (шрифт, размер,
цвета, обводка, тень, выравнивание, поля) хранится **между документами** и
применяется в один клик.

Хранилище — по файлу на шаблон в каталоге пресетов. Так их можно переносить,
присылать коллеге и класть в систему контроля версий поштучно, а повреждение
одного файла не уносит всю библиотеку.

Встроенные шаблоны не записываются на диск и не удаляются: это отправная
точка, от которой пользователь делает свои. Пользовательский шаблон с тем же
именем перекрывает встроенный.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from sfstudio.core.color import RGBA
from sfstudio.core.style import SubtitleStyle
from sfstudio.platform.paths import presets_dir

__all__ = ["BUILTIN_PRESETS", "PresetError", "PresetLibrary", "StylePreset"]

FORMAT_VERSION = 1

_SAFE_NAME = re.compile(r"[^\w \-.()]+", re.UNICODE)


class PresetError(RuntimeError):
    """Шаблон не удалось прочитать или сохранить."""


@dataclass(slots=True)
class StylePreset:
    """Именованный набор параметров оформления."""

    name: str
    style: SubtitleStyle
    description: str = ""
    #: Встроенные шаблоны нельзя удалить, только перекрыть своим.
    builtin: bool = False

    def apply_to(self, target: SubtitleStyle) -> SubtitleStyle:
        """Копия целевого стиля с параметрами шаблона.

        Имя стиля сохраняется: шаблон меняет вид, а не переименовывает стиль,
        на который ссылаются события.
        """
        return replace(self.style, name=target.name)

    def as_style(self, name: str) -> SubtitleStyle:
        """Новый стиль с заданным именем."""
        return replace(self.style, name=name)

    # -- сериализация ------------------------------------------------------------ #

    def to_dict(self) -> dict:
        s = self.style
        return {
            "format_version": FORMAT_VERSION,
            "name": self.name,
            "description": self.description,
            "style": {
                "fontname": s.fontname,
                "fontsize": s.fontsize,
                "primary": s.primary.to_ass(),
                "secondary": s.secondary.to_ass(),
                "outline_color": s.outline_color.to_ass(),
                "back_color": s.back_color.to_ass(),
                "bold": s.bold,
                "italic": s.italic,
                "underline": s.underline,
                "strikeout": s.strikeout,
                "scale_x": s.scale_x,
                "scale_y": s.scale_y,
                "spacing": s.spacing,
                "angle": s.angle,
                "border_style": s.border_style,
                "outline": s.outline,
                "shadow": s.shadow,
                "alignment": s.alignment,
                "margin_l": s.margin_l,
                "margin_r": s.margin_r,
                "margin_v": s.margin_v,
                "encoding": s.encoding,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> StylePreset:
        raw = data.get("style")
        if not isinstance(raw, dict):
            raise PresetError("в файле шаблона нет раздела style")

        def colour(key: str, fallback: RGBA) -> RGBA:
            try:
                return RGBA.from_ass(str(raw[key]))
            except (KeyError, ValueError):
                return fallback

        base = SubtitleStyle()
        style = SubtitleStyle(
            name=str(data.get("name", "Без имени")),
            fontname=str(raw.get("fontname", base.fontname)),
            fontsize=float(raw.get("fontsize", base.fontsize)),
            primary=colour("primary", base.primary),
            secondary=colour("secondary", base.secondary),
            outline_color=colour("outline_color", base.outline_color),
            back_color=colour("back_color", base.back_color),
            bold=bool(raw.get("bold", base.bold)),
            italic=bool(raw.get("italic", base.italic)),
            underline=bool(raw.get("underline", base.underline)),
            strikeout=bool(raw.get("strikeout", base.strikeout)),
            scale_x=float(raw.get("scale_x", base.scale_x)),
            scale_y=float(raw.get("scale_y", base.scale_y)),
            spacing=float(raw.get("spacing", base.spacing)),
            angle=float(raw.get("angle", base.angle)),
            border_style=int(raw.get("border_style", base.border_style)),
            outline=float(raw.get("outline", base.outline)),
            shadow=float(raw.get("shadow", base.shadow)),
            alignment=int(raw.get("alignment", base.alignment)),
            margin_l=int(raw.get("margin_l", base.margin_l)),
            margin_r=int(raw.get("margin_r", base.margin_r)),
            margin_v=int(raw.get("margin_v", base.margin_v)),
            encoding=int(raw.get("encoding", base.encoding)),
        )
        if not 1 <= style.alignment <= 9:
            style.alignment = 2
        return cls(
            name=str(data.get("name", "Без имени")),
            style=style,
            description=str(data.get("description", "")),
        )

    @classmethod
    def from_style(
        cls, style: SubtitleStyle, name: str | None = None, description: str = ""
    ) -> StylePreset:
        """Делает шаблон из стиля документа — «сохранить как шаблон»."""
        return cls(
            name=name or style.name,
            style=replace(style),
            description=description,
        )


def _preset(
    name: str,
    description: str,
    **overrides: object,
) -> StylePreset:
    style = SubtitleStyle(name=name)
    for key, value in overrides.items():
        setattr(style, key, value)
    return StylePreset(name=name, style=style, description=description, builtin=True)


#: Встроенные шаблоны — отправная точка, а не исчерпывающий набор.
BUILTIN_PRESETS: tuple[StylePreset, ...] = (
    _preset(
        "Классические белые",
        "Белый текст с чёрной обводкой — то, что ждут от субтитров по умолчанию",
        fontname="Arial", fontsize=48,
        primary=RGBA(255, 255, 255), outline_color=RGBA(0, 0, 0),
        outline=2.5, shadow=1.5,
    ),
    _preset(
        "Крупные для телевизора",
        "Увеличенный кегль и поля: читается с дивана и не липнет к краю экрана",
        fontname="Arial", fontsize=68,
        primary=RGBA(255, 255, 255), outline_color=RGBA(0, 0, 0),
        outline=3.5, shadow=2.0, margin_v=60, margin_l=80, margin_r=80,
    ),
    _preset(
        "Жёлтые",
        "Тёплый жёлтый: заметнее на светлом и пёстром фоне",
        fontname="Arial", fontsize=50,
        primary=RGBA(255, 222, 89), outline_color=RGBA(0, 0, 0),
        outline=2.5, shadow=1.5,
    ),
    _preset(
        "Надпись сверху",
        "Для вывесок и пояснений: прижата к верху кадра, курсив",
        fontname="Arial", fontsize=40, italic=True,
        primary=RGBA(255, 255, 255), outline_color=RGBA(0, 0, 0),
        outline=2.0, shadow=0.0, alignment=8, margin_v=30,
    ),
    _preset(
        "Плашка",
        "Непрозрачная подложка вместо обводки — для очень пёстрого видео",
        fontname="Arial", fontsize=46,
        primary=RGBA(255, 255, 255), back_color=RGBA(0, 0, 0, 190),
        border_style=3, outline=1.0, shadow=0.0,
    ),
    _preset(
        "Мелкие плотные",
        "Компактный вариант для длинных реплик и плотного диалога",
        fontname="Arial", fontsize=38,
        primary=RGBA(255, 255, 255), outline_color=RGBA(0, 0, 0),
        outline=2.0, shadow=1.0, margin_v=16,
    ),
)


@dataclass(slots=True)
class PresetLibrary:
    """Каталог шаблонов: встроенные плюс пользовательские."""

    directory: Path = field(default_factory=presets_dir)
    _user: dict[str, StylePreset] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.reload()

    # -- чтение ------------------------------------------------------------------- #

    def reload(self) -> None:
        """Перечитывает пользовательские шаблоны с диска.

        Повреждённый файл пропускается, а не роняет загрузку остальных:
        библиотека из двадцати шаблонов не должна становиться недоступной
        из-за одного испорченного.
        """
        self._user.clear()
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                preset = StylePreset.from_dict(data)
            except (OSError, json.JSONDecodeError, PresetError, ValueError, TypeError):
                continue
            self._user[preset.name] = preset

    def all(self) -> list[StylePreset]:
        """Все шаблоны. Пользовательский с тем же именем перекрывает встроенный."""
        merged: dict[str, StylePreset] = {p.name: p for p in BUILTIN_PRESETS}
        merged.update(self._user)
        return sorted(merged.values(), key=lambda p: (not p.builtin, p.name.lower()))

    def get(self, name: str) -> StylePreset | None:
        if name in self._user:
            return self._user[name]
        return next((p for p in BUILTIN_PRESETS if p.name == name), None)

    def names(self) -> list[str]:
        return [p.name for p in self.all()]

    # -- запись -------------------------------------------------------------------- #

    def save(self, preset: StylePreset) -> Path:
        """Сохраняет шаблон. Запись атомарная."""
        name = preset.name.strip()
        if not name:
            raise PresetError("у шаблона должно быть имя")

        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{_safe_filename(name)}.json"
        temporary = path.with_name(path.name + ".tmp")
        payload = replace(preset, name=name, builtin=False).to_dict()
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            raise PresetError(f"не удалось сохранить шаблон: {exc}") from exc
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

        self._user[name] = replace(preset, name=name, builtin=False)
        return path

    def delete(self, name: str) -> bool:
        """Удаляет пользовательский шаблон.

        Встроенный удалить нельзя — он вернётся при следующем запуске, и
        пользователь решит, что удаление не сработало. Вместо удаления
        встроенный перекрывается своим с тем же именем.
        """
        if name not in self._user:
            return False
        path = self.directory / f"{_safe_filename(name)}.json"
        path.unlink(missing_ok=True)
        del self._user[name]
        return True

    def is_builtin(self, name: str) -> bool:
        preset = self.get(name)
        return preset is not None and preset.builtin


def _safe_filename(name: str) -> str:
    """Имя файла из имени шаблона.

    Пользователь называет шаблоны как хочет, включая символы, недопустимые в
    именах файлов. Заменяем их, но кириллицу оставляем: имена должны
    оставаться узнаваемыми в проводнике.
    """
    cleaned = _SAFE_NAME.sub("_", name).strip(" .")
    return cleaned or "preset"
