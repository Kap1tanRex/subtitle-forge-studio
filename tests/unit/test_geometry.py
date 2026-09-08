"""Тесты геометрии оверлея.

Проверяют математику перетаскивания без окна и без libass. Именно эти
инварианты определяют, совпадёт ли позиция субтитра с тем, что увидит зритель.
"""

from __future__ import annotations

import pytest

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.style import SubtitleStyle
from sfstudio.render import geometry as geo
from sfstudio.render.geometry import Handle, Rect, Size, ViewTransform


class FixedMeasurer:
    """Измеритель-заглушка: любой текст имеет заданный размер."""

    def __init__(self, w: float = 400.0, h: float = 60.0) -> None:
        self.size = Size(w, h)

    def measure(self, event: SubtitleEvent, style: SubtitleStyle) -> Size:
        return self.size


@pytest.fixture
def doc() -> SubtitleDocument:
    d = SubtitleDocument.blank((1920, 1080))
    d.create_event(1000, 3000, "Реплика")
    return d


class TestViewTransform:
    def test_pillarbox_when_widget_is_wide(self) -> None:
        """Широкое окно → полосы слева и справа, высота использована полностью."""
        vt = ViewTransform.fit(1000, 400, (1920, 1080))
        assert vt.video_rect.h == 400
        assert vt.video_rect.w == pytest.approx(400 * 16 / 9)
        assert vt.video_rect.x > 0

    def test_letterbox_when_widget_is_tall(self) -> None:
        vt = ViewTransform.fit(800, 900, (1920, 1080))
        assert vt.video_rect.w == 800
        assert vt.video_rect.y > 0

    def test_roundtrip_widget_script(self) -> None:
        vt = ViewTransform.fit(1280, 720, (1920, 1080))
        for sx, sy in [(0, 0), (960, 540), (1920, 1080), (123.5, 456.5)]:
            wx, wy = vt.script_to_widget(sx, sy)
            back = vt.widget_to_script(wx, wy)
            assert back[0] == pytest.approx(sx)
            assert back[1] == pytest.approx(sy)

    def test_scale_is_uniform_for_matching_aspect(self) -> None:
        vt = ViewTransform.fit(1920, 1080, (1920, 1080))
        assert vt.scale_x == pytest.approx(vt.scale_y)
        assert vt.scale_x == pytest.approx(1.0)

    def test_rotation_swaps_aspect(self) -> None:
        """Вертикальное видео: без учёта rotation кадр разъезжается с субтитрами."""
        upright = ViewTransform.fit(1000, 1000, (1920, 1080), rotation=0)
        rotated = ViewTransform.fit(1000, 1000, (1920, 1080), rotation=90)
        assert upright.video_rect.w > upright.video_rect.h
        assert rotated.video_rect.h > rotated.video_rect.w

    def test_anamorphic_dar_respected(self) -> None:
        """DVD 720x576 с DAR 16:9 должен рисоваться широким, а не квадратным."""
        vt = ViewTransform.fit(1600, 1200, (720, 576), dar=16 / 9)
        assert vt.video_rect.w / vt.video_rect.h == pytest.approx(16 / 9)

    def test_degenerate_widget_size(self) -> None:
        vt = ViewTransform.fit(0, 0, (1920, 1080))
        assert vt.video_rect.w == 0
        assert vt.widget_to_script(10, 10) == (0.0, 0.0)


class TestAnchors:
    def test_all_nine_alignments(self) -> None:
        rect = Rect(100, 200, 400, 60)
        expected = {
            1: (100, 260), 2: (300, 260), 3: (500, 260),
            4: (100, 230), 5: (300, 230), 6: (500, 230),
            7: (100, 200), 8: (300, 200), 9: (500, 200),
        }
        for alignment, point in expected.items():
            assert geo.anchor_point_of(rect, alignment) == point

    @pytest.mark.parametrize("alignment", range(1, 10))
    def test_rect_from_anchor_is_inverse(self, alignment: int) -> None:
        rect = Rect(100, 200, 400, 60)
        ax, ay = geo.anchor_point_of(rect, alignment)
        rebuilt = geo.rect_from_anchor(ax, ay, Size(rect.w, rect.h), alignment)
        assert (rebuilt.x, rebuilt.y) == pytest.approx((rect.x, rect.y))


