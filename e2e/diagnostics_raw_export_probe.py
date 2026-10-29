#!/usr/bin/env python3
"""P1 #3218: REAL GTK4 native SAVE picker + local-file export contract.

GTK4/Xvfb fixture: no private journal, supplier, LLM or network. Tests the actual
Diagnostics callback, native file chooser and GIO enumeration, not a mocked
Gtk.FileChooserNative. The final desktop/portal recipe remains separate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from zipfile import ZipFile
from time import monotonic
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src/plugins/diagnostics-page"),
    str(ROOT / "e2e"),
    str(ROOT / "environnements/openai-chat-debian"),
]
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk
from PIL import ImageGrab
from diagnostics_page_probe import (
    Context, Host, ImmediateHandle, SelectionBroker, SelectionTasks,
    reveal_diagnostics_section, walk,
)

# Deliberately fictional, but exact export bytes, including sensitive-shaped
# values, must survive the raw journal path (no UI pseudonyms/transformations).
_PAYLOAD = (b'{"schema":1,"login":"private-example","password":"local-fake","rows":[]}'
            b'\n')


class JournalBroker(SelectionBroker):
    def __init__(self, out):
        super().__init__()
        self.exports = 0
        self.zip_exports = 0
        self.raw_log = out / "synthetic-cifereca.log"
        now = datetime.now()
        yesterday = now - timedelta(days=1)
        self.raw_log.write_text(
            f"{yesterday:%Y-%m-%d %H:%M:%S},000 INFO cifereca [Worker] old-event\n"
            f"{now:%Y-%m-%d %H:%M:%S},000 ERROR cifereca [Worker] today-private-token=SYNTHETIC\n",
            encoding="utf-8",
        )

    def export_raw_journal(self):
        self.exports += 1
        return _PAYLOAD

    def export_today_journal_zip(self, destination, *, cancelled=None):
        from cifereca.core.diagnostic_journal_zip import export_daily_journal_zip
        result = export_daily_journal_zip(self.raw_log, destination,
                                          cancelled=cancelled)
        self.zip_exports += 1
        return str(result)


class JournalTasks(SelectionTasks):
    def submit(self, fn, *, label, kind):
        if label in {"Export local du journal brut", "Export ZIP journal du jour"}:
            self.calls.append((label, kind))
            return ImmediateHandle(fn(SimpleNamespace(cancelled=lambda: False)))
        return super().submit(fn, label=label, kind=kind)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--width", type=int, choices=(1366,), required=True)
    args = ap.parse_args()
    if not os.environ.get("DISPLAY") or os.environ.get("GDK_BACKEND") != "x11":
        raise SystemExit("real GTK4/X11 required")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    module = importlib.import_module("cifereca_diagnostics_page")
    broker, tasks, host = JournalBroker(out), JournalTasks(), Host()
    plugin = module.DiagnosticsPagePlugin().activate(Context(broker, tasks))
    report = {
        "scenario": "diagnostics-raw-export-1366", "status": "RUNNING",
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "checks": {}, "screenshots": [], "backend": "GTK4/X11",
    }
    window = [None]
    page = [None]
    rc = [1]
    target = out / "cifereca-journal-brut.json"

    def save():
        (out / "screen.json").write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def screenshot(name):
        picture = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert picture.size == (1366, 900)
        path = out / (name + ".png")
        picture.save(path)
        (out / "screen.png").write_bytes(path.read_bytes())
        report["screenshots"].append({
            "name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    def fail(reason):
        if report["status"] != "RUNNING":
            return
        # No exception text, file paths or raw journal bytes in CI artifacts.
        report.update(status="FAIL", error_code=reason)
        save()
        app.quit()

    def later(fn, ms=150):
        def run():
            if report["status"] != "RUNNING":
                return False
            try:
                code = getattr(fn, "__code__", None)
                if code is not None and code.co_filename == __file__:
                    report["checkpoint_line"] = code.co_firstlineno
                save()
                fn()
            except Exception as exc:
                trace = exc.__traceback__
                while trace is not None:
                    if trace.tb_frame.f_code.co_filename == __file__:
                        report["failure_line"] = trace.tb_lineno
                    trace = trace.tb_next
                fail(type(exc).__name__)
            return False
        GLib.timeout_add(ms, run)

    def until(test, follow, seconds=8):
        from inspect import currentframe
        frame = currentframe()
        try:
            caller = frame.f_back if frame is not None else None
            if caller is not None and caller.f_code.co_filename == __file__:
                report["wait_line"] = caller.f_lineno
        finally:
            del frame
        deadline = monotonic() + seconds
        def poll():
            if test():
                follow()
            elif monotonic() > deadline:
                raise AssertionError("native-chooser-condition-timeout")
            else:
                later(poll, 110)
        later(poll, 110)

    def button():
        # Historical JSON moved beneath a collapsed advanced expander in
        # #3649. GTK may not construct its descendant widgets until expanded.
        for expander in walk(page[0]):
            if isinstance(expander, Gtk.Expander) and expander.get_label() == "Filtres avancés":
                reveal_diagnostics_section(expander)
                expander.set_expanded(True)
        matches = [widget for widget in walk(page[0])
                   if isinstance(widget, Gtk.Button)
                   and widget.get_label() in {
                       "Exporter tout l’historique (JSON)…",
                       "Confirmer l’export brut non masqué",
                   }]
        assert len(matches) == 1
        return reveal_diagnostics_section(matches[0])

    def press(button, follow):
        # Trigger the exact connected Gtk.Button signal on the real mounted
        # plugin page. Xvfb's lack of a window-manager focus grant must not
        # masquerade as a SAVE/GIO regression. Native chooser is NOT mocked.
        assert button.get_root() is window[0]
        button.emit("clicked")
        later(follow, 180)

    def begin():
        assert page[0].get_root() is window[0]
        assert plugin._raw_export_chooser is None
        press(button(), first_press_done)

    def first_press_done():
        assert plugin._raw_export_chooser is None
        assert "Confirmer l’export brut non masqué" == button().get_label()
        assert broker.exports == 0
        report["checks"]["first_press_only_warns"] = True
        press(button(), second_press_done)

    def second_press_done():
        until(lambda: plugin._raw_export_chooser is not None,
              chooser_opened)

    def chooser_opened():
        chooser = plugin._raw_export_chooser
        assert isinstance(chooser, Gtk.FileChooserNative)
        assert chooser.get_transient_for() is window[0]
        assert chooser.get_modal()
        assert chooser.get_action() == Gtk.FileChooserAction.SAVE
        assert chooser.get_visible()
        report["checks"]["native_parent_modal_and_save"] = True
        until(lambda: chooser.get_current_folder() is not None,
              lambda: folder_verified(chooser))

    def folder_verified(chooser):
        folder = chooser.get_current_folder()
        assert folder is not None and folder.get_path() == str(Path.home())
        # Real GIO enumeration of exactly the directory given to GTK;
        # a simulated chooser would not exercise this filesystem boundary.
        enumerator = folder.enumerate_children(
            "standard::name", Gio.FileQueryInfoFlags.NONE, None,
        )
        try:
            enumerator.next_file(None)
        finally:
            enumerator.close(None)
        report["checks"]["native_directory_enumerable"] = True
        screenshot("native-save-visible")
        chooser.emit("response", Gtk.ResponseType.CANCEL)
        later(cancelled, 180)

    def cancelled():
        assert plugin._raw_export_chooser is None
        assert not target.exists()
        assert broker.exports == 0
        report["checks"]["cancel_no_file_no_broker"] = True
        # Gtk.FileChooserNative forbids changing selections while visible.
        # Start the second REAL native dialog with an isolated writable HOME
        # rather than calling set_file/set_current_folder after show().
        target.write_bytes(b"OLD-SYNTHETIC-NONPRIVATE")
        os.environ["HOME"] = str(out)
        press(button(), third_press)

    def third_press():
        press(button(), third_press_done)

    def third_press_done():
        until(lambda: plugin._raw_export_chooser is not None, choose_target)

    def choose_target():
        chooser = plugin._raw_export_chooser
        assert chooser.get_visible()
        assert chooser.get_transient_for() is window[0]
        assert chooser.get_action() == Gtk.FileChooserAction.SAVE
        # Unlike Gtk.FileChooserDialog, the native picker must have its
        # folder/name set BEFORE show(). No mutating chooser calls here.
        until(lambda: chooser.get_current_folder() is not None
              and chooser.get_current_folder().get_path() == str(out)
              and chooser.get_current_name() == target.name,
              lambda: accept(chooser), seconds=8)

    def accept(chooser):
        report["checks"]["target_folder_ready"] = True
        # A native SAVE response must resolve the explicit, previously
        # prepared folder+name. Never bypass an absent target by writing it.
        selected = chooser.get_file()
        report["checks"]["native_file_selected"] = bool(
            selected is not None and selected.get_path() == str(target))
        if not report["checks"]["native_file_selected"]:
            fail("native_chooser_selection_not_resolved")
            return
        chooser.emit("response", Gtk.ResponseType.ACCEPT)
        until(lambda: target.exists() and target.read_bytes() == _PAYLOAD,
              saved, seconds=6)

    def saved():
        assert plugin._raw_export_chooser is None
        assert target.read_bytes() == _PAYLOAD
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert broker.exports == 1
        assert any(label == "Export local du journal brut"
                   for label, _kind in tasks.calls)
        report["checks"]["accept_exact_raw_bytes_once"] = True
        report["checks"]["permissions_0600"] = True
        report["checks"]["raw_secrets_unchanged"] = True
        # Continue with the NEW daily ZIP on the same mounted real GTK page.
        # Historical JSON compatibility alone is not acceptance of #3649.
        later(start_zip, 180)

    def zip_button():
        matches = [widget for widget in walk(page[0])
                   if isinstance(widget, Gtk.Button)
                   and widget.get_label() in {
                       "Exporter le journal brut du jour (ZIP)…",
                       "Confirmer l’export ZIP non masqué",
                   }]
        assert len(matches) == 1
        return reveal_diagnostics_section(matches[0])

    def start_zip():
        ctrl = plugin._daily_journal_export
        assert ctrl.chooser is None and broker.zip_exports == 0
        press(zip_button(), zip_first_done)

    def zip_first_done():
        assert plugin._daily_journal_export.chooser is None
        assert zip_button().get_label() == "Confirmer l’export ZIP non masqué"
        report["checks"]["zip_first_press_warns"] = True
        press(zip_button(), zip_second_done)

    def zip_second_done():
        until(lambda: plugin._daily_journal_export.chooser is not None,
              zip_chooser_open)

    def zip_chooser_open():
        chooser = plugin._daily_journal_export.chooser
        assert isinstance(chooser, Gtk.FileChooserNative)
        assert chooser.get_transient_for() is window[0]
        assert chooser.get_modal()
        assert chooser.get_action() == Gtk.FileChooserAction.SAVE
        until(lambda: chooser.get_current_folder() is not None,
              lambda: zip_cancel(chooser))

    def zip_cancel(chooser):
        assert chooser.get_current_folder().get_path() == str(out)
        chooser.emit("response", Gtk.ResponseType.CANCEL)
        later(zip_cancelled, 160)

    def zip_cancelled():
        assert plugin._daily_journal_export.chooser is None
        assert broker.zip_exports == 0
        assert not list(out.glob("*.zip"))
        report["checks"]["zip_cancel_no_file_no_broker"] = True
        press(zip_button(), zip_third_press)

    def zip_third_press():
        press(zip_button(), zip_third_done)

    def zip_third_done():
        until(lambda: plugin._daily_journal_export.chooser is not None,
              zip_choose_target)

    def zip_choose_target():
        chooser = plugin._daily_journal_export.chooser
        assert chooser.get_transient_for() is window[0]
        assert chooser.get_action() == Gtk.FileChooserAction.SAVE
        until(lambda: chooser.get_current_folder() is not None
              and chooser.get_current_folder().get_path() == str(out)
              and chooser.get_current_name() is not None,
              lambda: zip_accept(chooser), seconds=8)

    def zip_accept(chooser):
        name = chooser.get_current_name()
        assert name.startswith("cifereca-journal-brut-") and name.endswith(".zip")
        selected = chooser.get_file()
        expected = out / name
        assert selected is not None and selected.get_path() == str(expected)
        chooser.emit("response", Gtk.ResponseType.ACCEPT)
        until(expected.exists, lambda: zip_saved(expected), seconds=6)

    def zip_saved(target_zip):
        assert plugin._daily_journal_export.chooser is None
        assert broker.zip_exports == 1
        assert stat.S_IMODE(target_zip.stat().st_mode) == 0o600
        with ZipFile(target_zip) as archive:
            assert archive.testzip() is None
            manifest = json.loads(archive.read("manifest.json"))
            raw = json.loads(archive.read("cifereca-journal-brut.json"))
            assert manifest["event_count"] == 1
            assert manifest["ui_filters_applied"] is False
            assert raw["events"][0]["message"] == "today-private-token=SYNTHETIC"
        assert any(label == "Export ZIP journal du jour"
                   for label, _kind in tasks.calls)
        report["checks"]["zip_today_only_exact"] = True
        report["checks"]["zip_permissions_0600"] = True
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.DiagnosticsRawExportE2E")

        def do_activate(self):
            window[0] = Gtk.ApplicationWindow(application=self)
            window[0].set_default_size(1250, 780)
            host.window = window[0]
            root = plugin._build(host)
            page[0] = root
            window[0].set_child(root)
            window[0].present()
            later(begin, 700)

        def do_shutdown(self):
            plugin.deactivate()
            Gio.Application.do_shutdown(self)

    app = App()
    GLib.timeout_add_seconds(
        35, lambda: (fail("native-chooser-timeout"), False)[1]
        if report["status"] == "RUNNING" else False,
    )
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report["status"] = "FAIL"
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
