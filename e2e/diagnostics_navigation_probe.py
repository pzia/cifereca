#!/usr/bin/env python3
"""UX #1011: navigate from the real Cifereca Library shell to Diagnostics via XTEST.

Unlike diagnostics_page_probe, this mounts the real Diagnostics page contribution
in the real MainWindow. The plugin broker is deliberately local and deterministic;
this does not exercise ZIP installation or a live provider.
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
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/plugins/diagnostics-page"))
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "environnements/openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageGrab

from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow
from cifereca.builtin_ui.cifereca_builtin_ui import Pages
from diagnostics_page_probe import Broker, Context, Host, Tasks
from diagnostics_states_probe import StateTasks
from x11_input import X11Input


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


class SlowNavigationBroker(Broker):
    """Slow task only when explicitly clicked; never block GTK's main loop."""

    def active_checks(self):
        self.checks += 1
        sleep(1.15)
        return [{"status": "ok", "message": "Contrôle local fictif terminé"}]


class IsolatedContributions:
    """Real page contributions, no installed packages and no network providers."""

    def __init__(self, diagnostics_page):
        self.diagnostics_page = diagnostics_page

    def page_contributions(self):
        return [*Pages().pages(), *self.diagnostics_page.pages()]

    def statuses(self):
        return []

    def diagnostic(self):
        return []

    def package_inventory(self, *, include_expected=False):
        return []

    def widget_catalog(self):
        return None

    def select(self, _capability):
        return None

    def services(self, _capability):
        return ()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1366,), required=True)
    parser.add_argument("--slow-check", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("GTK/X11/Xvfb required")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    broker = SlowNavigationBroker() if args.slow_check else Broker()
    tasks = StateTasks() if args.slow_check else Tasks()
    page_plugin = importlib.import_module("cifereca_diagnostics_page").DiagnosticsPagePlugin()
    page_plugin.activate(Context(broker, tasks))
    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)
    started = monotonic()
    report = {
        "status": "RUNNING",
        "scenario": "diagnostics-navigation-slow-1366" if args.slow_check
                    else "diagnostics-navigation-1366",
        "width": args.width, "git_sha": sha, "backend": "GTK4/X11/XTEST",
        "scope": "real MainWindow and real Diagnostics page contribution, simulated brokers",
        "checks": {}, "screenshots": [], "pointer_actions": [],
    }
    window = [None]
    device = [None]
    rc = [1]
    diagnostics_deadline = [None]
    slow_started = [None]

    def save():
        report["duration_seconds"] = round(monotonic() - started, 2)
        report["active_checks"] = broker.checks
        report["event_queries"] = len(broker.event_queries)
        (out / "screen.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    def capture(stage):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert image.size == (args.width, 900), image.size
        assert image.getbbox() is not None
        file = out / (stage + ".png")
        image.save(file)
        shutil.copyfile(file, out / "screen.png")
        selected = window[0].nav_list.get_selected_row()
        report["screenshots"].append({
            "name": stage, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "route": window[0].view_stack.get_visible_child_name(),
            "selected_navigation": selected.get_name() if selected else None,
        })

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        report["status"] = "FAIL"
        report["error"] = str(exc)
        if window[0] is not None:
            try:
                capture("failure")
            except Exception:
                pass
        save()
        app.quit()

    def later(delay_ms, callback):
        def call():
            try:
                callback()
            except Exception as exc:
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(delay_ms, call)

    def await_route(route, callback, max_wait=8):
        deadline = monotonic() + max_wait

        def check():
            if window[0].view_stack.get_visible_child_name() == route:
                callback()
            elif monotonic() > deadline:
                raise AssertionError(f"Navigation {route!r} non observée")
            else:
                later(90, check)
        later(140, check)

    def labels_have(text):
        page = window[0]._plugin_page_widgets["diagnostics"]
        return any(
            isinstance(w, Gtk.Label) and text in (w.get_text() or "")
            for w in walk(page)
        )

    def initial():
        w = window[0]
        assert "library" in w.nav_rows and "diagnostics" in w.nav_rows
        assert w.view_stack.get_visible_child_name() == "dashboard"
        assert not w.nav_rows["diagnostics"].get_mapped(), "Configuration doit être repliée"
        report["checks"]["real_mainwindow_and_plugin_contribution"] = True
        capture("00-real-cifereca-shell")
        report["pointer_actions"].append(device[0].click_widget(w.nav_rows["library"]))
        await_route("library", library_open)

    def library_open():
        w = window[0]
        selected = w.nav_list.get_selected_row()
        assert selected is w.nav_rows["library"]
        assert w.root_stack.get_visible_child_name() == "main"
        report["checks"]["physical_library_navigation"] = True
        capture("01-library-selected")
        toggles = [
            widget for widget in walk(w.sidebar)
            if isinstance(widget, Gtk.ToggleButton)
            and "Configuration / Technique" in (widget.get_tooltip_text() or "")
        ]
        assert len(toggles) == 1, f"Configuration group: {len(toggles)}"
        assert not toggles[0].get_active()
        report["pointer_actions"].append(device[0].click_widget(toggles[0]))
        later(250, config_expanded)

    def config_expanded():
        w = window[0]
        assert w.nav_rows["diagnostics"].get_visible()
        assert w.nav_rows["diagnostics"].get_mapped()
        report["checks"]["physical_configuration_group_expansion"] = True
        report["pointer_actions"].append(device[0].click_widget(w.nav_rows["diagnostics"]))
        diagnostics_deadline[0] = monotonic() + 9
        await_route("diagnostics", diagnostics_open)

    def diagnostics_open():
        w = window[0]
        visible = {
            "selected": w.nav_list.get_selected_row() is w.nav_rows["diagnostics"],
            "subtitle": w.header_subtitle.get_text() == "Diagnostics",
            "mapped": w._plugin_page_widgets["diagnostics"].get_mapped(),
            "report": labels_have("Rapport support"),
            "journals": labels_have("Journaux par composant"),
            "system": labels_have("État système"),
            "page_loaded": labels_have("50 événements affichés"),
            "one_query": len(broker.event_queries) == 1,
        }
        if not all(visible.values()) and monotonic() < diagnostics_deadline[0]:
            later(160, diagnostics_open)
            return
        assert all(visible.values()), f"Diagnostics incomplet après navigation: {visible}"
        assert broker.checks == 0, "Diagnostic actif déclenché à la navigation"
        report["checks"]["real_diagnostics_route_and_visible_content"] = True
        report["checks"]["no_implicit_active_diagnostics"] = True
        capture("02-real-diagnostics-route")
        if args.slow_check:
            later(130, initiate_slow_check)
            return
        report["pointer_actions"].append(device[0].click_widget(w.nav_rows["library"]))
        await_route("library", returned_to_library)

    def initiate_slow_check():
        page = window[0]._plugin_page_widgets["diagnostics"]
        buttons = [
            w for w in walk(page) if isinstance(w, Gtk.Button)
            and w.get_tooltip_text() == "Vérifier les services configurés et runtimes"
        ]
        assert len(buttons) == 1 and buttons[0].get_mapped()
        assert broker.checks == 0, "Contrôle implicite avant geste utilisateur"
        slow_started[0] = monotonic()
        report["pointer_actions"].append(device[0].click_widget(buttons[0]))
        later(100, slow_check_pending)

    def slow_check_pending():
        handle = page_plugin._check_handle
        assert handle is not None and not handle.done()
        assert broker.checks == 1
        assert labels_have("Vérification en cours")
        report["checks"]["D04_explicit_async_check_started"] = True
        report["pointer_actions"].append(
            device[0].click_widget(window[0].nav_rows["library"])
        )
        await_route("library", library_while_check_pending)

    def library_while_check_pending():
        w = window[0]
        assert w.view_stack.get_visible_child_name() == "library"
        assert w.nav_list.get_selected_row() is w.nav_rows["library"]
        assert monotonic() - slow_started[0] < 1.15
        assert not page_plugin._check_handle.done(), "Navigation a attendu le contrôle"
        report["checks"]["D04_physical_library_navigation_during_pending_check"] = True
        capture("03-library-while-diagnostics-check-pending")
        deadline = monotonic() + 5

        def wait_completed():
            if labels_have("Contrôle local fictif terminé"):
                assert broker.checks == 1
                assert page_plugin._check_handle is None
                finish_slow_navigation()
            elif monotonic() >= deadline:
                raise AssertionError("Contrôle asynchrone non terminé après navigation")
            else:
                later(90, wait_completed)
        later(100, wait_completed)

    def finish_slow_navigation():
        report["checks"]["D04_async_check_completes_offscreen"] = True
        capture("04-library-after-diagnostics-check")
        report["pointer_actions"].append(
            device[0].click_widget(window[0].nav_rows["diagnostics"])
        )
        await_route("diagnostics", slow_diagnostics_return)

    def slow_diagnostics_return():
        assert labels_have("Contrôle local fictif terminé")
        assert window[0].nav_list.get_selected_row() is window[0].nav_rows["diagnostics"]
        report["checks"]["D04_check_result_visible_on_return"] = True
        capture("05-diagnostics-result-after-navigation")
        report["pointer_actions"].append(
            device[0].click_widget(window[0].nav_rows["library"])
        )
        await_route("library", returned_to_library)

    def returned_to_library():
        w = window[0]
        assert w.nav_list.get_selected_row() is w.nav_rows["library"]
        assert broker.checks == (1 if args.slow_check else 0)
        report["checks"]["physical_return_to_library"] = True
        capture("03-return-library")
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.DiagnosticsNavigationUXE2E")

        def do_activate(self):
            try:
                w = MainWindow(self, core.config, core.library, core.diagnostics)
                window[0] = w
                w.set_default_size(args.width - 40, 780)
                w.plugin_manager = IsolatedContributions(page_plugin)
                w._rebuild_plugin_pages()
                w.present()
                device[0] = X11Input()
                later(500, initial)
            except Exception as exc:
                fail(f"Activation MainWindow: {type(exc).__name__}: {exc}")

        def do_shutdown(self):
            if device[0] is not None:
                device[0].close()
            page_plugin.deactivate()
            core.close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(
        50, lambda: (fail("Timeout Diagnostics navigation 50s"), False)[1]
        if report["status"] == "RUNNING" else False,
    )
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report.update(status="FAIL", error="GTK fermé sans rapport")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
