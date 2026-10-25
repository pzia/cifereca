#!/usr/bin/env python3
"""UX #1011: vrai widget GTK4 Diagnostics, brokers simulés, Xvfb 1366/1600.

Recette d'intégration du plugin (pas une preuve de toute la fenêtre Cifereca).
Aucune identité ou service distant n'est utilisé.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from concurrent.futures import Future
from datetime import datetime, timezone
from threading import Event, Thread, get_ident
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "src/plugins/diagnostics-page"
sys.path.insert(0, str(PLUGIN_DIR))

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from PIL import ImageGrab

sys.path.insert(0, str(ROOT / "environnements/openai-chat-debian"))
from x11_input import X11Input


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


class Broker:
    def __init__(self):
        self.event_queries = []
        self.checks = 0
        self.core_version = "0.9.2"
        self.recorder_version = "0.1.3"

    def system_status(self):
        return {"version": self.core_version,
                "application": {"online": False, "attempts": 0,
                                "secret": "EXEMPLE-PRIVE-UX"},
                "diagnostics": "Simulation locale, sans réseau"}

    def inventory(self):
        return (
            {"plugin_id": "ui.page.diagnostics", "display_name": "Diagnostics",
             "version": "0.2.3", "package_version": "0.2.3",
             "expected_package_version": "0.2.3", "enabled": True,
             "state": "ready", "type": "ui.page",
             "token": "PRIVATE_VERSION_TOKEN_2665"},
            {"plugin_id": "browser.recorder", "display_name": "Browser Recorder",
             "version": self.recorder_version, "package_version": "0.1.3",
             "expected_package_version": "0.1.4", "enabled": True,
             "state": "blocked", "path": "/PRIVATE_HOME_2665/recorder"},
            {"plugin_id": "plugin.disabled", "display_name": "Extension arrêtée",
             "version": "1.7.0", "package_version": "1.7.0",
             "expected_package_version": "1.7.0", "enabled": False,
             "state": "disabled", "account_id": "PRIVATE_ACCOUNT_2665"},
        )

    def runtime_diagnostic(self, _plugin):
        return {}

    def snapshot(self):
        return ({"message": "Diagnostic anonymisé",
                 "timestamp": "2026-10-11T06:00:00+00:00",
                 "source": "fixture", "severity": "warning",
                 "detail": {"available": False, "count": 0,
                            "memo": "Valeur d’essai", "token": "SECRET-UX-TEST"}},)

    def export_support_text(self):
        return ("RAPPORT SUPPORT EXPURGÉ — FIXTURE UX\n" +
                "Événement fictif.\n" * 250)

    def active_checks(self):
        self.checks += 1
        return ()

    def events(self, **query):
        self.event_queries.append(dict(query))
        offset = int(query.get("offset", 0))
        limit = int(query.get("limit", 50))
        module = str(query.get("module") or "").strip().casefold()
        total = 65 if not module or "core.sync".startswith(module) else 0
        events = [
            {"event_id": f"evt-{i:03d}",
             "timestamp": f"2026-10-11T06:{i % 60:02d}:00+00:00",
             "source": "core_events", "module": "core.sync",
             "severity": "error" if i == 51 else "info",
             "status": "partial" if i == 51 else "ok",
             "message": f"Événement fictif {i}"}
            for i in range(offset, min(offset + limit, total))
        ]
        return {
            "events": events, "offset": offset, "total": total,
            "next_offset": offset + len(events) if offset + len(events) < total else None,
            "sources": [
                {"source": "core_events", "status": "available"},
                {"source": "app_log", "status": "unavailable"},
            ],
        }


class ImmediateHandle:
    def __init__(self, value):
        self._value = value

    def add_done_callback(self, callback):
        callback(self)

    def result(self):
        return self._value

    def done(self):
        return True

    def cancel(self):
        return None


class Tasks:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *, label, kind):
        self.calls.append((label, kind))
        return ImmediateHandle(fn(None))


class SelectionBroker(Broker):
    """Minimal public-broker fixture for the plugin's bulk-selection contract."""
    def __init__(self):
        super().__init__()
        self.bulk = False
        self.error_count = 55
        self.bulk_timestamp = datetime.now(timezone.utc).isoformat()
        self.ui_thread = get_ident()
        self.scan_threads = []

    def events(self, **query):
        if not self.bulk:
            return super().events(**query)
        self.event_queries.append(dict(query))
        if query.get("severity") in {"error", "critical"}:
            self.scan_threads.append(get_ident())
        rows = [{"event_id": f"bulk-{i:03d}", "timestamp":self.bulk_timestamp,
                 "source":"core_events", "module":"core.sync",
                 "severity": "error" if i < self.error_count else
                             ("critical" if i < self.error_count + 2 else "warning"),
                 "status":"partial", "message":f"Événement fictif {i} token=SECRET-SELECTION"}
                for i in range(self.error_count + 10)]
        level = query.get("severity")
        if level:
            rows = [row for row in rows if row["severity"] == level]
        module = query.get("module", "").strip().casefold()
        if module and not "core.sync".startswith(module):
            rows = []
        if query.get("source") not in {None, "all", "core_events"}:
            rows = []
        offset, limit = query.get("offset", 0), query.get("limit", 50)
        batch = rows[offset:offset + limit]
        return {"events":batch, "offset":offset, "total":len(rows),
                "next_offset":offset + len(batch) if offset + len(batch) < len(rows) else None,
                "sources":[{"source":"core_events","status":"available"},
                           {"source":"app_log","status":"unavailable"}]}


class SelectionHandle:
    def __init__(self, fn, gate=None):
        self.future = Future()
        self.cancel_event = Event()
        self.started = Event()
        def work():
            self.started.set()
            try:
                if gate is not None and not gate.wait(5):
                    raise RuntimeError("fixture gate timed out")
                self.future.set_result(fn(self))
            except BaseException as exc:
                self.future.set_exception(exc)
        Thread(target=work, daemon=True).start()

    def add_done_callback(self, callback):
        self.future.add_done_callback(lambda _future: callback(self))

    def result(self):
        return self.future.result()

    def done(self):
        return self.future.done()

    def cancelled(self):
        return self.cancel_event.is_set()

    def cancel(self):
        self.cancel_event.set()


class SelectionTasks(Tasks):
    def __init__(self):
        super().__init__()
        self.gate = None
        self.selection_handle = None

    def submit(self, fn, *, label, kind):
        if label != "Sélection expurgée des journaux":
            return super().submit(fn, label=label, kind=kind)
        self.calls.append((label, kind))
        gate, self.gate = self.gate, None
        self.selection_handle = SelectionHandle(fn, gate)
        return self.selection_handle


class Context:
    def __init__(self, broker, tasks):
        self._broker = broker
        self._tasks = tasks
        self.reports = []

    def broker(self, name):
        return {"diagnostics": self._broker, "tasks": self._tasks}.get(name)

    def service(self, _name):
        return None  # Aucun LLM, ni local ni distant.

    def report_diagnostic(self, *args, **kwargs):
        self.reports.append((args, kwargs))


def reveal_diagnostics_section(widget):
    """Set up legacy per-section assertions; tab navigation is tested separately."""
    child = widget
    parent = child.get_parent()
    while parent is not None:
        if isinstance(parent, Gtk.Expander):
            parent.set_expanded(True)
        if isinstance(parent, Gtk.Stack) and parent.get_name() == "diagnostics-sections":
            parent.set_visible_child(child)
            break
        child, parent = parent, parent.get_parent()
    return widget


