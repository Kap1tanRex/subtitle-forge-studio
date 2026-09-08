"""Тесты обёртки над libass и измерителя.

Проверяют то, что нельзя проверить на заглушке: что цвета не перепутаны,
что альфа инвертирована в нужную сторону, что bbox совпадает с ожидаемым
положением, и что теги позиционирования реально двигают текст.
"""

from __future__ import annotations

import pytest

from sfstudio.render.libass import available

if not available():
    pytest.skip("libass не установлена", allow_module_level=True)

from sfstudio.core.document import SubtitleDocument
from sfstudio.render.libass import (
    IMAGE_TYPE_CHARACTER,
    IMAGE_TYPE_SHADOW,
    AssContext,
)
from sfstudio.render.renderer import LibassMeasurer

pytestmark = pytest.mark.needs_native

PLAY_RES = (1920, 1080)


@pytest.fixture(scope="module")
def ctx() -> AssContext:
    context = AssContext()
    context.set_frame_size(*PLAY_RES)
    context.set_storage_size(*PLAY_RES)
    yield context
    context.close()


def make_doc(text: str = "Привет", **style_kw) -> SubtitleDocument:
    doc = SubtitleDocument.blank(PLAY_RES)
    style = doc.styles["Default"]
    style.fontsize = 64
    for key, value in style_kw.items():
        setattr(style, key, value)
    doc.create_event(0, 10_000, text)
    return doc


def script_of(doc: SubtitleDocument) -> str:
    from sfstudio.io.formats.ass import write_ass

    return write_ass(doc)


class TestContext:
    def test_version_is_plausible(self, ctx: AssContext) -> None:
        """Версия разбирается BCD-кодировкой: 0x01705001 это 0.17.5, а не 0.23.5."""
        parts = ctx.version.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)
        assert int(parts[1]) < 100  # при неверном разборе minor уезжает за сотню

    def test_renders_something(self, ctx: AssContext) -> None:
        ctx.load_track(script_of(make_doc()))
        assert ctx.render(1000)

    def test_empty_document_renders_nothing(self, ctx: AssContext) -> None:
        doc = SubtitleDocument.blank(PLAY_RES)
        doc.create_event(0, 1000, "текст")
        ctx.load_track(script_of(doc))
        assert ctx.render(5000) == []  # событие уже закончилось

    def test_layer_order_is_back_to_front(self, ctx: AssContext) -> None:
        """Тень должна идти раньше глифа: порядок списка — порядок наложения."""
        ctx.load_track(script_of(make_doc(shadow=3, outline=2)))
        kinds = [b.kind for b in ctx.render(1000)]
        assert IMAGE_TYPE_SHADOW in kinds
        assert IMAGE_TYPE_CHARACTER in kinds
        assert kinds.index(IMAGE_TYPE_SHADOW) < kinds.index(IMAGE_TYPE_CHARACTER)

    def test_alpha_is_inverted_from_raw(self, ctx: AssContext) -> None:
        """Непрозрачный белый в libass это 0xFFFFFF00, а у нас a=255."""
        ctx.load_track(script_of(make_doc()))
        glyphs = [b for b in ctx.render(1000) if b.kind == IMAGE_TYPE_CHARACTER]
        assert glyphs
        r, g, b, a = glyphs[0].rgba
        assert (r, g, b) == (255, 255, 255)
        assert a == 255

    def test_bitmap_data_size_matches_declared(self, ctx: AssContext) -> None:
        ctx.load_track(script_of(make_doc()))
        for bitmap in ctx.render(1000):
            assert len(bitmap.data) == bitmap.h * bitmap.stride
            assert bitmap.stride >= bitmap.w

    def test_bitmap_has_nonzero_coverage(self, ctx: AssContext) -> None:
        """Маска не должна быть пустой — иначе текст невидим."""
        ctx.load_track(script_of(make_doc()))
        glyphs = [b for b in ctx.render(1000) if b.kind == IMAGE_TYPE_CHARACTER]
        assert any(any(byte > 0 for byte in b.data) for b in glyphs)

    def test_bbox_excludes_shadow_by_default(self, ctx: AssContext) -> None:
        ctx.load_track(script_of(make_doc(shadow=8, outline=1)))
        tight = ctx.bbox(1000)
        loose = ctx.bbox(1000, include_shadow=True)
        assert tight is not None and loose is not None
        assert loose[2] >= tight[2]
        assert loose[3] >= tight[3]

    def test_track_can_be_reloaded(self, ctx: AssContext) -> None:
        ctx.load_track(script_of(make_doc("Короткий")))
        first = ctx.bbox(1000)
        ctx.load_track(script_of(make_doc("Значительно более длинный текст")))
        second = ctx.bbox(1000)
        assert first and second
        assert second[2] > first[2]


