"""#3649: real GTK4 Diagnostics page, native chooser, real daily ZIP writer.

Uses an isolated synthetic canonical journal and no provider or private data.
Run with Xvfb/GTK4; skip only when a graphical runtime is unavailable.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import stat
import sys
from time import monotonic, sleep
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from zipfile import ZipFile

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
except ValueError:
    pytest.skip("GTK4 unavailable", allow_module_level=True)
from gi.repository import Gtk, Gio, GLib

pytestmark = pytest.mark.skipif(not os.environ.get("DISPLAY"),
                                reason="real GTK display required")

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "e2e"))
from diagnostics_page_probe import (  # noqa: E402
    Context, Host, ImmediateHandle, SelectionBroker, SelectionTasks,
    walk, reveal_diagnostics_section,
)
import cifereca_diagnostics_page as page_module  # noqa: E402
from cifereca.core.diagnostic_journal_zip import export_daily_journal_zip  # noqa: E402


def spin_until(condition, seconds=8):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        while GLib.MainContext.default().iteration(False):
            pass
        if condition():
            return
        sleep(.01)
    raise AssertionError("GTK event did not complete")


def test_real_gtk_daily_journal_zip_cancel_then_accept(tmp_path, monkeypatch):
    Gtk.init()
    monkeypatch.setattr(page_module, "_readable_raw_export_folder",
                        lambda: str(tmp_path))
    today = datetime.now(ZoneInfo("Europe/Paris"))
    older = today.date().replace(day=1)
    active = tmp_path / "cifereca.log"
    active.write_text(
        f"{older} 12:00:00,000 INFO cifereca [Worker] old-event\n"
        f"{today.date()} 12:00:00,000 ERROR cifereca [Worker] "
        "today-private-token=FAKE\n",
        encoding="utf-8",
    )

    class Broker(SelectionBroker):
        exports = 0
        def export_today_journal_zip(self, destination, *, cancelled=None):
            result = export_daily_journal_zip(
                active, destination, now=today,
                zone_name="Europe/Paris", cancelled=cancelled,
            )
            self.exports += 1
            return str(result)

    class Tasks(SelectionTasks):
        def submit(self, fn, *, label, kind):
            if label == "Export ZIP journal du jour":
                self.calls.append((label, kind))
                return ImmediateHandle(fn(SimpleNamespace(
                    cancelled=lambda: False,
                )))
            return super().submit(fn, label=label, kind=kind)

    broker, tasks, host = Broker(), Tasks(), Host()
    plugin = page_module.DiagnosticsPagePlugin().activate(Context(broker, tasks))
    window = Gtk.Window()
    window.set_default_size(1250, 780)
    host.window = window
    try:
        page = plugin._build(host)
        window.set_child(page)
        window.present()
        spin_until(lambda: page.get_mapped())
        button = next(
            widget for widget in walk(page)
            if isinstance(widget, Gtk.Button)
            and widget.get_label() == "Exporter le journal brut du jour (ZIP)…"
        )
        controller = plugin._daily_journal_export
        assert button.get_sensitive()
        reveal_diagnostics_section(button)
        button.emit("clicked")
        assert controller.chooser is None and broker.exports == 0
        button.emit("clicked")
        chooser = controller.chooser
        assert isinstance(chooser, Gtk.FileChooserNative)
        assert chooser.get_transient_for() is window and chooser.get_modal()
        chooser.emit("response", Gtk.ResponseType.CANCEL)
        assert controller.chooser is None and broker.exports == 0
        assert not list(tmp_path.glob("*.zip"))

        button.emit("clicked")
        button.emit("clicked")
        chooser = controller.chooser
        target = tmp_path / "cifereca-journal-brut-gtk.zip"
        chooser.set_current_name(target.name)
        spin_until(lambda: chooser.get_file() is not None
                   and chooser.get_file().get_path() == str(target))
        chooser.emit("response", Gtk.ResponseType.ACCEPT)
        spin_until(target.exists)
        assert broker.exports == 1
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        with ZipFile(target) as archive:
            assert archive.testzip() is None
            meta = json.loads(archive.read("manifest.json"))
            raw = json.loads(archive.read("cifereca-journal-brut.json"))
            assert meta["event_count"] == 1
            assert raw["events"][0]["message"] == "today-private-token=FAKE"
            assert meta["date_local"] == today.date().isoformat()
    finally:
        plugin.deactivate()
        window.destroy()
