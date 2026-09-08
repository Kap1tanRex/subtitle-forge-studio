"""Распознавание в фоновом потоке.

Отдельная задача, а не вызов из обработчика кнопки: распознавание часовой
дорожки идёт минуты, и на это время главный поток обязан оставаться живым —
иначе окно перестаёт перерисовываться, и система помечает программу как
не отвечающую.

Сигналы Qt здесь не украшение, а необходимость: колбэк прогресса дёргается из
рабочего потока, а трогать виджеты откуда-либо, кроме главного, нельзя.
Сигнал — единственный законный способ доставить туда значение.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from sfstudio.services.asr import (
    CancelToken,
    RecognitionCancelled,
    RecognitionError,
    RecognitionRequest,
    get_engine,
)

__all__ = ["RecognitionSignals", "RecognitionTask"]


class RecognitionSignals(QObject):
    """QRunnable не наследует QObject, поэтому сигналы живут отдельно."""

    progress = Signal(float, str)   # 0..1, что происходит
    finished = Signal(object)       # RecognitionResult
    failed = Signal(str)            # сообщение для пользователя


class RecognitionTask(QRunnable):
    """Одно распознавание."""

    def __init__(self, engine_key: str, request: RecognitionRequest) -> None:
        super().__init__()
        self.signals = RecognitionSignals()
        self._engine_key = engine_key
        self._request = request
        self._cancel = CancelToken()

    def cancel(self) -> None:
        self._cancel.cancel()

    @Slot()
    def run(self) -> None:
        engine = get_engine(self._engine_key)
        if engine is None:
            self.signals.failed.emit(f"движок «{self._engine_key}» не найден")
            return

        try:
            result = engine.transcribe(
                self._request,
                progress=lambda fraction, note="": self.signals.progress.emit(
                    float(fraction), str(note)
                ),
                cancel=self._cancel,
            )
        except RecognitionCancelled:
            self.signals.failed.emit("Распознавание прервано.")
        except RecognitionError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:
            # Адаптеры оборачивают чужие библиотеки, и упасть они могут чем
            # угодно. Необработанное исключение в потоке пула убивает его
            # молча — окно осталось бы ждать сигнала, который не придёт.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.finished.emit(result)
