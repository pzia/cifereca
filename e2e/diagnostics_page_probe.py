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

    def system_status(self):
        return {"application": {"online": False, "attempts": 0,
                                "secret": "EXEMPLE-PRIVE-UX"},
                "diagnostics": "Simulation locale, sans réseau"}

    def inventory(self):
        return ({"plugin_id": "ui.page.diagnostics", "version": "fixture",
                 "state": "ready", "type": "ui.page"},)

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
        events = [
            {"event_id": f"evt-{i:03d}",
             "timestamp": f"2026-10-11T06:{i % 60:02d}:00+00:00",
             "source": "core_events", "module": "core.sync",
             "severity": "error" if i == 51 else "info",
             "status": "partial" if i == 51 else "ok",
             "message": f"Événement fictif {i}"}
            for i in range(offset, min(offset + limit, 65))
        ]
        return {
            "events": events, "offset": offset, "total": 65,
            "next_offset": offset + len(events) if offset + len(events) < 65 else None,
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


class Host:
    def __init__(self):
        self.toasts = []

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
    broker, tasks, host = Broker(), Tasks(), Host()
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
                callback()
            except Exception as exc:
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
        return buttons[0]

    def find_expander(label):
        expanders = [w for w in walk(page[0]) if isinstance(w, Gtk.Expander)
                     and w.get_label() == label]
        assert len(expanders) >= 1, f"volet {label!r} absent"
        return expanders[0]

    def until(predicate, done, *, seconds=10):
        deadline = monotonic() + seconds

        def check():
            if predicate():
                done()
            elif monotonic() >= deadline:
                raise AssertionError("Timeout de la condition GTK attendue")
            else:
                later(check, 90)
        later(check, 100)

    def initial():
        assert window[0].get_width() <= args.width, "Fenêtre trop large"
        assert page[0].get_mapped(), "Plugin non visible"
        assert find_label("Journaux par composant")
        assert find_label("Analyse IA")
        assert find_label("État système")
        assert find_label("Rapport support")
        assert find_label("50 événements affichés")
        assert find_label("Indisponible"), "Disponibilité des sources masquée"
        assert not find_button("Autoriser et analyser").get_sensitive()
        assert broker.checks == 0, "Contrôle actif lancé à l'ouverture"
        assert len(broker.event_queries) == 1, broker.event_queries
        report["checks"]["initial_and_no_implicit_active_or_ai"] = True
        screenshot("00-diagnostics-loaded")
        report_step()

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
        copied_detail = [
            w for w in walk(page[0]) if isinstance(w, Gtk.Button)
            and w.get_tooltip_text() == "Copier le détail expurgé actuellement affiché"
        ]
        assert copied_detail, "Bouton de copie expurgée absent"
        button = copied_detail[0]
        # The widget is keyboard activated via real XTEST events, not signal emission.
        assert button.grab_focus() is not False
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
        pass_scenario()

    def pass_scenario():
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

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
        pass_scenario()

    class Application(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.DiagnosticsUXProbe")

        def do_activate(self):
            window[0] = Gtk.ApplicationWindow(application=self)
            window[0].set_title("Diagnostics UX GTK isolé")
            window[0].set_default_size(args.width - 40, 870 if args.width == 1600 else 790)
            device[0] = X11Input()
            page[0] = plugin._build(host)
            window[0].set_child(page[0])
            window[0].present()
            until(lambda: find_label("50 événements affichés"), initial)

        def do_shutdown(self):
            plugin.deactivate()
            if device[0] is not None:
                device[0].close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(
        45, lambda: (fail("Timeout GTK 45s"), False)[1]
        if report["status"] == "RUNNING" else False)
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report.update(status="FAIL", error="La fenêtre GTK s'est arrêtée sans rapport")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
