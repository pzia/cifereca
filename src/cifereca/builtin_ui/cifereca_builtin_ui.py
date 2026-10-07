"""Compatibility entrypoint for the built-in Core UI package.

Concrete implementations live in focused modules.  The public plugin entrypoint
names remain stable for installed manifests and existing runtimes.
"""

from cifereca.builtin_ui.chrome import Titlebar, Widgets
from cifereca.builtin_ui.maintenance import CacheMaintenance
from cifereca.builtin_ui.pages import Pages
from cifereca.builtin_ui.system_page import SystemPages

__all__ = ["Pages", "Titlebar", "Widgets", "SystemPages", "CacheMaintenance"]
