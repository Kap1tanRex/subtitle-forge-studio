"""Проверка правил слоёв.

Архитектура держится не на договорённостях, а на тесте. ``core`` обязан
оставаться чистым Python без Qt и без нативных библиотек — только тогда он
тестируется за доли секунды и не тянет за собой GUI в фоновых задачах.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import sfstudio

SRC = Path(sfstudio.__file__).parent

#: Что каждому слою запрещено импортировать.
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "core": ("sfstudio.io", "sfstudio.media", "sfstudio.render", "sfstudio.ui",
             "PySide6", "av", "numpy", "mpv"),
    "io": ("sfstudio.ui", "sfstudio.render", "PySide6"),
    "render": ("sfstudio.ui",),
    "media": ("sfstudio.ui",),
}


def modules_of(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def imports_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_has_no_forbidden_imports(layer: str) -> None:
    banned = FORBIDDEN[layer]
    violations: list[str] = []

    for path in modules_of(layer):
        for name in imports_in(path):
            for prefix in banned:
                if name == prefix or name.startswith(prefix + "."):
                    violations.append(f"{path.relative_to(SRC)}: import {name}")

    assert not violations, "Нарушены правила слоёв:\n" + "\n".join(violations)


def test_core_imports_without_optional_dependencies() -> None:
    """core должен подниматься на голом интерпретаторе, без PySide6 и PyAV."""
    import importlib
    import sys

    blocked = {"PySide6", "av", "numpy", "mpv"}
    loaded_before = {name for name in sys.modules if name.split(".")[0] in blocked}

    for module in (
        "sfstudio.core.time",
        "sfstudio.core.color",
        "sfstudio.core.tags",
        "sfstudio.core.event",
        "sfstudio.core.style",
        "sfstudio.core.index",
        "sfstudio.core.document",
        "sfstudio.core.undo",
        "sfstudio.core.commands",
    ):
        importlib.import_module(module)

    loaded_after = {name for name in sys.modules if name.split(".")[0] in blocked}
    assert loaded_after == loaded_before, "core подтянул тяжёлую зависимость"


def test_no_blocking_calls_in_ui() -> None:
    """В ui/* запрещены синхронные примитивы ожидания.

    Блокировка главного потока замораживает окно. Проверяются вызовы, а не
    импорты: ``time.sleep`` ловится и как ``time.sleep()``, и как ``sleep()``.
    """
    banned_attrs = {"sleep", "msleep", "usleep", "wait"}
    ui_dir = SRC / "ui"
    if not ui_dir.exists():
        pytest.skip("слой ui ещё не создан")

    violations: list[str] = []
    for path in ui_dir.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in banned_attrs:
                continue
            line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            if "noqa: blocking-in-ui" in line:
                continue
            violations.append(f"{path.relative_to(SRC)}:{node.lineno}: {name}()")

    assert not violations, "Блокирующие вызовы в ui:\n" + "\n".join(violations)
