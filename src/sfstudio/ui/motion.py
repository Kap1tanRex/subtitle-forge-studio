"""Анимации интерфейса.

Правила, по которым они здесь сделаны.

**Коротко.** Сто двадцать миллисекунд на появление, сто восемьдесят на
прокрутку. Анимация в интерфейсе не украшение, а подсказка «вот это только
что появилось» и «вот куда уехал список». Всё, что длится дольше четверти
секунды, начинает мешать работать: субтитры правят сотнями реплик подряд, и
задержка на каждой складывается в потерянные минуты.

**Отключаемо, и по умолчанию — как в системе.** Windows умеет сообщить, что
человек попросил обходиться без анимаций (настройка для укачивания и для
слабых машин). Мы это уважаем: отдельная настройка есть, но пока её не
трогали, ответ берётся у системы.

**Чего здесь нет.** Плавных переходов цвета у обычных кнопок. Внешний вид
задан таблицей стилей Qt, а она переходов не поддерживает — в отличие от CSS
в браузере. Обойти это можно только отказавшись от таблицы стилей для этих
кнопок и рисуя их самим; цена (своя отрисовка каждого состояния каждой
кнопки) больше пользы. Вместо этого нажатие подтверждается короткой
вспышкой — она рисуется поверх и от таблицы стилей не зависит.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtWidgets import QAbstractScrollArea, QDialog, QMenu, QWidget

__all__ = [
    "DURATION",
    "animations_enabled",
    "fade_in",
    "flash",
    "install_effects",
    "set_enabled",
    "smooth_scroll",
    "system_animations_on",
]

#: Длительности в миллисекундах. Одно место на всю программу: разнобой
#: («тут 100, там 400») читается как неисправность, а не как замысел.
DURATION = {
    "fade": 120,      # появление окна
    "flash": 140,     # подтверждение нажатия
    "scroll": 180,    # прокрутка к строке
    "highlight": 900,  # подсветка найденного, чтобы успеть заметить
}

#: Код системного запроса «показывать анимацию в Windows».
_SPI_GETCLIENTAREAANIMATION = 0x1042

#: Общий выключатель. Ставится один раз при запуске и читается отовсюду:
#: тащить настройки в каждый виджет ради одного «да/нет» незачем.
_enabled = True


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


def animations_enabled() -> bool:
    return _enabled


def system_animations_on() -> bool:
    """Разрешены ли анимации в системе.

    Windows отдаёт это через ``SPI_GETCLIENTAREAANIMATION`` — тот самый
    флажок «Показывать анимацию в Windows». На других системах и при любой
    неудаче отвечаем «да»: отсутствие ответа не повод отбирать анимации.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        result = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            _SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(result), 0
        )
        return bool(result.value) if ok else True
    except Exception:
        return True


def resolve(setting: object) -> bool:
    """Что делать по настройке: ``None`` — спросить систему."""
    if setting is None:
        return system_animations_on()
    return bool(setting)


def install_effects(app, on: bool) -> None:
    """Включает встроенные эффекты Qt и наш фильтр появления окон.

    Меню, подсказки и выпадающие списки Qt умеет показывать плавно сам —
    своего кода это не требует и работает на всех стилях сразу.
    """
    set_enabled(on)
    if app is None:
        return
    for effect in (
        Qt.UIEffect.UI_AnimateMenu,
        Qt.UIEffect.UI_FadeMenu,
        Qt.UIEffect.UI_AnimateCombo,
        Qt.UIEffect.UI_AnimateTooltip,
        Qt.UIEffect.UI_FadeTooltip,
        Qt.UIEffect.UI_AnimateToolBox,
    ):
        app.setEffectEnabled(effect, on)

    existing = app.property("sfstudio_motion_filter")
    if on and existing is None:
        watcher = _ShowWatcher(app)
        app.installEventFilter(watcher)
        app.setProperty("sfstudio_motion_filter", watcher)
    elif not on and existing is not None:
        app.removeEventFilter(existing)
        app.setProperty("sfstudio_motion_filter", None)


class _ShowWatcher(QObject):
    """Ловит показ диалогов и проявляет их.

    Именно диалоги: главное окно появляется один раз, и мигать им при
    запуске незачем, а меню Qt анимирует само.
    """

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.Type.Show
            and isinstance(obj, QDialog)
            and not isinstance(obj, QMenu)
        ):
            fade_in(obj)
        return False


def fade_in(widget: QWidget, ms: int | None = None) -> QPropertyAnimation | None:
    """Проявляет окно из прозрачности. Возвращает анимацию или ``None``.

    Прозрачность возвращается к единице по завершении, а не остаётся
    анимированной: полупрозрачное окно, застрявшее на середине из-за
    закрытия во время анимации, выглядит как поломка драйвера.
    """
    if not _enabled or widget is None:
        return None
    duration = DURATION["fade"] if ms is None else ms

    animation = QPropertyAnimation(widget, b"windowOpacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.finished.connect(lambda: widget.setWindowOpacity(1.0))
    widget.setWindowOpacity(0.0)
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def smooth_scroll(area: QAbstractScrollArea, value: int,
                  ms: int | None = None) -> QPropertyAnimation | None:
    """Прокручивает область к значению плавно.

    Прыжок на двадцать строк не даёт понять, куда делся список; та же
    прокрутка за две десятых секунды читается как движение, и место в
    документе не теряется.
    """
    if area is None:
        return None
    bar = area.verticalScrollBar()
    if bar is None:
        return None
    target = max(bar.minimum(), min(bar.maximum(), int(value)))
    if not _enabled or target == bar.value():
        bar.setValue(target)
        return None

    animation = QPropertyAnimation(bar, b"value", area)
    animation.setDuration(DURATION["scroll"] if ms is None else ms)
    animation.setStartValue(bar.value())
    animation.setEndValue(target)
    animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def flash(widget: QWidget, color: str, ms: int | None = None) -> QPropertyAnimation | None:
    """Короткая вспышка поверх виджета — подтверждение действия.

    Рисуется отдельным прозрачным слоем поверх, а не сменой цвета в таблице
    стилей: таблица стилей переходов не умеет, а слой ни от чего не зависит
    и исчезает вместе с анимацией.
    """
    if not _enabled or widget is None or not widget.isVisible():
        return None

    layer = _Flash(widget, color)
    layer.setGeometry(widget.rect())
    layer.show()

    animation = QPropertyAnimation(layer, b"strength", layer)
    animation.setDuration(DURATION["flash"] if ms is None else ms)
    animation.setStartValue(1.0)
    animation.setEndValue(0.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.finished.connect(layer.deleteLater)
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


class _Flash(QWidget):
    """Прозрачный слой со сплошной заливкой убывающей насыщенности."""

    def __init__(self, parent: QWidget, color: str) -> None:
        super().__init__(parent)
        self._color = color
        self._strength = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def get_strength(self) -> float:
        return self._strength

    def set_strength(self, value: float) -> None:
        self._strength = max(0.0, min(1.0, float(value)))
        self.update()

    # Свойство Qt: анимировать можно только его, обычное поле Python
    # QPropertyAnimation не видит.
    from PySide6.QtCore import Property

    strength = Property(float, get_strength, set_strength)

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        from PySide6.QtGui import QColor, QPainter

        color = QColor(self._color)
        color.setAlphaF(0.28 * self._strength)
        painter = QPainter(self)
        painter.fillRect(self.rect(), color)
