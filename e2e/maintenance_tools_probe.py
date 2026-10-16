#!/usr/bin/env python3
"""P0 #1358/#1492: GTK/XTEST safety and cache progress on the tool row.

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
        self.progress_sent = threading.Event()
        self.release_run = threading.Event()
        self.run_finished = threading.Event()
        self.worker_threads = []

    def describe(self):
        return SimpleNamespace(
            id="core.cache.cleanup",
            title="Nettoyage test sans suppression", description="Simulation locale",
            supports_dry_run=True, cancellable=True, scope="identity", danger_level="destructive",
            icon_name="edit-delete-symbolic",
        )

    def plan(self, context):
        self.plan_calls += 1
        self.plan_started.set()
        time.sleep(self.delay)  # an intentionally slow scan, never on GTK
        return SimpleNamespace(
            tool_id="core.cache.cleanup",
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
        self.worker_threads.append(threading.get_ident())
        control.report(current=1, total=1, message="Témoin traité")
        self.progress_sent.set()
        try:
            if not self.release_run.wait(8):
                raise TimeoutError("GTK did not release simulated maintenance worker")
            # Exercise late progress after cancellation/refresh: it must never
            # repaint the detached row or the replacement row.
            control.report(current=1, total=1, message="Retour tardif")
            control.check_cancelled()
            return SimpleNamespace(ok=True, summary="Simulation confirmée")
        finally:
            self.run_finished.set()


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
    gtk_thread = threading.get_ident()
    detached_label = [None]

    def line_widgets():
        row = next(w for w in walk(window[0]._tools_box) if isinstance(w, Adw.ActionRow))
        label = next(w for w in walk(row) if isinstance(w, Gtk.Label)
                     and w.get_tooltip_text() == "État de l’opération")
        spinner = next(w for w in walk(row) if isinstance(w, Gtk.Spinner))
        bar = next(w for w in walk(row) if isinstance(w, Gtk.ProgressBar))
        return row, label, spinner, bar

    def line_is(text):
        return line_widgets()[1].get_text() == text

    def assert_line(text, *, fraction=None, busy=False):
        row, label, spinner, bar = line_widgets()
        assert row.get_mapped() and label.get_mapped(), "Progress must appear on the tool row"
        assert label.get_text() == text, label.get_text()
        assert spinner.get_visible() == busy
        assert bar.get_visible() == (fraction is not None)
        if fraction is not None:
            assert abs(bar.get_fraction() - fraction) < 0.001

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
        tool.release_run.set()
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
        if button.get_root() is window[0] and button in tuple(walk(window[0]._tools_box)):
            # The GTK4 ActionRow reports unreliable native transformations
            # under our rootless Xvfb runner (its computed click landed on the
            # title bar). This isolated screen is fixed at 1366/1600, window
            # starts at 0,0 and the trailing action is top-right at y=144.
            # Explicit screen coordinates still send genuine XTEST events.
            assert window[0].get_width() > 800, "Unexpected GTK window geometry"
            # Progress is now a suffix after the preview button. GTK's
            # horizontal bounds remain usable; keep the verified row y.
            ok, bounds = button.compute_bounds(window[0])
            assert ok, "Preview button horizontal bounds unavailable"
            x = round(bounds.get_x() + bounds.get_width() / 2)
            y = 144
            device[0].click(x, y)
            report["pointer_actions"].append({
                "widget": button.get_name(), "x": x, "y": y,
                "source": "XTEST pointer/button events (GTK window bounds)",
            })
        else:
            report["pointer_actions"].append(device[0].click_widget(button))

    def respond(dialog, label, on_clicked):
        response = next(
            (b for b in walk(dialog) if isinstance(b, Gtk.Button)
             and label in str(b.get_label() or "").replace("_", "")),
            None,
        )
        assert response is not None, f"Missing modal response: {label}"

        def allocated():
            # A GTK transient can be 'mapped' while its X11 origin is still
            # (-5, -5). Do not send XTEST until the real button is on-screen.
            if not dialog.get_visible() or not response.get_mapped() or not response.get_sensitive():
                return False
            try:
                x, y = device[0].widget_point(response)
            except RuntimeError:
                return False
            return x >= 0 and y >= 0

        def click_then_continue():
            physical(response)
            on_clicked()

        until(allocated, click_then_continue, timeout=6.0)

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
        assert_line("Au repos")
        report["checks"]["cache_progress_attached_to_real_tool_row"] = True
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
        # The worker event is raised off-thread before GLib has necessarily
        # delivered the row's queued "Analyse…" state. Wait for both
        # independent observations, without weakening the busy/spinner check.
        until(
            lambda: tool.plan_started.is_set() and line_is("Analyse…"),
            preview_pending,
        )

    def heartbeat_tick():
        heartbeat[0] += 1
        return report["status"] == "RUNNING"

    def preview_pending():
        assert modal() is None, "GTK dialog opened before slow plan finished"
        assert tool.run_calls == 0
        assert_line("Analyse…", busy=True)
        report["checks"]["analysis_indeterminate_on_tool_row"] = True
        before = heartbeat[0]
        report["checks"]["slow_worker_started_without_mutation"] = True
        def after():
            assert heartbeat[0] > before + 3, "GTK main loop blocked by scanning"
            until(preview_allocated, first_dialog)
        later(after, 260)

    def preview_allocated():
        dialog = modal()
        if dialog is None:
            return False
        preview_scroll = dialog.get_extra_child()
        if not isinstance(preview_scroll, Gtk.ScrolledWindow):
            return True  # first_dialog reports the actual layout violation.
        content = next((w for w in walk(preview_scroll) if isinstance(w, Gtk.Label)), None)
        if content is None:
            return True
        adjustment = preview_scroll.get_vadjustment()
        geometry = {
            "width": preview_scroll.get_width(),
            "height": preview_scroll.get_height(),
            "content_width": content.get_width(),
            "content_height": content.get_height(),
            "upper": adjustment.get_upper(),
            "page_size": adjustment.get_page_size(),
        }
        report["preview_allocation"] = geometry
        # Mapping precedes GTK's first size allocation. Wait for measurable
        # widgets, not for overflow: first_dialog must still detect a broken
        # scroll layout after the allocation has actually happened.
        # Modal mapping and the idle-delivered row state may arrive in
        # different main-loop turns. Wait for both; first_dialog still
        # validates the exact label, spinner, and layout independently.
        return line_is("Confirmation attendue") and all(geometry[key] > 0 for key in (
            "width", "height", "content_width", "content_height", "page_size",
        ))

    def first_dialog():
        dialog = modal()
        assert_line("Confirmation attendue")
        report["checks"]["confirmation_on_tool_row_without_percentage"] = True
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
        respond(dialog, "Annuler", lambda: later(after_cancel, 190))

    def after_cancel():
        assert tool.run_calls == 0
        report["checks"]["physical_cancel_never_runs_destructive_tool"] = True
        until(lambda: modal() is None and preview_button[0].get_sensitive()
              and line_is("Annulé"), cancel_scan)

    def cancel_scan():
        assert_line("Annulé")
        tool.plan_started.clear()
        physical(preview_button[0])
        until(lambda: tool.plan_started.is_set() and line_is("Analyse…"), cancel_pending_scan)

    def cancel_pending_scan():
        cancel = next(w for w in walk(window[0]._tools_box)
                      if isinstance(w, Gtk.Button) and w.get_label() == "Annuler")
        physical(cancel)
        def discarded():
            assert modal() is None and tool.run_calls == 0
            assert_line("Annulé")
            report["checks"]["cancelled_scan_never_reopens_confirmation"] = True
            until(lambda: preview_button[0].get_sensitive(), begin_stale)
        # Cancellation completes asynchronously after the simulated plan worker
        # has observed its cancellation token. A fixed 850 ms delay races
        # runner scheduling (the fake scan itself takes 650 ms), so assert
        # the eventual GTK state with an explicit upper bound instead.
        # A broken cancellation still fails rather than being silently retried.
        until(lambda: line_is("Annulé"), discarded, timeout=3.0)

    def begin_stale():
        tool.delay = 0.12
        physical(preview_button[0])
        until(lambda: modal() is not None and preview_button[0].get_sensitive(), stale_dialog)

    def stale_dialog():
        tool.version += 1
        assert tool.run_calls == 0
        respond(modal(), "Exécuter", lambda: until(
            lambda: any("bibliothèque a changé" in e for e in errors), stale_refused
        ))

    def stale_refused():
        assert tool.run_calls == 0
        report["checks"]["stale_plan_rejected_under_lock_without_delete"] = True
        until(lambda: modal() is None and preview_button[0].get_sensitive(), begin_final)

    def begin_final():
        physical(preview_button[0])
        until(lambda: modal() is not None and preview_button[0].get_sensitive(), final_dialog)

    def final_dialog():
        assert tool.run_calls == 0
        respond(modal(), "Exécuter", lambda: until(
            lambda: tool.progress_sent.is_set() and line_is("Purge en cours · 99 %"),
            progress_before_completion,
        ))

    def progress_before_completion():
        assert tool.run_calls == 1 and not tool.run_finished.is_set()
        assert tool.worker_threads[0] != gtk_thread, "Tool reports must originate in worker"
        assert_line("Purge en cours · 99 %", fraction=0.99)
        report["checks"]["real_worker_report_caps_percentage_before_success"] = True
        capture("02-worker-progress")
        tool.release_run.set()
        until(lambda: tool.run_finished.is_set() and line_is("Terminé · 100 %"), completed)

    def completed():
        assert tool.run_calls == 1
        assert lock[0] is None
        report["checks"]["only_explicit_confirmed_safe_simulation_executes"] = True
        assert_line("Terminé · 100 %", fraction=1.0)
        report["checks"]["success_alone_renders_100_percent"] = True
        capture("03-confirmed-noop")
        tool.progress_sent.clear()
        tool.release_run.clear()
        tool.run_finished.clear()
        physical(preview_button[0])
        until(lambda: modal() is not None and preview_button[0].get_sensitive(), refresh_dialog)

    def refresh_dialog():
        respond(modal(), "Exécuter", lambda: until(
            lambda: tool.progress_sent.is_set() and line_is("Purge en cours · 99 %"),
            refresh_running,
        ))

    def refresh_running():
        detached_label[0] = line_widgets()[1]
        window[0]._refresh_tools_page()
        until(lambda: line_is("Au repos"), refresh_ready)

    def refresh_ready():
        assert_line("Au repos")
        assert detached_label[0].get_root() is None
        tool.release_run.set()
        until(lambda: tool.run_finished.is_set(), refresh_finished)

    def refresh_finished():
        def verify():
            assert_line("Au repos")
            assert detached_label[0].get_text() == "Purge en cours · 99 %"
            assert tool.run_calls == 2
            report["checks"]["refresh_cancels_worker_and_discards_late_progress"] = True
            capture("04-refresh-discards-late-worker")
            finish()
        later(verify, 250)

    def finish():
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