class TestEffectiveAnchor:
    def test_uses_pos_tag_when_present(self, doc: SubtitleDocument) -> None:
        event = doc.events[0]
        event.set_text(r"{\pos(500,600)}Реплика")
        style = doc.style_for(event)
        assert geo.effective_anchor(event, style, Size(1, 1), (1920, 1080)) == (500.0, 600.0)

    def test_falls_back_to_style_margins(self, doc: SubtitleDocument) -> None:
        """Без \\pos позиция задаётся выравниванием и полями."""
        event = doc.events[0]
        style = doc.style_for(event)  # an=2, поля 20
        ax, ay = geo.effective_anchor(event, style, Size(400, 60), (1920, 1080))
        assert ax == pytest.approx(960)  # центр между полями
        assert ay == pytest.approx(1060)  # снизу минус margin_v

    def test_event_margins_override_style(self, doc: SubtitleDocument) -> None:
        event = doc.events[0]
        event.margin_v = 200
        style = doc.style_for(event)
        _, ay = geo.effective_anchor(event, style, Size(400, 60), (1920, 1080))
        assert ay == pytest.approx(880)

    def test_asymmetric_margins_shift_centre(self, doc: SubtitleDocument) -> None:
        """При разных левом и правом полях центр смещается — как в libass."""
        event = doc.events[0]
        style = doc.style_for(event)
        style.margin_l = 400
        style.margin_r = 0
        ax, _ = geo.effective_anchor(event, style, Size(400, 60), (1920, 1080))
        assert ax == pytest.approx(400 + (1920 - 400) / 2)

    def test_move_tag_uses_start_point(self, doc: SubtitleDocument) -> None:
        event = doc.events[0]
        event.set_text(r"{\move(100,200,800,900)}Реплика")
        style = doc.style_for(event)
        assert geo.effective_anchor(event, style, Size(1, 1), (1920, 1080)) == (100.0, 200.0)


class TestBBox:
    def test_centred_bottom_by_default(self, doc: SubtitleDocument) -> None:
        rect = geo.event_bbox(doc.events[0], doc, FixedMeasurer(400, 60))
        assert rect is not None
        assert rect.center_x == pytest.approx(960)
        assert rect.bottom == pytest.approx(1060)

    def test_pos_with_an5_centres_on_point(self, doc: SubtitleDocument) -> None:
        event = doc.events[0]
        event.set_text(r"{\pos(500,500)\an5}Реплика")
        rect = geo.event_bbox(event, doc, FixedMeasurer(400, 60))
        assert rect is not None
        assert (rect.center_x, rect.center_y) == pytest.approx((500, 500))

    def test_zero_size_gives_none(self, doc: SubtitleDocument) -> None:
        assert geo.event_bbox(doc.events[0], doc, FixedMeasurer(0, 0)) is None


