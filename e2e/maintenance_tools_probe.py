#!/usr/bin/env python3
"""P0 #1358: real GTK/XTEST maintenance confirmation and shared-lock exclusion.

The fake tool never touches a real book. All HOME/XDG locations come from the
runner's isolated fixture. Explicit actions are sent through XTEST.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from time import monotonic
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "environnements/openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageGrab

from x11_input import X11Input
from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow
from cifereca.locking import InterProcessLock
from cifereca.paths import acquisition_lock_file


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


class DummyTool:
    def __init__(self):
        self.plan_calls = 0
        self.run_calls = 0
        self.plan_started = threading.Event()
        self.version = 0
        self.delay = 0.65

    def describe(self):
        return SimpleNamespace(
            title="Nettoyage test sans suppression", description="Simulation locale",
            supports_dry_run=True, cancellable=True, scope="identity", danger_level="destructive",
            icon_name="edit-delete-symbolic",
        )

    def plan(self, context):
        self.plan_calls += 1
        self.plan_started.set()
        time.sleep(self.delay)  # an intentionally slow scan, never on GTK
        return SimpleNamespace(
            tool_id="library.duplicates.cleanup",
            summary="Aucune suppression réelle",
            changes=tuple(
                f"fichier témoin simulé {i:02d} /chemin/long/vers/la/bibliothèque/romans/doublons/exemple.epub"
                for i in range(20)
            ),
            warnings=("Protection des artefacts Kobo",),
            facts={"version": self.version, "read_failures": 0},
        )

    def run(self, plan, control):
        control.check_cancelled()
        self.run_calls += 1
        return SimpleNamespace(ok=True, summary="Simulation confirmée")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", required=True, type=int, choices=(1366, 1600))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)
    tool = DummyTool()
    report = {
        "status": "RUNNING", "scenario": "maintenance-tools", "width": args.width,
        "scope": "real GTK MainWindow/XTEST; simulated tool with zero destructive IO",
        "checks": {}, "screenshots": [], "pointer_actions": [],
    }
    window = [None]
    device = [None]
    preview_button = [None]
    errors = []
    lock = [None]
    started = monotonic()
    heartbeat = [0]
    result = [1]

    def save():
        report["duration_seconds"] = round(monotonic() - started, 2)
        report["plan_calls"] = tool.plan_calls
        report["simulated_runs"] = tool.run_calls
        (args.out / "screen.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    def capture(name):
        image = ImageGrab.grab(xdisplay=__import__("os").environ["DISPLAY"])
        path = args.out / (name + ".png")
        image.save(path)
        image.save(args.out / "screen.png")
        report["screenshots"].append({"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        report.update(
            status="FAIL", error=f"{type(exc).__name__}: {exc}",
            failure_stack=__import__("traceback").format_exc(limit=12),
        )
        if lock[0] is not None:
            lock[0].release()
            lock[0] = None
        try:
            capture("failure")
        except Exception:
            pass
        save()
        app.quit()

    def later(callback, delay=100):
        def run():
            try:
                callback()
            except Exception as exc:
                fail(exc)
            return False
        GLib.timeout_add(delay, run)

    def until(predicate, callback, *, timeout=6.0):
        deadline = monotonic() + timeout
        def check():
            if predicate():
                callback()
            elif monotonic() >= deadline:
                raise AssertionError("GTK maintenance operation timed out")
            else:
                later(check, 95)
        later(check, 90)

    def modal():
        return next((
            item for item in Gtk.Window.list_toplevels()
            if isinstance(item, Adw.MessageDialog) and item.get_visible() and item.get_mapped()
        ), None)

    def physical(button):
        assert button.get_mapped() and button.get_sensitive(), (
            f"GTK action unavailable: mapped={button.get_mapped()} "
            f"sensitive={button.get_sensitive()} page={window[0].view_stack.get_visible_child_name()}"
        )
        if button is preview_button[0]:
            # The GTK4 ActionRow reports unreliable native transformations
            # under our rootless Xvfb runner (its computed click landed on the
            # title bar). This isolated screen is fixed at 1366/1600, window
            # starts at 0,0 and the trailing action is top-right at y=144.
            # Explicit screen coordinates still send genuine XTEST events.
            assert window[0].get_width() > 800, "Unexpected GTK window geometry"
            x = window[0].get_width() - 116
            y = 144
            device[0].click(x, y)
            report["pointer_actions"].append({
                "widget": button.get_name(), "x": x, "y": y,
                "source": "XTEST pointer/button events (GTK window bounds)",
            })
        else:
            report["pointer_actions"].append(device[0].click_widget(button))

    def respond(dialog, label):
        response = next(
            (b for b in walk(dialog) if isinstance(b, Gtk.Button)
             and label in str(b.get_label() or "").replace("_", "")),
            None,
        )
        assert response is not None, f"Missing modal response: {label}"
        assert response.get_mapped(), f"Modal response not mapped: {label}"
        physical(response)

    def ready():
        w = window[0]
        assert "tools" in w.nav_rows
        # The technical navigation section can be collapsed in GTK; selecting
        # its hidden ListBox row does not necessarily switch the visible page.
        # Start via the public navigation API. If the technical group is
        # collapsed, a hidden ListBox row may not emit a selection change.
        w.navigate("tools")
        if w.view_stack.get_visible_child_name() != "tools":
            w._visit_route("tools")
        assert w.view_stack.get_visible_child_name() == "tools", "Outils page not visible"
        w._refresh_tools_page()
        preview_button[0] = next((
            b for b in walk(w._tools_box)
            if isinstance(b, Gtk.Button)
            and getattr(b, "_cifereca_action_label", "") == "Prévisualiser…"
        ), None)
        assert preview_button[0] is not None
        report["checks"]["real_tools_route_and_action"] = True
        until(
            lambda: preview_button[0].get_mapped()
            and not w.view_stack.get_transition_running(),
            begin_busy,
        )

    def begin_busy():
        lock[0] = InterProcessLock(acquisition_lock_file())
        lock[0].acquire()
        physical(preview_button[0])
        until(lambda: bool(errors), busy_refused)

    def busy_refused():
        assert "Synchronisation en cours" in errors[-1], errors
        assert tool.plan_calls == 0 and tool.run_calls == 0
        assert modal() is None
        report["checks"]["busy_sync_refuses_preview_before_scan_or_delete"] = True
        lock[0].release()
        lock[0] = None
        errors.clear()
        tool.plan_started.clear()
        physical(preview_button[0])
        until(lambda: tool.plan_started.is_set(), preview_pending)

    def heartbeat_tick():
        heartbeat[0] += 1
        return report["status"] == "RUNNING"

    def preview_pending():
        assert modal() is None, "GTK dialog opened before slow plan finished"
        assert tool.run_calls == 0
        before = heartbeat[0]
        report["checks"]["slow_worker_started_without_mutation"] = True
        def after():
            assert heartbeat[0] > before + 3, "GTK main loop blocked by scanning"
            until(lambda: modal() is not None, first_dialog)
        later(after, 260)

    def first_dialog():
        dialog = modal()
        preview_scroll = dialog.get_extra_child()
        assert isinstance(preview_scroll, Gtk.ScrolledWindow)
        # GTK may insert a Gtk.Viewport between ScrolledWindow and Label.
        content = next((w for w in walk(preview_scroll) if isinstance(w, Gtk.Label)), None)
        assert content is not None
        body = content.get_text()
        assert all(label in body for label in (
            "Résumé :", "Changements prévus :", "Avertissements :", "Portée :",
        ))
        assert "\n" in body and "\\n" not in body, repr(body)
        assert preview_scroll.get_policy()[1] == Gtk.PolicyType.AUTOMATIC
        buttons = {
            label: next((b for b in walk(dialog) if isinstance(b, Gtk.Button)
                         and label in str(b.get_label() or "").replace("_", "")), None)
            for label in ("Annuler", "Exécuter")
        }
        assert all(b is not None and b.get_mapped() for b in buttons.values()), (
            "Preview response buttons must remain visible outside scrolling content"
        )
        adjustment = preview_scroll.get_vadjustment()
        assert adjustment.get_upper() > adjustment.get_page_size(), (
            "20 long preview entries must scroll without hiding dialog responses"
        )
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        assert all(b.get_mapped() for b in buttons.values()), (
            "Dialog action buttons disappeared after scrolling the preview"
        )
        report["checks"]["scrollable_preview_with_visible_actions_20_items"] = True
        assert tool.run_calls == 0, "Acquisition executed without confirmation"
        report["checks"]["real_multiline_confirmation_after_async_scan"] = True
        report["checks"]["gtk_heartbeat_during_slow_scan"] = heartbeat[0] > 5
        capture("01-async-preview")
        respond(dialog, "Annuler")
        later(after_cancel, 190)

    def after_cancel():
        assert tool.run_calls == 0
        report["checks"]["physical_cancel_never_runs_destructive_tool"] = True
        until(lambda: modal() is None and preview_button[0].get_sensitive(), begin_stale)

    def begin_stale():
        tool.delay = 0.12
        physical(preview_button[0])
        until(lambda: modal() is not None and preview_button[0].get_sensitive(), stale_dialog)

    def stale_dialog():
        tool.version += 1
        assert tool.run_calls == 0
        respond(modal(), "Exécuter")
        until(lambda: any("bibliothèque a changé" in e for e in errors), stale_refused)

    def stale_refused():
        assert tool.run_calls == 0
        report["checks"]["stale_plan_rejected_under_lock_without_delete"] = True
        until(lambda: modal() is None and preview_button[0].get_sensitive(), begin_final)

    def begin_final():
        physical(preview_button[0])
        until(lambda: modal() is not None and preview_button[0].get_sensitive(), final_dialog)

    def final_dialog():
        assert tool.run_calls == 0
        respond(modal(), "Exécuter")
        until(lambda: tool.run_calls == 1, completed)

    def completed():
        assert tool.run_calls == 1
        assert lock[0] is None
        report["checks"]["only_explicit_confirmed_safe_simulation_executes"] = True
        capture("02-confirmed-noop")
        report["status"] = "PASS"
        result[0] = 0
        save()
        app.quit()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.MaintenanceSafetyE2E")

        def do_activate(self):
            try:
                w = MainWindow(self, core.config, core.library, core.diagnostics)
                window[0] = w
                w.plugin_manager = SimpleNamespace(services=lambda name: (
                    [SimpleNamespace(plugin_id="e2e.maintenance", service=tool)]
                    if name == "maintenance.tool" else ()
                ))
                w.show_error = lambda message, _title=None: errors.append(str(message))
                w.set_default_size(args.width - 40, 880 if args.width == 1600 else 780)
                w.present()
                device[0] = X11Input()
                GLib.timeout_add(45, heartbeat_tick)
                later(ready, 500)
            except Exception as exc:
                fail(exc)

        def do_shutdown(self):
            if device[0] is not None:
                device[0].close()
            core.close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(
        48, lambda: (fail(TimeoutError("Maintenance GTK recipe timeout")), False)[1]
        if report["status"] == "RUNNING" else False,
    )
    app.run([sys.argv[0]])
    if report["status"] == "RUNNING":
        report.update(status="FAIL", error="GTK exited without executing proof")
        save()
    return result[0]


if __name__ == "__main__":
    raise SystemExit(main())
