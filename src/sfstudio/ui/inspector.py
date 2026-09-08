"""Инспектор: тонкие настройки выделенной реплики, разложенные по вкладкам.

Разделение по вкладкам не косметическое. В ASS у реплики несколько десятков
свойств, и в одном списке они не читаются. Группировка идёт по вопросу, на
который отвечает вкладка:

* **Реплика** — что это за строка: тайминги, стиль, дорожка, говорящий;
* **Текст** — как она выглядит: шрифт, кегль, начертание, цвета;
* **Кадр** — где она стоит: выравнивание, координаты, поля, поворот;
* **Проверки** — что с ней не так: длительность, скорость чтения, замечания QC.

Инспектор **не хранит состояние реплики**. Он читает документ при каждом
обновлении и пишет через команды. Собственная копия значений неизбежно
разъезжается с документом при отмене, при правке из таблицы и при правке
мышью в кадре — а инспектор виден одновременно со всеми тремя.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sfstudio.core.color import RGBA
from sfstudio.core.commands import (
    AssignActor,
    MoveEventsToLayer,
    SetOverrideTag,
    SetStyle,
    SetTiming,
)
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.effective import effective_style
from sfstudio.core.style import ALIGNMENT_NAMES
from sfstudio.core.time import format_srt, parse_timecode
from sfstudio.core.undo import UndoStack
from sfstudio.ui.combo import index_of_data
from sfstudio.ui.font_box import FontComboBox

__all__ = ["Inspector"]


class Inspector(QTabWidget):
    """Свойства выделенной реплики."""

    document_edited = Signal()
    actors_requested = Signal()

    def __init__(
        self,
        doc: SubtitleDocument,
        undo: UndoStack,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._doc = doc
        self._undo = undo
        self._eid: int | None = None
        self._syncing = False
        self._qc_notes: list[str] = []

        self.addTab(self._build_event_tab(), "Реплика")
        self.addTab(self._build_text_tab(), "Текст")
        self.addTab(self._build_frame_tab(), "Кадр")
        self.addTab(self._build_checks_tab(), "Проверки")
        self.setDocumentMode(True)
        self.set_event(None)

    # -- вкладки ------------------------------------------------------------------ #

    def _build_event_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.start_edit = QLineEdit()
        self.start_edit.editingFinished.connect(lambda: self._commit_time("start"))
        form.addRow("Начало", self.start_edit)

        self.end_edit = QLineEdit()
        self.end_edit.editingFinished.connect(lambda: self._commit_time("end"))
        form.addRow("Конец", self.end_edit)

        self.duration_label = QLabel("—")
        form.addRow("Длительность", self.duration_label)

        self.style_box = QComboBox()
        self.style_box.currentTextChanged.connect(self._commit_style)
        form.addRow("Стиль", self.style_box)

        self.track_box = QComboBox()
        self.track_box.currentIndexChanged.connect(self._commit_track)
        form.addRow("Дорожка", self.track_box)

        actor_row = QHBoxLayout()
        self.actor_box = QComboBox()
        self.actor_box.setEditable(True)
        self.actor_box.currentTextChanged.connect(self._commit_actor)
        actor_row.addWidget(self.actor_box, 1)
        manage = QPushButton("…")
        manage.setFixedWidth(28)
        manage.setToolTip("Управление акторами")
        manage.clicked.connect(self.actors_requested)
        actor_row.addWidget(manage)
        form.addRow("Актор", actor_row)

        self.actor_swatch = QLabel("")
        self.actor_swatch.setFixedHeight(4)
        form.addRow("", self.actor_swatch)

        return page

    def _build_text_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.font_box = FontComboBox()
        self.font_box.currentFontChanged.connect(
            lambda f: self._set_tag("fn", f.family())
        )
        form.addRow("Шрифт", self.font_box)

        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(1.0, 800.0)
        self.size_spin.setDecimals(0)
        self.size_spin.valueChanged.connect(lambda v: self._set_tag("fs", v))
        form.addRow("Кегль", self.size_spin)

        flags = QHBoxLayout()
        self.bold_check = QCheckBox("Ж")
        self.italic_check = QCheckBox("К")
        self.underline_check = QCheckBox("Ч")
        self.strike_check = QCheckBox("З")
        for box, tag in (
            (self.bold_check, "b"), (self.italic_check, "i"),
            (self.underline_check, "u"), (self.strike_check, "s"),
        ):
            box.clicked.connect(
                lambda on, name=tag: self._set_tag(name, 1 if on else 0)
            )
            flags.addWidget(box)
        flags.addStretch(1)
        form.addRow("Начертание", flags)

        self.primary_button = QPushButton("Основной")
        self.primary_button.clicked.connect(lambda: self._pick_color("c"))
        form.addRow("Цвет текста", self.primary_button)

        self.outline_button = QPushButton("Обводка")
        self.outline_button.clicked.connect(lambda: self._pick_color("3c"))
        form.addRow("Цвет обводки", self.outline_button)

        self.spacing_spin = QDoubleSpinBox()
        self.spacing_spin.setRange(-50.0, 50.0)
        self.spacing_spin.setDecimals(1)
        self.spacing_spin.valueChanged.connect(lambda v: self._set_tag("fsp", v))
        form.addRow("Разрядка", self.spacing_spin)

        scale_row = QHBoxLayout()
        self.scale_x_spin = QDoubleSpinBox()
        self.scale_x_spin.setRange(1.0, 1000.0)
        self.scale_x_spin.setDecimals(0)
        self.scale_x_spin.setSuffix(" %")
        self.scale_x_spin.valueChanged.connect(lambda v: self._set_tag("fscx", v))
        self.scale_y_spin = QDoubleSpinBox()
        self.scale_y_spin.setRange(1.0, 1000.0)
        self.scale_y_spin.setDecimals(0)
        self.scale_y_spin.setSuffix(" %")
        self.scale_y_spin.valueChanged.connect(lambda v: self._set_tag("fscy", v))
        scale_row.addWidget(self.scale_x_spin)
        scale_row.addWidget(self.scale_y_spin)
        form.addRow("Масштаб", scale_row)

        self.text_hint = QLabel("")
        self.text_hint.setProperty("role", "hint")
        self.text_hint.setWordWrap(True)
        form.addRow("", self.text_hint)
        return page

    def _build_frame_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.align_box = QComboBox()
        for value, title in sorted(ALIGNMENT_NAMES.items()):
            self.align_box.addItem(f"{value} — {title}", value)
        self.align_box.currentIndexChanged.connect(self._commit_alignment)
        form.addRow("Выравнивание", self.align_box)

        pos_row = QHBoxLayout()
        self.pos_x_spin = QSpinBox()
        self.pos_x_spin.setRange(-10000, 10000)
        self.pos_y_spin = QSpinBox()
        self.pos_y_spin.setRange(-10000, 10000)
        for spin in (self.pos_x_spin, self.pos_y_spin):
            spin.valueChanged.connect(self._commit_position)
        pos_row.addWidget(self.pos_x_spin)
        pos_row.addWidget(self.pos_y_spin)
        form.addRow("Положение", pos_row)

        self.clear_pos_button = QPushButton("Убрать \\pos")
        self.clear_pos_button.setToolTip("Вернуть реплику на место по умолчанию")
        self.clear_pos_button.clicked.connect(lambda: self._set_tag("pos", None))
        form.addRow("", self.clear_pos_button)

        self.angle_spin = QDoubleSpinBox()
        self.angle_spin.setRange(-360.0, 360.0)
        self.angle_spin.setDecimals(1)
        self.angle_spin.setSuffix("°")
        self.angle_spin.valueChanged.connect(lambda v: self._set_tag("frz", v))
        form.addRow("Поворот", self.angle_spin)

        margins = QHBoxLayout()
        self.margin_l_spin = QSpinBox()
        self.margin_r_spin = QSpinBox()
        self.margin_v_spin = QSpinBox()
        for spin in (self.margin_l_spin, self.margin_r_spin, self.margin_v_spin):
            spin.setRange(0, 10000)
            spin.valueChanged.connect(self._commit_margins)
            margins.addWidget(spin)
        form.addRow("Поля Л/П/В", margins)
        return page

    def _build_checks_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.cps_label = QLabel("—")
        form.addRow("Знаков в секунду", self.cps_label)
        self.chars_label = QLabel("—")
        form.addRow("Символов", self.chars_label)
        self.lines_label = QLabel("—")
        form.addRow("Строк", self.lines_label)
        self.longest_label = QLabel("—")
        form.addRow("Длиннейшая строка", self.longest_label)
        layout.addLayout(form)

        self.qc_label = QLabel("")
        self.qc_label.setWordWrap(True)
        self.qc_label.setAlignment(Qt.AlignTop)
        layout.addWidget(self.qc_label, 1)
        return page

    # -- наполнение ------------------------------------------------------------- #

    def set_document(self, doc: SubtitleDocument, undo: UndoStack) -> None:
        self._doc = doc
        self._undo = undo
        self.set_event(None)

    def set_qc_notes(self, notes: list[str]) -> None:
        self._qc_notes = notes
        self._refresh_checks()

    def set_event(self, eid: int | None) -> None:
        self._eid = eid if eid is not None and self._doc.has(eid) else None
        self.setEnabled(self._eid is not None)
        if self._eid is None:
            self._clear()
            return
        self.refresh()

    def _clear(self) -> None:
        self._syncing = True
        try:
            self.start_edit.clear()
            self.end_edit.clear()
            self.duration_label.setText("—")
            self.qc_label.setText("")
            for label in (self.cps_label, self.chars_label,
                          self.lines_label, self.longest_label):
                label.setText("—")
        finally:
            self._syncing = False

    def refresh(self) -> None:
        """Перечитывает всё из документа. Зовётся после любой правки."""
        if self._eid is None or not self._doc.has(self._eid):
            self.set_event(None)
            return

        event = self._doc.by_eid(self._eid)
        eff = effective_style(event, self._doc.style_for(event))

        self._syncing = True
        try:
            self.start_edit.setText(format_srt(event.start))
            self.end_edit.setText(format_srt(event.end))
            self.duration_label.setText(f"{event.duration / 1000:.3f} с")

            self._fill_combo(self.style_box, list(self._doc.styles), event.style)
            self._fill_tracks(event.layer)
            self._fill_actors(event.name)

            self.font_box.set_family(eff.fontname)
            self.size_spin.setValue(eff.fontsize)
            self.bold_check.setChecked(eff.bold)
            self.italic_check.setChecked(eff.italic)
            self.underline_check.setChecked(eff.underline)
            self.strike_check.setChecked(eff.strikeout)
            self.spacing_spin.setValue(eff.spacing)
            self.scale_x_spin.setValue(eff.scale_x)
            self.scale_y_spin.setValue(eff.scale_y)
            self._paint_button(self.primary_button, eff.primary, "Основной")
            self._paint_button(self.outline_button, eff.outline_color, "Обводка")

            index = index_of_data(self.align_box, eff.alignment)
            if index >= 0:
                self.align_box.setCurrentIndex(index)

            position = event.position()
            has_pos = position is not None
            self.pos_x_spin.setEnabled(True)
            self.pos_y_spin.setEnabled(True)
            if has_pos:
                self.pos_x_spin.setValue(int(position[0]))
                self.pos_y_spin.setValue(int(position[1]))
            self.clear_pos_button.setEnabled(has_pos)
            self.angle_spin.setValue(eff.angle)

            self.margin_l_spin.setValue(event.margin_l)
            self.margin_r_spin.setValue(event.margin_r)
            self.margin_v_spin.setValue(event.margin_v)

            mixed = sorted(eff.mixed)
            self.text_hint.setText(
                "Внутри реплики меняется: " + ", ".join("\\" + n for n in mixed)
                + ". Значения показаны для её начала."
                if mixed
                else ""
            )
        finally:
            self._syncing = False

        self._refresh_checks()

    def _fill_combo(self, box: QComboBox, items: list[str], current: str) -> None:
        if [box.itemText(i) for i in range(box.count())] != items:
            box.clear()
            box.addItems(items)
        index = box.findText(current)
        box.setCurrentIndex(index if index >= 0 else 0)

    def _fill_tracks(self, layer: int) -> None:
        tracks = self._doc.tracks.display_order(with_media=False)
        wanted = [(t.display_name(), t.layer) for t in tracks]
        current = [
            (self.track_box.itemText(i), self.track_box.itemData(i))
            for i in range(self.track_box.count())
        ]
        if current != wanted:
            self.track_box.clear()
            for name, value in wanted:
                self.track_box.addItem(name, value)
        index = index_of_data(self.track_box, layer)
        if index >= 0:
            self.track_box.setCurrentIndex(index)

    def _fill_actors(self, name: str) -> None:
        names = self._doc.actors.names()
        # Имя из реплики может быть не заведено в реестре — файл пришёл со
        # стороны. Показать его всё равно надо, иначе поле выглядит пустым
        # при непустом значении.
        if name and name not in names:
            names = [*names, name]
        items = ["", *names]
        current = [self.actor_box.itemText(i) for i in range(self.actor_box.count())]
        if current != items:
            self.actor_box.clear()
            self.actor_box.addItems(items)
        self.actor_box.setCurrentText(name)

        color = self._doc.actors.color_of(name) if name else None
        self.actor_swatch.setStyleSheet(
            f"background-color: {color.to_hex()};" if color else ""
        )

    def _paint_button(self, button: QPushButton, color: RGBA, caption: str) -> None:
        button.setStyleSheet(
            f"background-color: {color.to_hex()}; color: {color.contrasting_text().to_hex()};"
        )
        button.setText(f"{caption} {color.to_hex()}")

    def _refresh_checks(self) -> None:
        if self._eid is None or not self._doc.has(self._eid):
            return
        event = self._doc.by_eid(self._eid)
        self.cps_label.setText(f"{event.cps():.1f}")
        self.chars_label.setText(str(len(event.plain)))
        self.lines_label.setText(str(event.line_count))
        self.longest_label.setText(str(event.longest_line))
        self.qc_label.setText(
            "\n".join(f"• {note}" for note in self._qc_notes)
            if self._qc_notes
            else "Замечаний нет."
        )

    # -- запись ------------------------------------------------------------------- #

    def _run(self, command) -> None:
        self._undo.run(command)
        self.document_edited.emit()
        self.refresh()

    def _set_tag(self, name: str, args: object) -> None:
        if self._syncing or self._eid is None:
            return
        self._run(SetOverrideTag(self._eid, name, args))

    def _commit_time(self, field: str) -> None:
        if self._syncing or self._eid is None:
            return
        edit = self.start_edit if field == "start" else self.end_edit
        # parse_timecode возвращает None, а не бросает исключение: решение,
        # считать ли это ошибкой ввода, оставлено вызывающему. Здесь — считаем
        # и возвращаем в поле прежнее значение, чтобы мусор не оставался на
        # экране, выглядя как принятый ввод.
        value = parse_timecode(edit.text())
        if value is None:
            self.refresh()
            return
        event = self._doc.by_eid(self._eid)
        if field == "start":
            self._run(SetTiming(self._eid, start=min(value, event.end - 1)))
        else:
            self._run(SetTiming(self._eid, end=max(value, event.start + 1)))

    def _commit_style(self, name: str) -> None:
        if self._syncing or self._eid is None or not name:
            return
        self._run(SetStyle(self._eid, name))

    def _commit_track(self, index: int) -> None:
        if self._syncing or self._eid is None or index < 0:
            return
        layer = self.track_box.itemData(index)
        if layer is not None:
            self._run(MoveEventsToLayer([self._eid], int(layer)))

    def _commit_actor(self, name: str) -> None:
        if self._syncing or self._eid is None:
            return
        self._run(AssignActor([self._eid], name))

    def _commit_alignment(self, index: int) -> None:
        if self._syncing or self._eid is None or index < 0:
            return
        value = self.align_box.itemData(index)
        if value is not None:
            self._set_tag("an", int(value))

    def _commit_position(self) -> None:
        if self._syncing or self._eid is None:
            return
        self._set_tag("pos", (self.pos_x_spin.value(), self.pos_y_spin.value()))

    def _commit_margins(self) -> None:
        """Поля — свойства строки, а не теги: пишем прямо в событие.

        Через команду, разумеется: прямая запись из виджета сломала бы отмену.
        """
        if self._syncing or self._eid is None:
            return
        from sfstudio.core.commands.text import SetMargins

        self._run(
            SetMargins(
                self._eid,
                self.margin_l_spin.value(),
                self.margin_r_spin.value(),
                self.margin_v_spin.value(),
            )
        )

    def _pick_color(self, tag: str) -> None:
        if self._eid is None:
            return
        event = self._doc.by_eid(self._eid)
        eff = effective_style(event, self._doc.style_for(event))
        current = eff.primary if tag == "c" else eff.outline_color
        chosen = QColorDialog.getColor(QColor(current.to_hex()), self, "Цвет")
        if not chosen.isValid():
            return
        picked = RGBA(chosen.red(), chosen.green(), chosen.blue(), 255)
        self._set_tag(tag, picked.to_ass(with_alpha=False))
