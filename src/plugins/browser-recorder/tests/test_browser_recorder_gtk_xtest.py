"""#2531: real X11/XTEST keyboard on the actual Browser Recorder GTK page.

No localhost provider, login, web navigation, browser launch or captured ZIP.
A deterministic Xvfb server and xdotool drive only disposable GTK controls.
This is NOT a test of a distributed package or a human usability review.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from browser_recorder_page import BrowserRecorderPage


def _flush():
    context = GLib.MainContext.default()
    for _ in range(80):
        if not context.pending():
            break
        context.iteration(False)


def _xtest(*args):
    result = subprocess.run(
        ["xdotool", *args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=8, check=False, text=True,
    )
    assert result.returncode == 0, "XTEST action unavailable"
    return result.stdout.strip()


@pytest.mark.parametrize("width", (1280, 1366))
def test_real_keyboard_tab_and_consent_without_browser_launch(tmp_path, width):
    """Native keyboard follows the primary flow, never folded legacy HAR."""
    assert shutil.which("xdotool"), "GTK CI must install XTEST/xdotool"
    Gtk.init()
    submissions = []

    class Tasks:
        def submit(self, *a, **kw):
            submissions.append(kw)
            raise AssertionError("keyboard focus must not start a capture")

    plugin = BrowserRecorderPage()
    plugin.activate(SimpleNamespace(
        broker=lambda name: Tasks() if name == "tasks" else None,
        data_directory=tmp_path,
    ))
    window = Gtk.Window()
    window.set_title(f"Cifereca XTEST synthetic {width}")
    window.set_default_size(width, 850)
    box = plugin.build(None)
    window.set_child(box)
    window.present()
    _flush()
    try:
        # XTest injects into an actual X11 top-level window, not a direct
        # GTK signal or a mocked button callback.
        hits = _xtest(
            "search", "--onlyvisible", "--name",
            f"Cifereca XTEST synthetic {width}",
        ).splitlines()
        assert len(hits) == 1
        _xtest("windowfocus", "--sync", hits[0])
        _flush()

        site = box.get_first_child().get_next_sibling()
        assert isinstance(site, Gtk.Entry)
        assert site.grab_focus()
        _flush()
        _xtest("type", "--clearmodifiers", "--delay", "3", "synthetic-example")
        _flush()
        assert site.get_text() == "synthetic-example"

        # Tab: site input -> affirmative consent checkbox. Toggle it
        # using a real XTest Space key; no start/capture has been requested.
        _xtest("key", "--clearmodifiers", "Tab")
        _flush()
        consent = site.get_next_sibling()
        focused = window.get_focus()
        assert focused is consent or focused.is_ancestor(consent)
        assert not consent.get_active()
        _xtest("key", "--clearmodifiers", "space")
        _flush()
        assert consent.get_active()

        advanced = box.get_last_child()
        assert isinstance(advanced, Gtk.Expander)
        assert not advanced.get_expanded()
        assert not advanced.get_child().get_mapped()
        private_start = plugin.private_panel.start
        for _ in range(8):
            _xtest("key", "--clearmodifiers", "Tab")
            _flush()
            focus = window.get_focus()
            if focus is private_start or focus.is_ancestor(private_start):
                break
        else:
            pytest.fail("private HTTP start not reachable by real keyboard")
        assert private_start.get_sensitive()
        assert not plugin.private_panel.export_button.get_sensitive()
        assert not submissions
    finally:
        plugin.deactivate()
        window.close()
        _flush()
