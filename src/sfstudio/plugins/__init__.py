"""Плагины: контракт и загрузка.

Смысл — в :mod:`sfstudio.plugins.api`; там же объяснено, почему плагин
общается с программой через контекст, а не напрямую.
"""

from sfstudio.plugins.api import (
    API_VERSION,
    MANIFEST_NAME,
    Plugin,
    PluginAction,
    PluginContext,
    PluginError,
    PluginInfo,
    PluginState,
)
from sfstudio.plugins.loader import LoadedPlugin, PluginManager, discover, load_plugin

__all__ = [
    "API_VERSION",
    "MANIFEST_NAME",
    "LoadedPlugin",
    "Plugin",
    "PluginAction",
    "PluginContext",
    "PluginError",
    "PluginInfo",
    "PluginManager",
    "PluginState",
    "discover",
    "load_plugin",
]
