"""P0 #2628 — Browser Recorder Python dependency stays Core-owned and opt-in."""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.plugins.manifest import load_manifest
from cifereca.plugins.manager import PluginManager
from cifereca.plugins.packages import PackageStore
from cifereca.plugins.state import PluginStateStore


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src/plugins/browser-recorder/cifereca-plugin.json"
PAGE = ROOT / "src/cifereca/gui/plugin_manager_page.py"


def test_recorder_declares_python_distribution_instead_of_installing_at_import():
    plugin = load_manifest(MANIFEST)
    assert plugin.package.version == "0.1.3"
    assert all(p.version == plugin.package.version for p in plugin.plugins)
    row = plugin.plugin("ui.page.browser-recorder")
    assert row.enabled_by_default is False
    assert len(row.system_requires) == 1
    req = row.system_requires[0]
    assert (req.type, req.name, req.version, req.import_name) == (
        "python-package", "playwright", ">=1.50,<2", "playwright",
    )


class FakeDeps:
    def __init__(self):
        self.calls = 0
        self.commits = 0

    def ensure(self, manifests, *, package_roots=()):
        self.calls += 1
        owner = self

        class Transaction:
            def commit(self):
                owner.commits += 1

        return Transaction()

    def diagnostic(self, manifests):
        return [{"distribution": "playwright", "ok": True}]


def test_core_repair_can_defer_activation_until_gtk_main_thread(tmp_path, monkeypatch):
    deps = FakeDeps()
    manager = PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "state.json"),
        python_dependencies=deps,
    )
    activated = []
    monkeypatch.setattr(manager, "activate", lambda: activated.append(True))
    assert manager.ensure_python_dependencies(activate=False) == [
        {"distribution": "playwright", "ok": True},
    ]
    assert deps.calls == 1 and deps.commits == 1
    assert activated == []  # no plugin import from the worker
    manager.ensure_python_dependencies()
    assert deps.calls == 2 and deps.commits == 2
    assert activated == [True]  # existing CLI semantics stay unchanged


def test_coreui_installer_is_confirmed_background_core_action_not_plugin_pip():
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(x for x in tree.body
               if isinstance(x, ast.ClassDef) and x.name == "PluginManagerPage")
    methods = {m.name: ast.unparse(m) for m in cls.body
               if isinstance(m, ast.FunctionDef)}
    confirm = methods["_confirm_playwright_python_repair"]
    worker = methods["_repair_playwright_python"]
    assert "MessageDialog.new" in confirm
    assert 'response == \'install\'' in confirm
    assert "ensure_python_dependencies(activate=False)" in worker
    assert "Thread(" in worker and "GLib.idle_add" in worker
    assert "self.manager.activate()" in worker
    assert "subprocess" not in worker
    assert "pip.install" not in worker
