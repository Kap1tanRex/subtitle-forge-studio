"""Проверки по итогам аудита: чужие данные не должны исполняться.

Каждый тест здесь закрывает конкретную найденную дыру, а не «безопасность
вообще». Субтитры, манифесты плагинов и ответы сети — данные, пришедшие со
стороны, и обращаться с ними надо как с данными.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from sfstudio.services.accel import binaries, libraries


class TestDownloadedArchives:
    """Архив из сети не должен писать файлы за пределы своего каталога."""

    def test_paths_escaping_the_folder_are_skipped(self, tmp_path) -> None:
        archive = tmp_path / "release.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../../подброшенный.dll", b"payload")
            zf.writestr("main.exe", b"ok")

        target = tmp_path / "bin"
        target.mkdir()
        binaries._unpack(archive, target)

        assert (target / "main.exe").is_file()
        assert not (tmp_path.parent / "подброшенный.dll").exists()
        assert not (tmp_path / "подброшенный.dll").exists()

    def test_absolute_paths_are_skipped(self, tmp_path) -> None:
        archive = tmp_path / "release.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            # Абсолютный путь внутри архива — запись куда угодно на диске.
            zf.writestr("C:/Windows/Temp/подброшенный.dll", b"payload")
            zf.writestr("main.exe", b"ok")

        target = tmp_path / "bin"
        target.mkdir()
        binaries._unpack(archive, target)
        assert [p.name for p in target.rglob("*") if p.is_file()] == ["main.exe"]

    def test_library_files_are_taken_by_name_only(self, tmp_path) -> None:
        """Имена в пакете не участвуют в построении пути назначения."""
        archive = tmp_path / "wheel.whl"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../../../nvidia/cublas64_12.dll", b"payload")

        folder = tmp_path / "libs"
        folder.mkdir()
        written = libraries._extract(archive, ("cublas64_12.dll",), folder)
        assert written == [folder / "cublas64_12.dll"]
        assert (folder / "cublas64_12.dll").read_bytes() == b"payload"


class TestPackageIndexAnswers:
    """Ответ указателя PyPI — это тоже данные из сети."""

    def entry(self, **changes) -> dict:
        base = {
            "filename": "nvidia_cublas_cu12-12.4.5-py3-none-win_amd64.whl",
            "url": "https://files.pythonhosted.org/packages/ab/cd/x.whl",
            "digests": {"sha256": "d" * 64},
        }
        base.update(changes)
        return base

    def test_expected_entry_is_accepted_with_its_digest(self) -> None:
        assert libraries._pick(self.entry()) == (
            "https://files.pythonhosted.org/packages/ab/cd/x.whl", "d" * 64
        )

    def test_plain_http_is_refused(self) -> None:
        assert libraries._pick(self.entry(url="http://files.pythonhosted.org/x.whl")) is None

    def test_foreign_host_is_refused(self) -> None:
        """Адрес приходит строкой и вести может куда угодно."""
        assert libraries._pick(self.entry(url="https://example.invalid/x.whl")) is None

    def test_other_platforms_are_ignored(self) -> None:
        assert libraries._pick(self.entry(filename="x-manylinux_x86_64.whl")) is None


class TestReleaseAssets:
    """Файл выпуска потом запускается как программа."""

    def answer(self, url: str):
        payload = json.dumps(
            {"assets": [{"name": "whisper-bin-x64.zip", "browser_download_url": url}]}
        ).encode()

        class Answer:
            def __init__(self) -> None:
                self._data = io.BytesIO(payload)

            def read(self, size: int = -1) -> bytes:
                return self._data.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> None:
                return None

        return Answer()

    def test_expected_host_is_accepted(self, monkeypatch) -> None:
        url = "https://github.com/ggml-org/whisper.cpp/releases/download/b4938/x.zip"
        monkeypatch.setattr(
            binaries.urllib.request, "urlopen", lambda *_a, **_k: self.answer(url)
        )
        assert binaries._asset_url("whisper-bin-x64.zip") == url

    def test_foreign_host_is_refused(self, monkeypatch) -> None:
        monkeypatch.setattr(
            binaries.urllib.request, "urlopen",
            lambda *_a, **_k: self.answer("https://example.invalid/whisper.zip"),
        )
        with pytest.raises(binaries.RecognitionError, match="неожиданный адрес"):
            binaries._asset_url("whisper-bin-x64.zip")


class TestDownloadIntegrity:
    """Скачанное сверяется с контрольной суммой из указателя."""

    def fake_answer(self, payload: bytes):
        class Answer:
            def __init__(self) -> None:
                self.headers = {"Content-Length": str(len(payload))}
                self._data = io.BytesIO(payload)

            def read(self, size: int) -> bytes:
                return self._data.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> None:
                return None

        return Answer()

    def test_mismatched_digest_is_refused(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            libraries.urllib.request, "urlopen",
            lambda *_a, **_k: self.fake_answer(b"tampered"),
        )
        with pytest.raises(libraries.RecognitionError, match="контрольной суммой"):
            libraries._download("https://files.pythonhosted.org/x.whl", tmp_path,
                                lambda *_a: None, None, "0" * 64)
        # Файл-обрубок не должен остаться и сойти за установленную библиотеку.
        assert not list(tmp_path.glob(".download-*"))

    def test_matching_digest_passes(self, tmp_path, monkeypatch) -> None:
        import hashlib

        payload = b"the real package"
        monkeypatch.setattr(
            libraries.urllib.request, "urlopen",
            lambda *_a, **_k: self.fake_answer(payload),
        )
        result = libraries._download(
            "https://files.pythonhosted.org/x.whl", tmp_path, lambda *_a: None, None,
            hashlib.sha256(payload).hexdigest(),
        )
        assert result.read_bytes() == payload


class TestPluginEntryPoint:
    """Манифест указывает, какой файл запустить, — и он пишется автором."""

    def make_plugin(self, folder: Path, entry: str) -> Path:
        plugin = folder / "plugins" / "sample"
        plugin.mkdir(parents=True)
        (plugin / "plugin.json").write_text(
            json.dumps({"name": "sample", "title": "Пример", "entry": entry}),
            encoding="utf-8",
        )
        return plugin

    def test_entry_outside_the_folder_is_refused(self, tmp_path) -> None:
        from sfstudio.plugins.api import PluginInfo, PluginState
        from sfstudio.plugins.loader import load_plugin

        (tmp_path / "чужой.py").write_text("raise SystemExit(1)", encoding="utf-8")
        plugin = self.make_plugin(tmp_path, "../../чужой.py")

        result = load_plugin(PluginInfo(name="sample", title="Пример",
                                        entry="../../чужой.py", folder=plugin))
        assert result.state is PluginState.FAILED
        assert "за пределами" in result.reason

    def test_ordinary_entry_still_loads(self, tmp_path) -> None:
        from sfstudio.plugins.api import PluginInfo, PluginState
        from sfstudio.plugins.loader import load_plugin

        plugin = self.make_plugin(tmp_path, "main.py")
        (plugin / "main.py").write_text("def setup(context):\n    pass\n", encoding="utf-8")

        result = load_plugin(PluginInfo(name="sample", title="Пример", folder=plugin))
        assert result.state is PluginState.LOADED


class TestLibrarySearch:
    """Загрузка DLL по голому имени включает текущий каталог. Не годится."""

    def test_relative_path_entries_are_ignored(self, monkeypatch, tmp_path) -> None:
        from sfstudio.services.asr.engines import faster_whisper

        # «.» в PATH — это снова текущий каталог, и файл оттуда брать нельзя.
        (tmp_path / "cublas64_12.dll").write_bytes(b"planted library")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PATH", ".")
        monkeypatch.delenv("CUDA_PATH", raising=False)
        assert faster_whisper._find_in_path("cublas64_12.dll") is None

    def test_absolute_path_entries_are_used(self, monkeypatch, tmp_path) -> None:
        from sfstudio.services.asr.engines import faster_whisper

        (tmp_path / "cublas64_12.dll").write_bytes(b"library")
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.delenv("CUDA_PATH", raising=False)
        found = faster_whisper._find_in_path("cublas64_12.dll")
        assert found == tmp_path / "cublas64_12.dll"


pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from sfstudio.ui.safe_text import menu_label, plain_tooltip  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class TestTooltipText:
    """Текст реплики приходит из файла и разметкой быть не должен.

    Проверено отдельно: ``QLabel`` с ``<img src=…>`` действительно загружает
    файл — принимает его размеры. Для пути вида ``\\\\сервер\\общий`` это
    поход в сеть и представление учётной записью Windows.
    """

    def test_ordinary_text_is_left_alone(self, qapp) -> None:
        assert plain_tooltip("Привет, как дела?") == "Привет, как дела?"
        assert plain_tooltip("1 < 2 и 3 > 2") == "1 < 2 и 3 > 2"

    def test_image_tag_is_neutralised(self, qapp) -> None:
        text = '<img src="\\\\сервер\\общий\\точка.png">Реплика'
        result = plain_tooltip(text)
        assert "<img" not in result
        assert "&lt;img" in result
        assert "Реплика" in result

    def test_link_is_neutralised(self, qapp) -> None:
        result = plain_tooltip('<a href="http://example.invalid">жми</a>')
        assert "<a href" not in result

    def test_line_breaks_survive(self, qapp) -> None:
        """Подсказку собирают из строк — склеивать их в одну нельзя."""
        result = plain_tooltip("<b>первая</b>\nвторая")
        assert "\n" in result
        assert "pre-wrap" in result

    def test_empty_text_is_safe(self, qapp) -> None:
        assert plain_tooltip("") == ""


class TestMenuLabels:
    def test_ampersand_is_doubled(self) -> None:
        """Иначе «Иван & Марья» покажется как «Иван  Марья»."""
        assert menu_label("Иван & Марья") == "Иван && Марья"

    def test_plain_name_is_unchanged(self) -> None:
        assert menu_label("Иван") == "Иван"

    def test_actor_menu_shows_the_ampersand(self, qapp) -> None:
        from sfstudio.core.document import SubtitleDocument
        from sfstudio.core.undo import UndoStack
        from sfstudio.ui.event_menu import actor_submenu

        doc = SubtitleDocument.blank()
        event = doc.create_event(0, 2000, "Реплика")
        doc.actors.add("Иван & Марья")
        menu = actor_submenu(None, doc, [event.eid], UndoStack(doc).run)
        assert any(a.text() == "Иван && Марья" for a in menu.actions())