class TestHitTest:
    def test_hits_body(self, doc: SubtitleDocument) -> None:
        vt = ViewTransform.fit(1920, 1080, (1920, 1080))
        result = geo.hit_test(960, 1030, 2000, doc, FixedMeasurer(), vt)
        assert result is not None
        assert result.handle is Handle.BODY
        assert result.eid == doc.events[0].eid

    def test_misses_outside(self, doc: SubtitleDocument) -> None:
        vt = ViewTransform.fit(1920, 1080, (1920, 1080))
        assert geo.hit_test(100, 100, 2000, doc, FixedMeasurer(), vt) is None

    def test_ignores_inactive_events(self, doc: SubtitleDocument) -> None:
        vt = ViewTransform.fit(1920, 1080, (1920, 1080))
        assert geo.hit_test(960, 1030, 9999, doc, FixedMeasurer(), vt) is None

    def test_grab_offset_prevents_jump(self, doc: SubtitleDocument) -> None:
        """Смещение захвата должно возвращать разницу между курсором и якорем."""
        vt = ViewTransform.fit(1920, 1080, (1920, 1080))
        result = geo.hit_test(1000, 1040, 2000, doc, FixedMeasurer(), vt)
        assert result is not None
        assert result.grab_dx == pytest.approx(40)  # 1000 - 960
        assert result.grab_dy == pytest.approx(-20)  # 1040 - 1060

    def test_topmost_layer_wins(self, doc: SubtitleDocument) -> None:
        """Перекрывающиеся события: ловится визуально верхнее."""
        upper = doc.create_event(1000, 3000, "Верхнее")
        upper.layer = 5
        vt = ViewTransform.fit(1920, 1080, (1920, 1080))
        result = geo.hit_test(960, 1030, 2000, doc, FixedMeasurer(), vt)
        assert result is not None
        assert result.eid == upper.eid

    def test_handles_take_priority_for_selection(self, doc: SubtitleDocument) -> None:
        """Угловая ручка должна ловиться, даже если она вне тела события."""
        event = doc.events[0]
        vt = ViewTransform.fit(1920, 1080, (1920, 1080))
        rect = geo.event_bbox(event, doc, FixedMeasurer())
        assert rect is not None
        widget_rect = vt.rect_to_widget(rect)
        result = geo.hit_test(
            widget_rect.left, widget_rect.top, 2000, doc, FixedMeasurer(), vt, selected=event.eid
        )
        assert result is not None
        assert result.handle is Handle.NW

    def test_rotate_handle_above_box(self, doc: SubtitleDocument) -> None:
        event = doc.events[0]
        vt = ViewTransform.fit(1920, 1080, (1920, 1080))
        rect = vt.rect_to_widget(geo.event_bbox(event, doc, FixedMeasurer()))
        result = geo.hit_test(
            rect.center_x, rect.top - geo.ROTATE_OFFSET_PX, 2000,
            doc, FixedMeasurer(), vt, selected=event.eid,
        )
        assert result is not None
        assert result.handle is Handle.ROTATE


class TestSnapping:
    def test_snaps_to_horizontal_centre(self, doc: SubtitleDocument) -> None:
        vt = ViewTransform.fit(1920, 1080, (1920, 1080))
        guides = geo.build_guides(doc, doc.styles["Default"])
        sx, sy, hit = geo.snap_to_guides(963, 500, guides, vt)
        assert sx == pytest.approx(960)
        assert any(g.kind == "center" for g in hit)

    def test_does_not_snap_when_far(self, doc: SubtitleDocument) -> None:
        vt = ViewTransform.fit(1920, 1080, (1920, 1080))
        guides = geo.build_guides(doc, doc.styles["Default"])
        sx, _, hit = geo.snap_to_guides(700, 500, guides, vt)
        assert sx == 700
        assert hit == []

    def test_radius_scales_with_zoom(self, doc: SubtitleDocument) -> None:
        """На маленьком окне магнит не должен хватать пол-кадра."""
        guides = geo.build_guides(doc, doc.styles["Default"])
        big = ViewTransform.fit(1920, 1080, (1920, 1080))
        small = ViewTransform.fit(480, 270, (1920, 1080))

        # 20 script-px от центра: при масштабе 1:1 это 20 px виджета — далеко.
        assert geo.snap_to_guides(940, 500, guides, big)[0] == 940
        # При масштабе 1:4 те же 20 script-px это 5 px виджета — уже близко.
        assert geo.snap_to_guides(940, 500, guides, small)[0] == pytest.approx(960)

    def test_clamp_allows_slight_overflow(self) -> None:
        assert geo.clamp_to_canvas(-50, 500, (1920, 1080)) == (-50, 500)
        assert geo.clamp_to_canvas(-9999, 500, (1920, 1080))[0] == -geo.OVERFLOW
        assert geo.clamp_to_canvas(99999, 500, (1920, 1080))[0] == 1920 + geo.OVERFLOW


