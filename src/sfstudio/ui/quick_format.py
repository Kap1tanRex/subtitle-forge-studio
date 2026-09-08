"""Панель быстрого оформления — всплывает по правому щелчку на реплике в кадре.

Устроена как панель символа в графических редакторах: шрифт, кегль, цвет,
начертание — всё в одном месте, рядом с тем, что правишь, без похода в диалог.

Два решения, определяющие поведение:

**Показывает действующее оформление, а не стиль.** Реплика ``{\\b1}Текст``
идёт жирной, даже если стиль говорит обратное, и переключатель обязан стоять
включённым. Считает это :func:`sfstudio.core.effective.effective_style`.

**Правит тегами, а не стилем.** Стиль общий для многих реплик; менять его,
когда пользователь правит одну строку в кадре, значит незаметно переоформить
половину файла. Поэтому панель ставит событийные теги — они действуют ровно на
эту реплику.

Смешанное состояние. Если свойство меняется внутри строки (``{\\b1}жирный
{\\b0}обычный``), переключатель показывается в третьем, неопределённом
положении: соврать одним значением нельзя. Щелчок по нему задаёт значение всей
реплике — это единственное осмысленное действие.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QColorDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QWidget,
)

from sfstudio.core.color import RGBA
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.effective import Effective, effective_style
from sfstudio.core.undo import UndoStack
from sfstudio.ui.font_box import FontComboBox

__all__ = ["QuickFormatPanel"]


class QuickFormatPanel(QFrame):
    """Всплывающая панель оформления одной реплики."""

    #: (имя тега, аргумент или None для снятия)
    tag_requested = Signal(str, object)
    style_requested = Signal()

    def __init__(
        self,
        doc: SubtitleDocument,
        undo: UndoStack,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.Popup)
        self._doc = doc
        self._undo = undo
        self._eid: int | None = None
        self._syncing = False
        self._color = RGBA(255, 255, 255, 255)

        self.setFrameShape(QFrame.StyledPanel)
        self.setProperty("role", "quick-format")

        grid = QGridLayout(self)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        grid.addWidget(self._caption("Шрифт"), 0, 0)
        self.font_box = FontComboBox()
        self.font_box.setEditable(True)
        self.font_box.setMinimumWidth(190)
        self.font_box.currentFontChanged.connect(self._on_font)
        grid.addWidget(self.font_box, 0, 1, 1, 3)

        grid.addWidget(self._caption("Кегль"), 1, 0)
        self.size_box = QDoubleSpinBox()
        self.size_box.setRange(1.0, 800.0)
        self.size_box.setDecimals(0)
        self.size_box.setSingleStep(2.0)
        self.size_box.valueChanged.connect(self._on_size)
        grid.addWidget(self.size_box, 1, 1)

        style_row = QHBoxLayout()
        style_row.setSpacing(4)
        self.btn_bold = self._toggle("Ж", "Жирный", bold=True)
        self.btn_italic = self._toggle("К", "Курсив", italic=True)
        self.btn_underline = self._toggle("Ч", "Подчёркнутый", underline=True)
        self.btn_strike = self._toggle("З", "Зачёркнутый", strike=True)
        for button in (self.btn_bold, self.btn_italic,
                       self.btn_underline, self.btn_strike):
            style_row.addWidget(button)
        style_row.addStretch(1)
        grid.addLayout(style_row, 1, 2, 1, 2)

        grid.addWidget(self._caption("Цвет"), 2, 0)
        self.color_button = QPushButton("Основной…")
        self.color_button.clicked.connect(self._pick_color)
        grid.addWidget(self.color_button, 2, 1, 1, 2)

        self.reset_button = QPushButton("Сбросить")
        self.reset_button.setToolTip("Убрать оформление, заданное тегами")
        self.reset_button.clicked.connect(self._reset)
        grid.addWidget(self.reset_button, 2, 3)

        self.hint = QLabel("")
        self.hint.setProperty("role", "hint")
        grid.addWidget(self.hint, 3, 0, 1, 4)

        self.btn_bold.clicked.connect(lambda: self._toggle_flag("b", self.btn_bold))
        self.btn_italic.clicked.connect(lambda: self._toggle_flag("i", self.btn_italic))
        self.btn_underline.clicked.connect(lambda: self._toggle_flag("u", self.btn_underline))
        self.btn_strike.clicked.connect(lambda: self._toggle_flag("s", self.btn_strike))

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "hint")
        return label

    def _toggle(self, glyph: str, tip: str, **font_hint: bool) -> QToolButton:
        button = QToolButton()
        button.setText(glyph)
        button.setToolTip(tip)
        # Базовую подпись храним отдельно: к ней дописывается пометка о
        # смешанном состоянии, и разбирать её обратно из текста — напрашиваться
        # на «Жирный — в реплике меняется — в реплике меняется».
        button.setProperty("base_tip", tip)
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        font = QFont()
        font.setBold(font_hint.get("bold", False))
        font.setItalic(font_hint.get("italic", False))
        font.setUnderline(font_hint.get("underline", False))
        font.setStrikeOut(font_hint.get("strike", False))
        button.setFont(font)
        return button

    # -- наполнение ------------------------------------------------------------- #

    def set_document(self, doc: SubtitleDocument, undo: UndoStack) -> None:
        self._doc = doc
        self._undo = undo

    def show_for(self, eid: int, global_pos) -> None:
        """Показывает панель для реплики рядом с указанной точкой."""
        if not self._doc.has(eid):
            return
        self._eid = eid
        self._load(eid)
        self.adjustSize()
        self.move(global_pos)
        self.show()

    def _load(self, eid: int) -> None:
        event = self._doc.by_eid(eid)
        eff = effective_style(event, self._doc.style_for(event))
        self._color = eff.primary

        self._syncing = True
        try:
            self.font_box.set_family(eff.fontname)
            self.size_box.setValue(eff.fontsize)
            self._apply_state(self.btn_bold, eff, "b", eff.bold)
            self._apply_state(self.btn_italic, eff, "i", eff.italic)
            self._apply_state(self.btn_underline, eff, "u", eff.underline)
            self._apply_state(self.btn_strike, eff, "s", eff.strikeout)
            self._paint_color_button(eff.primary)
        finally:
            self._syncing = False

        overridden = sorted(eff.overridden)
        self.hint.setText(
            "Переопределено тегами: " + ", ".join("\\" + n for n in overridden)
            if overridden
            else f"Всё из стиля «{event.style}»"
        )
        self.reset_button.setEnabled(bool(overridden))

    def _apply_state(
        self, button: QToolButton, eff: Effective, tag: str, value: bool
    ) -> None:
        mixed = eff.is_mixed(tag)
        button.setChecked(value)
        # Смешанное состояние помечаем свойством для стиля: рисовать
        # «включено» было бы враньём, а «выключено» — ещё большим.
        button.setProperty("mixed", mixed)
        base = str(button.property("base_tip") or "")
        button.setToolTip(f"{base} — в реплике меняется" if mixed else base)

    def _paint_color_button(self, color: RGBA) -> None:
        text_color = color.contrasting_text().to_hex()
        self.color_button.setStyleSheet(
            f"background-color: {color.to_hex()}; color: {text_color};"
        )
        self.color_button.setText(color.to_hex())

    # -- действия ---------------------------------------------------------------- #

    def _emit(self, name: str, args: object) -> None:
        if self._eid is not None and not self._syncing:
            self.tag_requested.emit(name, args)

    def _toggle_flag(self, tag: str, button: QToolButton) -> None:
        self._emit(tag, 1 if button.isChecked() else 0)

    def _on_font(self, font: QFont) -> None:
        self._emit("fn", font.family())

    def _on_size(self, value: float) -> None:
        self._emit("fs", value)

    def _pick_color(self) -> None:
        current = QColor(self._color.to_hex())
        chosen = QColorDialog.getColor(current, self, "Основной цвет")
        if not chosen.isValid():
            return
        picked = RGBA(chosen.red(), chosen.green(), chosen.blue(), 255)
        self._color = picked
        self._paint_color_button(picked)
        # В теге цвет пишется в порядке BBGGRR и без альфы — это делает to_ass.
        self._emit("c", picked.to_ass(with_alpha=False))

    def _reset(self) -> None:
        """Снимает всё оформление, заданное тегами: реплика возвращается к стилю."""
        for tag in ("b", "i", "u", "s", "fn", "fs", "c", "1c"):
            self._emit(tag, None)
        if self._eid is not None:
            self._load(self._eid)
