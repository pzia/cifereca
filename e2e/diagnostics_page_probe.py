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
        begin_escape_audit()

    def begin_escape_audit():
        # Esc and file export remain separate observations; neither should be
        # passed solely because Enter works and a clipboard action exists.
        report["stage"] = "audit_escape_and_file_export"
        expander = find_expander("Ouvrir le rapport support")
        report["audit"] = {"report_open_before_escape": expander.get_expanded()}
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
        report["unmet_criteria"] = [
            key for key, ok in (
                ("D15: Escape ferme l'agrandissement", esc_closes_enlargement),
                ("D13: export support explicite vers un fichier", bool(export_buttons)),
            ) if not ok
        ]
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
        focusable = expander.grab_focus() is not False and expander.has_focus()
        report["audit"]["D05_expander_focusable"] = focusable
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
                "D05: fermeture du volet par Entrée avec conservation focus/sélection non établie"
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
        65, lambda: (fail("Timeout GTK 65s"), False)[1]
        if report["status"] == "RUNNING" else False)
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report.update(status="FAIL", error="La fenêtre GTK s'est arrêtée sans rapport")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