class Host:
    def __init__(self):
        self.toasts = []
        from cifereca.gui.ui_perf import UiPerfMonitor
        self.performance = UiPerfMonitor(measurement_origin="synthetic")

    def performance_report(self):
        return self.performance.performance_report()

    def set_performance_capture(self, enabled):
        self.performance.enable_detailed_capture(enabled)
        return self.performance.performance_report()

    def toast(self, text):
        self.toasts.append(str(text))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", required=True, type=int, choices=(1366, 1600))
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("GTK/X11/Xvfb requis")
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                      cwd=ROOT, text=True).strip()
    module = importlib.import_module("cifereca_diagnostics_page")
    broker, tasks, host = SelectionBroker(), SelectionTasks(), Host()
    plugin = module.DiagnosticsPagePlugin().activate(Context(broker, tasks))
    start = monotonic()
    report = {"status": "RUNNING", "git_sha": git_sha, "width": args.width,
              "backend": "GTK4/X11", "plugin": "ui.page.diagnostics",
              "broker": "fixture bornée", "screenshots": [], "checks": {}}
    window = [None]
    page = [None]
    rc = [1]
    device = [None]

    def save():
        report["seconds"] = round(monotonic() - start, 2)
        report["events_queried"] = len(broker.event_queries)
        report["active_checks"] = broker.checks
        (out / "screen.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def screenshot(stage):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert image.size == (args.width, 1000 if args.width == 1600 else 900), image.size
        assert image.getbbox() is not None, "capture entièrement vide"
        path = out / (stage + ".png")
        image.save(path)
        shutil.copyfile(path, out / "screen.png")
        report["screenshots"].append({
            "name": stage, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "window_width": window[0].get_width(),
        })

    def fail(error):
        if report["status"] != "RUNNING":
            return
        report.update(status="FAIL", error=str(error))
        try:
            screenshot("failure")
        except Exception:
            pass
        save()
        app.quit()

    def step(callback):
        def wrapped():
            try:
                code = getattr(callback, "__code__", None)
                if code is not None and code.co_filename == __file__:
                    report["checkpoint_line"] = code.co_firstlineno
                # Persist the last scheduled source checkpoint before native
                # GTK work: SIGABRT cannot run Python's exception handler.
                save()
                callback()
            except Exception as exc:
                frames = []
                trace = exc.__traceback__
                while trace is not None:
                    if trace.tb_frame.f_code.co_filename == __file__:
                        frames.append(trace.tb_lineno)
                    trace = trace.tb_next
                report["failure_line"] = frames[-1] if frames else 0
                fail(f"{type(exc).__name__}: {exc}")
            return False
        return wrapped

    def later(callback, ms=150):
        GLib.timeout_add(ms, step(callback))

    def find_label(text):
        return any(isinstance(w, Gtk.Label) and
                   text in (w.get_text() or "") for w in walk(page[0]))

    def find_button(label):
        buttons = [w for w in walk(page[0]) if isinstance(w, Gtk.Button)
                   and w.get_label() == label]
        assert len(buttons) == 1, f"bouton {label!r}: {len(buttons)}"
        return reveal_diagnostics_section(buttons[0])

    def find_expander(label):
        expanders = [w for w in walk(page[0]) if isinstance(w, Gtk.Expander)
                     and w.get_label() == label]
        assert len(expanders) >= 1, f"volet {label!r} absent"
        return reveal_diagnostics_section(expanders[0])

    def until(predicate, done, *, seconds=10, on_timeout=None):
        # Always record the exact call site of this wait, including GI-bound
        # methods such as button.has_focus (not Python lambdas). The CI
        # exporter validates that numeric line against an AST `until` call.
        from inspect import currentframe
        frame = currentframe()
        try:
            caller = frame.f_back if frame is not None else None
            line = (caller.f_lineno if caller is not None
                    and caller.f_code.co_filename == __file__ else None)
        finally:
            del frame
        if type(line) is int:
            report["wait_line"] = line
        else:
            report.pop("wait_line", None)
        save()
        deadline = monotonic() + seconds

        def check():
            if predicate():
                done()
            elif monotonic() >= deadline:
                if on_timeout is not None:
                    on_timeout()
                raise AssertionError("Timeout de la condition GTK attendue")
            else:
                later(check, 90)
        later(check, 100)

    def diagnostics_entry_observation():
        """UX #2139: observe access to Journaux without grading GTK usability.

        All lookup names are fixed here; the stored report contains only
        geometry, booleans and fixed semantic IDs, never label/event text.
        """
        scroll = page[0]
        viewport_width = max(1, scroll.get_width())
        viewport_height = max(1, scroll.get_height())
        adjustment = scroll.get_vadjustment()
        nodes = list(walk(scroll))

        def label_named(name):
            return next(
                (node for node in nodes if isinstance(node, Gtk.Label)
                 and (node.get_text() or "") == name),
                None,
            )

        def button_named(name):
            return next(
                (node for node in nodes if isinstance(node, Gtk.Button)
                 and node.get_label() == name),
                None,
            )

        def entry_named(placeholder):
            return next(
                (node for node in nodes if isinstance(node, Gtk.Entry)
                 and node.get_placeholder_text() == placeholder),
                None,
            )

        def geometry(node):
            if node is None:
                return {"present": False}
            ok, bounds = node.compute_bounds(scroll)
            if not ok:
                return {"present": True, "allocated": False}
            x, y = float(bounds.get_x()), float(bounds.get_y())
            width, height = float(bounds.get_width()), float(bounds.get_height())
            visible_width = max(0.0, min(x + width, viewport_width) - max(x, 0.0))
            visible_height = max(0.0, min(y + height, viewport_height) - max(y, 0.0))
            fully = (
                node.get_mapped() and visible_width >= width > 0
                and visible_height >= height > 0
            )
            return {
                "present": True,
                "allocated": bool(node.get_mapped() and width > 0 and height > 0),
                "intersects_first_screen": bool(
                    node.get_mapped() and visible_width > 0 and visible_height > 0
                ),
                "fully_in_first_screen": bool(fully),
                "horizontal_clipping": bool(width > 0 and visible_width < width),
                "vertical_visible_percent": round(
                    100.0 * visible_height / height, 1
                ) if height > 0 and node.get_mapped() else 0.0,
                "top_offset_viewports": round(y / viewport_height, 2),
            }

        dropdowns = [node for node in nodes if isinstance(node, Gtk.DropDown)]
        targets = {
            "system_heading": label_named("État système"),
            "journals_heading": label_named("Journaux par composant"),
            "severity_filter": dropdowns[1] if len(dropdowns) > 1 else None,
            "source_filter": dropdowns[0] if dropdowns else None,
            "period_filter": dropdowns[2] if len(dropdowns) > 2 else None,
            "module_filter": entry_named("Préfixe module (Entrée pour appliquer)"),
            "search_filter": entry_named("Rechercher dans cette page"),
            "select_errors": button_named("Erreurs + critiques (filtres)"),
            "select_all": button_named("Tout sélectionner (filtres)"),
            "copy_selection": button_named("Copier la sélection"),
            "ai_heading": label_named("Analyse IA (facultative)"),
        }
        return {
            "window_width": int(window[0].get_width()),
            "window_height": int(window[0].get_height()),
            "viewport_width": int(scroll.get_width()),
            "viewport_height": int(scroll.get_height()),
            "vertical_scroll_extent_viewports": round(
                max(0.0, float(adjustment.get_upper()) -
                    float(adjustment.get_page_size()))
                / max(1.0, float(adjustment.get_page_size())), 2
            ),
            "controls": {key: geometry(target) for key, target in targets.items()},
        }

    def diagnostics_entry_at_1280():
        # Capture actual allocated width; set_default_size() is only a request.
        report["diagnostics_entry_ux"]["requested_1280"] = (
            diagnostics_entry_observation()
        )
        screenshot("00b-diagnostics-entry-1280")
        window[0].set_default_size(
            args.width - 40, 870 if args.width == 1600 else 790
        )
        later(capture_navigation, 350)

    def activate_x11_test_window():
        """Focus this mapped Xvfb toplevel, not a tab (XTEST remains required).

        CI intentionally runs without a window manager. Gtk.Window.present()
        is only a request, so GDK can leave is_active() false indefinitely;
        XTEST keyboard events would then be delivered to no GTK toplevel.
        """
        import ctypes as C
        from native_capture import pointer
        surface = window[0].get_surface()
        assert surface is not None, "GTK test window has no X11 surface"
        gtk = C.CDLL("libgtk-4.so.1")
        gtk.gdk_x11_surface_get_xid.argtypes = [C.c_void_p]
        gtk.gdk_x11_surface_get_xid.restype = C.c_ulong
        xid = gtk.gdk_x11_surface_get_xid(pointer(surface))
        assert xid, "GTK test window has no X11 window ID"
        x11 = device[0].x11
        x11.XSetInputFocus.argtypes = [
            C.c_void_p, C.c_ulong, C.c_int, C.c_ulong,
        ]
        x11.XSetInputFocus.restype = C.c_int
        x11.XSetInputFocus(device[0].display, xid, 1, 0)
        x11.XSync(device[0].display, False)

    def tab_key(title, key, done):
        switcher = next(w for w in walk(window[0].get_child())
                        if isinstance(w, Gtk.StackSwitcher))
        button = next(w for w in walk(switcher) if isinstance(w, Gtk.ToggleButton)
                      and any(isinstance(v, Gtk.Label) and v.get_text() == title
                              for v in walk(w)))
        assert button.get_mapped() and button.get_sensitive()
        # GTK4 may keep the former tab's focus after a window resize. Give
        # the real toplevel focus first, then explicitly target this mapped
        # StackSwitcher button. Space is still delivered ONLY by XTEST once
        # real focus is observed (no clicked()/set_visible_child shortcut).
        window[0].present()
        if not window[0].is_active():
            activate_x11_test_window()
        window[0].set_focus(button)
        assert button.grab_focus() is not False

        def activate_focused_tab():
            assert button.has_focus(), "XTEST tab target must hold keyboard focus"
            press("space")
            until(lambda: switcher.get_stack().get_visible_child_name() == key,
                  lambda: later(done, 120))

        # Explain this one strict XTEST focus gate by source-line only. Each
        # fixed raise can be identified through the existing AST-reviewed
        # public failure_line; no widget repr or private GTK content escapes.
        def tab_focus_timeout():
            if not window[0].is_active():
                raise AssertionError("GTK tab focus: inactive toplevel")
            if not button.get_focusable():
                raise AssertionError("GTK tab focus: unfocusable toggle")
            current = window[0].get_focus()
            if current is None:
                raise AssertionError("GTK tab focus: no focused widget")
            if current is not button:
                raise AssertionError("GTK tab focus: different focused widget")
            raise AssertionError("GTK tab focus: target reports no focus")

        # X11 focus changes are asynchronous. Do not dispatch a physical
        # Space while GTK may still route it to the previously focused tab.
        until(button.has_focus, lambda: later(activate_focused_tab, 120),
              seconds=4, on_timeout=tab_focus_timeout)

    def capture_navigation():
        # New acceptance uses physical keyboard navigation, not the legacy
        # section preconditions used for the pre-existing report assertions.
        tab_key("Capture", "capture", capture_initial)

    def capture_initial():
        assert not host.performance.detailed_capture_enabled
        assert find_label("Capture arrêtée")
        button = find_button("Démarrer la capture")
        assert button.get_mapped() and button.get_sensitive()
        assert button.grab_focus() is not False
        press("space")
        until(lambda: host.performance.detailed_capture_enabled, capture_started)

    def capture_started():
        assert find_label("Capture en cours")
        tab_key("Journaux", "journals", capture_survives_navigation)

    def capture_survives_navigation():
        assert host.performance.detailed_capture_enabled
        assert find_label("50 événements affichés")
        tab_key("Capture", "capture", capture_stop)

    def capture_stop():
        assert host.performance.detailed_capture_enabled
        button = find_button("Arrêter la capture")
        assert button.get_sensitive() and button.grab_focus() is not False
        press("space")
        until(lambda: not host.performance.detailed_capture_enabled, capture_export)

    def capture_export():
        assert find_label("Capture arrêtée")
        button = find_button("Enregistrer les mesures JSON…")
        assert button.get_sensitive() and button.grab_focus() is not False
        press("space")
        until(lambda: any(e.filename == "cifereca-performance.json" and e.dialog is not None
                          for e in plugin._support_exports), capture_file_choice)

    def capture_file_choice():
        exporter = next(e for e in plugin._support_exports
                        if e.filename == "cifereca-performance.json")
        assert exporter.dialog.get_visible()
        target = out / "cifereca-performance.json"
        target.write_bytes(b"CAPTURE-EXPORT-FIXTURE")
        # Gtk.FileChooserNative may clear an existing selection when SAVE's
        # current name is changed. Set the suggested name *before* selecting
        # the exact fixture GFile (as in diagnostics_structured_probe), then
        # keep the strict asynchronous path assertion below.
        exporter.dialog.set_current_name(target.name)
        assert exporter.dialog.set_file(Gio.File.new_for_path(str(target)))
        until(lambda: exporter.dialog.get_file() is not None
              and exporter.dialog.get_file().get_path() == str(target),
              lambda: capture_accept(exporter, target), seconds=5)

    def capture_accept(exporter, target):
        assert exporter.dialog.get_file().get_path() == str(target)
        exporter.dialog.emit("response", Gtk.ResponseType.ACCEPT)
        until(lambda: exporter.cancellable is None, lambda: capture_saved(target))

    def capture_saved(target):
        data = json.loads(target.read_text())
        assert data["schema"] == "cifereca.performance.phases/1"
        assert data["detailed_capture_enabled"] is False
        assert data["remote_upload"] is False
        assert "Mesures JSON enregistrées" in host.toasts
        assert broker.checks == 0 and plugin.llm is None
        report["checks"]["P0_2139_capture_keyboard_tabs_lifetime_and_local_json"] = True
        # Fixture destination selected programmatically; opening the chooser,
        # tab/start/stop/save activation and actual private write are real GTK.
        tab_key("Rapports et vérifications", "reports", report_step)

    def versions_expected():
        from cifereca.plugin_api.version_inventory import version_inventory
        return version_inventory(broker.system_status(), broker.inventory()).text

    def versions_initial():
        report["stage"] = "P1_2665_versions_visible_and_copy"
        assert find_label("Core Cifereca · v0.9.2")
        assert find_label("3 plugins installés / enregistrés")
        assert find_label("Browser Recorder [browser.recorder]")
        assert find_label("Extension : 0.1.3")
        assert find_label("Écart paquet actif/attendu")
        assert find_label("Désactivé · activé : non")
        button = find_button("Copier les versions")
        assert button.get_mapped() and button.get_sensitive()
        assert button.get_tooltip_text()
        assert broker.checks == 0 and plugin.llm is None
        screenshot("02-versions-core-plugins")
        keyboard_action("Copier les versions", versions_clipboard_first)

    def versions_clipboard_first():
        expected = versions_expected()
        def copied(clipboard, task):
            try:
                actual = clipboard.read_text_finish(task)
                assert actual == expected
                assert actual.startswith("Core Cifereca · v0.9.2")
                assert actual.count("browser.recorder") == 1
                for secret in (
                    "EXEMPLE-PRIVE-UX", "PRIVATE_VERSION_TOKEN_2665",
                    "PRIVATE_HOME_2665", "PRIVATE_ACCOUNT_2665",
                    "RAPPORT SUPPORT EXPURGÉ",
                ):
                    assert secret not in actual
                assert "Versions copiées" in host.toasts
                report["checks"]["P1_2665_exact_versions_clipboard_no_support_secrets"] = True
                # Simulate new broker inventory (plugin updated in place),
                # refreshing the *public snapshot*, never a remote provider.
                broker.core_version = "0.9.3"
                broker.recorder_version = "0.1.5"
                refresh = next(w for w in walk(page[0])
                               if isinstance(w, Gtk.Button)
                               and w.get_tooltip_text() == "Actualiser les diagnostics")
                assert refresh.get_sensitive()
                refresh.emit("clicked")
                later(versions_refreshed, 180)
            except Exception:
                fail("P1_2665_versions_clipboard_mismatch")
        Gdk.Display.get_default().get_clipboard().read_text_async(None, copied)

    def versions_refreshed():
        assert find_label("Core Cifereca · v0.9.3")
        assert find_label("Extension : 0.1.5")
        assert not find_label("Core Cifereca · v0.9.2")
        assert "Core Cifereca · v0.9.3" in versions_expected()
        keyboard_action("Copier les versions", versions_clipboard_updated)

    def versions_clipboard_updated():
        expected = versions_expected()
        def copied(clipboard, task):
            try:
                actual = clipboard.read_text_finish(task)
                assert actual == expected
                assert "Core Cifereca · v0.9.2" not in actual
                assert "Extension : 0.1.3" not in actual
                assert "Extension : 0.1.5" in actual
                assert broker.checks == 0
                report["checks"]["P1_2665_refresh_replaces_clipboard_snapshot"] = True
                complete_scenario()
            except Exception:
                fail("P1_2665_refreshed_clipboard_mismatch")
        Gdk.Display.get_default().get_clipboard().read_text_async(None, copied)

    def initial():
        assert window[0].get_width() <= args.width, "Fenêtre trop large"
        assert page[0].get_mapped(), "Plugin non visible"
        stack = next(w for w in walk(page[0]) if isinstance(w, Gtk.Stack)
                     and w.get_name() == "diagnostics-sections")
        assert stack.get_visible_child_name() == "journals"
        assert host.performance.detailed_capture_enabled is False
        module_entry = next(w for w in walk(page[0]) if isinstance(w, Gtk.Entry)
                            and w.get_placeholder_text() == "Préfixe module (Entrée pour appliquer)")
        assert module_entry.get_mapped()
        ok, bounds = module_entry.compute_bounds(page[0])
        assert ok and bounds.get_y() >= 0
        assert bounds.get_y() + bounds.get_height() <= page[0].get_height()
        report["checks"]["P0_2139_journals_default_no_implicit_capture"] = True
        assert find_label("Journaux par composant")
        assert find_label("Analyse IA")
        assert find_label("État système")
        assert find_label("Rapport support")
        assert find_label("50 événements affichés")
        assert find_label("Indisponible"), "Disponibilité des sources masquée"
        assert not next(w for w in walk(page[0]) if isinstance(w, Gtk.Button)
                        and w.get_label() == "Autoriser et analyser").get_sensitive()
        assert broker.checks == 0, "Contrôle actif lancé à l'ouverture"
        assert len(broker.event_queries) == 1, broker.event_queries
        report["checks"]["initial_and_no_implicit_active_or_ai"] = True
        screenshot("00-diagnostics-loaded")
        report["diagnostics_entry_ux"] = {
            "baseline": diagnostics_entry_observation()
        }
        # The target is near 1300px, not the 1600px test display.
        # The original window size and all existing XTEST gates are restored.
        window[0].set_default_size(1280, 850)
        later(diagnostics_entry_at_1280, 350)

    def report_step():
        expander = find_expander("Ouvrir le rapport support")
        assert not expander.get_expanded()
        expander.set_expanded(True)
        scrollers = [w for w in walk(expander) if isinstance(w, Gtk.ScrolledWindow)]
        views = [w for w in walk(expander) if isinstance(w, Gtk.TextView)]
        assert len(scrollers) == 1 and len(views) == 1
        assert "RAPPORT SUPPORT EXPURGÉ" in views[0].get_buffer().get_text(
            views[0].get_buffer().get_start_iter(),
            views[0].get_buffer().get_end_iter(), False)
        find_button("Agrandir").emit("clicked")
        assert scrollers[0].get_size_request()[1] == 560
        assert find_button("Réduire").get_sensitive()
        report["checks"]["report_scroll_and_expand"] = True
        later(report_capture)

    def report_capture():
        screenshot("01-report-expanded")
        find_button("Réduire").emit("clicked")
        expander = find_expander("Détails expurgés")
        expander.set_expanded(True)
        dropdowns = [w for w in walk(expander) if isinstance(w, Gtk.DropDown)]
        views = [w for w in walk(expander) if isinstance(w, Gtk.TextView)]
        assert len(dropdowns) == 1 and len(views) == 1
        dropdowns[0].set_selected(1)  # JSON indenté
        later(lambda: check_json(views[0]))

    def check_json(view):
        buf = view.get_buffer()
        payload = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        obj = json.loads(payload)
        assert obj
        assert "EXEMPLE-PRIVE-UX" not in payload
        assert "SECRET-UX-TEST" not in payload
        report["checks"]["json_structured_and_redacted"] = True
        screenshot("02-structured-json")
        detail_pane = view.get_parent()
        while detail_pane is not None and not isinstance(detail_pane, Gtk.Expander):
            detail_pane = detail_pane.get_parent()
        assert detail_pane is not None
        copied_detail = [
            w for w in walk(detail_pane) if isinstance(w, Gtk.Button)
            and w.get_tooltip_text() == "Copier le détail expurgé actuellement affiché"
        ]
        assert copied_detail, "Bouton de copie expurgée absent"
        button = reveal_diagnostics_section(copied_detail[0])
        assert button.get_mapped()
        # Expansion and viewport allocation settle on the GTK main loop.
        # Match the other keyboard acceptance paths: reveal the actual button,
        # wait for real focus, then retain XTEST and exact clipboard assertions.
        content = page[0].get_child()
        ok, bounds = button.compute_bounds(content)
        assert ok
        outer = page[0].get_vadjustment()
        outer.set_value(max(0, min(float(bounds.get_y()) - 60,
                                 outer.get_upper() - outer.get_page_size())))

        def focus_copy():
            assert button.grab_focus() is not False
            until(button.has_focus, activate_copy, seconds=4)

        def activate_copy():
            assert button.has_focus(), "Le focus clavier doit être visible sur Copier"
            device[0].key("space", True)
            device[0].key("space", False)
            report["checks"]["copy_button_keyboard_xtest"] = True
            expected = payload

            def clipboard_done(clipboard, task):
                try:
                    actual = clipboard.read_text_finish(task)
                    assert actual == expected, "Presse-papiers différent du JSON affiché"
                    assert "EXEMPLE-PRIVE-UX" not in actual
                    assert "SECRET-UX-TEST" not in actual
                    report["checks"]["clipboard_exact_redacted_json"] = True
                    later(next_page)
                except Exception as exc:
                    fail(f"Clipboard: {type(exc).__name__}: {exc}")

            later(lambda: Gdk.Display.get_default().get_clipboard().read_text_async(None, clipboard_done), 250)
        later(focus_copy, 100)

    def next_page():
        find_button("Suivant").emit("clicked")
        until(lambda: len(broker.event_queries) >= 2 and
              find_label("15 événements affichés"), finish)

    def finish():
        assert broker.event_queries[-1]["offset"] == 50
        assert broker.event_queries[-1]["limit"] == 50
        assert broker.checks == 0
        assert plugin.llm is None
        report["checks"]["pagination_50_then_15"] = True
        screenshot("03-journals-second-page")
        if args.width == 1600:
            later(extended_keyboard, 120)
            return
        # D15: keyboard-only module filtering, using actual XTEST events.
        later(begin_keyboard_filter, 140)

    def entry_with_placeholder(placeholder):
        rows = [
            w for w in walk(page[0])
            if isinstance(w, Gtk.Entry)
            and w.get_placeholder_text() == placeholder
        ]
        assert len(rows) == 1, (placeholder, len(rows))
        return rows[0]

    def focus_inside(widget):
        # Gtk.Entry delegates keyboard focus to its internal Gtk.Text child:
        # entry.has_focus() can be False although the entry is visibly focused.
        current = window[0].get_focus()
        while current is not None:
            if current is widget:
                return True
            current = current.get_parent()
        return False

    def begin_keyboard_filter():
        assert args.width == 1366
        assert len(broker.event_queries) == 2
        module = entry_with_placeholder("Préfixe module (Entrée pour appliquer)")
        search = entry_with_placeholder("Rechercher dans cette page")
        assert module.grab_focus() is not False
        until(lambda: focus_inside(module),
              lambda: type_keyboard_module(module, search), seconds=5)

    def type_keyboard_module(module, search):
        report["stage"] = "D15_type_physical_module_filter"
        device[0].type_text("paris")
        until(lambda: module.get_text() == "paris",
              lambda: keyboard_tab_forward(module, search), seconds=5)

    def keyboard_tab_forward(module, search):
        # Typing should not fetch until Enter explicitly submits the filter.
        assert len(broker.event_queries) == 2
        report["checks"]["D15_typing_does_not_implicitly_query"] = True
        press("Tab")
        until(lambda: focus_inside(search),
              lambda: keyboard_tab_backward(module), seconds=5)

    def keyboard_tab_backward(module):
        device[0].key("Shift_L", True)
        try:
            press("Tab")
        finally:
            device[0].key("Shift_L", False)
        until(lambda: focus_inside(module),
              keyboard_filter_submit, seconds=5)

    def keyboard_filter_submit():
        report["checks"]["D15_tab_and_shift_tab_between_filters_xtest"] = True
        press("Return")
        until(lambda: len(broker.event_queries) == 3
              and broker.event_queries[-1]["module"] == "paris"
              and broker.event_queries[-1]["offset"] == 0
              and find_label("0 événements affichés"),
              keyboard_filter_result, seconds=8)

    def keyboard_filter_result():
        assert find_label("Aucun événement sur cette page")
        assert not find_button("Suivant").get_sensitive()
        assert not find_button("Précédent").get_sensitive()
        assert broker.checks == 0 and plugin.llm is None
        report["checks"]["D15_enter_applies_filter_and_resets_pagination"] = True
        screenshot("04-module-filter-enter-1366")
        module = entry_with_placeholder("Préfixe module (Entrée pour appliquer)")
        assert module.grab_focus() is not False
        until(lambda: focus_inside(module),
              lambda: clear_keyboard_filter(module), seconds=5)

    def clear_keyboard_filter(module):
        device[0].key("Control_L", True)
        try:
            press("a")
        finally:
            device[0].key("Control_L", False)
        press("BackSpace")
        until(lambda: module.get_text() == "",
              keyboard_filter_clear_submit, seconds=5)

    def keyboard_filter_clear_submit():
        assert len(broker.event_queries) == 3
        press("Return")
        until(lambda: len(broker.event_queries) == 4
              and broker.event_queries[-1]["module"] == ""
              and find_label("50 événements affichés"),
              keyboard_filter_restored, seconds=8)

    def keyboard_filter_restored():
        assert find_button("Suivant").get_sensitive()
        assert broker.checks == 0 and plugin.llm is None
        report["checks"]["D15_ctrl_a_delete_return_restores_results"] = True
        screenshot("05-module-filter-restored-1366")
        pass_scenario()

    def pass_scenario():
        later(begin_bulk_selection)

    def complete_scenario():
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

    def keyboard_action(label, done):
        button = find_button(label)
        assert button.get_sensitive(), label
        content = page[0].get_child()
        ok, bounds = button.compute_bounds(content)
        assert ok
        outer = page[0].get_vadjustment()
        outer.set_value(max(0, min(float(bounds.get_y()) - 60,
                                 outer.get_upper() - outer.get_page_size())))
        def focused():
            # GTK may reallocate/reparent a FlowBox child while the mapped
            # diagnostics window narrows to 760px. A one-shot grab_focus()
            # can be discarded during that layout tick. Retry the *real*
            # focus request; the XTEST key is sent only once focus is proven.
            def acquired_focus():
                if not button.get_mapped():
                    return False
                button.grab_focus()
                return button.has_focus()
            until(acquired_focus, activate, seconds=4)
        def activate():
            assert button.has_focus()
            press("space")
            later(done, 100)
        later(focused, 100)

    def selection_count(count):
        return find_label(f"{count} sélectionné(s) · portée :")

    def begin_bulk_selection():
        report["stage"] = "P0_2140_bulk_fixture"
        broker.bulk = True
        assert entry_with_placeholder("Préfixe module (Entrée pour appliquer)").get_text() == ""
        refresh = [w for w in walk(page[0]) if isinstance(w, Gtk.Button)
                   and w.get_tooltip_text() == "Actualiser les journaux sans synchronisation"]
        assert len(refresh) == 1
        refresh[0].emit("clicked")
        until(lambda: find_label("50 événements affichés"),
              lambda: keyboard_action("Erreurs + critiques (filtres)", wait_bulk_errors))

    def wait_bulk_errors():
        report["stage"] = "P0_2140_wait_57_errors"
        until(lambda: selection_count(57), verify_bulk_errors)

    def selection_preview():
        expander = find_expander("Aperçu des événements expurgés sélectionnés")
        assert expander.get_expanded()
        views = [w for w in walk(expander) if isinstance(w, Gtk.TextView)]
        assert len(views) == 1
        buf = views[0].get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def verify_bulk_errors():
        payload = selection_preview()
        value = json.loads(payload)
        assert len(value["events"]) == 57
        assert {e["severity"] for e in value["events"]} == {"error", "critical"}
        assert len({e["event_id"] for e in value["events"]}) == 57
        assert value["selection"]["scope"] == "all_accessible_filtered_pages"
        assert value["selection"]["pagination_consistency"] == "first_page_rechecked_non_atomic"
        assert value["selection"]["sources_partial_or_unavailable"] is True
        assert "SECRET-SELECTION" not in payload
        assert broker.scan_threads and all(t != broker.ui_thread for t in broker.scan_threads)
        assert any(q.get("severity") == "error" and q["offset"] == 50 for q in broker.event_queries)
        report["checks"]["P0_2140_errors_57_across_pages_keyboard_background_redacted"] = True
        screenshot("14-bulk-errors-57")
        if args.width == 1366:
            report["stage"] = "P0_2140_resize_selected_window_760"
            content = page[0].get_child()
            rows = []
            child = content.get_first_child()
            while child is not None:
                rows.append(min(10000, max(0, child.measure(Gtk.Orientation.HORIZONTAL, -1)[0])))
                child = child.get_next_sibling()
            minimum = window[0].measure(Gtk.Orientation.HORIZONTAL, -1)[0]
            report["stage"] = ("P0_2140_narrow_window_minimum" if minimum > 760
                               else "P0_2140_narrow_allocation_pending")
            if rows and max(rows) > 724:
                report["minimum_row"] = rows.index(max(rows))
            # Request the resize on the next GTK tick, after the current
            # selection/measurement callback finishes. Retain the exact
            # 760px bound and the original overflow/focus/clipboard checks.
            def request_narrow_bulk():
                try:
                    # set_default_size is only a hint for *initial*
                    # geometry. This window is already mapped at 1366px;
                    # request an actual X11 ConfigureNotify on its toplevel.
                    window[0].set_default_size(760, 790)
                    device[0].resize_toplevel(window[0], 760, 790)
                except Exception:
                    raise AssertionError("GTK/X11 760px resize request rejected") from None
                until(lambda: window[0].get_width() <= 760,
                      lambda: verify_narrow_bulk(payload), seconds=5)
            later(request_narrow_bulk, 120)
        else:
            keyboard_action("Copier la sélection", lambda: read_bulk_clipboard(payload))

    def verify_narrow_bulk(payload):
        assert 700 <= window[0].get_width() <= 760
        assert selection_count(57) and selection_preview() == payload
        horizontal = page[0].get_hadjustment()
        assert horizontal.get_upper() - horizontal.get_page_size() < 5
        actions = find_button("Erreurs + critiques (filtres)").get_parent().get_parent()
        assert isinstance(actions, Gtk.FlowBox)
        # Validate every actual GTK button allocation against the page width,
        # rather than assuming wrapping from the presence of a FlowBox.
        for button in [w for w in walk(actions) if isinstance(w, Gtk.Button)]:
            ok, bounds = button.compute_bounds(page[0])
            assert ok
            assert bounds.get_x() >= 0 and bounds.get_x() + bounds.get_width() <= page[0].get_width()
        report["checks"]["P0_2140_selected_window_760_without_horizontal_overflow"] = True
        screenshot("14b-bulk-errors-760")
        # The remaining real XTEST copy/page/invert/all/cancel/refusal sequence
        # now runs at760 too, with unchanged assertions and operation deadlines.
        keyboard_action("Copier la sélection", lambda: read_bulk_clipboard(payload))

    def read_bulk_clipboard(expected):
        def copied(clipboard, task):
            try:
                assert clipboard.read_text_finish(task) == expected
                report["checks"]["P0_2140_clipboard_exact_bulk_preview"] = True
                keyboard_action("Suivant", wait_bulk_page)
            except Exception as exc:
                fail(f"Bulk clipboard: {type(exc).__name__}: {exc}")
        Gdk.Display.get_default().get_clipboard().read_text_async(None, copied)

    def wait_bulk_page():
        until(lambda: find_label("15 événements affichés"), verify_bulk_page)

    def verify_bulk_page():
        assert selection_count(57)
        assert len(json.loads(selection_preview())["events"]) == 57
        report["checks"]["P0_2140_pagination_preserves_frozen_bulk_selection"] = True
        keyboard_action("Inverser cette page", wait_bulk_invert)

    def wait_bulk_invert():
        until(lambda: selection_count(15), verify_bulk_invert)

    def verify_bulk_invert():
        assert find_label("Inversion limitée à cette page")
        report["checks"]["P0_2140_invert_replaces_bulk_with_current_page"] = True
        keyboard_action("Effacer la sélection", wait_bulk_clear)

    def wait_bulk_clear():
        until(lambda: selection_count(0), verify_bulk_clear)

    def verify_bulk_clear():
        assert not find_button("Copier la sélection").get_sensitive()
        report["checks"]["P0_2140_clear_disables_export"] = True
        keyboard_action("Tout sélectionner (filtres)", wait_bulk_all)

    def wait_bulk_all():
        until(lambda: selection_count(65), verify_bulk_all)

    def verify_bulk_all():
        assert len(json.loads(selection_preview())["events"]) == 65
        assert {e["severity"] for e in json.loads(selection_preview())["events"]} == {
            "error", "critical", "warning"}
        report["checks"]["P0_2140_all_65_includes_warnings"] = True
        entry_with_placeholder("Rechercher dans cette page").set_text("no-match-fixture")
        assert selection_count(0)
        assert find_label("Sélection effacée après changement de filtres")
        assert not find_button("Copier la sélection").get_sensitive()
        report["checks"]["P0_2140_filter_change_explicitly_invalidates"] = True
        entry_with_placeholder("Rechercher dans cette page").set_text("")
        tasks.gate = Event()
        gate = tasks.gate
        keyboard_action("Erreurs + critiques (filtres)", lambda: wait_delayed_bulk(gate))

    def wait_delayed_bulk(gate):
        handle = tasks.selection_handle
        assert handle is not None and handle.started.is_set() and not handle.done()
        assert find_label("Sélection filtrée multi-page en cours")
        # GTK remains responsive while the real worker waits. Invalidate its
        # generation before releasing the delayed response, never by timing.
        entry_with_placeholder("Rechercher dans cette page").set_text("obsolete-fixture")
        assert handle.cancelled()
        gate.set()
        until(handle.done, lambda: later(verify_obsolete_bulk, 150))

    def verify_obsolete_bulk():
        assert selection_count(0)
        assert not find_button("Copier la sélection").get_sensitive()
        report["checks"]["P0_2140_late_cancelled_response_cannot_restore_selection"] = True
        entry_with_placeholder("Rechercher dans cette page").set_text("")
        broker.error_count = 201
        keyboard_action("Erreurs + critiques (filtres)", wait_bulk_refusal)

    def wait_bulk_refusal():
        until(lambda: find_label("diagnostic selection exceeds 200 events"), verify_bulk_refusal)

    def verify_bulk_refusal():
        assert selection_count(0)
        assert not find_button("Copier la sélection").get_sensitive()
        report["checks"]["P0_2140_over_200_explicit_refusal_no_partial_export"] = True
        assert broker.checks == 0 and plugin.llm is None
        screenshot("15-bulk-limit-refused")
        # Version refresh changes the public event-broker snapshot. Keep
        # legacy journal pagination and bulk-selection assertions intact:
        # run the independent version clipboard acceptance only afterwards.
        tab_key("État système", "system", versions_initial)

    def press(key):
        device[0].key(key, True)
        device[0].key(key, False)

    def extended_keyboard():
        # Deliberately keep the 1366 baseline unchanged: this adds only
        # a large-display, long-report and physical keyboard exercise.
        assert args.width == 1600
        assert window[0].get_width() <= args.width
        expander = find_expander("Ouvrir le rapport support")
        assert expander.get_expanded(), "Rapport refermé pendant la navigation"
        scrollers = [w for w in walk(expander) if isinstance(w, Gtk.ScrolledWindow)]
        texts = [w for w in walk(expander) if isinstance(w, Gtk.TextView)]
        wraps = [w for w in walk(expander) if isinstance(w, Gtk.CheckButton)
                 and w.get_label() == "Retour à la ligne"]
        assert len(scrollers) == len(texts) == len(wraps) == 1
        text = texts[0].get_buffer()
        contents = text.get_text(text.get_start_iter(), text.get_end_iter(), False)
        assert contents.count("Événement fictif.") >= 250, "Rapport multipage absent"
        assert "SECRET-UX-TEST" not in contents
        assert "EXEMPLE-PRIVE-UX" not in contents
        report["checks"]["long_redacted_report_1600"] = True
        resize = find_button("Agrandir")
        # Journals paging can leave the report below the visible viewport.
        # Scroll the real outer page to the actual GTK button allocation
        # before asking for keyboard focus: no guessed pixel coordinates.
        content = page[0].get_child()
        ok, bounds = resize.compute_bounds(content)
        assert ok, "Position GTK du bouton Agrandir introuvable"
        outer = page[0].get_vadjustment()
        destination = max(0, min(
            float(bounds.get_y()) - 36,
            outer.get_upper() - outer.get_page_size(),
        ))
        outer.set_value(destination)
        report["outer_scroll_to_report"] = round(destination, 2)
        report["stage"] = "scroll_to_report_button"
        later(lambda: focus_report_resize(resize, wraps[0], scrollers[0]), 200)

    def focus_report_resize(resize, wrap, scroller):
        report["stage"] = "focus_report_resize"
        focused = resize.grab_focus()
        assert focused is not False, "Agrandir refuse le focus clavier GTK"
        until(lambda: resize.has_focus(),
              lambda: activate_report_resize(resize, wrap, scroller), seconds=4)

    def activate_report_resize(resize, wrap, scroller):
        report["stage"] = "activate_resize_space_xtest"
        assert resize.has_focus(), "Agrandir n'a pas de focus clavier"
        # A real XTEST key event changes the GTK button and its scroller.
        press("space")
        until(lambda: resize.get_label() == "Réduire" and
              scroller.get_size_request()[1] == 560,
              lambda: tab_forward(resize, wrap, scroller), seconds=4)

    def tab_forward(resize, wrap, scroller):
        report["stage"] = "tab_forward_xtest"
        assert resize.has_focus(), "Le focus a quitté le bouton après Space"
        press("Tab")
        until(lambda: wrap.has_focus(),
              lambda: tab_backward(resize, wrap, scroller), seconds=4)

    def tab_backward(resize, wrap, scroller):
        report["stage"] = "shift_tab_back_xtest"
        assert wrap.has_focus(), "Tab ne rejoint pas Retour à la ligne"
        device[0].key("Shift_L", True)
        try:
            press("Tab")
        finally:
            device[0].key("Shift_L", False)
        until(lambda: resize.has_focus(),
              lambda: verify_tab(resize, scroller), seconds=4)

    def verify_tab(resize, scroller):
        report["stage"] = "verify_scroll_independent"
        assert resize.has_focus(), "Shift+Tab ne revient pas à Agrandir/Réduire"
        report["checks"]["tab_shift_tab_and_space_xtest"] = True
        screenshot("04-report-keyboard-1600")
        adjustment = scroller.get_vadjustment()
        bottom = max(0, adjustment.get_upper() - adjustment.get_page_size())
        assert bottom > 500, ("Rapport sans défilement interne", bottom)
        outer = page[0].get_vadjustment()
        outer_before = outer.get_value()
        adjustment.set_value(bottom)
        later(lambda: verify_scrolled(scroller, outer_before, bottom), 200)

    def verify_scrolled(scroller, outer_before, bottom):
        adjustment = scroller.get_vadjustment()
        assert adjustment.get_value() >= bottom - 3, (
            "Bas du rapport inaccessible", adjustment.get_value(), bottom
        )
        outer = page[0].get_vadjustment()
        assert abs(outer.get_value() - outer_before) <= 4, (
            "Défilement interne modifie la page", outer_before, outer.get_value()
        )
        report["checks"]["independent_scroll_to_report_end"] = True
        report["scroll"] = {
            "inner_bottom": round(bottom, 2),
            "inner_value": round(adjustment.get_value(), 2),
            "outer_before": round(outer_before, 2),
            "outer_after": round(outer.get_value(), 2),
        }
        screenshot("05-report-bottom-1600")
        assert broker.checks == 0 and plugin.llm is None
        # D16: resize the *visible* GTK window; Xvfb remains 1600x1000.
        report["stage"] = "resize_visible_window_narrow"
        report["window_before_resize"] = [
            window[0].get_width(), window[0].get_height()
        ]
        window[0].set_default_size(1240, 800)
        until(
            lambda: window[0].get_width() <= 1280
            and window[0].get_height() <= 820,
            resized_narrow,
            seconds=6,
        )

    def resized_narrow():
        report["stage"] = "validate_resize_narrow"
        window_width = window[0].get_width()
        window_height = window[0].get_height()
        assert window_width >= 760, ("Fenêtre trop petite", window_width)
        assert window_height >= 540, ("Fenêtre trop basse", window_height)
        assert page[0].get_mapped() and find_label("Rapport support")
        assert find_label("Journaux par composant")
        horizontal = page[0].get_hadjustment()
        overflow = max(0, horizontal.get_upper() - horizontal.get_page_size())
        assert overflow < 5, ("Débordement horizontal global", overflow)
        report["window_narrow"] = [window_width, window_height]
        report["horizontal_overflow_narrow"] = round(overflow, 2)
        screenshot("06-live-resized-narrow-1600")
        window[0].set_default_size(1560, 870)
        until(
            lambda: window[0].get_width() >= 1510
            and window[0].get_height() >= 840,
            resized_wide,
            seconds=6,
        )

    def resized_wide():
        report["stage"] = "validate_resize_wide"
        assert page[0].get_mapped() and find_label("Rapport support")
        assert window[0].get_width() > report["window_narrow"][0]
        report["window_wide_again"] = [
            window[0].get_width(), window[0].get_height()
        ]
        report["checks"]["live_resize_narrow_and_wide_without_global_horizontal_overflow"] = True
        screenshot("07-live-resized-wide-1600")
        # D05/D15: reduce the actual long support report with Enter XTEST,
        # retaining the focus and an already selected portion of its text.
        # This must not be confused with the still-unimplemented Escape action.
        report["stage"] = "keyboard_enter_reduce"
        expander = find_expander("Ouvrir le rapport support")
        report_view = [w for w in walk(expander) if isinstance(w, Gtk.TextView)]
        assert len(report_view) == 1
        buffer = report_view[0].get_buffer()
        assert buffer.get_char_count() > 4000
        buffer.select_range(buffer.get_end_iter(), buffer.get_start_iter())
        assert buffer.get_has_selection()
        report["audit"] = {"D05_selection_before_reduce": True}
        resize = find_button("Réduire")
        outer = page[0].get_vadjustment()
        content = page[0].get_child()
        ok, bounds = resize.compute_bounds(content)
        assert ok
        outer.set_value(max(0, min(
            float(bounds.get_y()) - 36,
            outer.get_upper() - outer.get_page_size(),
        )))
        later(lambda: focus_enter_reduce(expander, resize, buffer), 180)

    def focus_enter_reduce(expander, resize, buffer):
        assert resize.grab_focus() is not False
        until(lambda: resize.has_focus(),
              lambda: press_enter_reduce(expander, resize, buffer), seconds=4)

    def press_enter_reduce(expander, resize, buffer):
        report["stage"] = "enter_xtest_reduce"
        assert resize.has_focus()
        press("Return")
        until(lambda: resize.get_label() == "Agrandir"
              and [w for w in walk(expander) if isinstance(w, Gtk.ScrolledWindow)][0]
                  .get_size_request()[1] == 320,
              lambda: verify_enter_reduce(expander, resize, buffer), seconds=4)

    def verify_enter_reduce(expander, resize, buffer):
        assert resize.has_focus(), "Le focus a quitté le bouton après Entrée"
        assert buffer.get_has_selection(), "La sélection a disparu à la réduction"
        report["checks"]["D05_report_reduced_with_selection_preserved"] = True
        report["checks"]["D15_return_xtest_activates_button_and_keeps_focus"] = True
        screenshot("08-report-reduced-enter-1600")
        report["stage"] = "enter_xtest_reexpand"
        press("Return")
        until(lambda: resize.get_label() == "Réduire"
              and [w for w in walk(expander) if isinstance(w, Gtk.ScrolledWindow)][0]
                  .get_size_request()[1] == 560,
              lambda: verify_enter_reexpand(expander, resize, buffer), seconds=4)

    def verify_enter_reexpand(expander, resize, buffer):
        assert resize.has_focus(), "Le focus n'est pas revenu à la réouverture"
        assert buffer.get_has_selection(), "La sélection a disparu à la réouverture"
        report["checks"]["D05_reexpand_preserves_focus_and_selection"] = True
        screenshot("09-report-reexpanded-enter-1600")
        # D05: use a genuine XTEST mouse-wheel event, not a direct GTK
        # adjustment assignment. A direct assignment is only used to reset
        # the test position and bring the control into the outer viewport.
        later(prepare_wheel_scroll, 130)

    def prepare_wheel_scroll():
        report["stage"] = "D05_prepare_physical_wheel"
        expander = find_expander("Ouvrir le rapport support")
        scrollers = [w for w in walk(expander) if isinstance(w, Gtk.ScrolledWindow)]
        views = [w for w in walk(expander) if isinstance(w, Gtk.TextView)]
        assert len(scrollers) == len(views) == 1
        scroller, view = scrollers[0], views[0]
        content = page[0].get_child()
        ok, bounds = scroller.compute_bounds(content)
        assert ok, "Rapport scroller not allocated"
        outer = page[0].get_vadjustment()
        outer.set_value(max(0, min(
            float(bounds.get_y()) - 70,
            outer.get_upper() - outer.get_page_size(),
        )))
        later(lambda: send_wheel_scroll(scroller, view), 260)

    def send_wheel_scroll(scroller, view):
        report["stage"] = "D05_xtest_mousewheel_inside_report"
        inner = scroller.get_vadjustment()
        assert inner.get_upper() - inner.get_page_size() > 400
        # Gtk.TextView can allocate a taller rectangle than the clipped
        # viewport. Its geometric centre can be near/below the window edge,
        # causing the outer page to handle a real XTEST wheel event instead.
        # Target the Gtk.ScrolledWindow viewport itself, whose visible centre
        # is unambiguous. Keep the strict outer-scroll assertion unchanged.
        pointed = device[0].click_widget(scroller)
        # Gtk.ScrolledWindow itself may be partly clipped by the page.
        # Reposition the XTEST pointer inside the intersection of its GTK
        # allocation with the VISIBLE parent viewport. The first two traces
        # selected y=804 (near the lower window edge) on both widget types.
        ok, box = scroller.compute_bounds(page[0])
        assert ok
        visible_top = max(0, float(box.get_y()))
        visible_bottom = min(
            float(page[0].get_height()),
            float(box.get_y() + box.get_height()),
        )
        assert visible_bottom - visible_top > 120, (
            "Report viewport too small for physical mouse wheel",
            visible_top, visible_bottom,
        )
        widget_middle = float(box.get_y() + box.get_height() / 2)
        visible_middle = (visible_top + visible_bottom) / 2
        new_y = pointed["y"] + round(visible_middle - widget_middle)
        assert 40 < new_y < 900, ("Wheel pointer outside Xvfb", new_y)
        device[0].xtst.XTestFakeMotionEvent(
            device[0].display, -1, pointed["x"], new_y, 0,
        )
        device[0].x11.XSync(device[0].display, False)
        report["wheel_pointer"] = {
            **pointed, "y": new_y, "clipped_viewport_height": round(
                visible_bottom - visible_top, 2
            ),
        }
        # Let GTK process the XTEST pointer crossing before the wheel. XSync
        # flushes X11 but does not itself iterate the GTK/GLib event loop.
        later(lambda: issue_wheel_scroll(scroller, view), 160)

    def issue_wheel_scroll(scroller, view):
        inner = scroller.get_vadjustment()
        inner.set_value(0)
        outer_before = page[0].get_vadjustment().get_value()
        report["wheel_outer_before"] = round(outer_before, 2)
        for _ in range(4):
            device[0].xtst.XTestFakeButtonEvent(device[0].display, 5, True, 0)
            device[0].xtst.XTestFakeButtonEvent(device[0].display, 5, False, 0)
        device[0].x11.XSync(device[0].display, False)
        until(lambda: inner.get_value() > 8,
              lambda: verify_wheel_scroll(scroller, view, outer_before),
              seconds=5)

    def verify_wheel_scroll(scroller, view, outer_before):
        report["stage"] = "D05_verify_xtest_wheel"
        inner = scroller.get_vadjustment()
        outer = page[0].get_vadjustment()
        assert inner.get_value() > 8, "XTEST wheel did not scroll inside report"
        outer_fixed = abs(outer.get_value() - outer_before) <= 5
        report.setdefault("audit", {})["D05_xtest_wheel_outer_page_fixed"] = outer_fixed
        report["wheel_inner_after"] = round(inner.get_value(), 2)
        report["wheel_outer_after"] = round(outer.get_value(), 2)
        assert outer_fixed, (
            "D05: molette XTEST fait aussi défiler la page externe",
            outer_before, outer.get_value(),
        )
        # Detect delayed ancestor scrolling as well, after the guard expires.
        later(lambda: verify_wheel_settled(view, outer_before), 530)

    def verify_wheel_settled(view, outer_before):
        outer = page[0].get_vadjustment()
        assert abs(outer.get_value() - outer_before) <= 5, (
            "D05: déplacement retardé de la page après la molette",
            outer_before, outer.get_value(),
        )
        report["checks"]["D05_xtest_wheel_keeps_outer_page_fixed"] = True
        screenshot("09b-report-physical-wheel-1600")
        # A nested scroller must not trap the physical wheel at its lower
        # bound. After the parent guard expires, the same XTEST wheel must
        # propagate to the outer Diagnostics page when the report is at end.
        later(lambda: prepare_wheel_at_report_edge(view), 130)

    def prepare_wheel_at_report_edge(view):
        report["stage"] = "D05_prepare_wheel_at_report_edge"
        scroller = view.get_parent()
        while scroller is not None and not isinstance(scroller, Gtk.ScrolledWindow):
            scroller = scroller.get_parent()
        assert scroller is not None, "D05: support report scroll parent missing"
        inner = scroller.get_vadjustment()
        inner_max = max(inner.get_lower(), inner.get_upper() - inner.get_page_size())
        assert inner_max > 400, "D05: edge test requires a long report"
        inner.set_value(inner_max)

        outer = page[0].get_vadjustment()
        outer_max = max(outer.get_lower(), outer.get_upper() - outer.get_page_size())
        # Keep room below the page scroll position; otherwise a valid
        # propagation would be invisible when the parent is already at end.
        outer.set_value(min(outer.get_value(), max(outer.get_lower(), outer_max - 180)))
        # The parent was repositioned to leave scroll room, so the report
        # has moved on screen. Re-aim the physical XTEST pointer into the
        # newly visible part of its viewport before testing propagation.
        pointed = device[0].click_widget(scroller)
        ok, box = scroller.compute_bounds(page[0])
        assert ok, "D05: edge report bounds unavailable"
        visible_top = max(0, float(box.get_y()))
        visible_bottom = min(
            float(page[0].get_height()),
            float(box.get_y() + box.get_height()),
        )
        assert visible_bottom - visible_top > 100, (
            "D05: report viewport clipped after page reposition",
            visible_top, visible_bottom,
        )
        widget_middle = float(box.get_y() + box.get_height() / 2)
        new_y = pointed["y"] + round(
            (visible_top + visible_bottom) / 2 - widget_middle
        )
        assert 40 < new_y < 900, ("D05: edge pointer outside Xvfb", new_y)
        device[0].xtst.XTestFakeMotionEvent(
            device[0].display, -1, pointed["x"], new_y, 0,
        )
        device[0].x11.XSync(device[0].display, False)
        report["wheel_edge_pointer"] = {
            **pointed, "y": new_y,
            "clipped_viewport_height": round(visible_bottom - visible_top, 2),
        }

        outer_before = outer.get_value()
        assert outer_max - outer_before > 20, "D05: no outer scroll range for edge test"
        assert abs(inner.get_value() - inner_max) <= 2, "D05: report not at bottom"
        report["wheel_edge_outer_before"] = round(outer_before, 2)
        report["wheel_edge_inner_before"] = round(inner.get_value(), 2)
        # Let GLib dispatch pointer crossing; XSync alone only flushes X11.
        later(lambda: send_wheel_at_report_edge(view, inner_max, outer_before), 180)

    def send_wheel_at_report_edge(view, inner_max, outer_before):
        report["stage"] = "D05_xtest_wheel_at_report_bottom"
        for _ in range(4):
            device[0].xtst.XTestFakeButtonEvent(device[0].display, 5, True, 0)
            device[0].xtst.XTestFakeButtonEvent(device[0].display, 5, False, 0)
        device[0].x11.XSync(device[0].display, False)
        # Measure after GTK has handled the physical input. A positive
        # timeout otherwise hides the actual edge values behind a generic
        # "condition GTK" failure and wastes five seconds per run.
        later(lambda: verify_wheel_at_report_edge(view, inner_max, outer_before), 700)

    def verify_wheel_at_report_edge(view, inner_max, outer_before):
        report["stage"] = "D05_verify_wheel_at_report_bottom"
        scroller = view.get_parent()
        while scroller is not None and not isinstance(scroller, Gtk.ScrolledWindow):
            scroller = scroller.get_parent()
        assert scroller is not None
        inner_after = scroller.get_vadjustment().get_value()
        outer_after = page[0].get_vadjustment().get_value()
        report["wheel_edge_inner_after"] = round(inner_after, 2)
        report["wheel_edge_outer_after"] = round(outer_after, 2)
        inside_at_end = abs(inner_after - inner_max) <= 2
        outer_propagated = outer_after > outer_before + 5
        report.setdefault("audit", {})[
            "D05_xtest_wheel_at_inner_edge_scrolls_outer_page"
        ] = outer_propagated
        # Save the actual post-wheel GTK state even on a genuine product FAIL.
        screenshot("09bb-report-wheel-edge-propagates-1600")
        assert inside_at_end, (
            "D05: inner adjustment should stay at its lower edge", inner_after, inner_max,
        )
        assert outer_propagated, (
            "D05: physical wheel is trapped by report at the lower edge",
            outer_before, outer_after,
        )
        report["checks"]["D05_xtest_wheel_at_inner_edge_scrolls_outer_page"] = True
        select_support_report(view)

    def select_support_report(view):
        # D13: use physical Ctrl+C on the actual SUPPORT TextView, not the
        # already-tested structured diagnostic detail TextView.
        report["stage"] = "D13_select_support_text"
        buffer = view.get_buffer()
        begin = buffer.get_start_iter()
        end = buffer.get_iter_at_offset(160)
        buffer.select_range(end, begin)
        assert buffer.get_has_selection()
        sel_start, sel_end = buffer.get_selection_bounds()
        expected = buffer.get_text(sel_start, sel_end, False)
        assert "RAPPORT SUPPORT EXPURGÉ" in expected
        assert "SECRET-UX-TEST" not in expected
        assert "EXEMPLE-PRIVE-UX" not in expected
        assert view.grab_focus() is not False
        until(lambda: view.has_focus(),
              lambda: copy_support_xtest(view, expected), seconds=5)

    def copy_support_xtest(view, expected):
        report["stage"] = "D13_ctrl_c_support_report_xtest"
        assert view.has_focus()
        device[0].key("Control_L", True)
        try:
            press("c")
        finally:
            device[0].key("Control_L", False)
        later(lambda: Gdk.Display.get_default().get_clipboard().read_text_async(
            None,
            lambda clipboard, task: support_clipboard_done(
                clipboard, task, expected
            ),
        ), 260)

    def support_clipboard_done(clipboard, task, expected):
        try:
            actual = clipboard.read_text_finish(task)
            assert actual == expected, (
                "Le presse-papiers ne contient pas exactement la sélection support",
                repr((actual or "")[:100]),
            )
            assert "SECRET-UX-TEST" not in actual
            report["checks"]["D13_support_textview_ctrl_c_exact_selection"] = True
            report["support_selection_length"] = len(actual)
            screenshot("09c-support-report-ctrl-c-1600")
            # Existing Escape/export audits remain negative until #1158
            # delivers an actual UI feature; retain these as unmet criteria.
            later(begin_escape_audit, 130)
        except Exception as exc:
            fail(f"D13 support clipboard: {type(exc).__name__}: {exc}")

    def begin_escape_audit():
        # Esc and file export remain separate observations; neither should be
        # passed solely because Enter works and a clipboard action exists.
        report["stage"] = "audit_escape_and_file_export"
        expander = find_expander("Ouvrir le rapport support")
        report.setdefault("audit", {})["report_open_before_escape"] = expander.get_expanded()
        focus = find_button("Réduire")
        outer = page[0].get_vadjustment()
        content = page[0].get_child()
        ok, bounds = focus.compute_bounds(content)
        assert ok
        outer.set_value(max(0, min(
            float(bounds.get_y()) - 36,
            outer.get_upper() - outer.get_page_size(),
        )))
        later(lambda: audit_escape(focus, expander), 170)

    def audit_escape(focus, expander):
        assert focus.grab_focus() is not False
        until(lambda: focus.has_focus(),
              lambda: send_escape(expander), seconds=4)

    def send_escape(expander):
        report["stage"] = "observe_escape_xtest"
        press("Escape")
        later(lambda: finish_audit(expander), 260)

    def finish_audit(expander):
        report["stage"] = "finish_audit"
        reduced = find_button("Agrandir") if any(
            isinstance(w, Gtk.Button) and w.get_label() == "Agrandir"
            for w in walk(page[0])
        ) else None
        esc_closes_enlargement = (
            reduced is not None or not expander.get_expanded()
        )
        export_buttons = [
            (w.get_label() or "", w.get_tooltip_text() or "")
            for w in walk(page[0]) if isinstance(w, Gtk.Button)
            and ("export" in ((w.get_label() or "") + " " +
                             (w.get_tooltip_text() or "")).casefold()
                 or "enregistrer" in ((w.get_label() or "") + " " +
                                     (w.get_tooltip_text() or "")).casefold())
        ]
        report["audit"].update({
            "escape_collapses_or_reduces_report": esc_closes_enlargement,
            "report_open_after_escape": expander.get_expanded(),
            "file_export_action_present": bool(export_buttons),
            "file_export_actions": export_buttons,
            "scope": "Observation de la vraie UI; aucun export de fichier simulé",
        })
        report.setdefault("unmet_criteria", []).extend(
            key for key, ok in (
                ("D15: Escape ferme l'agrandissement", esc_closes_enlargement),
                ("D13: export support explicite vers un fichier", bool(export_buttons)),
            ) if not ok
        )
        screenshot("10-escape-export-audit-1600")
        assert broker.checks == 0 and plugin.llm is None
        # D13: prove Ctrl+C only copies a selection in the REAL support
        # report, not an entire block or the previous JSON clipboard.
        later(lambda: begin_support_selection_copy(expander), 130)

    def begin_support_selection_copy(expander):
        report["stage"] = "D13_select_support_excerpt"
        assert expander.get_expanded()
        views = [w for w in walk(expander) if isinstance(w, Gtk.TextView)]
        assert len(views) == 1
        view = views[0]
        buf = view.get_buffer()
        full = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        prefix = "RAPPORT SUPPORT EXPURGÉ — FIXTURE UX"
        assert full.startswith(prefix) and len(full) > len(prefix) * 10
        start_iter = buf.get_start_iter()
        end_iter = buf.get_iter_at_offset(len(prefix))
        buf.select_range(start_iter, end_iter)
        assert buf.get_has_selection()
        report["audit"]["D13_support_selected_chars"] = len(prefix)
        assert view.grab_focus() is not False
        until(lambda: view.has_focus(),
              lambda: send_support_selection_copy(expander, full, prefix),
              seconds=5)

    def send_support_selection_copy(expander, full, prefix):
        report["stage"] = "D13_ctrl_c_support_excerpt_xtest"
        device[0].key("Control_L", True)
        try:
            press("c")
        finally:
            device[0].key("Control_L", False)
        later(lambda: Gdk.Display.get_default().get_clipboard().read_text_async(
            None,
            lambda clipboard, task: verify_support_selection_copy(
                clipboard, task, expander, full, prefix),
        ), 260)

    def verify_support_selection_copy(clipboard, task, expander, full, prefix):
        try:
            selected = clipboard.read_text_finish(task)
            assert selected == prefix, ("Ctrl+C n'a pas copié l'extrait exact", repr(selected))
            assert selected != full
            assert "SECRET-UX-TEST" not in selected
            assert "EXEMPLE-PRIVE-UX" not in selected
            assert expander.get_expanded()
            report["checks"]["D13_support_partial_selection_ctrl_c_xtest"] = True
            screenshot("11-support-excerpt-ctrl-c-1600")
            later(lambda: focus_support_copy_button(expander, full), 130)
        except Exception as exc:
            fail(f"Ctrl+C support: {type(exc).__name__}: {exc}")

    def focus_support_copy_button(expander, full):
        report["stage"] = "D13_focus_support_full_copy_button"
        buttons = [
            w for w in walk(page[0])
            if isinstance(w, Gtk.Button)
            and w.get_tooltip_text() == "Copier le diagnostic support expurgé"
        ]
        assert len(buttons) == 1
        button = buttons[0]
        content = page[0].get_child()
        ok, bounds = button.compute_bounds(content)
        assert ok
        outer = page[0].get_vadjustment()
        outer.set_value(max(0, min(
            float(bounds.get_y()) - 32,
            outer.get_upper() - outer.get_page_size(),
        )))
        later(lambda: trigger_support_full_copy(button, expander, full), 220)

    def trigger_support_full_copy(button, expander, full):
        assert button.grab_focus() is not False
        until(lambda: button.has_focus(),
              lambda: press_support_full_copy(expander, full), seconds=5)

    def press_support_full_copy(expander, full):
        report["stage"] = "D13_copy_full_support_button_xtest"
        press("space")
        later(lambda: Gdk.Display.get_default().get_clipboard().read_text_async(
            None,
            lambda clipboard, task: verify_support_full_copy(
                clipboard, task, expander, full),
        ), 270)

    def verify_support_full_copy(clipboard, task, expander, full):
        try:
            copied = clipboard.read_text_finish(task)
            assert copied == full, "Copier support doit renvoyer le rapport complet"
            assert "RAPPORT SUPPORT EXPURGÉ" in copied
            assert copied.count("Événement fictif.") >= 250
            assert "SECRET-UX-TEST" not in copied
            assert "EXEMPLE-PRIVE-UX" not in copied
            report["checks"]["D13_support_full_copy_button_xtest"] = True
            report["audit"]["D13_copy_selected_vs_full_distinct"] = True
            screenshot("12-support-full-copy-1600")
            # Closing the expander after interacting with both clipboard paths
            # must preserve the existing focus/selection observations for D05.
            later(lambda: audit_expander_keyboard_close(expander), 120)
        except Exception as exc:
            fail(f"Copie support complète: {type(exc).__name__}: {exc}")

    def audit_expander_keyboard_close(expander):
        report["stage"] = "audit_report_close_focus"
        views = [w for w in walk(expander) if isinstance(w, Gtk.TextView)]
        assert len(views) == 1
        buffer = views[0].get_buffer()
        # Re-establish a known selection immediately before the close action:
        # previous D13 clipboard/focus gestures may have cleared it already.
        buffer.select_range(buffer.get_end_iter(), buffer.get_start_iter())
        assert buffer.get_has_selection(), "Sélection préalable D05 introuvable"
        report["audit"]["D05_selection_reestablished_before_close"] = True
        focusable = expander.grab_focus() is not False and expander.has_focus()
        report["audit"]["D05_expander_focusable"] = focusable
        report["audit"]["D05_selection_after_focus_transfer"] = buffer.get_has_selection()
        if focusable:
            press("Return")
        later(lambda: finish_expander_audit(expander, buffer, focusable), 280)

    def finish_expander_audit(expander, buffer, focusable):
        collapsed = not expander.get_expanded()
        focused = expander.has_focus()
        selected = buffer.get_has_selection()
        report["audit"]["D05_return_collapsed_expander"] = focusable and collapsed
        report["audit"]["D05_focus_after_collapse"] = focused if collapsed else False
        report["audit"]["D05_text_selection_after_collapse"] = selected
        if focusable and collapsed and focused and selected:
            report["checks"]["D05_keyboard_collapse_focus_and_selection"] = True
        else:
            report["unmet_criteria"].append(
                "D05: perte de la sélection pendant transfert de focus ou fermeture du volet Entrée"
            )
        screenshot("13-report-collapse-focus-audit-1600")
        pass_scenario()

    class Application(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.DiagnosticsUXProbe")

        def do_activate(self):
            window[0] = Gtk.ApplicationWindow(application=self)
            window[0].set_title("Diagnostics UX GTK isolé")
            window[0].set_default_size(args.width - 40, 870 if args.width == 1600 else 790)
            device[0] = X11Input()
            root = plugin._build(host)
            window[0].set_child(root)
            # The Diagnostics root now captures nested wheel events before
            # GTK's outer ScrolledWindow; keep existing viewport assertions.
            outer_scrollers = [
                w for w in walk(root) if isinstance(w, Gtk.ScrolledWindow)
            ]
            assert outer_scrollers, "Page scroller absent"
            page[0] = outer_scrollers[0]
            window[0].present()
            until(lambda: find_label("50 événements affichés"), initial)

        def do_shutdown(self):
            plugin.deactivate()
            if device[0] is not None:
                device[0].close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(
        65, lambda: (fail("Timeout GTK 65s"), False)[1]
        if report["status"] == "RUNNING" else False)
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report.update(status="FAIL", error="La fenêtre GTK s'est arrêtée sans rapport")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
