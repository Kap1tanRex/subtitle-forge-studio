"""Тесты каталогов загрузок и определения вычислителей."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from sfstudio.app.settings import Settings
from sfstudio.app.storage import (
    StorageMode,
    beside_program,
    data_root,
    libraries_dir,
    models_dir,
    plugins_dir,
    resolve_root,
    writable,
)
from sfstudio.services.accel import Vendor, detect_accelerators, detect_gpus, vendor_of
from sfstudio.services.accel.libraries import (
    CUDA_PACKAGES,
    _extract,
    cuda_ready,
    installed_libraries,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path / "settings.json")


class TestStorageModes:
    def test_custom_folder_is_used(self, settings: Settings, tmp_path: Path) -> None:
        target = tmp_path / "второй-диск"
        settings.set("storage.mode", "custom")
        settings.set("storage.folder", str(target))
        assert data_root(settings) == target

    def test_empty_custom_folder_falls_back(self, settings: Settings) -> None:
        """Режим выбран, папка не указана — это не повод падать."""
        settings.set("storage.mode", "custom")
        settings.set("storage.folder", "")
        assert data_root(settings).is_absolute()

    def test_unknown_mode_does_not_crash(self, settings: Settings) -> None:
        settings.set("storage.mode", "чепуха")
        assert data_root(settings).is_absolute()

    def test_beside_mode_points_next_to_the_program(self, settings: Settings) -> None:
        settings.set("storage.mode", "beside")
        assert data_root(settings) == beside_program()

    def test_subfolders_differ(self, settings: Settings, tmp_path: Path) -> None:
        settings.set("storage.mode", "custom")
        settings.set("storage.folder", str(tmp_path))
        assert models_dir(settings) != libraries_dir(settings) != plugins_dir(settings)
        assert models_dir(settings).parent == tmp_path

    def test_explicit_models_dir_wins(self, settings: Settings, tmp_path: Path) -> None:
        """Каталог моделей заполняли раньше — молча переезжать нельзя."""
        chosen = tmp_path / "мои-модели"
        settings.set("storage.mode", "custom")
        settings.set("storage.folder", str(tmp_path / "другое"))
        settings.set("asr.models_dir", str(chosen))
        assert models_dir(settings) == chosen

    def test_every_mode_has_a_readable_title(self) -> None:
        for mode in StorageMode:
            assert mode.title and mode.note

    def test_resolve_root_accepts_plain_strings(self, tmp_path: Path) -> None:
        assert resolve_root("custom", str(tmp_path)) == tmp_path


class TestWritable:
    def test_writable_folder(self, tmp_path: Path) -> None:
        assert writable(tmp_path / "новая")

    def test_folder_is_created_by_the_check(self, tmp_path: Path) -> None:
        target = tmp_path / "создастся"
        assert writable(target)
        assert target.is_dir()

    def test_path_blocked_by_a_file(self, tmp_path: Path) -> None:
        """Проверяем записью, а не правами: так надёжнее и на сетевых дисках."""
        blocker = tmp_path / "занято"
        blocker.write_text("я файл", encoding="utf-8")
        assert not writable(blocker)


class TestVendorDetection:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("NVIDIA GeForce RTX 5070 Ti", Vendor.NVIDIA),
            ("AMD Radeon RX 7900 XTX", Vendor.AMD),
            ("Intel(R) Iris(R) Xe Graphics", Vendor.INTEL),
            ("Microsoft Basic Display Adapter", Vendor.OTHER),
        ],
    )
    def test_vendor_by_name(self, name: str, expected: Vendor) -> None:
        assert vendor_of(name) is expected

    def test_detection_returns_a_list(self) -> None:
        """На любой машине это должен быть список, пусть и пустой."""
        assert isinstance(detect_gpus(), list)


class TestAccelerators:
    def test_processor_is_always_first(self) -> None:
        """Список начинается с того, что работает заведомо."""
        found = detect_accelerators()
        assert found[0].key == "cpu"
        assert found[0].available

    def test_every_entry_explains_itself(self) -> None:
        for accel in detect_accelerators():
            assert accel.note, accel.key
            assert accel.title

    def test_unavailable_entries_are_not_hidden(self) -> None:
        """Иначе владелец Radeon решит, что программа не увидела его карту."""
        found = detect_accelerators()
        assert all(a.engines for a in found)

    def test_caption_names_the_card_when_known(self) -> None:
        for accel in detect_accelerators():
            if accel.devices:
                assert accel.devices[0] in accel.caption


class TestLibraryFiles:
    def test_nothing_installed_in_an_empty_folder(self, tmp_path: Path) -> None:
        assert installed_libraries(tmp_path) == set()
        assert not cuda_ready(tmp_path)

    def test_missing_folder_is_not_an_error(self, tmp_path: Path) -> None:
        assert installed_libraries(tmp_path / "нет") == set()
        assert installed_libraries(None) == set()

    def test_ready_when_every_file_is_present(self, tmp_path: Path) -> None:
        for package in CUDA_PACKAGES:
            for name in package.files:
                (tmp_path / name).write_bytes(b"x")
        assert cuda_ready(tmp_path)

    def test_partial_set_is_not_ready(self, tmp_path: Path) -> None:
        """Половина комплекта хуже, чем ничего: упадёт уже посреди работы."""
        (tmp_path / CUDA_PACKAGES[0].files[0]).write_bytes(b"x")
        assert not cuda_ready(tmp_path)

    def test_only_cublas_is_downloaded(self) -> None:
        """cuDNN проверенно не нужен — это полтора гигабайта разницы."""
        projects = {p.project for p in CUDA_PACKAGES}
        assert projects == {"nvidia-cublas-cu12"}


class TestExtraction:
    def test_files_are_taken_by_name_not_by_path(self, tmp_path: Path) -> None:
        """Раскладка внутри пакета у разных версий разная, имя файла — нет."""
        archive = tmp_path / "package.whl"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("nvidia/cublas/bin/lib.dll", b"content")

        written = _extract(archive, ("lib.dll",), tmp_path)
        assert written == [tmp_path / "lib.dll"]
        assert (tmp_path / "lib.dll").read_bytes() == b"content"

    def test_path_from_the_archive_is_ignored(self, tmp_path: Path) -> None:
        """Иначе архив с именем «../../evil.dll» писал бы куда захочет."""
        archive = tmp_path / "package.whl"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../../../lib.dll", b"content")

        _extract(archive, ("lib.dll",), tmp_path)
        assert (tmp_path / "lib.dll").is_file()

    def test_missing_file_is_reported(self, tmp_path: Path) -> None:
        archive = tmp_path / "package.whl"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("readme.txt", b"nothing here")

        from sfstudio.services.asr.base import RecognitionError

        with pytest.raises(RecognitionError, match=r"lib\.dll"):
            _extract(archive, ("lib.dll",), tmp_path)


class TestPluginFolderSetting:
    def test_plugins_live_under_the_data_root(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        settings.set("storage.mode", "custom")
        settings.set("storage.folder", str(tmp_path))
        assert plugins_dir(settings).parent == tmp_path

    def test_manifest_of_the_bundled_example_is_valid(self) -> None:
        """Пример в поставке обязан грузиться: по нему пишут свои плагины."""
        from sfstudio.plugins import PluginInfo

        root = Path(__file__).resolve().parents[2] / "examples" / "plugins"
        if not root.is_dir():
            pytest.skip("примеры не поставляются")
        for folder in root.iterdir():
            manifest = folder / "plugin.json"
            if manifest.is_file():
                info = PluginInfo.from_dict(
                    json.loads(manifest.read_text(encoding="utf-8")), folder
                )
                assert info.compatible, folder.name
                assert (folder / info.entry).is_file()


class TestWhisperCppSetup:
    """Загрузка второго движка: он ставится одним архивом, без пакетов."""

    def test_builds_are_described(self) -> None:
        from sfstudio.services.accel.binaries import WHISPER_CPP_BUILDS

        assert WHISPER_CPP_BUILDS
        for build in WHISPER_CPP_BUILDS:
            assert build.title and build.asset and build.size_mb > 0

    def test_cpu_build_is_the_smallest(self) -> None:
        """По умолчанию предлагаем не самую тяжёлую: cuBLAS весит 640 МБ."""
        from sfstudio.services.accel.binaries import WHISPER_CPP_BUILDS

        cpu = next(b for b in WHISPER_CPP_BUILDS if b.key == "cpu")
        assert cpu.size_mb == min(b.size_mb for b in WHISPER_CPP_BUILDS)

    def test_nothing_installed_in_an_empty_folder(self, tmp_path: Path) -> None:
        from sfstudio.services.accel.binaries import installed_whisper_cpp

        assert installed_whisper_cpp(tmp_path) is None
        assert installed_whisper_cpp(None) is None

    def test_binary_is_found_in_a_subfolder(self, tmp_path: Path) -> None:
        """Раскладка архива меняется от выпуска к выпуску — ищем вглубь."""
        from sfstudio.services.accel.binaries import installed_whisper_cpp, whisper_cpp_dir

        nested = whisper_cpp_dir(tmp_path) / "Release"
        nested.mkdir(parents=True)
        (nested / "whisper-cli.exe").write_bytes(b"x")
        assert installed_whisper_cpp(tmp_path) is not None

    def test_unpack_refuses_paths_outside_the_folder(self, tmp_path: Path) -> None:
        """Имена в архиве приходят из сети: «../../» записал бы куда угодно."""
        from sfstudio.services.accel.binaries import _unpack

        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../../сбежал.txt", b"escaped")
            zf.writestr("свой.txt", b"ok")

        target = tmp_path / "куда"
        target.mkdir()
        _unpack(archive, target)
        assert (target / "свой.txt").is_file()
        assert not (tmp_path.parent / "сбежал.txt").exists()


class TestGgmlModels:
    """Модели для whisper.cpp — те же веса в другом формате."""

    def test_catalog_reuses_the_common_one(self) -> None:
        from sfstudio.services.asr.models import ggml_models

        names = {info.name for info in ggml_models()}
        assert "small" in names and "large-v3-turbo" in names

    def test_english_only_model_is_absent(self) -> None:
        """Для whisper.cpp дистиллированной версии не публикуют."""
        from sfstudio.services.asr.models import GGML_FILES

        assert "distil-large-v3" not in GGML_FILES

    def test_installed_detects_the_file(self, tmp_path: Path) -> None:
        from sfstudio.services.asr.models import installed_ggml

        (tmp_path / "ggml-small.bin").write_bytes(b"x")
        assert installed_ggml(tmp_path) == {"small"}

    def test_unknown_model_is_refused(self, tmp_path: Path) -> None:
        from sfstudio.services.asr.base import RecognitionError
        from sfstudio.services.asr.models import download_ggml_model

        with pytest.raises(RecognitionError, match=r"whisper\.cpp"):
            download_ggml_model("такой-нет", tmp_path)


class TestWhisperCppEngine:
    """Движок обязан находить то, что программа для него скачала."""

    def test_downloaded_binary_is_found(self, tmp_path: Path) -> None:
        from sfstudio.services.accel.binaries import whisper_cpp_dir
        from sfstudio.services.asr.engines.whisper_cpp import WhisperCppEngine

        folder = whisper_cpp_dir(tmp_path)
        folder.mkdir(parents=True)
        (folder / "whisper-cli.exe").write_bytes(b"x")

        engine = WhisperCppEngine(downloads=tmp_path)
        assert engine.binary() is not None

    def test_explicit_path_wins(self, tmp_path: Path) -> None:
        """Свою сборку — например с Vulkan — подменять нашей нельзя."""
        from sfstudio.services.accel.binaries import whisper_cpp_dir
        from sfstudio.services.asr.engines.whisper_cpp import WhisperCppEngine

        downloaded = whisper_cpp_dir(tmp_path)
        downloaded.mkdir(parents=True)
        (downloaded / "whisper-cli.exe").write_bytes(b"x")
        mine = tmp_path / "своя" / "whisper-cli.exe"
        mine.parent.mkdir()
        mine.write_bytes(b"x")

        engine = WhisperCppEngine(binary=mine, downloads=tmp_path)
        assert engine.binary() == mine

    def test_model_name_is_accepted_instead_of_a_filename(self, tmp_path: Path) -> None:
        """Человек выбирает «small» в общем списке, а не помнит имя файла."""
        from sfstudio.services.asr.base import RecognitionRequest
        from sfstudio.services.asr.engines.whisper_cpp import WhisperCppEngine

        (tmp_path / "ggml-small.bin").write_bytes(b"x")
        request = RecognitionRequest(media=Path("x"), model="small", models_dir=tmp_path)
        found = WhisperCppEngine()._model_file(request)
        assert found.name == "ggml-small.bin"

    def test_missing_model_says_what_to_do(self, tmp_path: Path) -> None:
        from sfstudio.services.asr.base import RecognitionError, RecognitionRequest
        from sfstudio.services.asr.engines.whisper_cpp import WhisperCppEngine

        request = RecognitionRequest(media=Path("x"), model="small", models_dir=tmp_path)
        with pytest.raises(RecognitionError, match="Скачайте"):
            WhisperCppEngine()._model_file(request)
