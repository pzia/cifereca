"""P0 #2531: installed Browser Recorder ZIP → PluginManager → real GTK route.

Unlike source-tree UI tests, no Browser Recorder Python module is imported
or put on PYTHONPATH before Core activation. All test resources are synthetic.
A real Playwright *Python distribution* is required in the isolated GTK test
venv; no Chromium binary, HTTP capture or download is used in this case.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import zipfile

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.plugins.configuration import PluginConfigStore


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/plugins/browser-recorder"
PACKAGE_ID = "org.cifereca.technical.browser-recorder"
PLUGIN_ID = "ui.page.browser-recorder"


def _flush():
    main = GLib.MainContext.default()
    for _ in range(80):
        if not main.pending():
            return
        main.iteration(False)


def _children(widget):
    result = []
    child = widget.get_first_child()
    while child is not None:
        result.append(child)
        child = child.get_next_sibling()
    return result


def _real_package_zip(path):
    """Recreate the distributable code ZIP, without source-test imports."""
    sources = list(SOURCE.glob("*.py"))
    assert len(sources) >= 5
    assert (SOURCE / "cifereca-plugin.json").is_file()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for member in sorted([*sources, SOURCE / "cifereca-plugin.json", SOURCE / "README.md"]):
            bundle.write(member, member.name)
    with zipfile.ZipFile(path) as bundle:
        assert "browser_recorder_page.py" in bundle.namelist()
        assert "browser_recorder_review_ui.py" in bundle.namelist()
        assert "tests/" not in " ".join(bundle.namelist())
    return path


class FailClosedTasks:
    def __init__(self):
        self.submissions = []

    def submit(self, task, **metadata):
        # No capture should occur while opening the installed UI.
        self.submissions.append(metadata)
        raise AssertionError("unexpected browser capture during plugin activation")


@pytest.mark.parametrize("width", (1280, 1366, 1600))
def test_install_zip_activate_via_core_and_open_public_gtk_page(
    monkeypatch, tmp_path, width,
):
    Gtk.init()
    assert "playwright" in {m.name for m in __import__("pkgutil").iter_modules()}
    # In this dedicated test process, the plugin package can ONLY be resolved
    # from PackageStore; no source directory is added as a Python search root.
    assert str(SOURCE.resolve()) not in sys.path
    assert "browser_recorder_page" not in sys.modules
    assert "browser_recorder_review_ui" not in sys.modules
    monkeypatch.setattr("cifereca.plugins.manager.data_dir", lambda: tmp_path / "core-data")
    monkeypatch.setattr("cifereca.plugins.manager.cache_dir", lambda: tmp_path / "core-cache")
    archive = _real_package_zip(tmp_path / "browser-recorder.zip")
    tasks = FailClosedTasks()
    store = PackageStore(tmp_path / "packages")
    manager = PluginManager(
        package_store=store,
        state_store=PluginStateStore(tmp_path / "state.json"),
        config_store=PluginConfigStore(tmp_path / "config.json"),
        brokers={"tasks": tasks},
    )
    window = Gtk.Window()
    window.set_default_size(width, 850)
    try:
        # PackageInstaller exercises archive validation, staging, manifest
        # extraction, version pinning and active pointer. We deliberately
        # avoid PluginManager.install(... activate=True) here: its Core-owned
        # pip resolver is tested elsewhere and may download dependencies.
        package = manager.installer.install(archive, activate=True)
        assert package.root == store.version_root(PACKAGE_ID, "0.1.3")
        assert package.root.is_dir()
        assert package.sha256
        assert manager.packages.active_version(PACKAGE_ID) == "0.1.3"
        manager.activate()
        assert manager.status(PLUGIN_ID).enabled is False
        assert manager.service(PLUGIN_ID) is None
        manager.set_enabled(PLUGIN_ID, True)
        status = manager.status(PLUGIN_ID)
        assert status.enabled and status.state == "ready", (
            status.reason, status.reason_code,
        )

        # Public plugin API; never import browser_recorder_page directly.
        service = manager.service(PLUGIN_ID)
        assert service is not None
        assert "browser_recorder_page" in sys.modules
        assert Path(sys.modules["browser_recorder_page"].__file__).resolve().is_relative_to(
            package.root.resolve()
        )
        contributions = service.pages()
        assert len(contributions) == 1
        page = contributions[0]
        assert page.route == "browser-recorder"
        assert page.title == "Browser Recorder"
        root = page.factory(None)
        window.set_child(root)
        window.present()
        _flush()
        description, site, consent, workflow, private, advanced = _children(root)
        assert isinstance(site, Gtk.Entry)
        assert isinstance(consent, Gtk.CheckButton)
        assert isinstance(advanced, Gtk.Expander)
        assert not advanced.get_expanded()
        assert "HTTP" in description.get_label()
        assert "ZIP" in workflow.get_label()
        assert "HAR" in advanced.get_label()
        assert not advanced.get_child().get_mapped()

        # No manual login, browser launch or ZIP writes on display.
        assert tasks.submissions == []
        assert list((tmp_path / "core-data" / "plugins" / "data"
                     / PLUGIN_ID).glob("exports/*.zip")) == []
        assert site.grab_focus()
        focused = window.get_focus()
        assert focused is site or focused.is_ancestor(site)
        advanced.set_expanded(True)
        _flush()
        assert advanced.get_child().get_mapped()
        assert tasks.submissions == []
    finally:
        window.close()
        manager.close()
        _flush()
        # Cached plugin modules may be detached at teardown; this test runs
        # alone, without the source-tree Browser Recorder test suite.
