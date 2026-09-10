# -*- mode: python ; coding: utf-8 -*-
"""Сборка одним файлом.

Запуск:  pyinstaller --clean --noconfirm build/onefile.spec

Про размер. Нативная часть — 134 МБ, из них 120 МБ приходится на libmpv-2.dll
(сборка shinchiro статически линкует в себя весь FFmpeg со всеми кодеками).
В режиме onefile всё это при каждом запуске распаковывается во временный
каталог, поэтому старт заметно дольше, чем у обычной установки. Это цена
одного файла, а не недоработка сборки: разложенный каталог (onedir)
стартует практически мгновенно.

Про исключения. Список ``excludes`` вычищает то, что PySide6 тянет по
умолчанию: веб-движок, 3D, мультимедиа, графы состояний. Каждый пункт —
десятки мегабайт, а приложению нужны только QtCore, QtGui, QtWidgets и
QtOpenGLWidgets (последний тянет QtOpenGL сам).
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent
SRC = ROOT / "src"
NATIVE = SRC / "sfstudio" / "_native"

if not NATIVE.is_dir():
    raise SystemExit(
        f"нет нативных библиотек в {NATIVE}\n"
        "разложите их: python build/vendor_libs.py"
    )

# Нативные библиотеки кладём тем же путём, что и в установленном пакете:
# sfstudio/_native/<платформа>/. Тогда NativeLibrary.vendor_dir(), который
# считает путь от __file__, находит их и в замороженном виде без правок.
datas = []
for item in NATIVE.rglob("*"):
    if item.is_file():
        target = Path("sfstudio") / item.relative_to(SRC / "sfstudio").parent
        datas.append((str(item), str(target)))

# Каталоги перевода. PyInstaller собирает только .py, а переводы лежат в
# JSON рядом с кодом — без этой строки собранная программа знала бы один
# язык, хотя файлы перевода в исходниках есть.
for _catalog in (SRC / "sfstudio" / "locale").glob("*.json"):
    datas.append((str(_catalog), "sfstudio/locale"))

# Данные пакетов, которые PyInstaller сам не забирает. faster-whisper держит
# рядом с кодом модель определения речи (silero_vad_v6.onnx, около 1,2 МБ), и
# без неё распознавание падает с «File doesn't exist» — код-то собрался, а
# файл рядом с ним нет. Собираем такие файлы явно.
# spylls держит рядом словари русского и английского (около 6 МБ) — без них
# проверка орфографии в собранной программе молча не работает.
for _package in ("faster_whisper", "ctranslate2", "onnxruntime", "tokenizers",
                 "spylls"):
    try:
        datas += collect_data_files(_package)
    except Exception:
        # Пакет не установлен — движок просто не попадёт в сборку, и окно
        # распознавания честно скажет об этом.
        pass

# Qt-модули, которых в коде нет. Проверено: приложение импортирует только
# QtCore, QtGui, QtWidgets и QtOpenGLWidgets.
QT_UNUSED = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtLocation",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtNfc",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPositioning",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtSerialBus",
    "PySide6.QtSerialPort", "PySide6.QtSpatialAudio", "PySide6.QtSql",
    "PySide6.QtStateMachine", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
    "PySide6.QtTest", "PySide6.QtTextToSpeech", "PySide6.QtUiTools",
    "PySide6.QtWebChannel", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets", "PySide6.QtXml",
    "PySide6.QtHttpServer", "PySide6.QtGraphs", "PySide6.QtGraphsWidgets",
    "PySide6.QtNetworkAuth", "PySide6.QtVirtualKeyboard",
]

# Инструменты разработки в бинарнике не нужны.
DEV_UNUSED = [
    "pytest", "_pytest", "pluggy", "hypothesis", "ruff",
    "setuptools", "pip", "wheel", "pkg_resources",
    "tkinter", "matplotlib", "scipy", "pandas", "PIL", "IPython",
    "sqlite3", "pydoc_data", "lib2to3", "shiboken6_generator",
]

a = Analysis(
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    # mpv импортируется лениво и по строке — статический анализ его находит,
    # но перечисляем явно, чтобы сборка не молчала, если импорт переедет.
    hiddenimports=["mpv", "sfstudio.ui.app"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=QT_UNUSED + DEV_UNUSED,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SubtitleForgeStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # Без консоли: окно чёрного терминала рядом с приложением пользователю не
    # нужно. Диагностика при этом не теряется — entry.py уводит stdout/stderr
    # и сообщения Qt в файл журнала (каталог настроек, sfstudio.log), а путь к
    # нему печатает --version.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
