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
from cifereca.gui.exact_search_notice_navigation import (
    can_open_loaded_search_notice as core_can_open_exact,
    open_loaded_search_notice as core_open_exact,
)
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
        docbase="BOOKS", source_collection="catalog", isbn=isbn,
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
        "p1_1354_phase": "probe_start",
    }
    app = Adw.Application(
        application_id="org.cifereca.E2ERecommendations",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    state = {"view": None, "plugin": None, "window": None,
             "device": None, "searches": [], "toasts": [],
             "old_action": None, "exact_navigations": [],
             "exact_selected": [], "exact_button": None, "exact_clicked": 0,
             "stale_page_button": None}

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
        # Stable closed error phase for CI. Never print exception contents,
        # user-entered text, source coordinates, GTK labels or traceback.
        allowed = {
            "probe_start", "initial", "long_detail", "scrolled",
            "exact_setup", "exact_button", "exact_focus", "exact_key",
            "exact_page_focus", "exact_page_key", "exact_page_verdict",
            "exact_verdict", "exact_key_dispatch", "exact_navigation",
            "exact_selection", "exact_no_search", "return_list",
        }
        phase = report.get("p1_1354_phase")
        if type(phase) is str and phase in allowed:
            print("P1_1354_CLOSED_FAILURE_STAGE=" + phase, flush=True)
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
        report["p1_1354_phase"] = "initial"
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
        # Populate 32 *attested synthetic* authors in the shared Catalogue
        # section: a stable height stress case independent of how libadwaita
        # lays out and wraps one long subtitle.
        reactivated.item["authors"] = [
            "Autrice Attestée",
            *(f"Contributeur de référence {index:02d}" for index in range(1, 32)),
        ]
        view._ranked = [reactivated]
        view._render()
        assert view._master._page.store.get_n_items() == 1
        view._master._page.selection.set_selected(0)
        later(250, after_reactivation_detail)

    def after_reactivation_detail():
        report["p1_1354_phase"] = "long_detail"
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
        # A post-selection Gtk allocation may need an extra frame. Probe
        # the actual shared body scroller after layout, bounded to <1.2 s.
        assert isinstance(view._master._page.detail.body_scroll, Gtk.ScrolledWindow)
        later(120, lambda: verify_scrolling(0))

    def verify_scrolling(attempt):
        view = state["view"]
        panel = view._master._page.detail
        scroller = panel.body_scroll
        assert scroller.get_mapped(), "Shared detail scroller not mapped"
        adjustment = scroller.get_vadjustment()
        assert adjustment is not None
        end = adjustment.get_upper() - adjustment.get_page_size()
        if end <= 12 and attempt < 8:
            later(120, lambda: verify_scrolling(attempt + 1))
            return
        assert end > 12, "Synthetic long detail never overflows body scrollbar"
        adjustment.set_value(min(end, 140))
        assert adjustment.get_value() > 0
        report["checks"]["detail_scroll_functional"] = True
        capture("05-reopened-scrolled")
        report["p1_1354_phase"] = "scrolled"
        # This test uses the REAL Core exact-notice resolver and REAL
        # Recommandations GTK action. The Search target is a synthetic,
        # previously loaded Core-owned row; its select_key is a pure recorder,
        # never an acquisition or provider call.
        later(100, exact_loaded_bridge)

    def exact_loaded_bridge():
        report["p1_1354_phase"] = "exact_setup"
        view = state["view"]
        window = state["window"]
        notice = NS(
            source_provider="fixture", source_credential="fixture-public",
            record_id="edition-reopened", docbase="BOOKS",
            source_collection="catalog", id="core-loaded-1",
            metadata={"search_provenance": "remote"},
        )
        key = "fixture:core-loaded-1"
        window._search_mode = "global"
        window._loaded_search_context = ("édition", "title", "all", "alice")
        window.search_controls = NS(
            state=lambda: NS(text="édition", field="title", scope="all"),
        )
        window.service.searchable_providers = lambda: [("fixture", "Fixture")]
        window._remote_search_results = [notice]
        window.search_objects = {key: notice}
        window.search_page = NS(
            store=NS(
                get_n_items=lambda: 1,
                get_item=lambda _index: NS(key=key),
            ),
            select_key=lambda target: (
                state["exact_selected"].append(target) or target == key
            ),
        )
        window.navigate = lambda route: state["exact_navigations"].append(route)
        window.can_open_loaded_search_notice = lambda **coordinates: (
            core_can_open_exact(window, **coordinates)
        )
        window.open_loaded_search_notice = lambda **coordinates: (
            core_open_exact(window, **coordinates)
        )
        row = view._master._page.selection.get_selected_item()
        assert row is not None
        original = view._ranked_by_key[row.key]
        # Preserve identity and public recommendation data. Sixteen distinct
        # editions are NOT present in Core Search; edition #17 is the only
        # exact, already-loaded Search notice. No provider or HTTP is called.
        missing = tuple(CatalogNoticeVariant(
            item=original.item, source_provider="fixture",
            source_credential="fixture-public", record_id=f"unloaded-{i:02}",
            docbase="BOOKS", source_collection="catalog",
            isbn="9780306406157",
        ) for i in range(1, 17))
        view._ranked_by_key[row.key] = RankedCandidate(
            item=original.item, score=original.score,
            reasons=original.reasons, sources=original.sources,
            notices=missing + original.notices,
        )
        view._selected_row(row)
        assert not any(
            str(getattr(widget, "_cifereca_action_id", "")).startswith(
                "recommendation.loaded-notice."
            ) for widget in walk(view.widget)
        )
        next_buttons = [
            widget for widget in walk(view.widget)
            if isinstance(widget, Gtk.Button)
            and getattr(widget, "_cifereca_action_id", "") ==
                "recommendation.exact-notice.next"
        ]
        assert len(next_buttons) == 1 and next_buttons[0].get_mapped()
        next_button = next_buttons[0]
        state["stale_page_button"] = next_button
        report["p1_1354_phase"] = "exact_page_focus"
        assert next_button.grab_focus() is not False
        later(170, lambda: press_exact_page(next_button))

    def press_exact_page(button):
        report["p1_1354_phase"] = "exact_page_key"
        assert button.has_focus() and button.get_sensitive()
        state["device"].key("Return", True)
        state["device"].key("Return", False)
        later(240, after_exact_page)

    def after_exact_page():
        report["p1_1354_phase"] = "exact_page_verdict"
        view = state["view"]
        assert view._exact_notice_state["page"] == 1
        assert len(state["searches"]) == 1
        assert not state["exact_navigations"] and not state["exact_selected"]
        assert view._master._page.selection.get_selected() == 0
        report["checks"]["P1_1354_gtk_page17_keyboard_local_only"] = True
        buttons = [
            widget for widget in walk(view.widget)
            if isinstance(widget, Gtk.Button)
            and str(getattr(widget, "_cifereca_action_id", "")).startswith(
                "recommendation.loaded-notice."
            )
        ]
        report["p1_1354_phase"] = "exact_button"
        assert len(buttons) == 1, "Loaded exact Search route is missing"
        button = buttons[0]
        assert button.get_mapped()
        assert "vault" not in str(
            getattr(button, "_cifereca_action_label", "") or ""
        ).casefold()
        assert not state["exact_navigations"] and not state["exact_selected"]
        assert not state["searches"][1:], "Rendering must not search"
        state["exact_button"] = button
        button.connect("clicked", lambda _button: state.__setitem__(
            "exact_clicked", state["exact_clicked"] + 1
        ))
        capture("05b-loaded-exact-route")
        report["p1_1354_phase"] = "exact_focus"
        # This action lives below a long detail's scrolled content.
        # Separate GTK focus/scroll from XTEST Return: activation in the
        # same GTK main-loop turn is not guaranteed to reach the button.
        assert button.grab_focus() is not False
        later(170, lambda: exact_press(button))

    def exact_press(button):
        report["p1_1354_phase"] = "exact_key"
        assert button.get_mapped() and button.get_sensitive()
        assert button.has_focus(), "Exact action did not receive GTK focus"
        state["device"].key("Return", True)
        state["device"].key("Return", False)
        later(300, after_exact_loaded_bridge)

    def after_exact_loaded_bridge():
        report["p1_1354_phase"] = "exact_verdict"
        view = state["view"]
        report["p1_1354_phase"] = "exact_key_dispatch"
        assert state["exact_clicked"] == 1, "Exact action did not receive keyboard activation"
        report["p1_1354_phase"] = "exact_navigation"
        assert state["exact_navigations"] == ["search"]
        report["p1_1354_phase"] = "exact_selection"
        assert state["exact_selected"] == ["fixture:core-loaded-1"]
        report["p1_1354_phase"] = "exact_no_search"
        assert len(state["searches"]) == 1  # Only the earlier explicit ISBN
        assert view.selected_identity_id() == "alice"
        assert view._master._page.store.get_n_items() == 1
        report["checks"]["P1_1354_real_gtk_exact_notice_core_navigation_no_network"] = True
        # Same real GTK Recommandations view, but Search no longer has the
        # variant loaded. The optional cache-only Core gateway is probed
        # without returning rights or calling any supplier. The Core/SQLite
        # resolver itself is tested with real Storage outside this E2E probe.
        known_calls = []
        state["window"].can_open_loaded_search_notice = lambda **_kw: False
        state["window"].can_open_known_search_notice = lambda **kw: (
            kw.get("record_id") == "edition-reopened"
            and kw.get("credential") == "fixture-public"
            and kw.get("identity_id") == "alice"
        )
        state["window"].open_known_search_notice = lambda **kw: (
            known_calls.append(kw.copy()) or True
        )
        selected_row = view._master._page.selection.get_selected_item()
        assert selected_row is not None
        view._selected_row(selected_row)
        cached_buttons = [
            widget for widget in walk(view.widget)
            if isinstance(widget, Gtk.Button)
            and str(getattr(widget, "_cifereca_action_id", "")).startswith(
                "recommendation.known-notice."
            )
        ]
        assert len(cached_buttons) == 1
        assert not known_calls
        cached_buttons[0].emit("clicked")
        assert len(known_calls) == 1
        assert known_calls[0]["record_id"] == "edition-reopened"
        assert len(state["searches"]) == 1
        state["known_button"] = cached_buttons[0]
        state["known_calls"] = known_calls
        report["checks"]["P0_1354_real_gtk_known_notice_gateway_explicit_only"] = True
        # Returning to recommendations preserves identity and the selection
        # without resurrecting an earlier Core/GTK callback.
        view._master._page.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        later(200, after_return_to_list)

    def after_return_to_list():
        report["p1_1354_phase"] = "return_list"
        view = state["view"]
        assert view._master._page.store.get_n_items() == 1
        assert view._master._page.selection.get_selected_item() is None
        state["old_action"].emit("clicked")
        assert len(state["searches"]) == 1
        state["exact_button"].emit("clicked")
        state["stale_page_button"].emit("clicked")
        state["known_button"].emit("clicked")
        assert len(state["known_calls"]) == 1  # detached cached button inert
        assert state["exact_navigations"] == ["search"]
        assert state["exact_selected"] == ["fixture:core-loaded-1"]
        report["checks"]["return_to_list_keeps_old_action_inert"] = True
        report["checks"]["P1_1354_detached_exact_action_is_inert"] = True
        report["checks"]["P1_1354_detached_pagination_is_inert"] = True
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
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