class TestMeasurer:
    def test_default_position_is_bottom_centre(self) -> None:
        doc = make_doc()
        measurer = LibassMeasurer(doc)
        rect = measurer.bbox(doc.events[0])
        assert rect is not None
        assert rect.center_x == pytest.approx(960, abs=25)
        assert rect.bottom > 900  # у нижнего края кадра
        measurer.close()

    def test_pos_tag_moves_the_box(self) -> None:
        doc = make_doc()
        doc.events[0].set_text(r"{\pos(400,300)\an5}Привет")
        measurer = LibassMeasurer(doc)
        rect = measurer.bbox(doc.events[0])
        assert rect is not None
        assert rect.center_x == pytest.approx(400, abs=25)
        assert rect.center_y == pytest.approx(300, abs=25)
        measurer.close()

    def test_alignment_moves_to_top(self) -> None:
        doc = make_doc()
        doc.events[0].set_text(r"{\an8}Привет")
        measurer = LibassMeasurer(doc)
        rect = measurer.bbox(doc.events[0])
        assert rect is not None
        assert rect.top < 200
        measurer.close()

    def test_longer_text_is_wider(self) -> None:
        doc = make_doc("Аб")
        measurer = LibassMeasurer(doc)
        short = measurer.bbox(doc.events[0])
        doc.events[0].set_text("Абвгдежзийклмноп")
        long = measurer.bbox(doc.events[0])
        assert short and long
        assert long.w > short.w * 2
        measurer.close()

    def test_two_lines_are_taller(self) -> None:
        doc = make_doc("Одна строка")
        measurer = LibassMeasurer(doc)
        one = measurer.bbox(doc.events[0])
        doc.events[0].set_text(r"Одна строка\NВторая строка")
        two = measurer.bbox(doc.events[0])
        assert one and two
        assert two.h > one.h * 1.5
        measurer.close()

    def test_bigger_font_gives_bigger_box(self) -> None:
        doc = make_doc()
        measurer = LibassMeasurer(doc)
        small = measurer.bbox(doc.events[0])
        doc.styles["Default"].fontsize = 128
        measurer.set_document(doc)  # сбрасывает кэш
        big = measurer.bbox(doc.events[0])
        assert small and big
        assert big.h > small.h * 1.5
        measurer.close()

    def test_cache_returns_same_result(self) -> None:
        doc = make_doc()
        measurer = LibassMeasurer(doc)
        first = measurer.bbox(doc.events[0])
        second = measurer.bbox(doc.events[0])
        assert first == second
        measurer.close()

    def test_measure_protocol_matches_bbox(self) -> None:
        doc = make_doc()
        measurer = LibassMeasurer(doc)
        event = doc.events[0]
        rect = measurer.bbox(event)
        size = measurer.measure(event, doc.style_for(event))
        assert rect is not None
        assert (size.w, size.h) == (rect.w, rect.h)
        measurer.close()

    def test_empty_text_gives_no_box(self) -> None:
        doc = make_doc("")
        measurer = LibassMeasurer(doc)
        assert measurer.bbox(doc.events[0]) is None
        measurer.close()


class TestGeometryIntegration:
    def test_event_bbox_prefers_exact_measurement(self) -> None:
        """geometry должен брать готовый прямоугольник, а не пересчитывать якорь."""
        from sfstudio.render import geometry as geo

        doc = make_doc()
        doc.events[0].set_text(r"{\pos(500,500)\an5}Привет")
        measurer = LibassMeasurer(doc)
        rect = geo.event_bbox(doc.events[0], doc, measurer)
        assert rect is not None
        assert rect.center_x == pytest.approx(500, abs=25)
        assert rect.center_y == pytest.approx(500, abs=25)
        measurer.close()

    def test_hit_test_uses_exact_box(self) -> None:
        from sfstudio.render import geometry as geo
        from sfstudio.render.geometry import ViewTransform

        doc = make_doc()
        doc.events[0].set_text(r"{\pos(960,540)\an5}Привет")
        measurer = LibassMeasurer(doc)
        vt = ViewTransform.fit(1920, 1080, PLAY_RES)

        assert geo.hit_test(960, 540, 1000, doc, measurer, vt) is not None
        assert geo.hit_test(100, 100, 1000, doc, measurer, vt) is None
        measurer.close()
