"""Регрессии по скорости на большом документе.

Смысл этих тестов — не в измерении, а в **защите вложенного труда**. За
несколько заходов сделаны оптимизации, каждая из которых опиралась на замер:
отрисовка таймлайна 30,4 → 11,4 мс, цвет актора 1,07 → 0,08 мкс, пересборка
трека libass 79 → 0,24 мс, экран таблицы 12,2 → 3,2 мс. Без тестов всё это
тихо разъезжается обратно при первой же правке «для наглядности».

Как устроены проверки, чтобы не быть капризными.

**Главное — форма роста, а не абсолютное время.** Большинство тестов
сравнивают документ в 2 000 реплик с документом в 20 000 и требуют, чтобы
операция не подорожала. Такая проверка не зависит от того, насколько быстра
машина: медленный компьютер замедлит оба замера одинаково.

**Абсолютные пороги — с десятикратным запасом.** Они ловят катастрофу («стало
в сто раз медленнее»), а не колебания в проценты. Числа в комментариях — это
измеренное на машине разработки, пороги выставлены много выше.

**Берётся медиана нескольких прогонов.** Минимум льстит и прячет обычный
случай, среднее портится единственным чужим процессом, проснувшимся не вовремя.

Тесты помечены ``slow``: ``pytest -m "not slow"`` их пропустит.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable

import pytest

from sfstudio.core.commands import SetText
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.io.formats.ass import read_ass, write_ass
from sfstudio.services.qc import QcRunner
from sfstudio.services.search import SearchQuery, find_all

pytestmark = pytest.mark.slow

#: Размеры, на которых сравнивается рост. Разница ровно в десять раз, чтобы
#: линейная зависимость была видна невооружённым глазом.
SMALL = 2_000
LARGE = 20_000

#: Во сколько раз операция вправе подорожать при десятикратном росте
#: документа, чтобы всё ещё считаться независящей от его размера. Три —
#: с запасом на шум измерения; линейная зависимость дала бы около десяти.
FLAT_LIMIT = 3.0


def make_doc(count: int) -> SubtitleDocument:
    doc = SubtitleDocument.blank()
    for i in range(count):
        doc.create_event(
            i * 2000,
            i * 2000 + 1700,
            f"Реплика номер {i}, достаточно длинная чтобы считаться настоящей",
        )
    return doc


def median_ms(action: Callable[[], object], repeats: int = 5) -> float:
    """Медиана времени в миллисекундах."""
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


def growth(build: Callable[[int], object], action, repeats: int = 5) -> tuple[float, float]:
    """Время на малом и на большом документе."""
    small_data = build(SMALL)
    large_data = build(LARGE)
    small = median_ms(lambda: action(small_data), repeats)
    large = median_ms(lambda: action(large_data), repeats)
    return small, large


def assert_flat(title: str, small: float, large: float) -> None:
    """Требует, чтобы вдесятеро больший документ не стоил дороже."""
    # Слишком быстрые операции сравнивать по отношению бессмысленно: там
    # шум измерения больше самого измеряемого.
    if max(small, large) < 0.5:
        return
    ratio = large / small if small else 0.0
    assert ratio < FLAT_LIMIT, (
        f"{title}: подорожало в {ratio:.1f} раза при росте документа вдесятеро "
        f"({small:.2f} мс против {large:.2f} мс) — похоже, потеряна "
        f"независимость от размера файла"
    )


# --------------------------------------------------------------------------- #
# Операции, которые обязаны не зависеть от размера документа
# --------------------------------------------------------------------------- #


class TestConstantCost:
    """То, что делается на каждое нажатие клавиши и на каждый кадр."""

    def test_single_edit_does_not_depend_on_size(self) -> None:
        """Правка одной реплики. Измерено: 0,00 мс на обоих размерах."""

        def build(count):
            doc = make_doc(count)
            return doc, UndoStack(doc)

        def edit(pair):
            doc, undo = pair
            undo.run(SetText(doc.events[len(doc.events) // 2].eid, "новый текст"))

        small, large = growth(build, edit, repeats=30)
        assert_flat("правка реплики", small, large)

    def test_qc_recheck_does_not_depend_on_size(self) -> None:
        """Пересчёт проверок для одной реплики. Измерено: 0,02 мс.

        Соседи берутся из индекса за O(1); стоит вернуться к обходу
        документа — и правка текста начнёт тормозить на каждой букве.
        """

        def build(count):
            doc = make_doc(count)
            runner = QcRunner()
            runner.run_all(doc)
            return doc, runner

        def recheck(pair):
            doc, runner = pair
            runner.recheck(doc, [doc.events[len(doc.events) // 2].eid])

        small, large = growth(build, recheck, repeats=30)
        assert_flat("пересчёт проверок", small, large)

    def test_qc_recheck_is_fast(self) -> None:
        doc = make_doc(LARGE)
        runner = QcRunner()
        runner.run_all(doc)
        eid = doc.events[len(doc.events) // 2].eid
        spent = median_ms(lambda: runner.recheck(doc, [eid]), repeats=30)
        assert spent < 5.0, f"пересчёт одной реплики занял {spent:.2f} мс"


class TestFullRunsStayLinear:
    """Разовые операции: они растут с размером, но не должны взрываться."""

    def test_full_qc_run(self) -> None:
        """Полный прогон проверок при открытии файла. Измерено: 134 мс."""
        doc = make_doc(LARGE)
        spent = median_ms(lambda: QcRunner().run_all(doc), repeats=3)
        assert spent < 1500, f"полный прогон проверок занял {spent:.0f} мс"

    def test_search_over_everything(self) -> None:
        """Поиск по всему документу. Измерено: 24 мс."""
        doc = make_doc(LARGE)
        query = SearchQuery("номер")
        spent = median_ms(lambda: find_all(doc, query), repeats=3)
        assert spent < 400, f"поиск занял {spent:.0f} мс"

    def test_search_grows_no_faster_than_linearly(self) -> None:
        query = SearchQuery("номер")
        small, large = growth(make_doc, lambda d: find_all(d, query), repeats=3)
        ratio = large / small if small else 0.0
        assert ratio < 25, (
            f"поиск подорожал в {ratio:.0f} раз при росте документа вдесятеро — "
            "это хуже линейного"
        )

    def test_writing_ass(self) -> None:
        """Запись файла. Измерено: 58 мс."""
        doc = make_doc(LARGE)
        spent = median_ms(lambda: write_ass(doc), repeats=3)
        assert spent < 600, f"запись заняла {spent:.0f} мс"

    def test_reading_ass(self) -> None:
        """Чтение файла. Измерено: 91 мс."""
        text = write_ass(make_doc(LARGE))
        spent = median_ms(lambda: read_ass(text), repeats=3)
        assert spent < 900, f"чтение заняло {spent:.0f} мс"

    def test_round_trip_is_not_quadratic(self) -> None:
        """Чтение и запись должны расти линейно, а не квадратично."""
        small_text = write_ass(make_doc(SMALL))
        large_text = write_ass(make_doc(LARGE))
        small = median_ms(lambda: read_ass(small_text), repeats=3)
        large = median_ms(lambda: read_ass(large_text), repeats=3)
        ratio = large / small if small else 0.0
        assert ratio < 25, f"чтение подорожало в {ratio:.0f} раз вместо десяти"


# --------------------------------------------------------------------------- #
# Интерфейс
# --------------------------------------------------------------------------- #

pyside = pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sfstudio.ui.event_table import EventTableModel  # noqa: E402
from sfstudio.ui.timeline import TimelineWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class TestTimelinePainting:
    """Отрисовка таймлайна не должна зависеть от длины фильма.

    Она рисует только видимый отрезок времени и сворачивает волну до ширины
    виджета. Стоит начать обходить все события — и прокрутка встанет.
    """

    @staticmethod
    def paint_ms(count: int, repeats: int = 5) -> float:
        doc = make_doc(count)
        widget = TimelineWidget(doc, UndoStack(doc))
        widget.resize(1920, 400)
        image = QImage(1920, 400, QImage.Format_ARGB32)
        return median_ms(lambda: widget.render(image), repeats)

    def test_painting_does_not_depend_on_size(self, qapp) -> None:
        """Измерено: 3,1 мс против 2,2 мс — большой документ даже дешевле."""
        small = self.paint_ms(SMALL)
        large = self.paint_ms(LARGE)
        assert_flat("отрисовка таймлайна", small, large)

    def test_painting_is_fast(self, qapp) -> None:
        spent = self.paint_ms(LARGE)
        assert spent < 60, f"кадр таймлайна рисуется {spent:.1f} мс"


class TestTableScrolling:
    """Экран таблицы: то, что пересчитывается при каждой прокрутке.

    Здесь ловится конкретная ловушка: сравнение ролей Qt между собой стоит
    1,9 мкс против 0,027 мкс у целых чисел. Возврат к ``role == Qt.DisplayRole``
    вместо целочисленных констант возвращает и 12 мс на экран.
    """

    @staticmethod
    def screen_ms(count: int, repeats: int = 10) -> float:
        doc = make_doc(count)
        model = EventTableModel(doc)
        roles = (Qt.DisplayRole, Qt.ForegroundRole, Qt.BackgroundRole)

        def read_screen() -> None:
            for row in range(40):
                for col in range(8):
                    index = model.index(row, col)
                    for role in roles:
                        model.data(index, role)

        return median_ms(read_screen, repeats)

    def test_screen_does_not_depend_on_size(self, qapp) -> None:
        assert_flat("экран таблицы", self.screen_ms(SMALL), self.screen_ms(LARGE))

    def test_screen_is_fast(self, qapp) -> None:
        """Измерено: 3,2 мс на экран из сорока строк (было 12,2 мс).

        Порог грубый — он ловит только катастрофу. Настоящую регрессию по
        этому месту ловит тест ниже: абсолютное время слишком зависит от
        машины, чтобы ставить порог там, где разница вчетверо.
        """
        spent = self.screen_ms(LARGE)
        assert spent < 25, (
            f"экран таблицы обходится за {spent:.1f} мс — при прокрутке это заметно"
        )

    def test_model_does_not_compare_qt_enums(self, qapp) -> None:
        """Один вызов ``data`` должен стоить меньше сравнения Qt-ролей.

        Проверка калибруется по машине сама: эталон — стоимость **одного**
        сравнения двух перечислений Qt, измеренная здесь же. На медленном
        компьютере вырастет и эталон, и измеряемое, а отношение сохранится.
        Абсолютный порог так не умеет: разница между «хорошо» и «плохо»
        здесь вчетверо, а машины различаются сильнее.

        Если в ``data`` вернутся сравнения вида ``role == Qt.DisplayRole``,
        их наберётся до пяти на вызов — и тест это увидит.
        """
        doc = make_doc(SMALL)
        model = EventTableModel(doc)
        index = model.index(0, 0)
        rounds = 2000

        enum_cost = median_ms(
            lambda: [Qt.ForegroundRole == Qt.DisplayRole for _ in range(rounds)],
            repeats=7,
        )
        data_cost = median_ms(
            lambda: [model.data(index, Qt.ForegroundRole) for _ in range(rounds)],
            repeats=7,
        )

        assert data_cost < enum_cost * 1.5, (
            f"вызов data() стоит {data_cost / enum_cost:.1f} сравнения Qt-ролей "
            f"({data_cost:.1f} мс против {enum_cost:.1f} мс на {rounds} повторов). "
            "Похоже, роли снова сравниваются как перечисления, а не как числа."
        )

    def test_unwanted_roles_are_cheap(self, qapp) -> None:
        """Qt спрашивает два десятка ролей, из которых модель знает семь.

        На незнакомую роль ответ обязан быть почти бесплатным: иначе три
        четверти работы уходит на вопросы, ответа на которые нет.
        """
        doc = make_doc(SMALL)
        model = EventTableModel(doc)
        index = model.index(0, 0)
        spent = median_ms(
            lambda: [model.data(index, Qt.SizeHintRole) for _ in range(1000)],
            repeats=10,
        )
        assert spent < 5.0, f"тысяча отказов заняла {spent:.2f} мс"
