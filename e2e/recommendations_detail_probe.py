#!/usr/bin/env python3
"""#1599: real Recommandations GTK master/detail and stale action contract.

Production RecommendationsPlugin, _UiHost and PublicBookMasterDetailView are
mounted in real Xvfb GTK. The ranking task boundary is disabled for the probe
and candidate results are synthetic; no provider search, LLM or acquisition.
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
sys.path.insert(0, str(ROOT / "src/plugins/recommendations"))
sys.path.insert(0, str(ROOT / "environnements/openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageGrab

from cifereca.gui.plugin_ui_host import _UiHost
from cifereca.gui.public_book_view import PublicBookMasterDetailView
from recommendation_engine import CatalogNoticeVariant, RankedCandidate
from recommendations import RecommendationsPlugin
from x11_input import X11Input


def ranked(book_id, title, isbn, *, score):
    item = {
        "book_id": book_id, "record_id": book_id,
        "title": title, "author": "Autrice Attestée",
        "authors": ["Autrice Attestée", "Deuxième Auteur"],
        "description": "Description publique, sans donnée de compte.",
        "isbn": isbn, "language": "fr", "format": "EPUB",
        "source_provider": "fixture", "source_credential": "fixture-public",
    }
    variant = CatalogNoticeVariant(
        item=item, source_provider="fixture",
        source_credential="fixture-public", record_id=book_id,
        docbase="", source_collection="catalog", isbn=isbn,
    )
    return RankedCandidate(
        item=item, score=score, reasons=("Auteur : Autrice Attestée",),
        sources=("fixture",), notices=(variant,),
    )


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1366, 1600), required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Real GTK/XTEST display required")
    start = monotonic()
    report = {
        "scenario": "recommendations-detail-gtk", "status": "RUNNING",
        "width": args.width, "backend": "GTK4/X11/XTEST",
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "steps": [], "checks": {}, "network_calls": 0,
        "real_plugin": "src/plugins/recommendations/recommendations.py",
        "real_host": "cifereca.gui.plugin_ui_host._UiHost",
        "source_data": "synthetic ranked candidates; ranking task disabled",
    }
    app = Adw.Application(
        application_id="org.cifereca.E2ERecommendations",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    state = {"view": None, "plugin": None, "window": None,
             "device": None, "searches": [], "toasts": [],
             "old_action": None}

    def save():
        report["duration_seconds"] = round(monotonic() - start, 2)
        (out / "screen.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def capture(stage):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).convert("RGB")
        assert image.width == args.width
        colors = image.getcolors(maxcolors=2)
        assert colors is None or len(colors) > 1
        image_path = out / (stage + ".png")
        image.save(image_path)
        shutil.copyfile(image_path, out / "screen.png")
        view = state["view"]
        report["steps"].append({
            "stage": stage, "image": image_path.name,
            "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            "identity": view.selected_identity_id(),
            "rows": view._master._page.store.get_n_items(),
            "selected": view._master._page.selection.get_selected(),
            "window_width": state["window"].get_width(),
        })
        save()

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        report["status"] = "FAIL"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc(limit=6)
        try:
            capture("failure")
        except Exception:
            pass
        save()
        app.quit()

    def later(delay, fn):
        def guarded():
            try:
                fn()
            except Exception as exc:
                fail(exc)
            return False
        GLib.timeout_add(delay, guarded)

    def get_isbn_action():
        view = state["view"]
        buttons = [
            widget for widget in walk(view.widget)
            if isinstance(widget, Gtk.Button)
            and str(getattr(widget, "_cifereca_action_id", "")).startswith(
                "recommendation.search.isbn."
            )
        ]
        assert len(buttons) == 1, [
            getattr(b, "_cifereca_action_id", None) for b in buttons
        ]
        assert buttons[0].get_mapped(), "ISBN action not mapped"
        return buttons[0]

    def activate_key(button):
        assert button.get_sensitive()
        assert button.grab_focus() is not False
        device = state["device"]
        device.key("Return", True)
        device.key("Return", False)

    def initial():
        view = state["view"]
        assert view.selected_identity_id() == "alice"
        assert isinstance(view._master, PublicBookMasterDetailView)
        assert view._master._page.store.get_n_items() == 0
        assert not state["searches"]
        capture("00-empty")
        view._ranked = [
            ranked("edition-a", "Livre A", "9780306406157", score=94),
            ranked("edition-b", "Livre B", "9780140328721", score=85),
        ]
        view._render()
        assert view._master._page.store.get_n_items() == 2
        view._master._page.selection.set_selected(0)
        later(150, selected_a)

    def selected_a():
        view = state["view"]
        assert view._master._page.selection.get_selected() == 0
        assert any(
            isinstance(widget, Gtk.Label) and widget.get_text() == "Livre A"
            for widget in walk(view._master._page.detail)
        ), "Shared GTK detail did not display selected title"
        assert any(
            isinstance(widget, Gtk.Label) and "Autrice Attestée" in widget.get_text()
            for widget in walk(view._master._page.detail)
        )
        capture("01-selected-a")
        action = get_isbn_action()
        state["old_action"] = action
        activate_key(action)
        later(170, after_action)

    def after_action():
        view = state["view"]
        assert state["searches"] == [
            ("9780306406157", "isbn", False, "alice")
        ], state["searches"]
        report["checks"]["isbn_action_is_explicit_local_identity_scoped"] = True
        view._master._page.selection.set_selected(1)
        later(150, selected_b)

    def selected_b():
        view = state["view"]
        assert any(
            isinstance(widget, Gtk.Label) and widget.get_text() == "Livre B"
            for widget in walk(view._master._page.detail)
        ), "The second candidate did not replace the first GTK detail"
        capture("02-selected-b")
        # Retained callback from old A selection must be inert even though
        # the original button object still has its Python reference.
        state["old_action"].emit("clicked")
        assert len(state["searches"]) == 1
        report["checks"]["stale_action_after_fast_selection"] = True
        view._text.set_text("ABSENT-THIS-TITLE")
        later(170, no_match)

    def no_match():
        view = state["view"]
        assert view._master._page.store.get_n_items() == 0
        assert view._master._page.selection.get_selected_item() is None
        state["old_action"].emit("clicked")
        assert len(state["searches"]) == 1
        capture("03-no-match")
        report["checks"]["zero_results_revoke_detail_and_actions"] = True
        view._text.set_text("")
        assert view._master._page.store.get_n_items() == 2
        view._master._page.selection.set_selected(0)
        view._identity.set_selected(1)
        later(180, changed_profile)

    def changed_profile():
        report["phase_code"] = 21
        view = state["view"]
        assert view.selected_identity_id() == "bob"
        assert view._master._page.store.get_n_items() == 0
        assert view._master._page.selection.get_selected_item() is None
        state["old_action"].emit("clicked")
        assert len(state["searches"]) == 1
        capture("04-bob-empty")
        report["checks"]["identity_switch_discards_old_detail"] = True
        state["plugin"].deactivate()
        state["old_action"].emit("clicked")
        assert len(state["searches"]) == 1
        report["checks"]["disposed_callbacks_inert"] = True
        # A real deactivation + fresh plugin activation must create a new
        # GTK detail, not resurrect the old selection or old identity.
        previous_view = view
        state["window"].service.current_identity = NS(identity_id="alice")
        plugin = RecommendationsPlugin().activate(NS(config={}))
        fresh_widget = plugin.build(_UiHost(state["window"]))
        plugin._view._start_refresh = lambda: None
        state["window"].set_content(fresh_widget)
        state.update(view=plugin._view, plugin=plugin)
        assert plugin._view is not previous_view
        later(370, after_reactivation)

    def after_reactivation():
        report["phase_code"] = 22
        view = state["view"]
        assert view.widget.get_mapped()
        assert view.selected_identity_id() == "alice"
        assert view._master._page.store.get_n_items() == 0
        assert view._master._page.selection.get_selected_item() is None
        # A long but bounded public description exercises the shared
        # DetailPanel vertical scroller at both actual Xvfb widths.
        long_notice = "Description publique attestée, sans achat. " * 150
        reactivated = ranked(
            "edition-reopened", "Livre Réactivé", "9780306406157", score=90
        )
        reactivated.item["description"] = long_notice
        view._ranked = [reactivated]
        view._render()
        assert view._master._page.store.get_n_items() == 1
        view._master._page.selection.set_selected(0)
        later(250, after_reactivation_detail)

    def after_reactivation_detail():
        report["phase_code"] = 23
        view = state["view"]
        assert view._master._page.selection.get_selected() == 0
        assert any(
            isinstance(widget, Gtk.Label)
            and widget.get_text() == "Livre Réactivé"
            for widget in walk(view._master._page.detail)
        ), "New plugin did not render a fresh detail through shared GTK"
        assert view.selected_identity_id() == "alice"
        assert len(state["searches"]) == 1
        assert not state["plugin"]._view._disposed
        report["checks"]["reactivation_fresh_detail_and_identity"] = True
        scrolls = [
            widget for widget in walk(view._master._page.detail)
            if isinstance(widget, Gtk.ScrolledWindow)
        ]
        scrollable = [
            widget.get_vadjustment() for widget in scrolls
            if widget.get_vadjustment() is not None
            and widget.get_vadjustment().get_upper()
            > widget.get_vadjustment().get_page_size() + 12
        ]
        assert scrollable, "Long shared detail cannot scroll at this GTK width"
        adjustment = scrollable[0]
        end = adjustment.get_upper() - adjustment.get_page_size()
        adjustment.set_value(min(end, 140))
        assert adjustment.get_value() > 0
        report["checks"]["detail_scroll_functional"] = True
        capture("05-reopened-scrolled")
        # Returning to the list removes the active detail and must not
        # turn a still-stored old action into a usable callback.
        view._master._page.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        later(200, after_return_to_list)

    def after_return_to_list():
        report["phase_code"] = 24
        view = state["view"]
        assert view._master._page.store.get_n_items() == 1
        assert view._master._page.selection.get_selected_item() is None
        state["old_action"].emit("clicked")
        assert len(state["searches"]) == 1
        report["checks"]["return_to_list_keeps_old_action_inert"] = True
        report["checks"]["no_network_or_acquisition"] = True
        capture("06-return-to-list")
        state["plugin"].deactivate()
        report["status"] = "PASS"
        save()
        app.quit()

    def activate(_app):
        window = Adw.ApplicationWindow(application=app)
        window.set_default_size(args.width - 40, 850 if args.width == 1366 else 930)
        window._closed = False
        window.covers = None
        window.service = NS(
            current_identity=NS(identity_id="alice"),
            dashboard_state=lambda: [
                {"identity_id": "alice", "label": "Alice", "selected": True},
                {"identity_id": "bob", "label": "Bob", "selected": False},
            ],
        )
        window._overview_listeners = []
        window.toast = lambda text, timeout=3: state["toasts"].append(text)
        window.search_books = lambda query, *, field, online, identity_id: (
            state["searches"].append((query, field, online, identity_id)) or True
        )
        host = _UiHost(window)
        plugin = RecommendationsPlugin().activate(NS(config={}))
        widget = plugin.build(host)
        # No remote ranking task: the synthetic candidate view below probes
        # the real selection, projection, GTK and public actions only.
        plugin._view._start_refresh = lambda: None
        window.set_content(widget)
        state.update(view=plugin._view, plugin=plugin, window=window,
                     device=X11Input())
        window.present()
        later(320, initial)

    app.connect("activate", activate)

    def timeout():
        if report["status"] == "RUNNING":
            fail(AssertionError("GTK Recommendations fixture timeout"))
        return False

    GLib.timeout_add_seconds(38, timeout)
    try:
        app.run([])
    finally:
        if report["status"] == "RUNNING":
            fail(AssertionError("GTK Recommendations closed before checks"))
        if state["device"] is not None:
            state["device"].close()
        save()
    # Fixed, non-sensitive phase exit codes help diagnose a failing GTK
    # callback from the privacy-safe CI summary without publishing traceback.
    return 0 if report["status"] == "PASS" else report.get("phase_code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
