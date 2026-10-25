#!/usr/bin/env python3
"""UX #1011 D07/D13: real GTK4 detail formats, redaction and Ctrl+C.

Tests keyboard and clipboard through XTEST/X11; no network, LLM or account.
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
sys.path[:0] = [
    str(ROOT / "src/plugins/diagnostics-page"),
    str(ROOT / "e2e"),
    str(ROOT / "environnements/openai-chat-debian"),
]

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from PIL import ImageGrab

from diagnostics_page_probe import reveal_diagnostics_section, Broker, Context, Tasks, Host, walk
from x11_input import X11Input

SECRET = "SECRET-NE-JAMAIS-COPIER-D07"
SUPPORT_REPORT = (
    "Rapport support fictif expurgé\n"
    "Provenance : Core local\n"
    "Période : 24 heures\n"
    "Identifiants privés : [EXPURGÉ]\n"
    "Aucun envoi réseau"
)
STRUCTURED = {
    "null": None,
    "false": False,
    "zero": 0,
    "empty_array": [],
    "empty_object": {},
    "unicode": "Élève 🌍 漢字",
    "multiline": "Ligne A\nLigne B",
    "nested": {
        "items": [None, False, 0, [], {}],
        "token": SECRET,
    },
    "text": "token=" + SECRET,
}


class StructuredBroker(Broker):
    def __init__(self):
        super().__init__()
        self.support_exports = 0

    def system_status(self):
        return {"STRUCTURE UX D07": STRUCTURED}

    def inventory(self):
        return ()

    def snapshot(self):
        return ()

    def export_support_text(self):
        self.support_exports += 1
        return SUPPORT_REPORT

    def events(self, **query):
        self.event_queries.append(dict(query))
        return {
            "events": [], "total": 0, "offset": 0, "next_offset": None,
            "sources": [{"source": "core_events", "status": "empty"}],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1366,), required=True)
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("X11/XTEST et GTK requis")

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    broker, host = StructuredBroker(), Host()
    plugin = importlib.import_module("cifereca_diagnostics_page").DiagnosticsPagePlugin().activate(
        Context(broker, Tasks())
    )
    report = {
        "scenario": "diagnostics-structured-1366",
        "status": "RUNNING", "checks": {}, "screenshots": [], "audits": {},
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "scope": "vrai widget GTK4 et clavier XTEST, données et broker fictifs",
    }
    window, page, device = [None], [None], [None]
    rc = [1]
    started = monotonic()

    def save():
        report["elapsed_seconds"] = round(monotonic() - started, 3)
        report["active_checks"] = broker.checks
        report["event_queries"] = len(broker.event_queries)
        (out / "screen.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def screenshot(stage):
        im = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert im.size == (1366, 900)
        assert im.getbbox() is not None
        path = out / (stage + ".png")
        im.save(path)
        shutil.copyfile(path, out / "screen.png")
        report["screenshots"].append({
            "name": stage,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        report.update(status="FAIL", error=str(exc))
        try:
            screenshot("failure")
        except Exception:
            pass
        save()
        app.quit()

    def later(callback, ms=140):
        def invoke():
            try:
                code = getattr(callback, "__code__", None)
                if code is not None and code.co_filename == __file__:
                    report["checkpoint_line"] = code.co_firstlineno
                save()
                callback()
            except Exception as exc:
                lines = []
                trace = exc.__traceback__
                while trace is not None:
                    if trace.tb_frame.f_code.co_filename == __file__:
                        lines.append(trace.tb_lineno)
                    trace = trace.tb_next
                report["failure_line"] = lines[-1] if lines else 0
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, invoke)

    def until(predicate, then, seconds=7):
        # CI exports only AST-reviewed source locations, never UI values.
        from inspect import currentframe
        frame = currentframe()
        try:
            caller = frame.f_back if frame is not None else None
            if caller is not None and caller.f_code.co_filename == __file__:
                report["wait_line"] = caller.f_lineno
        finally:
            del frame
        save()
        deadline = monotonic() + seconds
        def poll():
            if predicate():
                then()
            elif monotonic() >= deadline:
                raise AssertionError("Timeout GTK D07/D13")
            else:
                later(poll, 100)
        later(poll, 120)

    def detail():
        panes = [w for w in walk(page[0])
                 if isinstance(w, Gtk.Expander) and w.get_label() == "Détails expurgés"]
        assert len(panes) == 1, ("Volets de détail", len(panes))
        return reveal_diagnostics_section(panes[0])

    def detail_controls():
        pane = detail()
        dropdowns = [w for w in walk(pane) if isinstance(w, Gtk.DropDown)]
        views = [w for w in walk(pane) if isinstance(w, Gtk.TextView)]
        copies = [w for w in walk(pane)
                  if isinstance(w, Gtk.Button)
                  and w.get_tooltip_text() == "Copier le détail expurgé actuellement affiché"]
        assert len(dropdowns) == len(views) == len(copies) == 1
        return dropdowns[0], views[0], copies[0]

    def text(view):
        buf = view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def physical(key):
        device[0].key(key, True)
        device[0].key(key, False)

    def choose_mode_keyboard(selector, keys, wanted, next_step):
        """Change the actual GTK DropDown via physical XTEST, not set_selected."""
        assert selector.grab_focus() is not False

        def press():
            # Gtk.DropDown may delegate focus to an internal toggle child.
            focused = window[0].get_focus()
            assert focused is not None and (
                focused is selector or focused.is_ancestor(selector)
            ), "D07 : mode de détail inaccessible au clavier"
            for key in ("space", *keys, "Return"):
                physical(key)
            until(lambda: selector.get_selected() == wanted, next_step, seconds=6)

        later(press, 190)

    def start():
        assert page[0].get_mapped()
        assert len(broker.event_queries) == 1
        assert broker.checks == 0 and plugin.llm is None
        pane = detail()
        pane.set_expanded(True)
        until(lambda: detail_controls()[1].get_mapped(), readable)

    def readable():
        selector, view, _copy = detail_controls()
        assert selector.get_selected() == 0
        body = text(view)
        for marker in ("null: null", "false: false", "zero: 0", "Élève 🌍 漢字"):
            assert marker in body, (marker, body[:400])
        assert "empty_array:" in body and "empty_object:" in body
        assert "multiline:" in body and "Ligne A\\nLigne B" in body
        assert "absent:" not in body, "Champ absent inventé dans le détail"
        assert SECRET in body
        report["checks"]["D07_readable_preserves_false_zero_null_empty_and_unicode"] = True
        screenshot("00-structure-lisible")
        choose_mode_keyboard(
            selector, ("End", "Up"), 1,
            lambda: until(
                lambda: text(view).lstrip().startswith("{") and "\n  " in text(view),
                lambda: json_mode(view),
            ),
        )

    def json_mode(view):
        body = text(view)
        data = json.loads(body)
        assert data["null"] is None
        assert data["false"] is False
        assert type(data["zero"]) is int and data["zero"] == 0
        assert data["empty_array"] == []
        assert data["empty_object"] == {}
        assert data["unicode"] == "Élève 🌍 漢字"
        assert data["nested"]["items"] == [None, False, 0, [], {}]
        assert data["nested"]["token"] == SECRET
        assert data["multiline"] == "Ligne A\nLigne B"
        assert "absent" not in data, "Clé absente ajoutée au JSON"
        assert SECRET in body
        assert "\n  " in body
        report["checks"]["D07_json_indented_typed_and_redacted"] = True
        report["checks"]["D07_json_mode_selected_by_real_keyboard_xtest"] = True
        screenshot("01-structure-json-indent")
        report["json_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        selector, _view, _copy = detail_controls()
        choose_mode_keyboard(
            selector, ("End",), 2,
            lambda: until(
                lambda: text(view).lstrip().startswith("{") and "\n  " not in text(view),
                lambda: raw_mode(view),
            ),
        )

    def raw_mode(view):
        body = text(view)
        parsed = json.loads(body)
        assert parsed["null"] is None and parsed["false"] is False
        assert parsed["empty_array"] == []
        assert SECRET in body
        assert "Élève 🌍 漢字" in body
        assert parsed["multiline"] == "Ligne A\nLigne B"
        assert "absent" not in parsed
        report["checks"]["D07_raw_matches_json_without_secret"] = True
        report["checks"]["D07_raw_mode_selected_by_real_keyboard_xtest"] = True
        screenshot("02-structure-brut-expurge")
        selector, _view, _copy = detail_controls()
        choose_mode_keyboard(
            selector, ("End", "Up"), 1,
            lambda: until(lambda: "\n  " in text(view), lambda: copy_json_button(view)),
        )

    def copy_json_button(view):
        # Distinct from TextView Ctrl+C: exercise the real Copy detail button
        # by keyboard and prove it copies exactly the visible JSON snapshot.
        selector, _view, button = detail_controls()
        assert selector.get_selected() == 1
        expected = text(view)
        assert json.loads(expected)["multiline"] == "Ligne A\nLigne B"
        assert SECRET in expected
        Gdk.Display.get_default().get_clipboard().set("D07-before-copy-sentinel")
        assert button.grab_focus() is not False

        def press_button():
            assert button.has_focus(), "D07 : bouton Copier inaccessible au clavier"
            physical("space")
            later(lambda: Gdk.Display.get_default().get_clipboard().read_text_async(
                None, lambda clipboard, task: json_button_clipboard_done(
                    clipboard, task, view, expected,
                ),
            ), 250)

        later(press_button, 190)

    def json_button_clipboard_done(clipboard, task, view, expected):
        try:
            copied = clipboard.read_text_finish(task)
            assert copied == expected, "D07 : bouton Copier ≠ JSON affiché"
            assert copied != "D07-before-copy-sentinel"
            assert json.loads(copied)["nested"]["items"] == [None, False, 0, [], {}]
            assert "absent" not in json.loads(copied)
            assert SECRET in copied
            assert len(broker.event_queries) == 1 and broker.checks == 0
            report["checks"]["D07_json_copy_button_keyboard_xtest_exact"] = True
            report["audits"]["D07_json_copy_sha256_matches_visible"] = (
                hashlib.sha256(copied.encode("utf-8")).hexdigest()
                == report["json_sha256"]
            )
            assert report["audits"]["D07_json_copy_sha256_matches_visible"]
            screenshot("02b-copie-json-bouton-xtest")
            later(lambda: select_and_copy(view), 160)
        except Exception as exc:
            fail(f"Copie JSON via bouton GTK : {type(exc).__name__}: {exc}")

    def export_json_button(view):
        # Real GTK button and FileChooserNative; only the sandbox file choice
        # and acceptance are fixture-controlled, not a physical Save click.
        expected = text(view)
        assert expected.lstrip().startswith("{") and "\n  " in expected
        assert hashlib.sha256(expected.encode("utf-8")).hexdigest() == report["json_sha256"]
        buttons = [
            w for w in walk(detail()) if isinstance(w, Gtk.Button)
            and w.get_label() == "Enregistrer JSON…"
        ]
        assert len(buttons) == 1, "D07: bouton GTK Enregistrer JSON absent"
        button = buttons[0]
        assert button.grab_focus() is not False
        until(lambda: button.has_focus(), lambda: export_json_press_xtest(expected))

    def export_json_press_xtest(expected):
        physical("space")
        until(
            lambda: any(
                e.filename == "cifereca-diagnostic.json" and e.dialog is not None
                for e in plugin._support_exports
            ),
            lambda: later(lambda: export_json_select_fixture_path(expected), 400),
        )

    def export_json_select_fixture_path(expected):
        exporters = [
            e for e in plugin._support_exports
            if e.filename == "cifereca-diagnostic.json" and e.dialog is not None
        ]
        assert len(exporters) == 1, "D07: file chooser JSON absent"
        exporter = exporters[0]
        dialog = exporter.dialog
        assert dialog.get_visible(), "D07: dialogue de sauvegarde non visible"
        assert dialog.get_current_name() == "cifereca-diagnostic.json"
        assert exporter.data == expected.encode("utf-8"), (
            "D07: bytes capturés avant choix ≠ JSON affiché"
        )
        target = out / "d07-json-export-local.json"
        assert not target.exists(), "D07: fichier de test déjà présent"
        # GTK4's native chooser may defer a folder change until its main
        # loop finishes. Select an existing harmless fixture destination
        # explicitly (documented FileChooser.set_file SAVE contract) rather
        # than accidentally exporting into the repository working directory.
        target.write_bytes(b"D07-EXPORT-FIXTURE-MUST-BE-REPLACED")
        assert dialog.set_file(Gio.File.new_for_path(str(target))), (
            "D07: native file chooser refused local fixture destination"
        )
        # Native GtkFileChooser may update its selected GFile asynchronously.
        # Let GLib finish folder selection before accepting the GTK dialog.
        later(lambda: export_json_accept_fixture_path(exporter, expected, target), 500)

    def export_json_accept_fixture_path(exporter, expected, target):
        dialog = exporter.dialog
        assert dialog is not None and dialog.get_visible()
        selected = dialog.get_file()
        assert selected is not None and selected.get_path() == str(target), (
            "D07: FileChooserNative ignore la destination locale après GLib",
            selected.get_path() if selected is not None else None,
        )
        report["audits"]["D07_real_gtk_file_chooser_opened_by_xtest"] = True
        screenshot("02c-dialogue-enregistrer-json-gtk")
        dialog.emit("response", Gtk.ResponseType.ACCEPT)
        until(
            lambda: target.is_file() and exporter.data is None
            and target.read_bytes() != b"D07-EXPORT-FIXTURE-MUST-BE-REPLACED",
            lambda: export_json_verify_file(expected, target),
        )

    def export_json_verify_file(expected, target):
        raw = target.read_bytes()
        assert raw == expected.encode("utf-8"), "D07: export JSON différent de la vue GTK"
        data = json.loads(raw)
        assert data["null"] is None and data["false"] is False
        assert type(data["zero"]) is int and data["zero"] == 0
        assert data["nested"]["items"] == [None, False, 0, [], {}]
        assert data["unicode"] == "Élève 🌍 漢字"
        assert data["multiline"] == "Ligne A\nLigne B"
        assert "absent" not in data and SECRET.encode() in raw
        assert len(broker.event_queries) == 1 and broker.checks == 0 and plugin.llm is None
        assert "Détail JSON expurgé enregistré" in host.toasts
        report["checks"]["D07_json_file_export_exact_redacted_via_gtk"] = True
        report["audits"]["D07_json_file_sha256_matches_visible"] = (
            hashlib.sha256(raw).hexdigest() == report["json_sha256"]
        )
        assert report["audits"]["D07_json_file_sha256_matches_visible"]
        report["json_file_size_bytes"] = len(raw)
        screenshot("02d-json-fichier-local-verifie")
        later(finish_structured_e2e, 160)

    def select_and_copy(view):
        # Keep the established GTK/XTEST Ctrl+C checks before invoking the
        # native Save chooser, which may retain WM focus in a headless runner.
        assert detail().get_expanded()
        buf = view.get_buffer()
        buf.select_range(buf.get_end_iter(), buf.get_start_iter())
        assert buf.get_has_selection()
        selected = text(view)
        assert SECRET in selected
        assert view.grab_focus() is not False
        until(lambda: view.has_focus(), lambda: send_ctrl_c(view, selected))

    def send_ctrl_c(view, selected):
        assert view.has_focus(), "TextView doit recevoir les touches XTEST"
        report["audits"]["D13_selection_is_real_GTK"] = view.get_buffer().get_has_selection()
        device[0].key("Control_L", True)
        try:
            physical("c")
        finally:
            device[0].key("Control_L", False)

        def read_clipboard():
            Gdk.Display.get_default().get_clipboard().read_text_async(
                None, lambda clipboard, task: clipboard_done(
                    clipboard, task, selected
                )
            )
        later(read_clipboard, 260)

    def clipboard_done(clipboard, task, expected):
        try:
            actual = clipboard.read_text_finish(task)
            assert actual == expected, (
                "Ctrl+C TextView doit copier la sélection expurgée",
                repr((actual or "")[:100]),
            )
            assert SECRET in actual
            report["checks"]["D13_ctrl_c_selection_expurgated"] = True
            screenshot("03-selection-copie-ctrl-c")
            assert broker.checks == 0
            later(support_report_begin, 150)
        except Exception as exc:
            fail(f"Clipboard Ctrl+C: {type(exc).__name__}: {exc}")

    def support_report_begin():
        panes = [
            w for w in walk(page[0]) if isinstance(w, Gtk.Expander)
            and w.get_label() == "Ouvrir le rapport support"
        ]
        assert len(panes) == 1
        # The support expander is inside the Reports tab. A Gtk.Expander can
        # be expanded but remain unmapped while its Gtk.Stack child is hidden.
        # Reveal the containing tab *and* expand the target itself:
        # reveal_diagnostics_section expands ancestors but not its argument.
        # Both are required before the real mapped/clipboard assertions.
        reveal_diagnostics_section(panes[0])
        panes[0].set_expanded(True)
        views = [w for w in walk(panes[0]) if isinstance(w, Gtk.TextView)]
        assert len(views) == 1
        until(lambda: views[0].get_mapped() and text(views[0]) == SUPPORT_REPORT,
              lambda: support_copy_prepare(views[0]), seconds=6)

    def support_copy_prepare(view):
        assert SECRET not in text(view)
        assert "Provenance : Core local" in text(view)
        buttons = [
            w for w in walk(page[0]) if isinstance(w, Gtk.Button)
            and w.get_tooltip_text() == "Copier le diagnostic support expurgé"
        ]
        assert len(buttons) == 1, "Copie support explicite manquante"
        button = buttons[0]
        # One read to render the report; subsequent read only on user gesture.
        before = broker.support_exports
        assert before >= 1
        assert button.grab_focus() is not False
        until(lambda: button.has_focus(),
              lambda: support_copy_xtest(view, before), seconds=5)

    def support_copy_xtest(view, before):
        assert broker.support_exports == before
        physical("space")
        until(lambda: broker.support_exports == before + 1,
              lambda: later(lambda: support_read_full(view), 230), seconds=5)

    def support_read_full(view):
        Gdk.Display.get_default().get_clipboard().read_text_async(
            None, lambda clipboard, task: support_full_done(
                clipboard, task, view
            )
        )

    def support_full_done(clipboard, task, view):
        try:
            content = clipboard.read_text_finish(task)
            assert content == SUPPORT_REPORT, "Copie support inexacte"
            assert SECRET not in content
            assert "Provenance : Core local" in content
            report["checks"]["D13_support_copy_button_xtest_exact_redacted"] = True
            report["audits"]["D13_report_provenance_visible_in_copy"] = True
            screenshot("04-support-action-copy")
            later(lambda: support_select_partial(view), 150)
        except Exception as exc:
            fail(f"Copie support complète : {type(exc).__name__}: {exc}")

    def support_select_partial(view):
        # Selection is made in the actual Gtk.TextBuffer, then Ctrl+C is
        # delivered physically; unlike 'Copier le diagnostic', this must copy
        # ONLY the chosen portion.
        needle = "Provenance : Core local"
        body = text(view)
        begin = body.index(needle)
        buffer = view.get_buffer()
        buffer.select_range(
            buffer.get_iter_at_offset(begin + len(needle)),
            buffer.get_iter_at_offset(begin),
        )
        assert buffer.get_has_selection()
        assert view.grab_focus() is not False
        until(lambda: view.has_focus(),
              lambda: support_slice_xtest(needle), seconds=5)

    def support_slice_xtest(expected):
        device[0].key("Control_L", True)
        try:
            physical("c")
        finally:
            device[0].key("Control_L", False)
        later(lambda: Gdk.Display.get_default().get_clipboard().read_text_async(
            None, lambda clipboard, task: support_slice_done(
                clipboard, task, expected
            )
        ), 260)

    def support_slice_done(clipboard, task, expected):
        try:
            content = clipboard.read_text_finish(task)
            assert content == expected, (
                "Ctrl+C ne doit copier que le segment sélectionné",
                repr(content),
            )
            assert content != SUPPORT_REPORT
            assert SECRET not in content
            assert broker.checks == 0 and plugin.llm is None
            assert len(broker.event_queries) == 1
            report["checks"]["D13_support_partial_selection_ctrl_c_xtest"] = True
            screenshot("05-support-ctrl-c-selection-partielle")
            later(lambda: export_json_button(detail_controls()[1]), 180)
        except Exception as exc:
            fail(f"Ctrl+C support partiel : {type(exc).__name__}: {exc}")

    def finish_structured_e2e():
        # Only complete once the original D07/D13 copy journey *and* the
        # new real GIO JSON file export have all passed.
        assert report["checks"]["D13_support_partial_selection_ctrl_c_xtest"]
        assert report["checks"]["D07_json_file_export_exact_redacted_via_gtk"]
        report["unmet_criteria"] = [
            "D13: export du rapport support en fichier non exercé (détail JSON exporté)",
            "D05: fermeture/sélection non exercée par ce scénario (recette rapport long)",
        ]
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

    class Application(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.DiagnosticsStructuredE2E")

        def do_activate(self):
            window[0] = Gtk.ApplicationWindow(application=self)
            window[0].set_title("Diagnostics : valeurs structurées D07 / sélection D13")
            window[0].set_default_size(1300, 790)
            device[0] = X11Input()
            page[0] = plugin._build(host)
            window[0].set_child(page[0])
            window[0].present()
            until(lambda: any(
                isinstance(w, Gtk.Label) and w.get_text() == "STRUCTURE UX D07"
                for w in walk(page[0])
            ), start)

        def do_shutdown(self):
            plugin.deactivate()
            if device[0] is not None:
                device[0].close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(
        35, lambda: (fail("Timeout global structuré D07/D13"), False)[1]
        if report["status"] == "RUNNING" else False
    )
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report.update(status="FAIL", error="GTK fermé sans rapport")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
