"""P0 #2628 — explicit Python-only Browser Recorder dependency repair.

No pip, Playwright import, Chromium download, GTK launch or external network
is performed by these regression tests.
"""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.plugins.manager import PluginManager
from cifereca.plugins.manifest import load_manifest
from tests.test_core037_python_dependencies import _FakeDeps, _manager


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "src/plugins/browser-recorder/cifereca-plugin.json"
GUI = ROOT / "src/cifereca/gui/plugin_manager_page.py"


def test_browser_recorder_manifest_declares_playwright_and_vault_crypto():
    manifest = load_manifest(PLUGIN)
    assert manifest.package.id == "org.cifereca.technical.browser-recorder"
    assert manifest.package.version == "0.2.22"
    descriptor, = manifest.plugins
    assert descriptor.enabled_by_default is False
    requirements = {
        r.name: (r.type, r.version, r.import_name)
        for r in descriptor.system_requires
    }
    assert requirements == {
        "playwright": ("python-package", ">=1.50,<2", "playwright"),
        "cryptography": ("python-package", ">=42,<48", "cryptography"),
    }
    assert not any(r.type == "binary" for r in descriptor.system_requires)


def test_core_dependency_repair_can_defer_activation_to_gtk_thread(tmp_path, monkeypatch):
    deps = _FakeDeps(tmp_path / "resolved")
    manager = _manager(tmp_path, deps)
    activations = []
    monkeypatch.setattr(manager, "activate", lambda: activations.append(True))
    try:
        result = manager.ensure_python_dependencies(activate=False)
        assert result == []
        assert deps.calls and deps.txn.committed
        assert not activations
        manager.ensure_python_dependencies()
        assert activations == [True]
    finally:
        manager.close()


def test_plugins_ui_repair_requires_confirmation_and_does_not_spawn_pip_or_browser():
    source = GUI.read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "PluginManagerPage")
    members = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert {
        "_confirm_python_dependencies", "_install_python_dependencies",
        "_finish_python_dependencies",
    } <= members.keys()
    dialog = members["_confirm_python_dependencies"]
    install = members["_install_python_dependencies"]
    complete = members["_finish_python_dependencies"]
    # Only an affirmative dialog response can reach installation.
    assert "response == 'install'" in ast.unparse(dialog).replace('"', "'")
    # Installation happens via the Core-owned manager and does not cause any
    # GTK plugin import/activation in the worker context.
    assert "self.manager.ensure_python_dependencies(activate=False)" in ast.unparse(install)
    assert "GLib.idle_add(self._finish_python_dependencies, success)" in ast.unparse(install)
    assert "self.manager.activate()" not in ast.unparse(install)
    assert "self.manager.activate()" in ast.unparse(complete)
    assert "subprocess" not in ast.unparse(install)
    assert "playwright install" not in ast.unparse(install)


def test_plugin_activation_is_passive_when_playwright_not_installed():
    manifest = load_manifest(PLUGIN)
    assert manifest.plugins[0].enabled_by_default is False
    # One manifest requirement is *declarative*. PythonDependencyManager
    # mutates the venv only in explicit manager.install / repair, never when
    # a page is rendered or plugin discovery scans descriptors.
    assert PluginManager.ensure_python_dependencies.__defaults__ is None
