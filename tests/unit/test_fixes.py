"""Тесты на исправления этого захода: перемотка, громкость, реплики, шрифты."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from sfstudio.app.settings import Settings
from sfstudio.core.commands import AddTrack, SetTrackFlags
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.services.asr.models import MODEL_CATALOG, installed_models
from sfstudio.ui.font_box import SAMPLE_EN, SAMPLE_RU, FontComboBox, font_sample_html
from sfstudio.ui.timeline import NEW_EVENT_MS, TimelineWidget
from sfstudio.ui.transport import TransportBar

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def timeline(qapp: QApplication) -> TimelineWidget:
    doc = SubtitleDocument.blank()
    doc.create_event(1000, 3000, "Существующая")
    widget = TimelineWidget(doc, UndoStack(doc))
    widget.resize(1000, 400)
    widget.fit_all()
    return widget


class TestCreateEvent:
    """Создание реплики на таймлайне — самое частое действие, и его не было."""

    def test_button_exists_in_the_ruler(self, timeline: TimelineWidget) -> None:
        rect = timeline._new_event_button_rect()
        assert rect.width() > 0
        assert rect.right() <= timeline._add_button_rect().left()

    def test_button_creates_an_event(self, timeline: TimelineWidget) -> None:
        before = len(timeline._doc)
        rect = timeline._new_event_button_rect()
        point = QPointF(rect.center().x(), rect.center().y())
        timeline.mousePressEvent(
            QMouseEvent(QEvent.MouseButtonPress, point, point,
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        )
        assert len(timeline._doc) == before + 1

    def test_created_event_is_visible_length(self, timeline: TimelineWidget) -> None:
        """Нулевая длительность была бы невидима — создание выглядело бы зря."""
        eid = timeline.create_event_at(20_000)
        assert eid is not None
        assert timeline._doc.by_eid(eid).duration == NEW_EVENT_MS

    def test_created_event_is_selected(self, timeline: TimelineWidget) -> None:
        eid = timeline.create_event_at(20_000)
        assert timeline._selected == eid

    def test_creation_is_undoable(self, timeline: TimelineWidget) -> None:
        before = len(timeline._doc)
        timeline.create_event_at(20_000)
        timeline._undo.undo()
        assert len(timeline._doc) == before

    def test_does_not_overlap_existing(self, timeline: TimelineWidget) -> None:
        """Щелчок по занятому месту не должен класть реплику поверх."""
        eid = timeline.create_event_at(2000)  # внутри 1000..3000
        assert eid is not None
        assert timeline._doc.by_eid(eid).start >= 3000

    def test_locked_track_refuses(self, timeline: TimelineWidget) -> None:
        timeline._undo.run(SetTrackFlags(0, locked=True))
        assert timeline.create_event_at(20_000, 0) is None

    def test_uses_selected_track(self, timeline: TimelineWidget) -> None:
        """Работа продолжается там же, где шла, а не в нулевом слое."""
        timeline._undo.run(AddTrack("Надписи"))
        first = timeline.create_event_at(20_000, 1)
        assert first is not None
        second = timeline.create_event_at(30_000)
        assert timeline._doc.by_eid(second).layer == 1

    def test_falls_back_past_locked_track(self, timeline: TimelineWidget) -> None:
        timeline._undo.run(AddTrack())
        timeline._undo.run(SetTrackFlags(0, locked=True))
        eid = timeline.create_event_at(20_000)
        assert eid is not None
        assert timeline._doc.by_eid(eid).layer == 1


class TestVolume:
    @pytest.fixture
    def bar(self, qapp: QApplication) -> TransportBar:
        widget = TransportBar()
        widget.set_enabled_transport(True)
        return widget

    def test_has_controls(self, bar: TransportBar) -> None:
        assert bar.volume_slider is not None
        assert not bar.btn_mute.icon().isNull()

    def test_slider_emits(self, bar: TransportBar) -> None:
        seen: list[float] = []
        bar.volume_changed.connect(seen.append)
        bar.volume_slider.setValue(35)
        assert seen == [35.0]

    def test_setting_value_does_not_echo(self, bar: TransportBar) -> None:
        """Показ громкости плеера не должен отправлять её обратно плееру."""
        seen: list[float] = []
        bar.volume_changed.connect(seen.append)
        bar.set_volume(42)
        assert seen == []
        assert bar.volume() == 42

    def test_zero_looks_muted(self, bar: TransportBar) -> None:
        """Ноль на ползунке и есть тишина — кнопка обязана это показывать."""
        bar.set_volume(0)
        assert bar.btn_mute.isChecked()

    def test_label_follows(self, bar: TransportBar) -> None:
        bar.set_volume(64)
        assert "64" in bar.volume_label.text()

    def test_value_is_clamped(self, bar: TransportBar) -> None:
        bar.set_volume(500)
        assert bar.volume() == 100
        bar.set_volume(-20)
        assert bar.volume() == 0


class TestFontBox:
    def test_sample_contains_both_languages(self) -> None:
        """Латиница есть везде; по ней не понять, есть ли кириллица."""
        html = font_sample_html("Arial")
        assert SAMPLE_RU in html
        assert SAMPLE_EN in html

    def test_sample_uses_the_family(self) -> None:
        assert "Arial" in font_sample_html("Arial")

    def test_family_with_special_characters_is_escaped(self) -> None:
        assert "&lt;" in font_sample_html("Weird <Font>")

    def test_widget_builds(self, qapp: QApplication) -> None:
        """Список шрифтов в offscreen пуст — проверяем сам виджет, не базу."""
        box = FontComboBox()
        assert box.isEditable()
        assert box.view().hasMouseTracking()

    def test_set_family_is_silent(self, qapp: QApplication) -> None:
        """Показ шрифта реплики не должен считаться правкой."""
        box = FontComboBox()
        seen: list[str] = []
        box.currentFontChanged.connect(lambda f: seen.append(f.family()))
        box.set_family("Arial")
        assert seen == []


class TestDefaultFont:
    def test_setting_has_a_sane_default(self, tmp_path: Path) -> None:
        settings = Settings(tmp_path / "s.json")
        assert settings.get("subtitles.default_font")
        assert float(settings.get("subtitles.default_size")) > 0

    def test_new_project_uses_it(self, qapp: QApplication, tmp_path: Path) -> None:
        """Стиль нового проекта берёт шрифт и кегль из настроек.

        Имя шрифта подменяем: в offscreen база шрифтов пуста, и настоящий
        выбор проверить нечем — а вот то, что ``build`` спрашивает именно
        виджет, проверить можно и нужно.
        """
        from sfstudio.ui.new_project_dialog import NewProjectDialog

        dialog = NewProjectDialog(tmp_path, default_font="Georgia", default_size=40.0)
        dialog.font_family = lambda: "Georgia"
        project = dialog.build()
        assert project.document.styles["Default"].fontname == "Georgia"
        assert project.document.styles["Default"].fontsize == 40.0


class TestModelCatalog:
    def test_every_model_declares_a_size(self) -> None:
        for info in MODEL_CATALOG:
            assert info.size_mb > 0
            assert info.note

    def test_caption_mentions_size(self) -> None:
        assert "МБ" in MODEL_CATALOG[0].caption

    def test_nothing_installed_in_empty_dir(self, tmp_path: Path) -> None:
        assert installed_models(tmp_path) == set()

    def test_missing_dir_is_not_an_error(self, tmp_path: Path) -> None:
        assert installed_models(tmp_path / "нет") == set()

    def test_none_dir(self) -> None:
        assert installed_models(None) == set()

    def test_partial_download_is_not_counted(self, tmp_path: Path) -> None:
        """Прерванная загрузка оставляет конфиг без весов — это не модель."""
        folder = tmp_path / "faster-whisper-small"
        folder.mkdir()
        (folder / "config.json").write_text("{}", encoding="utf-8")
        assert "small" not in installed_models(tmp_path)

    def test_complete_download_is_counted(self, tmp_path: Path) -> None:
        folder = tmp_path / "faster-whisper-small"
        folder.mkdir()
        (folder / "model.bin").write_bytes(b"x")
        assert "small" in installed_models(tmp_path)


class TestPlayerSeekGuard:
    """Перемотка до готовности файла роняла программу при открытии проекта."""

    def test_seek_before_load_is_remembered(self) -> None:
        from sfstudio.media.player import mpv_available

        if not mpv_available():
            pytest.skip("libmpv недоступна")

        from sfstudio.media.player import MpvPlayer

        player = MpvPlayer(video_output="null")
        try:
            assert not player.is_ready
            player.seek_ms(5000)  # раньше здесь падало
            assert player._pending_seek_ms == 5000
        finally:
            player.close()

    def test_ready_requires_duration(self) -> None:
        from sfstudio.media.player import mpv_available

        if not mpv_available():
            pytest.skip("libmpv недоступна")

        from sfstudio.media.player import MpvPlayer

        player = MpvPlayer(video_output="null")
        try:
            assert not player.is_ready
        finally:
            player.close()


class TestModelLookup:
    """Поиск скачанной модели: без него она качается второй раз."""

    def _make(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "model.bin").write_bytes(b"x")

    def test_found_in_the_folder_itself(self, tmp_path: Path) -> None:
        from sfstudio.services.asr.engines.faster_whisper import _resolve_source

        self._make(tmp_path)
        source, download = _resolve_source("small", tmp_path)
        assert source == str(tmp_path)
        assert download is None

    def test_found_in_the_named_subfolder(self, tmp_path: Path) -> None:
        from sfstudio.services.asr.engines.faster_whisper import _resolve_source

        self._make(tmp_path / "faster-whisper-small")
        source, _ = _resolve_source("small", tmp_path)
        assert source.endswith("faster-whisper-small")

    def test_found_deeper(self, tmp_path: Path) -> None:
        """Каталог могли указать выше — например общую папку «Модели»."""
        from sfstudio.services.asr.engines.faster_whisper import _resolve_source

        self._make(tmp_path / "год" / "whisper" / "faster-whisper-small")
        source, download = _resolve_source("small", tmp_path)
        assert source.endswith("faster-whisper-small")
        assert download is None

    def test_other_size_is_not_taken(self, tmp_path: Path) -> None:
        """Для «small» не подойдёт «large», случайно оказавшаяся рядом."""
        from sfstudio.services.asr.engines.faster_whisper import _resolve_source

        self._make(tmp_path / "faster-whisper-large-v3")
        source, download = _resolve_source("small", tmp_path)
        assert source == "small"
        assert download == str(tmp_path)

    def test_missing_model_asks_to_download(self, tmp_path: Path) -> None:
        from sfstudio.services.asr.engines.faster_whisper import _resolve_source

        source, download = _resolve_source("small", tmp_path)
        assert source == "small"
        assert download == str(tmp_path)


class TestVadFallback:
    """Файл модели VAD легко не доехать в сборку — это не повод падать."""

    def test_recognises_the_missing_file_error(self) -> None:
        from sfstudio.services.asr.engines.faster_whisper import _looks_like_missing_vad

        real = RuntimeError(
            "[ONNXRuntimeError] : 3 : NO_SUCHFILE : Load model from "
            r"C:\Temp\_MEI\faster_whisper\assets\silero_vad_v6.onnx failed"
        )
        assert _looks_like_missing_vad(real)

    def test_other_errors_are_not_mistaken_for_it(self) -> None:
        from sfstudio.services.asr.engines.faster_whisper import _looks_like_missing_vad

        assert not _looks_like_missing_vad(RuntimeError("out of memory"))
        assert not _looks_like_missing_vad(RuntimeError("model.bin is corrupt"))

    def test_retries_without_vad(self) -> None:
        """Первый вызов падает на VAD, второй обязан пройти без него."""
        from sfstudio.services.asr.base import RecognitionRequest
        from sfstudio.services.asr.engines.faster_whisper import FasterWhisperEngine

        calls: list[bool] = []

        class FakeModel:
            def transcribe(self, _samples, **kwargs):
                calls.append(kwargs["vad_filter"])
                if kwargs["vad_filter"]:
                    raise RuntimeError(
                        "NO_SUCHFILE : Load model from silero_vad_v6.onnx failed"
                    )
                return ([], object())

        class FakeChunk:
            samples = (0.0,)

        engine = FasterWhisperEngine()
        engine._transcribe(FakeModel(), FakeChunk(), RecognitionRequest(media=Path("x")))
        assert calls == [True, False]

    def test_unrelated_error_is_not_swallowed(self) -> None:
        from sfstudio.services.asr.base import RecognitionRequest
        from sfstudio.services.asr.engines.faster_whisper import FasterWhisperEngine

        class FakeModel:
            def transcribe(self, _samples, **_kwargs):
                raise RuntimeError("совсем другая беда")

        class FakeChunk:
            samples = (0.0,)

        with pytest.raises(RuntimeError, match="другая беда"):
            FasterWhisperEngine()._transcribe(
                FakeModel(), FakeChunk(), RecognitionRequest(media=Path("x"))
            )


class TestModelSizes:
    """Размеры измерены по метаданным репозиториев, а не взяты из документации."""

    def test_sizes_match_parameter_counts(self) -> None:
        """Вес пропорционален числу параметров — по два байта на каждый."""
        for info in MODEL_CATALOG:
            expected = info.params_m * 2  # МБ при float16
            assert abs(info.size_mb - expected) / expected < 0.15, info.name

    def test_medium_is_three_times_small(self) -> None:
        """Ответ на вопрос «почему medium на гигабайт больше»: параметров втрое."""
        sizes = {info.name: info for info in MODEL_CATALOG}
        ratio = sizes["medium"].params_m / sizes["small"].params_m
        assert 3.0 < ratio < 3.3
        assert sizes["medium"].size_mb - sizes["small"].size_mb > 900

    def test_plain_models_go_from_light_to_heavy(self) -> None:
        """Обычные модели идут по возрастанию — так их и выбирают.

        Дистиллированная в этот ряд не встаёт: у неё параметров как у medium,
        а качество ближе к large. Она стоит перед large намеренно — как более
        дешёвая альтернатива именно ей, а не как ступень между размерами.
        """
        plain = [i.size_mb for i in MODEL_CATALOG if not i.name.startswith("distil")]
        assert plain == sorted(plain)

    def test_distilled_stands_before_large(self) -> None:
        names = [info.name for info in MODEL_CATALOG]
        assert names.index("distil-large-v3") < names.index("large-v3")
        assert names.index("distil-large-v3") > names.index("small")

    def test_distilled_model_is_offered(self) -> None:
        """Качество близко к large, а весит как medium — стоит знать о ней."""
        names = {info.name for info in MODEL_CATALOG}
        assert "distil-large-v3" in names


class TestCudaSupport:
    def test_enabling_libraries_is_safe(self) -> None:
        """Функция должна отрабатывать и там, где никакой CUDA нет."""
        from sfstudio.services.asr.engines.faster_whisper import enable_cuda_libraries

        assert isinstance(enable_cuda_libraries(), list)

    def test_repeated_calls_do_not_duplicate(self) -> None:
        from sfstudio.services.asr.engines.faster_whisper import enable_cuda_libraries

        enable_cuda_libraries()
        assert enable_cuda_libraries() == []

    def test_device_is_one_of_two(self) -> None:
        from sfstudio.services.asr.engines.faster_whisper import pick_device

        assert pick_device() in ("cpu", "cuda")


class TestDownloadProgress:
    """Индикатор загрузки: hub заводит несколько счётчиков, и их нельзя складывать."""

    def _tracker(self):
        from sfstudio.services.asr.models import _ProgressTqdm

        seen: list[tuple[float, str]] = []
        cls = _ProgressTqdm.factory(
            lambda fraction, note="": seen.append((fraction, note)), None, "tiny"
        )
        return cls, seen

    def test_bytes_are_not_counted_twice(self) -> None:
        """Модель на 1446 МБ показывалась как 2287 — байты считались дважды.

        Hub заводит «Downloading bytes», «Reconstructing» и «Fetching N files».
        Первые два считают одни и те же байты, третий — файлы.
        """
        cls, seen = self._tracker()
        downloading = cls(total=100 * 1024 * 1024, unit="B")
        reconstructing = cls(total=100 * 1024 * 1024, unit="B")
        files = cls(total=6, unit=None)

        downloading.update(50 * 1024 * 1024)
        reconstructing.update(50 * 1024 * 1024)
        files.update(3)

        assert seen
        fraction, note = seen[-1]
        assert fraction == pytest.approx(0.5, abs=0.01)
        assert "50 из 100 МБ" in note

    def test_file_counter_does_not_set_the_volume(self) -> None:
        """Счётчик файлов не должен подписываться мегабайтами."""
        cls, seen = self._tracker()
        files = cls(total=6, unit=None)
        files.update(3)
        assert seen
        assert "МБ" not in seen[-1][1]

    def test_progress_never_goes_back(self) -> None:
        """Ведущий счётчик меняется — полоса не должна дёргаться назад."""
        cls, seen = self._tracker()
        first = cls(total=100, unit="B")
        first.update(80)
        second = cls(total=1000, unit="B")
        second.update(10)
        fractions = [f for f, _ in seen]
        assert fractions == sorted(fractions)

    def test_stops_short_of_completion(self) -> None:
        """Сотню ставит только сам загрузчик, когда файлы действительно на месте."""
        cls, seen = self._tracker()
        bar = cls(total=100, unit="B")
        bar.update(100)
        assert seen[-1][0] < 1.0

    def test_cancellation_is_honoured(self) -> None:
        from sfstudio.services.asr import CancelToken, RecognitionCancelled
        from sfstudio.services.asr.models import _ProgressTqdm

        token = CancelToken()
        token.cancel()
        cls = _ProgressTqdm.factory(None, token, "tiny")
        bar = cls(total=100, unit="B")
        with pytest.raises(RecognitionCancelled):
            bar.update(1)

    def test_hub_extras_do_not_break_it(self) -> None:
        """Hub зовёт методы, которых у заглушки может не быть.

        Про set_postfix_str мы узнали ошибкой посреди настоящей загрузки —
        поэтому набор проверяется, а не додумывается.
        """
        from sfstudio.services.asr.models import _ProgressTqdm

        bar = _ProgressTqdm.factory(None, None, "tiny")(total=10, unit="B")
        bar.set_postfix_str("100 MB/s")
        bar.set_description("что-то")
        bar.clear()
        bar.display()
        bar.unpause()
        bar.refresh()
        assert "rate" in bar.format_dict


class TestModelLanguages:
    """Одноязычные модели должны быть видны как одноязычные.

    distil-large-v3 попала в каталог по весу и качеству, а то, что она знает
    только английский, я не проверил. Пользователь выбрал русский язык и
    получил английский текст — молча, без единого предупреждения.
    """

    def test_distilled_model_is_english_only(self) -> None:
        info = next(i for i in MODEL_CATALOG if i.name == "distil-large-v3")
        assert info.languages == ("en",)
        assert info.english_only

    def test_english_only_model_refuses_russian(self) -> None:
        info = next(i for i in MODEL_CATALOG if i.name == "distil-large-v3")
        assert not info.supports("ru")
        assert info.supports("en")

    def test_english_only_model_refuses_autodetection(self) -> None:
        """Определять язык одноязычной модели нечем — ответит она всё равно своим."""
        info = next(i for i in MODEL_CATALOG if i.name == "distil-large-v3")
        assert not info.supports(None)
        assert not info.supports("")

    def test_multilingual_models_take_anything(self) -> None:
        for info in MODEL_CATALOG:
            if info.english_only:
                continue
            assert info.supports("ru") and info.supports(None), info.name

    def test_limitation_is_visible_in_caption(self) -> None:
        """Ограничение должно быть видно в самом списке, а не в примечании:
        по списку модель и выбирают."""
        info = next(i for i in MODEL_CATALOG if i.name == "distil-large-v3")
        assert "английск" in info.caption.lower()
        assert "английск" in info.note.lower()

    def test_russian_advice_names_a_working_model(self) -> None:
        """Мало сказать «не подойдёт» — надо сказать, что взять взамен."""
        info = next(i for i in MODEL_CATALOG if i.name == "distil-large-v3")
        names = {i.name for i in MODEL_CATALOG if not i.english_only}
        assert any(name in info.note for name in names)


class TestCudaLibraryLoading:
    """Библиотеки CUDA грузятся по полному пути, а не ищутся по имени.

    ``os.add_dll_directory`` влияет только на загрузку с флагами
    ``LOAD_LIBRARY_SEARCH_*``; CTranslate2 зовёт ``LoadLibrary`` по голому
    имени и добавленных каталогов не видит. Выглядело это как неподдерживаемая
    видеокарта: откат на процессор и прогресс, замерший на десятой доле.
    """

    def test_dependencies_load_before_dependents(self) -> None:
        """cublasLt раньше cublas: иначе зависимость поищется мимо каталога."""
        from sfstudio.services.asr.engines.faster_whisper import _CUDA_LIBRARIES

        order = list(_CUDA_LIBRARIES)
        assert order.index("cublasLt64_12") < order.index("cublas64_12")

    def test_loaded_handles_are_kept(self) -> None:
        """Ссылки хранятся: выгруженная библиотека — снова «не найдена»."""
        from sfstudio.services.asr.engines import faster_whisper

        faster_whisper.enable_cuda_libraries()
        assert isinstance(faster_whisper._cuda_loaded, dict)

    def test_search_covers_a_folder_next_to_the_program(self, tmp_path) -> None:
        """Собранной версии site-packages не поможет: там пакетов nvidia нет."""
        from sfstudio.services.asr.engines import faster_whisper

        target = tmp_path / "cuda"
        target.mkdir()
        with mock.patch.dict(
            os.environ, {faster_whisper.CUDA_DIR_ENV: str(target)}
        ):
            assert target in faster_whisper._cuda_search_roots()

    def test_missing_folders_are_skipped(self, tmp_path) -> None:
        from sfstudio.services.asr.engines import faster_whisper

        absent = tmp_path / "nothing-here"
        with mock.patch.dict(
            os.environ, {faster_whisper.CUDA_DIR_ENV: str(absent)}
        ):
            assert absent not in faster_whisper._cuda_search_roots()