def test_drag_preserves_grab_point(doc: SubtitleDocument) -> None:
    """Сквозной инвариант жеста: субтитр не прыгает к курсору при захвате.

    Схватили за точку, сдвинули мышь на (dx, dy) — субтитр обязан сместиться
    ровно на столько же, а точка захвата остаться под курсором.
    """
    vt = ViewTransform.fit(1280, 720, (1920, 1080))
    measurer = FixedMeasurer()
    event = doc.events[0]

    press_x, press_y = 660, 700
    hit = geo.hit_test(press_x, press_y, 2000, doc, measurer, vt)
    assert hit is not None

    dx_widget, dy_widget = 45.0, -80.0
    dsx, dsy = vt.delta_to_script(dx_widget, dy_widget)

    start_anchor = geo.effective_anchor(
        event, doc.style_for(event), Size(400, 60), doc.script_info.play_res
    )
    new_anchor = (start_anchor[0] + dsx, start_anchor[1] + dsy)
    event.set_text(r"{\pos(%d,%d)}Реплика" % (round(new_anchor[0]), round(new_anchor[1])))

    moved = geo.effective_anchor(event, doc.style_for(event), Size(400, 60), (1920, 1080))
    assert moved[0] == pytest.approx(start_anchor[0] + dsx, abs=1.0)
    assert moved[1] == pytest.approx(start_anchor[1] + dsy, abs=1.0)


class TestThinBoxRegression:
    """Регрессия: у тонкой рамки ручки не должны съедать всё тело.

    Строка субтитра в уменьшенном превью — это рамка высотой 15-25 px.
    При фиксированной зоне захвата в 10 px зоны верхней и нижней ручек
    смыкались посередине, и субтитр становилось невозможно перетащить:
    любой клик по нему попадал в ручку изменения размера.
    """

    def _thin_setup(self) -> tuple[SubtitleDocument, ViewTransform, FixedMeasurer]:
        doc = SubtitleDocument.blank((1920, 1080))
        doc.create_event(0, 5000, "Тонкая строка")
        # 643x804 виджета при 1920x1080 → масштаб ~0.33, рамка высотой ~19 px.
        vt = ViewTransform.fit(643, 804, (1920, 1080))
        return doc, vt, FixedMeasurer(1462, 58)

    def test_centre_of_thin_box_is_body(self) -> None:
        doc, vt, measurer = self._thin_setup()
        event = doc.events[0]
        rect = vt.rect_to_widget(geo.event_bbox(event, doc, measurer))
        assert rect.h < 2 * geo.HANDLE_HIT_PX, "рамка должна быть тоньше двух зон захвата"

        result = geo.hit_test(
            rect.center_x, rect.center_y, 2000, doc, measurer, vt, selected=event.eid
        )
        assert result is not None
        assert result.handle is Handle.BODY, "центр тонкой рамки обязан оставаться перетаскиваемым"

    def test_corners_of_thin_box_still_work(self) -> None:
        doc, vt, measurer = self._thin_setup()
        event = doc.events[0]
        rect = vt.rect_to_widget(geo.event_bbox(event, doc, measurer))
        result = geo.hit_test(
            rect.left, rect.top, 2000, doc, measurer, vt, selected=event.eid
        )
        assert result is not None
        assert result.handle is Handle.NW

    def test_tiny_box_does_not_divide_by_zero(self) -> None:
        doc = SubtitleDocument.blank((1920, 1080))
        doc.create_event(0, 5000, ".")
        vt = ViewTransform.fit(640, 360, (1920, 1080))
        measurer = FixedMeasurer(1, 1)
        event = doc.events[0]
        result = geo.hit_test(
            *geo.anchor_point_of(vt.rect_to_widget(geo.event_bbox(event, doc, measurer)), 5),
            2000, doc, measurer, vt, selected=event.eid,
        )
        assert result is None or result.handle in tuple(Handle)
