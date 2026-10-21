"""#2628: a REAL Plugins GTK page installs official Chromium on first use.

Fresh Playwright Python venv, empty Chromium cache, native GTK4/libadwaita,
real installed Browser Recorder package and Core PluginManager. No vendor,
account, capture or human files. Network is allowed ONLY after the actual
confirmation-dialog response is generated in GTK. CI runner opt-in required.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from cifereca.gui.plugin_manager_page import PluginManagerPage
from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.plugins.configuration import PluginConfigStore
from cifereca.plugins.playwright_browser import inspect_chromium
from scripts.plugin_packaging import build_plugin_zip, validate_plugin_zip

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/plugins/browser-recorder"
PLUGIN_ID = "ui.page.browser-recorder"

pytestmark = pytest.mark.skipif(
    os.environ.get("CIFERECA_REAL_CHROMIUM_PROVISION_E2E") != "1",
    reason="fresh GUI download explicitly opted in only on CI",
)


def flush():
    main = GLib.MainContext.default()
    # Bound even when background workers are running; idle sources may remain.
    for _ in range(150):
        if not main.pending():
            break
        main.iteration(False)


def eventually(fn, seconds=80):
    stop = monotonic() + seconds
    while monotonic() < stop:
        flush()
        if fn():
            return True
        sleep(.05)
    return False


def descendants(root):
    yield root
    child = root.get_first_child()
    while child is not None:
        yield from descendants(child)
        child = child.get_next_sibling()


def button_named(root, label):
    buttons = [widget for widget in descendants(root)
               if isinstance(widget, Gtk.Button) and widget.get_label() == label]
    assert len(buttons) == 1, f"expected exactly one button: {label}"
    return buttons[0]


def visible_dialog():
    model = Gtk.Window.get_toplevels()
    dialogs = [
        win for idx in range(model.get_n_items())
        if isinstance((win := model.get_item(idx)), Adw.MessageDialog)
        and win.get_visible()
    ]
    assert len(dialogs) <= 1
    return dialogs[0] if dialogs else None


class FakeWindow(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.messages = []
        self.service = type("Service", (), {"identities": ()})()
        self._closed = False

    def toast(self, message, timeout=4):
        self.messages.append((message, timeout))

    def show_error(self, message, title):
        self.messages.append((title, message))


def test_real_plugin_page_dialog_consent_then_core_download(
    monkeypatch, tmp_path,
):
    assert sys.platform.startswith("linux") and sys.prefix != sys.base_prefix
    assert Path.home().name == "empty-ui-home"
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
    cache = Path.home() / ".cache" / "ms-playwright"
    assert not cache.exists()
    assert inspect_chromium().code == "chromium_missing"
    Gtk.init()
    Adw.init()

    monkeypatch.setattr("cifereca.plugins.manager.data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr("cifereca.plugins.manager.cache_dir", lambda: tmp_path / "cache")
    package_path = build_plugin_zip(SOURCE, tmp_path)
    verified = validate_plugin_zip(package_path)
    assert verified["package"]["id"] == "org.cifereca.technical.browser-recorder"
    manager = PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "state.json"),
        config_store=PluginConfigStore(tmp_path / "config.json"),
        brokers={},
    )
    window = FakeWindow()
    window.set_title("Cifereca Browser Recorder Plugins fixture")
    window.set_default_size(1366, 850)
    try:
        manager.installer.install(package_path, activate=True)
        manager.activate()
        status = manager.status(PLUGIN_ID)
        assert not status.enabled
        # This is the genuine initial state: disabled plugins are intentionally
        # not runtime-probed by PluginManager (requirements is empty).
        assert status.requirements == ()
        page = PluginManagerPage(window, manager)
        window.set_child(page)
        window.present()
        flush()
        assert PLUGIN_ID in page._plugin_widgets
        installer = button_named(page._plugin_widgets[PLUGIN_ID], "Installer Chromium…")
        assert installer.get_sensitive()
        # Simulate an absent Python wheel without a subprocess or network:
        # the disabled plugin must offer the Core repair action first.
        from cifereca.plugins import playwright_browser
        with monkeypatch.context() as missing_python:
            missing_python.setattr(playwright_browser, "_version", lambda: None)
            page.refresh()
            row = page._plugin_widgets[PLUGIN_ID]
            assert button_named(row, "Installer les dépendances Python…").get_sensitive()
            assert not any(
                isinstance(widget, Gtk.Button) and widget.get_label() == "Installer Chromium…"
                for widget in descendants(row)
            )
            assert not cache.exists()
        page.refresh()
        installer = button_named(page._plugin_widgets[PLUGIN_ID], "Installer Chromium…")
        assert installer.get_sensitive()
        assert not cache.exists()
        assert not page._chromium_operation_running

        # Initial button presents the REAL Adw.MessageDialog, but no download.
        installer.emit("clicked")
        assert eventually(lambda: visible_dialog() is not None)
        dialog = visible_dialog()
        assert "Chromium" in dialog.get_heading()
        assert not cache.exists()
        assert not page._chromium_operation_running
        dialog.response("cancel")
        assert eventually(lambda: visible_dialog() is None)
        assert not cache.exists()
        assert not page._chromium_operation_running

        # Second real UI invocation and explicit confirmation.
        button_named(page._plugin_widgets[PLUGIN_ID], "Installer Chromium…").emit("clicked")
        assert eventually(lambda: visible_dialog() is not None)
        visible_dialog().response("install")
        assert eventually(lambda: page._chromium_operation_running)
        assert eventually(lambda: "Annuler Chromium" in [
            b.get_label() for b in descendants(page._plugin_widgets[PLUGIN_ID])
            if isinstance(b, Gtk.Button)
        ])
        assert button_named(page._plugin_widgets[PLUGIN_ID], "Vérifier Chromium…").get_sensitive() is False
        assert eventually(lambda: not page._chromium_operation_running, seconds=600)
        result = visible_dialog()
        assert result is not None
        assert result.get_heading() == "État Chromium"
        assert "prêt" in result.get_body().lower()
        result.response("close")
        assert eventually(lambda: visible_dialog() is None)
        assert inspect_chromium(test_launch=True).code == "ready"

        # A fresh render must not trigger another download or leave busy state.
        page.refresh()
        flush()
        assert not page._chromium_operation_running
        assert button_named(page._plugin_widgets[PLUGIN_ID], "Installer Chromium…").get_sensitive()
        assert not any("traceback" in str(item).lower() for item in window.messages)
    finally:
        window._closed = True
        window.close()
        manager.close()
        flush()
