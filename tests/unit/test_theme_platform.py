"""Оформление и системный стиль: одинаковый вид на любой Windows.

Здесь проверяется то, на чём программа уже обожглась. На Windows 10 стиль по
умолчанию — «windowsvista», и он рисует вкладки, кнопки и списки сам, беря
фон из **системной** палитры. Цвет текста при этом задавала наша таблица
стилей. У человека со светлой системной темой это давало белый текст на белом
фоне; у того, у кого система тёмная, всё выглядело правильно, и потому
неисправность долго выглядела как «зависит от компьютера».

Измерено на настоящих стилях Windows: под тёмной темой и светлой системной
палитрой «windowsvista» давал 8,2 % светлых пикселей окна против 0,3 % после
починки.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.settings import Settings
from sfstudio.ui.appearance import apply as apply_appearance
from sfstudio.ui.appearance import qt_palette
from sfstudio.ui.theme import BUILTIN, CONTRAST, DARK, LIGHT, build_qss, is_dark


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path) -> Settings:
    made = Settings(tmp_path / "settings.json")
    made.set("storage.mode", "custom")
    made.set("storage.folder", str(tmp_path))
    return made


def luminance(color: QColor) -> float:
    """Относительная яркость по WCAG."""
    def channel(value: float) -> float:
        value /= 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    return (0.2126 * channel(color.red())
            + 0.7152 * channel(color.green())
            + 0.0722 * channel(color.blue()))


def contrast(first: str, second: str) -> float:
    a, b = luminance(QColor(first)), luminance(QColor(second))
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def sample_window() -> QWidget:
    """Набор виджетов, которые системный стиль рисовал по-своему."""
    window = QWidget()
    window.resize(480, 320)
    box = QVBoxLayout(window)
    tabs = QTabWidget()
    for name in ("Первая", "Вторая"):
        page = QWidget()
        inner = QVBoxLayout(page)
        inner.addWidget(QPushButton("Кнопка"))
        inner.addWidget(QCheckBox("Флажок"))
        inner.addWidget(QComboBox())
        inner.addWidget(QLineEdit("поле"))
        listing = QListWidget()
        listing.addItems(["раз", "два"])
        inner.addWidget(listing)
        bar = QProgressBar()
        bar.setValue(60)
        inner.addWidget(bar)
        tabs.addTab(page, name)
    box.addWidget(tabs)
    return window


def light_share(widget: QWidget) -> float:
    """Доля светлых пикселей, в процентах."""
    image = QImage(widget.size(), QImage.Format_RGB32)
    widget.render(image)
    light = total = 0
    for y in range(0, image.height(), 3):
        for x in range(0, image.width(), 3):
            color = QColor(image.pixel(x, y))
            total += 1
            if (0.299 * color.red() + 0.587 * color.green()
                    + 0.114 * color.blue()) > 170:
                light += 1
    return 100.0 * light / max(1, total)


class TestStyleAndPalette:
    """Внешний вид не должен зависеть от системной темы и версии Windows."""

    def test_style_is_fixed(self, qapp, settings) -> None:
        """Системный стиль рисует часть виджетов мимо нашей таблицы стилей.

        Имя стиля после установки таблицы стилей пустое: Qt подменяет его
        обёрткой. Поэтому смотрим на класс — важно, что это не «windowsvista»
        и не «windows11», каждый из которых рисует по-своему.
        """
        apply_appearance(qapp, settings)
        assert qapp.property("sfstudio_style_fixed") is True
        name = qapp.style().metaObject().className()
        assert name in {"QFusionStyle", "QStyleSheetStyle"}, name

    def test_palette_follows_the_theme(self, qapp, settings) -> None:
        settings.set("ui.theme", "dark")
        apply_appearance(qapp, settings)
        palette = qapp.palette()
        assert palette.color(QPalette.ColorRole.Window).name().lower() == DARK.bg_base.lower()
        assert palette.color(QPalette.ColorRole.WindowText).name().lower() == DARK.text_primary.lower()
        assert palette.color(QPalette.ColorRole.Button).name().lower() == DARK.bg_elevated.lower()

    def test_palette_changes_with_the_theme(self, qapp, settings) -> None:
        settings.set("ui.theme", "dark")
        apply_appearance(qapp, settings)
        dark_window = qapp.palette().color(QPalette.ColorRole.Window).name()

        settings.set("ui.theme", "light")
        apply_appearance(qapp, settings)
        light_window = qapp.palette().color(QPalette.ColorRole.Window).name()
        assert dark_window != light_window
        assert light_window.lower() == LIGHT.bg_base.lower()

    def test_disabled_text_is_not_invisible(self) -> None:
        """Иначе Qt берёт «недоступный» цвет от системного — серое на сером."""
        palette = qt_palette(DARK)
        muted = palette.color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText
        )
        assert contrast(muted.name(), DARK.bg_base) >= 2.5


class TestStyleSheetCoverage:
    """Виджет без своего правила достаётся системному стилю."""

    #: Классы, из-за которых всё и случилось.
    REQUIRED = (
        "QTabBar::tab",
        "QTabWidget::pane",
        "QPushButton",
        "QCheckBox::indicator",
        "QRadioButton::indicator",
        "QListView",
        "QTreeView",
        "QToolTip",
        "QProgressBar",
        "QSlider::handle:horizontal",
        "QDockWidget::title",
        "QComboBox QAbstractItemView",
        "QDialog",
    )

    def test_every_troublesome_widget_is_styled(self) -> None:
        qss = build_qss(DARK)
        missing = [name for name in self.REQUIRED if name not in qss]
        assert not missing, f"без правил остались: {missing}"

    def test_popup_list_has_its_own_background(self) -> None:
        """Выпадающий список — отдельное окно, общий фон на него не идёт."""
        qss = build_qss(DARK)
        block = qss.split("QComboBox QAbstractItemView")[1].split("}")[0]
        assert DARK.bg_elevated in block


class TestThemeContrast:
    """Цвета темы должны читаться, а не просто отличаться."""

    @pytest.mark.parametrize("name", sorted(BUILTIN))
    def test_main_text_is_readable(self, name) -> None:
        palette = BUILTIN[name]
        assert contrast(palette.text_primary, palette.bg_base) >= 4.5

    @pytest.mark.parametrize("name", sorted(BUILTIN))
    def test_muted_text_is_readable(self, name) -> None:
        palette = BUILTIN[name]
        assert contrast(palette.text_muted, palette.bg_base) >= 3.0

    @pytest.mark.parametrize("name", sorted(BUILTIN))
    def test_selection_keeps_the_text_legible(self, name) -> None:
        """Выделение в списках красит фон — текст на нём остаётся тем же."""
        palette = BUILTIN[name]
        assert contrast(palette.text_primary, palette.accent_muted) >= 3.0

    def test_contrast_theme_lives_up_to_its_name(self) -> None:
        assert contrast(CONTRAST.text_primary, CONTRAST.bg_base) >= 15.0


class TestRendering:
    """Тёмная тема не должна давать светлое окно, и наоборот."""

    def test_dark_theme_paints_dark(self, qapp, settings) -> None:
        settings.set("ui.theme", "dark")
        apply_appearance(qapp, settings)
        window = sample_window()
        try:
            assert light_share(window) < 3.0
        finally:
            window.deleteLater()

    def test_light_theme_paints_light(self, qapp, settings) -> None:
        settings.set("ui.theme", "light")
        apply_appearance(qapp, settings)
        window = sample_window()
        try:
            assert light_share(window) > 50.0
        finally:
            window.deleteLater()

    def test_theme_darkness_matches_its_palette(self) -> None:
        assert is_dark(DARK) and is_dark(CONTRAST)
        assert not is_dark(LIGHT)
