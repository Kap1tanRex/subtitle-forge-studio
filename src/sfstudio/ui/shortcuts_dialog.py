"""Настройка горячих клавиш.

Реестр действий и так знает все команды и умеет находить конфликты — здесь
только окно к этому знанию.

Конфликты **показываются, но не запрещаются**. Два действия с одним
сочетанием — обычно ошибка, но не всегда: одно может жить в контексте
таймлайна, другое в контексте таблицы, и Qt разведёт их сам. Запрещать
человеку то, что может быть осмысленно, хуже, чем предупредить.

Изменения применяются сразу к уже созданным ``QAction`` — перезапуск не
нужен: подобрать удобное сочетание, не проверив его тут же в работе,
невозможно.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.ui.actions import ActionRegistry

__all__ = ["ShortcutsDialog"]

COL_TITLE = 0
COL_MENU = 1
COL_SHORTCUT = 2


class ShortcutsDialog(QDialog):
    """Список команд с полем ввода сочетания."""

    #: Раскладка изменилась: ``{ключ команды: сочетание}``.
    changed = Signal(dict)

    def __init__(
        self,
        registry: ActionRegistry,
        overrides: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Горячие клавиши'))
        self.resize(680, 560)
        self._registry = registry
        self._overrides = dict(overrides or {})

        layout = QVBoxLayout(self)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(tr('поиск по названию команды'))
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([tr('Команда'), tr('Меню'), tr('Сочетание')])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        self.conflict_label = QLabel("")
        self.conflict_label.setWordWrap(True)
        layout.addWidget(self.conflict_label)

        buttons = QDialogButtonBox()
        reset = buttons.addButton(tr('Вернуть умолчания'), QDialogButtonBox.ResetRole)
        reset.clicked.connect(self._reset_all)
        close = buttons.addButton(tr('Закрыть'), QDialogButtonBox.RejectRole)
        close.clicked.connect(self.accept)
        layout.addWidget(buttons)

        self._fill()
        self._refresh_conflicts()

    # -- построение ---------------------------------------------------------- #

    def _fill(self) -> None:
        self.tree.clear()
        self._editors: dict[str, QKeySequenceEdit] = {}

        for spec in sorted(self._registry, key=lambda s: (s.menu, s.title)):
            item = QTreeWidgetItem([spec.title, spec.menu or "—", ""])
            item.setData(COL_TITLE, Qt.UserRole, spec.key)
            self.tree.addTopLevelItem(item)

            editor = QKeySequenceEdit(QKeySequence(spec.shortcut))
            editor.setClearButtonEnabled(True)
            editor.keySequenceChanged.connect(
                lambda sequence, key=spec.key: self._on_changed(key, sequence)
            )
            self._editors[spec.key] = editor
            self.tree.setItemWidget(item, COL_SHORTCUT, editor)

        self.tree.resizeColumnToContents(COL_MENU)
        self.tree.resizeColumnToContents(COL_SHORTCUT)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            haystack = f"{item.text(COL_TITLE)} {item.text(COL_MENU)}".casefold()
            item.setHidden(bool(needle) and needle not in haystack)

    # -- правка -------------------------------------------------------------- #

    def _on_changed(self, key: str, sequence: QKeySequence) -> None:
        text = sequence.toString(QKeySequence.PortableText)
        self._overrides[key] = text
        # Применяем немедленно: подобрать удобное сочетание, не попробовав
        # его в работе, нельзя.
        self._registry.apply_overrides({key: text})
        self._refresh_conflicts()
        self.changed.emit(dict(self._overrides))

    def _reset_all(self) -> None:
        defaults = self._registry.default_shortcuts()
        self._registry.apply_overrides(defaults)
        self._overrides = {}
        for key, editor in self._editors.items():
            editor.blockSignals(True)
            editor.setKeySequence(QKeySequence(defaults.get(key, "")))
            editor.blockSignals(False)
        self._refresh_conflicts()
        self.changed.emit({})

    def _refresh_conflicts(self) -> None:
        conflicts = self._registry.conflicts()
        if not conflicts:
            self.conflict_label.setProperty("role", "hint")
            self.conflict_label.setText(tr('Совпадающих сочетаний нет.'))
        else:
            lines = [
                tr('«{0}» — {1} команды').format(conflict.shortcut, len(conflict.keys))
                for conflict in conflicts[:5]
            ]
            tail = "" if len(conflicts) <= 5 else tr(' и ещё {0}').format(len(conflicts) - 5)
            self.conflict_label.setProperty("role", "warning")
            self.conflict_label.setText(
                tr('Одно сочетание у нескольких команд: ') + "; ".join(lines) + tail + "."
                + tr(' Это допустимо, если команды работают в разных панелях.')
            )
        style = self.conflict_label.style()
        style.unpolish(self.conflict_label)
        style.polish(self.conflict_label)

    @property
    def overrides(self) -> dict[str, str]:
        """Только отличия от умолчаний — хранить полный список незачем."""
        defaults = self._registry.default_shortcuts()
        return {
            key: value
            for key, value in self._overrides.items()
            if value != defaults.get(key, "")
        }
