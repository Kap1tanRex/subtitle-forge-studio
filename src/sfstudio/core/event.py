"""Событие субтитра — одна реплика."""

from __future__ import annotations

from dataclasses import dataclass, field

from sfstudio.core import tags as tagmod

__all__ = ["SubtitleEvent"]


@dataclass(slots=True)
class SubtitleEvent:
    """Одна строка ``Dialogue:``/``Comment:``.

    ``eid`` стабилен внутри документа и не зависит от порядка: выделение, undo,
    маркеры QC ссылаются именно на него. Индексы строк меняются при сортировке,
    ``eid`` — нет.
    """

    eid: int
    start: int = 0  # мс
    end: int = 0  # мс
    text: str = ""  # ASS-текст с тегами; перевод строки — literal \N
    style: str = "Default"
    layer: int = 0
    name: str = ""  # Actor
    margin_l: int = 0  # 0 = наследовать из стиля
    margin_r: int = 0
    margin_v: int = 0
    effect: str = ""
    comment: bool = False

    #: Порядковый номер при чтении — нужен для побайтового round-trip и для
    #: восстановления исходного порядка после сортировок.
    read_order: int = 0

    # Кэш разбора тегов; сбрасывается через invalidate() при смене text.
    _plain: str | None = field(default=None, repr=False, compare=False)
    _parsed: tagmod.ParsedTags | None = field(default=None, repr=False, compare=False)

    # -- производные ------------------------------------------------------- #

    @property
    def duration(self) -> int:
        return self.end - self.start

    def invalidate(self) -> None:
        """Сбрасывает кэши. Обязателен после прямой записи в ``text``."""
        self._plain = None
        self._parsed = None

    def set_text(self, value: str) -> None:
        self.text = value
        self.invalidate()

    @property
    def parsed(self) -> tagmod.ParsedTags:
        if self._parsed is None:
            self._parsed = tagmod.parse_tags(self.text)
        return self._parsed

    @property
    def plain(self) -> str:
        """Видимый текст: без тегов, с настоящими переводами строк."""
        if self._plain is None:
            self._plain = tagmod.plain_text(self.text)
        return self._plain

    @property
    def line_count(self) -> int:
        return self.plain.count("\n") + 1

    @property
    def longest_line(self) -> int:
        return max((len(line) for line in self.plain.split("\n")), default=0)

    def cps(self, *, count_spaces: bool = False) -> float:
        """Символов в секунду. При нулевой длительности — 0, не деление на ноль."""
        if self.duration <= 0:
            return 0.0
        text = self.plain if count_spaces else self.plain.replace(" ", "").replace("\n", "")
        return len(text) * 1000.0 / self.duration

    # -- позиция ------------------------------------------------------------ #

    def position(self) -> tuple[float, float] | None:
        """Координаты из ``\\pos``, либо начальная точка ``\\move``, либо ``None``."""
        tag = self.parsed.get_tag("pos")
        if tag is not None:
            nums = tag.numbers()
            if len(nums) >= 2:
                return nums[0], nums[1]
        tag = self.parsed.get_tag("move")
        if tag is not None:
            nums = tag.numbers()
            if len(nums) >= 2:
                return nums[0], nums[1]
        return None

    def alignment_override(self) -> int | None:
        """``\\an`` события, либо конверсия legacy ``\\a``, либо ``None``."""
        tag = self.parsed.get_tag("an")
        if tag is not None:
            nums = tag.numbers()
            if nums and 1 <= int(nums[0]) <= 9:
                return int(nums[0])
        tag = self.parsed.get_tag("a")
        if tag is not None:
            nums = tag.numbers()
            if nums:
                return _legacy_alignment(int(nums[0]))
        return None

    def overlaps(self, other: SubtitleEvent) -> bool:
        return self.start < other.end and other.start < self.end


def _legacy_alignment(value: int) -> int | None:
    """SSA ``\\a`` → ASS ``\\an``.

    Старая нотация кодирует ряд битами: 4 = верх, 8 = центр по вертикали.
    """
    col = value & 0b0011
    if col == 0:
        return None
    vertical = value & 0b1100
    if vertical == 4:  # верх
        return 6 + col
    if vertical == 8:  # середина
        return 3 + col
    return col  # низ
