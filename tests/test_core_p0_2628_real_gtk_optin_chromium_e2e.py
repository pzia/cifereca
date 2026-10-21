"""#2628: a REAL Plugins GTK page installs official Chromium on first use.

Fresh Core venv WITHOUT Playwright, empty Chromium cache, GTK4/libadwaita,
real installed Browser Recorder package and Core PluginManager. No vendor,
account, capture or human files. Network is allowed ONLY after the actual
confirmation button is physically activated with XTEST on the GTK dialog. CI runner opt-in required.
"""
from __future__ import annotations

import os
import shutil
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


def label_of_button(widget):
    # Core action_button embeds Adw.ButtonContent, so Gtk.Button.get_label()
    # is intentionally empty. Its retained accessible label is authoritative.
    return getattr(widget, "_cifereca_action_label", None) or widget.get_label()


def button_named(root, label):
    buttons = [widget for widget in descendants(root)
               if isinstance(widget, Gtk.Button) and label_of_button(widget) == label]
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



def xtest(*args):
    """Dispatch physical XTEST input, never GTK signal emission."""
    assert shutil.which("xdotool"), "XTEST dependency absent on native runner"
    result = subprocess.run(
        ["xdotool", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=8, check=False,
    )
    assert result.returncode == 0, "XTEST keyboard dispatch failed"
    return result.stdout.strip()


def physical_dialog_choice(dialog, label: str):
    """Choose a real libadwaita dialog button with physical X11 keyboard.

    GTK internal response buttons may contain a Gtk.Label child rather than
    setting Gtk.Button.label directly. We inspect the actual widget tree,
    focus the chosen button and send XTEST Space; never call response() or
    emit GTK clicked from the test.
    """
    def text_of_button(button):
        words = [button.get_label() or ""]
        words.extend(widget.get_label()
                     for widget in descendants(button)
                     if isinstance(widget, Gtk.Label))
        return " ".join(words)

    buttons = [node for node in descendants(dialog)
               if isinstance(node, Gtk.Button)
               and label in text_of_button(node)]
    assert len(buttons) == 1, "dialog has no unique keyboard action"
    button = buttons[0]
    assert button.get_sensitive() and button.get_visible()
    # Set a synthetic title solely to select this dialog's real X11 window.
    # Never inspect or record user/browser window contents.
    title = "Cifereca XTEST Chromium confirmation fixture"
    dialog.set_title(title)
    dialog.present()
    flush()
    matches = xtest("search", "--onlyvisible", "--name", title).splitlines()
    assert len(matches) == 1, "expected one physical confirmation window"
    xtest("windowfocus", "--sync", matches[0])
    flush()
    assert button.grab_focus()
    flush()
    sleep(.08)
    flush()
    focused = dialog.get_focus()
    assert focused is button or focused.is_ancestor(button)
    xtest("key", "--clearmodifiers", "space")
    assert eventually(lambda: visible_dialog() is None), (
        "physical confirmation did not dismiss dialog"
    )


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
    assert inspect_chromium().code == "playwright_missing"
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
        # Entire Python prerequisite is absent, not a mock. The default-
        # disabled Browser Recorder must offer Core pip repair anyway.
        repair = button_named(
            page._plugin_widgets[PLUGIN_ID], "Installer les dépendances Python…"
        )
        assert repair.get_sensitive()
        assert not any(
            isinstance(widget, Gtk.Button) and
            label_of_button(widget) == "Installer Chromium…"
            for widget in descendants(page._plugin_widgets[PLUGIN_ID])
        )
        assert not cache.exists()

        # Cancel the actual Python-repair dialog; no package/browser mutation.
        repair.emit("clicked")
        assert eventually(lambda: visible_dialog() is not None)
        dialog = visible_dialog()
        assert "Python" in dialog.get_heading()
        physical_dialog_choice(dialog, "Annuler")
        assert inspect_chromium().code == "playwright_missing"
        assert not cache.exists()

        # Explicit second consent: Core installs declared Python dependencies
        # into the managed venv asynchronously (no GUI-thread pip invocation).
        button_named(
            page._plugin_widgets[PLUGIN_ID], "Installer les dépendances Python…"
        ).emit("clicked")
        assert eventually(lambda: visible_dialog() is not None)
        dialog = visible_dialog()
        physical_dialog_choice(dialog, "Installer")
        assert eventually(lambda: page._python_deps_install_running)
        assert eventually(lambda: not page._python_deps_install_running, seconds=240)
        assert inspect_chromium().code == "chromium_missing", window.messages
        assert not cache.exists()
        assert not manager.status(PLUGIN_ID).enabled

        # Python stage is independently complete; only now does Chromium
        # show up as a second opt-in operation.
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
        # XTEST activates the real libadwaita response button and modal close.
        physical_dialog_choice(dialog, "Annuler")
        assert not cache.exists()
        assert not page._chromium_operation_running

        # Second real UI invocation and explicit confirmation.
        button_named(page._plugin_widgets[PLUGIN_ID], "Installer Chromium…").emit("clicked")
        assert eventually(lambda: visible_dialog() is not None)
        dialog = visible_dialog()
        physical_dialog_choice(dialog, "Installer")
        assert eventually(lambda: page._chromium_operation_running)
        assert eventually(lambda: "Annuler Chromium" in [
            label_of_button(b) for b in descendants(page._plugin_widgets[PLUGIN_ID])
            if isinstance(b, Gtk.Button)
        ])
        assert button_named(page._plugin_widgets[PLUGIN_ID], "Vérifier Chromium…").get_sensitive() is False
        assert eventually(lambda: not page._chromium_operation_running, seconds=600)
        result = visible_dialog()
        assert result is not None
        assert result.get_heading() == "État Chromium"
        assert "prêt" in result.get_body().lower()
        physical_dialog_choice(result, "Fermer")
        assert inspect_chromium(test_launch=True).code == "ready"
        assert not manager.status(PLUGIN_ID).enabled  # no implicit activation

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
