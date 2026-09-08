"""Окно времени в рендерере: та же картинка при постоянной стоимости.

Полная пересборка трека libass стоила 79 мс на документе в двадцать тысяч
реплик — и платилась при каждой правке. Здесь проверяется, что окно вокруг
текущего времени эту стоимость убрало, **не изменив результата**: ускорение,
которое рисует другую картинку, не ускорение, а поломка.
"""

from __future__ import annotations

import time

import pytest

from sfstudio.core.document import SubtitleDocument
from sfstudio.render.renderer import WINDOW_PAD_MS, LibassRenderer, available

pytestmark = pytest.mark.skipif(not available(), reason="нужна libass")


def make_doc(count: int, *, step: int = 1000, length: int = 900) -> SubtitleDocument:
    doc = SubtitleDocument.blank()
    for i in range(count):
        doc.create_event(i * step, i * step + length, f"Реплика номер {i}")
    return doc


def make_renderer(doc: SubtitleDocument) -> LibassRenderer:
    renderer = LibassRenderer()
    renderer.set_frame_size(1920, 1080, doc.script_info.play_res)
    return renderer


def shapes(layers) -> list[tuple]:
    """Признаки нарисованного: положение и размер каждого куска."""
    return [(b.x, b.y, b.w, b.h) for b in layers]


class TestSameResult:
    """Главное требование: окно не меняет того, что видит зритель."""

    @pytest.mark.parametrize("moment", [500, 5_000, 50_000, 199_500])
    def test_frame_matches_the_full_track(self, moment: int) -> None:
        doc = make_doc(400)

        windowed = make_renderer(doc)
        with_window = shapes(windowed.render(doc, moment))
        windowed.close()

        whole = make_renderer(doc)
        whole.sync(doc)  # без времени — весь документ, как было раньше
        without_window = shapes(whole._ctx.render(moment))
        whole.close()

        assert with_window == without_window

    def test_long_event_started_before_the_window_is_visible(self) -> None:
        """Вывеска на весь фильм не должна пропасть из-за обрезки по времени."""
        doc = SubtitleDocument.blank()
        doc.create_event(0, 600_000, "Долгая вывеска")
        renderer = make_renderer(doc)
        try:
            moment = 300_000  # далеко за окном от начала события
            assert renderer.render(doc, moment), "длинное событие пропало"
        finally:
            renderer.close()

    def test_neighbours_still_collide_properly(self) -> None:
        """libass раздвигает одновременные реплики — они все внутри окна."""
        doc = SubtitleDocument.blank()
        doc.create_event(10_000, 12_000, "Первая одновременная")
        doc.create_event(10_000, 12_000, "Вторая одновременная")

        renderer = make_renderer(doc)
        try:
            drawn = renderer.render(doc, 11_000)
            tops = {b.y for b in drawn}
            assert len(tops) > 1, "реплики нарисованы одна поверх другой"
        finally:
            renderer.close()


class TestWindowBehaviour:
    def test_moving_inside_the_window_does_not_rebuild(self) -> None:
        doc = make_doc(100)
        renderer = make_renderer(doc)
        try:
            renderer.render(doc, 50_000)
            window = renderer._window
            renderer.render(doc, 50_000 + WINDOW_PAD_MS // 2)
            assert renderer._window == window
        finally:
            renderer.close()

    def test_leaving_the_window_rebuilds(self) -> None:
        doc = make_doc(400)
        renderer = make_renderer(doc)
        try:
            renderer.render(doc, 50_000)
            window = renderer._window
            renderer.render(doc, 50_000 + WINDOW_PAD_MS * 3)
            assert renderer._window != window
        finally:
            renderer.close()

    def test_edit_inside_the_window_is_shown(self) -> None:
        """Правка обязана появиться на кадре сразу, а не после перемотки."""
        doc = SubtitleDocument.blank()
        event = doc.create_event(10_000, 12_000, "Было")
        renderer = make_renderer(doc)
        try:
            before = shapes(renderer.render(doc, 11_000))
            event.text = "Стало заметно длиннее прежнего"
            doc.bump_revision()
            after = shapes(renderer.render(doc, 11_000))
            assert before != after
        finally:
            renderer.close()

    def test_invalidate_forces_a_rebuild(self) -> None:
        doc = make_doc(50)
        renderer = make_renderer(doc)
        try:
            renderer.render(doc, 10_000)
            renderer.invalidate()
            assert renderer._window is None
        finally:
            renderer.close()

    def test_full_sync_still_available(self) -> None:
        """Режим «весь документ» остаётся: на нём держатся замеры и проверки."""
        doc = make_doc(50)
        renderer = make_renderer(doc)
        try:
            renderer.sync(doc)
            assert renderer._window is None
        finally:
            renderer.close()


class TestCostDoesNotGrow:
    """Регрессия по скорости: стоимость правки не должна зависеть от длины.

    Тест намеренно сравнивает документы, различающиеся в десять раз. Пороги
    взяты с большим запасом — здесь ловится возврат к полной пересборке
    (разница была в 315 раз), а не колебания в проценты.
    """

    @staticmethod
    def rebuild_ms(doc: SubtitleDocument, moment: int, *, repeats: int = 15) -> float:
        renderer = make_renderer(doc)
        try:
            renderer.render(doc, moment)
            samples = []
            for _ in range(repeats):
                doc.bump_revision()
                started = time.perf_counter()
                renderer.sync(doc, moment)
                samples.append((time.perf_counter() - started) * 1000)
            samples.sort()
            return samples[len(samples) // 2]
        finally:
            renderer.close()

    def test_ten_times_more_events_cost_the_same(self) -> None:
        small = self.rebuild_ms(make_doc(2_000), 1_000_000)
        large = self.rebuild_ms(make_doc(20_000), 10_000_000)
        assert large < max(small * 4, 5.0), (
            f"правка подорожала с ростом документа: {small:.2f} мс против {large:.2f} мс"
        )

    def test_rebuild_is_fast_in_absolute_terms(self) -> None:
        """Пересборка окна — единицы миллисекунд, а не десятки."""
        assert self.rebuild_ms(make_doc(20_000), 10_000_000) < 10.0

    def test_window_holds_only_nearby_events(self) -> None:
        doc = make_doc(20_000)
        nearby = doc.in_range(10_000_000 - WINDOW_PAD_MS, 10_000_000 + WINDOW_PAD_MS)
        assert len(nearby) < 200, "окно захватило слишком много — смысл теряется"
