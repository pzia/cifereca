from __future__ import annotations

import importlib
import json
from pathlib import Path

from cifereca.builtin_ui.chrome import Titlebar, Widgets
from cifereca.builtin_ui.maintenance import CacheMaintenance
from cifereca.builtin_ui.pages import Pages
from cifereca.builtin_ui.system_page import SystemPages


def test_builtin_ui_compatibility_entrypoints_are_stable():
    legacy = importlib.import_module("cifereca.builtin_ui.cifereca_builtin_ui")
    assert legacy.Pages is Pages
    assert legacy.Titlebar is Titlebar
    assert legacy.Widgets is Widgets
    assert legacy.SystemPages is SystemPages
    assert legacy.CacheMaintenance is CacheMaintenance


def test_builtin_ui_manifest_entrypoints_resolve_to_compatibility_shim():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "src/cifereca/builtin_ui/cifereca-plugin.json").read_text(encoding="utf-8")
    )
    expected = {
        "core.ui.pages": Pages,
        "core.ui.titlebar": Titlebar,
        "core.ui.widgets": Widgets,
        "core.ui.system": SystemPages,
        "core.maintenance.cache": CacheMaintenance,
    }
    legacy = importlib.import_module("cifereca.builtin_ui.cifereca_builtin_ui")
    for plugin in manifest["plugins"]:
        target = expected[plugin["id"]]
        module_name, object_name = plugin["entrypoint"].split(":", 1)
        assert module_name == "cifereca_builtin_ui"
        assert getattr(legacy, object_name) is target


def test_builtin_pages_keep_routes_groups_and_library_action():
    rows = Pages().pages()
    assert [row.route for row in rows] == [
        "dashboard",
        "search",
        "library",
        "recent",
        "loans",
        "history",
        "bookings",
        "sync",
        "tools",
        "plugins",
        "settings",
    ]
    library = next(row for row in rows if row.route == "library")
    assert [action.id for action in library.actions] == ["import-acsm"]
    assert library.group == "usage"
    assert next(row for row in rows if row.route == "settings").group == "configuration"


def test_cache_maintenance_remains_broker_driven():
    class Cache:
        def namespaces(self):
            return ()
        def purge(self):
            return None

    class Context:
        def broker(self, name):
            assert name == "cache"
            return Cache()

    tool = CacheMaintenance().activate(Context())
    info = tool.describe()
    plan = tool.plan(None)
    assert info.id == "core.cache.cleanup"
    assert plan.tool_id == "core.cache.cleanup"
    assert plan.facts["entries"] == 0
