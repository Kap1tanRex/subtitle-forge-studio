"""Маркеры на таймлайне: заметки, привязанные ко времени, а не к реплике.

Зачем это отдельно от пометок к репликам (:mod:`sfstudio.core.workflow`).
Пометка живёт на реплике и отвечает на вопрос «что с этой строкой». Маркер
живёт на времени и отвечает на другой: «здесь смена сцены», «тут заказчик
просил перевести вывеску», «отсюда начинается второй акт». Реплики в этом
месте может не быть вовсе — а отметить его нужно.

Так это устроено в монтажных программах, и человек, пришедший из DaVinci
Resolve или Premiere, ждёт именно такого поведения: цветной флажок на
линейке, имя, примечание, ключевое слово для поиска.

**Длительность.** Маркер бывает точечным и протяжённым: «проверить этот кусок
целиком» — обычная задача. Ноль значит точку. Хранится в миллисекундах, а не
в кадрах: частота кадров у проекта может смениться, а время события — нет.

Где живёт в файле. Одним ключом ``SFStudio Markers`` в ``[Script Info]``, тем
же способом, что реестр акторов и рабочие пометки: незнакомые ключи этой
секции наш разбор, Aegisub и libass сохраняют дословно и игнорируют. Файл
остаётся обычным ASS.

**Привязка ко времени надёжнее, чем у пометок к репликам.** Те держатся на
порядковом номере строки и разъезжаются, если файл перекроят в другой
программе. У маркера привязки к строке нет: время остаётся временем.
"""

from __future__ import annotations

import json
from bisect import bisect_left, insort
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace

from sfstudio.app.i18n import tr

__all__ = [
    "DEFAULT_COLOR",
    "INFO_KEY",
    "MARKER_COLORS",
    "Marker",
    "MarkerList",
    "color_title",
    "color_value",
    "is_known_color",
]

#: Ключ в ``[Script Info]``. С префиксом — чтобы не столкнуться с полями
#: других программ.
INFO_KEY = "SFStudio Markers"

#: Цвета маркеров: ключ, подпись, что рисовать. Набор и порядок такие же, как
#: в монтажных программах: кто ставил маркеры в Resolve, найдёт свой цвет на
#: том же месте. Ключ уходит в файл, подпись видит человек.
MARKER_COLORS: tuple[tuple[str, str, str], ...] = (
    ("blue", tr('Синий'), "#3C8CE0"),
    ("cyan", tr('Голубой'), "#35B8C4"),
    ("green", tr('Зелёный'), "#4CAF50"),
    ("yellow", tr('Жёлтый'), "#D6BE3A"),
    ("red", tr('Красный'), "#D9483B"),
    ("pink", tr('Розовый'), "#E06CA0"),
    ("purple", tr('Фиолетовый'), "#8E5BD0"),
    ("fuchsia", tr('Пурпурный'), "#C24CC2"),
    ("rose", tr('Пепельно-розовый'), "#E08A9B"),
    ("lavender", tr('Лавандовый'), "#A99BE0"),
    ("sky", tr('Небесный'), "#7FC4E8"),
    ("mint", tr('Мятный'), "#7FD6A8"),
    ("lemon", tr('Лимонный'), "#E3E07A"),
    ("sand", tr('Песочный'), "#D6A96A"),
    ("cocoa", tr('Какао'), "#9B7A5A"),
    ("cream", tr('Кремовый'), "#E8E0CC"),
)

#: Цвет нового маркера. Синий — первый в палитре и самый нейтральный.
DEFAULT_COLOR = MARKER_COLORS[0][0]

_TITLES = {key: title for key, title, _value in MARKER_COLORS}
_VALUES = {key: value for key, _title, value in MARKER_COLORS}


def is_known_color(key: str) -> bool:
    return key in _VALUES


def color_title(key: str) -> str:
    return _TITLES.get(key, key)


def color_value(key: str) -> str:
    """Что рисовать. Незнакомый ключ даёт цвет по умолчанию, а не ошибку."""
    return _VALUES.get(key, _VALUES[DEFAULT_COLOR])


@dataclass(frozen=True, slots=True, order=True)
class Marker:
    """Отметка на таймлайне.

    Поля упорядочены так, что сортировка списка идёт по времени: маркеры
    почти всегда нужны по порядку — для перехода к следующему, для рисования
    видимого участка, для записи в файл.
    """

    time: int
    #: Ноль — точка. Иначе отмеченный отрезок.
    duration: int = 0
    name: str = ""
    note: str = ""
    #: Слово для поиска и группировки: «вывеска», «спросить», «переозвучка».
    keyword: str = ""
    color: str = DEFAULT_COLOR

    @property
    def end(self) -> int:
        return self.time + max(0, self.duration)

    def covers(self, ms: int) -> bool:
        """Точечный маркер накрывает своё мгновение, протяжённый — отрезок."""
        return self.time <= ms <= self.end

    def moved_to(self, ms: int) -> Marker:
        return replace(self, time=max(0, int(ms)))

    def title(self) -> str:
        """Как назвать маркер в списке, если имени ему не дали."""
        return self.name or self.keyword or self.note.split("\n")[0] or tr('Без имени')


