#!/usr/bin/env python3
"""#1599: real Constellations GTK page + shared Core renderer under Xvfb/XTEST.

Uses the actual Constellations plugin and the production _UiHost +
PublicBookMasterDetailView. Brokers are deterministic, identity-scoped and
offline; no real provider, acquisition, downloads or LLM requests are made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from time import monotonic
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/plugins/constellations"))
sys.path.insert(0, str(ROOT / "environnements/openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1.0")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageGrab

from cifereca.plugin_api import BookRef
from cifereca.gui.plugin_ui_host import _UiHost
from cifereca.gui.public_book_view import PublicBookMasterDetailView
from constellations_page import ConstellationsPlugin
from x11_input import X11Input


BOOKS = (
    {"book_id": "edition-a", "record_id": "edition-a",
     "item_id": "edition-a", "provider_id": "fixture",
     "title": "Livre A", "authors": ["Autrice commune"],
     "metadata": {"subjects": ["Voyages"], "language": "fr"}},
    {"book_id": "edition-b", "record_id": "edition-b",
     "item_id": "edition-b", "provider_id": "fixture",
     "title": "Livre B", "authors": ["Autrice commune"],
     "metadata": {"subjects": ["Voyages"], "language": "fr"}},
    {"book_id": "edition-c", "record_id": "edition-c",
     "item_id": "edition-c", "provider_id": "fixture",
     "title": "Livre C", "authors": ["Autrice commune"],
     "metadata": {"subjects": ["Voyages"], "language": "fr"}},
)


class Control:
    def __init__(self):
        self._cancelled = False

    def cancelled(self):
        return self._cancelled

    def cancel(self):
        self._cancelled = True


class CompletedHandle:
    def __init__(self, operation):
        self.control = Control()
        self._value = operation(self.control)

    def result(self):
        return self._value

    def cancel(self):
        self.control.cancel()

    def add_done_callback(self, callback):
        # Deliver through the real plugin's GLib.idle_add completion guard.
        callback(self)


class Tasks:
    def submit(self, operation, *, label, kind):
        assert kind == "constellations", kind
        return CompletedHandle(operation)


class Identities:
    def identities(self):
        return [
            {"identity_id": "alice", "label": "Alice"},
            {"identity_id": "bob", "label": "Bob"},
        ]


class Metadata:
    def library_entries(self, *, identity_id, scan):
        assert scan is False
        if identity_id == "alice":
            return tuple(dict(row) for row in BOOKS)
        if identity_id == "bob":
            return ({"book_id": "bob-only", "record_id": "bob-only",
                     "provider_id": "fixture", "title": "Livre de Bob",
                     "authors": ["Autre auteur"]},)
        raise AssertionError("Unknown fixture identity")


class Context:
    config = {"llm_semantic_hypotheses": False}

    def __init__(self):
        self.brokers = {
            "identities": Identities(),
            "metadata": Metadata(),
            "tasks": Tasks(),
        }

    def broker(self, name):
        if name not in self.brokers:
            raise AssertionError("Unexpected broker requested: " + name)
        return self.brokers[name]


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def neighbour_button(view, name):
    buttons = [
        widget for widget in walk(view.flow)
        if isinstance(widget, Gtk.Button)
        and (widget.get_tooltip_text() or "") == "Visiter : " + name
    ]
    assert len(buttons) == 1, (name, [x.get_tooltip_text() for x in buttons])
    assert buttons[0].get_mapped(), name + " is not in mapped GTK"
    return buttons[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1366, 1600), required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Real GTK4/X11 display required")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    start = monotonic()
    report = {
        "status": "RUNNING", "scenario": "constellations-detail-gtk",
        "width": args.width, "backend": "GTK4/X11/XTEST",
        "git_sha": sha, "steps": [], "checks": {},
        "real_plugin": "src/plugins/constellations/constellations_page.py",
        "real_host": "cifereca.gui.plugin_ui_host._UiHost",
        "external_calls": 0,
    }
    ref = BookRef(
        "edition-a", "Livre A", authors=("Autrice commune",),
        identifiers={"provider": "fixture", "source_item_id": "edition-a"},
    )
    app = Adw.Application(
        application_id="org.cifereca.E2EConstellations",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    state = {"window": None, "plugin": None, "view": None, "device": None,
             "host": None, "origin": ref, "old_button": None,
             "opened": [], "toasts": [], "routes": []}

    def write_report():
        report["duration_seconds"] = round(monotonic() - start, 2)
        (out / "screen.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def capture(stage):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).convert("RGB")
        assert image.width == args.width
        assert len(image.getcolors(maxcolors=2) or []) > 1, "Blank X11 screenshot"
        filename = out / (stage + ".png")
        image.save(filename)
        shutil.copyfile(filename, out / "screen.png")
        view = state["view"]
        report["steps"].append({
            "name": stage,
            "image": filename.name,
            "sha256": hashlib.sha256(filename.read_bytes()).hexdigest(),
            "center": view.center.book.title if view.center else None,
            "identity": view.identity_id(),
            "history_size": len(view.nav.history),
            "window_width": state["window"].get_width(),
        })
        write_report()

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        report["status"] = "FAIL"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc(limit=6)
        try:
            if state["window"] is not None:
                capture("failure")
        except Exception:
            pass
        write_report()
        app.quit()

    def later(delay, fn):
        def guarded():
            try:
                fn()
            except Exception as exc:
                fail(exc)
            return False
        GLib.timeout_add(delay, guarded)

    def key_activate(button):
        assert button.get_mapped(), "Button not mapped for XTEST"
        assert button.get_sensitive(), "Button disabled"
        assert button.grab_focus() is not False
        device = state["device"]
        device.key("Return", True)
        device.key("Return", False)

    def wait_loaded():
        view = state["view"]
        if view.center is None:
            if monotonic() - start > 16:
                raise AssertionError("Constellations initial seed never loaded")
            later(100, wait_loaded)
            return
        assert view.identity_id() == "alice"
        assert view.origin_book is ref and view.origin_identity == "alice"
        assert view.center.book.title == "Livre A", view.center.book.title
        assert isinstance(view.master, PublicBookMasterDetailView)
        assert view._nodes_identity == "alice"
        assert len(view.nodes) >= 3
        assert view.heading.get_text() == "Centre : Livre A"
        assert "voisin" in view.neighbours_summary.get_text()
        assert not state["opened"]
        assert not state["toasts"]
        capture("00-origin")
        state["old_button"] = neighbour_button(view, "Livre B")
        key_activate(state["old_button"])
        later(180, check_b)

    def check_b():
        view = state["view"]
        assert view.center.book.title == "Livre B"
        assert len(view.nav.history) == 2
        assert view.back_button.get_sensitive()
        assert view.heading.get_text() == "Centre : Livre B"
        capture("01-neighbour-b")
        key_activate(neighbour_button(view, "Livre C"))
        later(160, check_c)

    def check_c():
        view = state["view"]
        assert view.center.book.title == "Livre C"
        assert [view.nodes[0].key in view.nav.history]  # Every step is a real graph key.
        assert len(view.nav.history) == 3
        capture("02-neighbour-c")
        key_activate(view.back_button)
        later(160, check_back_b)

    def check_back_b():
        view = state["view"]
        assert view.center.book.title == "Livre B"
        assert view.nav.position == 1
        key_activate(view.back_button)
        later(160, check_back_a)

    def check_back_a():
        view = state["view"]
        assert view.center.book.title == "Livre A"
        assert view.nav.position == 0
        assert not view.back_button.get_sensitive()
        assert view.forward_button.get_sensitive()
        capture("03-history-origin")
        key_activate(view.origin_button)
        later(150, check_origin)

    def check_origin():
        view = state["view"]
        assert state["opened"] == [(ref, "alice")], state["opened"]
        assert view.center.book.title == "Livre A"
        report["checks"]["navigation_a_b_c_back_origin"] = True
        report["checks"]["exact_original_book_and_profile"] = True
        # Old GTK callback can remain retained after a profile switch. It
        # must not reopen the old A/B graph; no HTTP or new entitlement.
        view.identity.set_selected(1)
        later(250, check_bob)

    def check_bob():
        view = state["view"]
        if view._nodes_identity != "bob":
            later(120, check_bob)
            return
        assert view.identity_id() == "bob"
        assert not any(n.book.title in ("Livre A", "Livre B", "Livre C")
                       for n in view.nodes)
        assert view.center is None, "No arbitrary first book on profile switch"
        assert not view.nav.history
        assert "Choisissez" in view.heading.get_text()
        capture("04-bob-empty")
        prior = len(state["opened"])
        # This retained GTK button is no longer mapped, so invoking its
        # signal is deliberately synthetic. The earlier navigation used XTEST.
        state["old_button"].emit("clicked")
        assert view.center is None and len(state["opened"]) == prior
        report["checks"]["stale_neighbour_after_identity_change"] = True
        state["plugin"].deactivate()
        state["old_button"].emit("clicked")
        assert len(state["opened"]) == prior
        report["checks"]["stale_neighbour_after_plugin_deactivation"] = True
        report["checks"]["no_network_or_acquisition"] = True
        report["status"] = "PASS"
        write_report()
        app.quit()

    def activate(_app):
        window = Adw.ApplicationWindow(application=app)
        window.set_default_size(args.width - 40, 850 if args.width == 1366 else 930)
        window._closed = False
        window.covers = None  # All fixture covers are absent; no loader request.
        window.service = NS(current_identity=NS(identity_id="alice"))
        window._overview_listeners = []
        window.toast = lambda message, timeout=3: state["toasts"].append(message)
        window.navigate = lambda route: state["routes"].append(route)
        window.open_book_detail = (
            lambda book, *, identity_id=None:
                state["opened"].append((book, identity_id)) or True
        )
        host = _UiHost(window, action_context=NS(book=ref, identity_id="alice"))
        plugin = ConstellationsPlugin().activate(Context())
        plugin.from_book(host)
        assert state["routes"] == ["constellations"]
        widget = plugin.build(host)
        window.set_child(widget)
        state.update(window=window, plugin=plugin, view=plugin.view,
                     host=host, device=X11Input())
        window.present()
        later(300, wait_loaded)

    app.connect("activate", activate)

    def timeout():
        if report["status"] == "RUNNING":
            fail(AssertionError("GTK Constellations fixture timeout"))
        return False

    GLib.timeout_add_seconds(36, timeout)
    try:
        app.run([])
    finally:
        if report["status"] == "RUNNING":
            fail(AssertionError("GTK Constellations closed before assertion"))
        if state["device"] is not None:
            state["device"].close()
        write_report()
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