class MarkerList:
    """Маркеры документа, всегда по возрастанию времени.

    Список, а не словарь по времени: два маркера в одном кадре — законный
    случай (конец одной задачи и начало другой), и терять один из них
    молча недопустимо.
    """

    __slots__ = ("_items",)

    def __init__(self, markers: Iterable[Marker] = ()) -> None:
        self._items: list[Marker] = sorted(markers)

    # -- доступ -------------------------------------------------------------- #

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Marker]:
        return iter(self._items)

    def __getitem__(self, index: int) -> Marker:
        return self._items[index]

    def __contains__(self, marker: object) -> bool:
        return marker in self._items

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MarkerList):
            return self._items == other._items
        return NotImplemented

    def __repr__(self) -> str:
        return tr('MarkerList({0} маркеров)').format(len(self._items))

    @property
    def items(self) -> list[Marker]:
        """Копия списка. Правка идёт через методы, а не через чужую ссылку."""
        return list(self._items)

    def keywords(self) -> list[str]:
        """Ключевые слова без повторов, по алфавиту — для подсказки в поле."""
        return sorted({m.keyword for m in self._items if m.keyword})

    # -- изменение ------------------------------------------------------------ #

    def add(self, marker: Marker) -> Marker:
        insort(self._items, marker)
        return marker

    def remove(self, marker: Marker) -> bool:
        """Убирает ровно этот маркер. ``False``, если его уже нет."""
        try:
            self._items.remove(marker)
        except ValueError:
            return False
        return True

    def replace(self, old: Marker, new: Marker) -> bool:
        """Заменяет маркер. Порядок восстанавливается: время могло смениться."""
        if not self.remove(old):
            return False
        self.add(new)
        return True

    def clear(self) -> None:
        self._items.clear()

    # -- поиск --------------------------------------------------------------- #

    def at(self, ms: int, radius: int = 0) -> Marker | None:
        """Маркер в этой точке. ``radius`` — допуск попадания в мс.

        Из нескольких подходящих берётся ближайший по началу: попасть мышью
        в один из двух соседних маркеров иначе было бы делом случая.
        """
        hits = [m for m in self._items if m.time - radius <= ms <= m.end + radius]
        if not hits:
            return None
        return min(hits, key=lambda m: abs(m.time - ms))

    def in_range(self, start: int, end: int) -> list[Marker]:
        """Маркеры, попадающие в отрезок, — для отрисовки видимого участка."""
        return [m for m in self._items if m.end >= start and m.time <= end]

    def after(self, ms: int) -> Marker | None:
        """Первый маркер строго позже указанного времени."""
        index = bisect_left(self._items, (ms + 1,), key=lambda m: (m.time,))
        return self._items[index] if index < len(self._items) else None

    def before(self, ms: int) -> Marker | None:
        """Последний маркер строго раньше указанного времени."""
        index = bisect_left(self._items, (ms,), key=lambda m: (m.time,))
        return self._items[index - 1] if index > 0 else None

    # -- хранение ------------------------------------------------------------- #

    def to_json(self) -> str:
        """Пустые поля не пишутся: файл не должен пухнуть от умолчаний."""
        payload = []
        for marker in self._items:
            item: dict[str, object] = {"t": marker.time}
            if marker.duration:
                item["d"] = marker.duration
            if marker.name:
                item["n"] = marker.name
            if marker.note:
                item["c"] = marker.note
            if marker.keyword:
                item["k"] = marker.keyword
            if marker.color != DEFAULT_COLOR:
                item["p"] = marker.color
            payload.append(item)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def from_json(text: str) -> MarkerList:
        """Разбирает список. Мусор даёт пустой список, а не исключение.

        Поле пишем не только мы: файл могли поправить руками, а другая
        программа — обрезать длинную строку. Терять из-за маркеров
        возможность открыть субтитры нельзя.
        """
        markers = MarkerList()
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return markers
        if not isinstance(payload, list):
            return markers

        for item in payload:
            if not isinstance(item, dict):
                continue
            time = item.get("t")
            if not isinstance(time, int | float):
                continue
            duration = item.get("d", 0)
            if not isinstance(duration, int | float):
                duration = 0
            color = str(item.get("p", DEFAULT_COLOR))
            markers.add(
                Marker(
                    time=max(0, int(time)),
                    duration=max(0, int(duration)),
                    name=str(item.get("n", "")),
                    note=str(item.get("c", "")),
                    keyword=str(item.get("k", "")),
                    # Цвет из будущей версии рисовать нечем — берём обычный.
                    # Отбросить сам маркер из-за незнакомого цвета было бы
                    # несоразмерно: важна отметка, а не её оттенок.
                    color=color if is_known_color(color) else DEFAULT_COLOR,
                )
            )
        return markers
