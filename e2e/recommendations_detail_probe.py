#!/usr/bin/env python3
"""#1599: real Recommandations GTK master/detail and stale action contract.

Production RecommendationsPlugin, _UiHost and PublicBookMasterDetailView are
mounted in real Xvfb GTK. The ranking task boundary is disabled for the probe
and candidate results are synthetic; no provider search, LLM or acquisition.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
from io import BytesIO
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
import httpx
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import Image, ImageGrab

from cifereca.gui.book_widgets import BookListCell, CoverView
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.action_widgets import action_button
from cifereca.gui.main_window import MainWindow
from cifereca.models import SearchResult, ItemDetail
from cifereca.gui.plugin_ui_host import _UiHost
from cifereca.gui.public_book_view import PublicBookMasterDetailView
from cifereca.gui.exact_search_notice_navigation import (
    can_open_loaded_search_notice as core_can_open_exact,
    open_loaded_search_notice as core_open_exact,
)
from recommendation_engine import CatalogNoticeVariant, RankedCandidate
from recommendations import RecommendationsPlugin
from x11_input import X11Input


COVER_URL = "https://covers.example.org/recommendation.png"


def ranked(book_id, title, isbn, *, score, provider="fixture"):
    item = {
        "book_id": book_id, "record_id": book_id,
        "title": title, "author": "Autrice Attestée",
        "authors": ["Autrice Attestée", "Deuxième Auteur"],
        "description": "Description publique, sans donnée de compte.",
        "isbn": isbn, "language": "fr", "format": "EPUB",
        "source_provider": provider, "source_credential": "fixture-public",
    }
    variant = CatalogNoticeVariant(
        item=item, source_provider=provider,
        source_credential="fixture-public", record_id=book_id,
        docbase="BOOKS", source_collection="catalog", isbn=isbn,
    )
    return RankedCandidate(
        item=item, score=score, reasons=("Auteur : Autrice Attestée",),
        sources=(provider,), notices=(variant,),
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
    parser.add_argument("--width", type=int, choices=(1024, 1366, 1600), required=True)
    parser.add_argument("--scale", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Real GTK/XTEST display required")
    start = monotonic()
    report = {
        "scenario": "recommendations-detail-gtk", "status": "RUNNING",
        "width": args.width, "scale": args.scale, "backend": "GTK4/X11/XTEST",
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "steps": [], "checks": {}, "network_calls": 0,
        "real_plugin": "src/plugins/recommendations/recommendations.py",
        "real_host": "cifereca.gui.plugin_ui_host._UiHost",
        "source_data": "synthetic ranked candidates; ranking task disabled",
        "p1_1354_phase": "probe_start",
        "p1_4664_phase": "unstarted",
        "p1_774_phase": "setup",
    }
    app = Adw.Application(
        application_id="org.cifereca.E2ERecommendations",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    state = {"view": None, "plugin": None, "window": None,
             "device": None, "searches": [], "toasts": [],
             "old_action": None, "exact_navigations": [],
             "exact_selected": [], "exact_button": None, "exact_clicked": 0,
             "stale_page_button": None, "in_place_jobs": [],
             "in_place_holdings": [], "in_place_obtain": [],
             "in_place_activity": [], "in_place_item": None,
             "in_place_detail": None, "in_place_download": None}
    image_requests = []
    cover_loader = None
    cover_bytes = BytesIO()
    Image.new("RGB", (64, 96), (130, 20, 100)).save(cover_bytes, format="PNG")

    def image_response(request):
        assert request.method == "GET" and str(request.url) == COVER_URL
        image_requests.append(str(request.url))
        return httpx.Response(
            200, headers={"Content-Type": "image/png"}, content=cover_bytes.getvalue(),
        )

    def save():
        report["duration_seconds"] = round(monotonic() - start, 2)
        (out / "screen.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def capture(stage):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).convert("RGB")
        expected_height = {1024: 768, 1366: 900, 1600: 1000}[args.width]
        report["p1_774_phase"] = "display_geometry"
        window = state["window"]
        window_width, window_height = window.get_width(), window.get_height()
        report["p1_774_geometry"] = {
            "display_width": image.width, "display_height": image.height,
            "window_width": window_width, "window_height": window_height,
        }
        assert image.size == (
            args.width * args.scale, expected_height * args.scale,
        ), "Physical Xvfb screen must be sized in device pixels"
        report["p1_774_phase"] = "gtk_geometry"
        assert window.get_scale_factor() == args.scale, (
            "GTK must actually use the requested HiDPI scale"
        )
        assert args.width - 100 <= window_width <= args.width, (
            "Toplevel logical width must remain in the 1024px layout budget"
        )
        assert 500 <= window_height <= expected_height
        report["checks"]["P1_774_real_gtk_logical_hidpi_geometry"] = True
        report["p1_774_phase"] = "image_pixels"
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
            "window_width": window_width,
            "window_height": window_height,
            "display_width": image.width,
            "display_height": image.height, "scale": args.scale,
        })
        save()

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        # Stable closed error phase for CI. Never print exception contents,
        # user-entered text, source coordinates, GTK labels or traceback.
        allowed = {
            "probe_start", "initial", "long_detail", "scrolled",
            "exact_setup", "exact_button", "exact_overflow",
            "exact_overflow_ready", "exact_focus", "exact_key",
            "exact_page_focus", "exact_page_key", "exact_page_verdict",
            "exact_verdict", "exact_key_dispatch", "exact_navigation",
            "exact_selection", "exact_no_search", "return_list",
        }
        phase = report.get("p1_1354_phase")
        if type(phase) is str and phase in allowed:
            print("P1_1354_CLOSED_FAILURE_STAGE=" + phase, flush=True)
        # Only a fixed vocabulary leaves the private GTK runner; never
        # stringify provider errors, account IDs or the failing callback.
        phase_4664 = report.get("p1_4664_phase")
        if phase_4664 in {
            "setup", "button", "button_mapped", "button_selection",
            "button_jobs", "button_focus", "key", "holdings", "download",
            "obtain", "unavailable",
        }:
            print("P1_4664_CLOSED_FAILURE_STAGE=" + phase_4664, flush=True)
        # Stable nonprivate markers. Exception strings / titles remain local.
        closed_774 = {
            "setup", "display_geometry", "gtk_geometry", "image_pixels",
            "keyboard_focus", "keyboard_type", "keyboard_verdict",
            "original_scenarios",
        }
        if report.get("p1_774_phase") in closed_774:
            print("P1_774_CLOSED_FAILURE_STAGE=" + report["p1_774_phase"], flush=True)
        # A line index *inside this fixed synthetic probe* is a bounded,
        # non-private diagnostic; preserve it before capture('failure')
        # overwrites the last phase with image_pixels.
        try:
            frames = [
                frame.lineno for frame in traceback.extract_tb(exc.__traceback__)
                if Path(frame.filename).name == "recommendations_detail_probe.py"
                and 1 <= frame.lineno <= 1200
            ]
            if frames:
                report["p1_774_probe_line"] = frames[-1]
        except Exception:
            pass
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
            ranked("edition-b", "Livre B", "9780140328721", score=85, provider="second"),
            ranked("edition-c", "Livre C", "9780140328721", score=60, provider="third"),
        ]
        view._ranked[0].item["thumbnail_url"] = COVER_URL
        view._ranked[0].item["metadata"] = {
            "subjects": ["Astronomie"], "series": "Planètes",
        }
        view._ranked[1].item["metadata"] = {
            "subjects": ["Navigation"], "series": "Océans",
        }
        view._reload_providers((
            ("fixture", "Premier"), ("second", "Deuxième"), ("third", "Troisième"),
        ))
        view._reload_languages(view._ranked)
        view._ranked[1].item["format"] = "PDF"
        view._ranked[2].item["format"] = ""
        view._ranked[2].item["medium"] = "physical"
        view._reload_document_choices()
        view._render()
        query = view._query_controls
        store = view._master._page.store
        generation = view._generation
        # #4761: verify REAL GTK hierarchy, not merely a static builder.
        # Calculation options form one horizontal row with a scrollable
        # viewport. Search's own LocalQueryControls remains below it.
        strip = view._calculation_parameters_scroll
        parameters = view._calculation_parameters
        assert isinstance(strip, Gtk.ScrolledWindow)
        # GTK4 wraps non-Scrollable Gtk.Box in its own GtkViewport.
        # ScrolledWindow.get_child() returns that adapter, not the Box.
        viewport = strip.get_child()
        assert isinstance(viewport, Gtk.Viewport)
        assert viewport.get_child() is parameters
        assert parameters.get_orientation() == Gtk.Orientation.HORIZONTAL
        assert strip.get_policy() == (
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER,
        )
        assert strip.get_mapped()
        assert view._exclude_owned.get_parent() is parameters
        assert view._refresh_button.get_parent() is parameters
        assert view._score.get_parent().get_parent() is parameters
        assert view._identity.get_parent().get_parent() is parameters
        assert view._reference.get_parent().get_parent() is parameters
        assert query.widget.get_parent() is not parameters
        assert query.widget.get_mapped() and store.get_n_items() == 3
        report["checks"]["P1_4761_gtk_single_row_calculation_strip"] = True
        query.provider.buttons["fixture"].set_active(True)
        assert store.get_n_items() == 1
        query.provider.buttons["second"].set_active(True)
        assert query.selected_sources() == ("fixture", "second")
        assert store.get_n_items() == 2
        assert view._generation == generation and not state["searches"]
        report["checks"]["shared_multisource_filter_is_local"] = True
        query.provider.set_values(())
        query.types.buttons["pdf"].set_active(True)
        assert store.get_n_items() == 1 and store.get_item(0).title == "Livre B"
        # #774/#3687: assert the *real GTK* counter follows the same Core
        # local document facet as the rows, without another recommendation
        # calculation or provider search.
        assert query.active_document_labels() == ("PDF",)
        assert "1 sur 3" in view._summary.get_text()
        assert "PDF" in view._summary.get_text()
        query.types.set_values(())
        assert query.active_document_labels() == ()
        assert store.get_n_items() == 3
        assert "PDF" not in view._summary.get_text()
        query.scope.set_selected(1)
        assert store.get_n_items() == 1 and store.get_item(0).title == "Livre C"
        assert query.active_document_labels() == ("Type · Physique",)
        assert "Type · Physique" in view._summary.get_text()
        query.scope.set_selected(0)
        assert query.active_document_labels() == ()
        assert "Type · Physique" not in view._summary.get_text()
        assert view._generation == generation and not state["searches"]
        report["checks"]["shared_document_filters_are_local"] = True
        report["checks"]["shared_document_counter_reversible_gtk"] = True
        # #4761: Search's SAME "Champ" dropdown is visible in the local
        # recommendation controls. Switching title -> subject -> all
        # only refilters existing rows, never requests the catalogue.
        assert query.field.get_visible()
        query.field.set_selected(4)  # Champ · Sujet
        query.entry.set_text("astronomie")
        # Gtk.SearchEntry.search-changed is debounced. The same explicit
        # activation used by this real GTK probe commits the local filter.
        query.entry.emit("activate")
        assert store.get_n_items() == 1
        assert store.get_item(0).title == "Livre A"
        assert "Champ · Sujet" in view._summary.get_text()
        query.field.set_selected(1)  # Champ · Titre
        assert store.get_n_items() == 0
        assert "0 sur 3" in view._summary.get_text()
        assert "Champ · Titre" in view._summary.get_text()
        assert view._master._page.count_label.get_text() == "0 / 3 éléments"
        # A real GTK reset is local, atomic and does not create an implicit
        # recommendation query, even when all three rows are filtered out.
        assert query.clear_button.get_sensitive()
        query.clear_button.emit("clicked")
        assert query.entry.get_text() == ""
        assert query.field.get_selected() == 0
        assert query.selected_sources() == ()
        assert query.selected_languages() == ()
        assert not query.clear_button.get_sensitive()
        assert store.get_n_items() == 3
        assert view._master._page.count_label.get_text() == "3 élément(s)"
        assert view._generation == generation and not state["searches"]
        report["checks"]["P1_3687_shared_empty_count_and_reset_gtk"] = True
        query.entry.set_text("")
        query.entry.emit("activate")
        query.field.set_selected(0)  # Tous les champs (legacy title/author)
        assert store.get_n_items() == 3
        assert query.active_document_labels() == ()
        assert view._generation == generation and not state["searches"]
        report["checks"]["P1_4761_shared_local_field_filter_gtk"] = True
        report["checks"]["P1_4761_local_field_filter_reversible"] = True
        # #4761: an edited affinity threshold is itself a visible local
        # filter and must disappear from the count when reset to its default.
        view._score.set_value(95)
        assert store.get_n_items() == 0
        assert "0 sur 3" in view._summary.get_text()
        assert "Affinité ≥ 95" in view._summary.get_text()
        view._score.set_value(15)
        assert store.get_n_items() == 3
        assert "Affinité ≥" not in view._summary.get_text()
        assert view._generation == generation and not state["searches"]
        report["checks"]["P1_4761_affinity_filter_count_reversible_gtk"] = True
        # #4761: the ranked candidate can omit the aggregate ISBN while
        # the original, exact catalogue notice retains its verified ISBN.
        # Field · ISBN must still find that edition, without a network call.
        original_isbn = view._ranked[0].item.pop("isbn")
        assert view._ranked[0].notices[0].isbn == original_isbn
        query.field.set_selected(3)  # Champ · ISBN (shared Search widget)
        query.entry.set_text(original_isbn)
        query.entry.emit("activate")  # SearchEntry signal is debounced
        assert store.get_n_items() == 1 and store.get_item(0).title == "Livre A"
        assert "Champ · ISBN" in view._summary.get_text()
        assert view._generation == generation and not state["searches"]
        report["checks"]["P1_4761_verified_variant_isbn_local_gtk"] = True
        query.entry.set_text("")
        query.entry.emit("activate")
        query.field.set_selected(0)
        view._ranked[0].item["isbn"] = original_isbn
        assert store.get_n_items() == 3
        # Prove local filtering by a genuine XTEST mouse focus and keyboard
        # event. Gtk.SearchEntry.grab_focus() alone may lose focus before
        # an asynchronous callback; the test must use the visible pointer.
        state["p1_774_filter_generation"] = generation
        state["p1_774_focus_retry"] = 0
        state["p1_774_filter_retry"] = 0
        report["p1_774_phase"] = "keyboard_focus"
        assert query.entry.get_mapped()
        state["device"].click_widget(query.entry)
        later(160, type_filter_by_keyboard)

    def search_entry_has_native_focus(entry):
        """GTK4 SearchEntry delegates keyboard focus to its inner GtkText."""
        root = entry.get_root()
        current = root.get_focus() if isinstance(root, Gtk.Window) else None
        for _ in range(24):
            if current is None:
                return entry.has_focus()
            if current is entry:
                return True
            current = current.get_parent()
        return False

    def type_filter_by_keyboard():
        report["p1_774_phase"] = "keyboard_type"
        view = state["view"]
        query = view._query_controls
        if not search_entry_has_native_focus(query.entry) and state["p1_774_focus_retry"] < 2:
            state["p1_774_focus_retry"] += 1
            state["device"].click_widget(query.entry)
            later(140, type_filter_by_keyboard)
            return
        assert search_entry_has_native_focus(query.entry)
        state["device"].type_text("Livre B")
        later(350, after_keyboard_filter)

    def after_keyboard_filter():
        report["p1_774_phase"] = "keyboard_verdict"
        view = state["view"]
        query = view._query_controls
        store = view._master._page.store
        # SearchEntry notifications are debounced while GTK also rebuilds
        # the live row model and its counter. Only the *complete*, coherent
        # result may end this bounded wait; a correct row with a stale count
        # is not a successful keyboard filter.
        count_text = view._summary.get_text()
        complete = (
            query.entry.get_text() == "Livre B"
            and store.get_n_items() == 1
            and store.get_item(0).title == "Livre B"
            and "1 sur 3" in count_text
            and "Livre B" in count_text
        )
        # HiDPI can take longer to process native XTEST input and deliver
        # debounced GTK model/counter updates. Keep a finite deadline and
        # require every original assertion; do not accept a stale counter.
        max_retries = 12 if args.scale == 2 else 3
        if not complete and state["p1_774_filter_retry"] < max_retries:
            state["p1_774_filter_retry"] += 1
            later(150, after_keyboard_filter)
            return
        # Closed, synthetic-only diagnostic: boolean coherence flags,
        # never user text, provider labels, or account information.
        report["p1_774_keyboard_coherence"] = {
            "text": int(query.entry.get_text() == "Livre B"),
            "rows": int(store.get_n_items() == 1),
            "title": int(store.get_n_items() == 1 and store.get_item(0).title == "Livre B"),
            "count": int("1 sur 3" in view._summary.get_text()),
            "summary_title": int("Livre B" in view._summary.get_text()),
            "retries": min(30, int(state["p1_774_filter_retry"])),
        }
        # Each independent assert has a distinct safe source-line marker
        # in the public E2E reporter. This differentiates focus/text,
        # row-model and counter failures without logging any input text.
        assert query.entry.get_text() == "Livre B"
        assert store.get_n_items() == 1
        assert store.get_item(0).title == "Livre B"
        # Closed-source-line assertions separate an inconsistent *model* from
        # a stale summary: no titles, account IDs or actual query are logged.
        assert len(view._ranked) == 3
        assert view._score.get_value() == 15
        assert view._local_filter_state()[0] == "Livre B"
        assert sum(1 for ranked in view._ranked if view._matches(ranked)) == 1
        assert "1 sur 3" in view._summary.get_text()
        assert "Livre B" in view._summary.get_text()
        assert view._generation == state["p1_774_filter_generation"]
        assert not state["searches"]
        report["checks"]["P1_774_real_gtk_xtest_keyboard_local_filter"] = True
        capture("00b-keyboard-local-filter")
        # Restoring the old scenario via a normal entry reset is not the
        # keyboard proof. The preceding XTEST assertion is the proof.
        query.entry.set_text("")
        query.entry.emit("activate")
        assert store.get_n_items() == 3
        report["p1_774_phase"] = "original_scenarios"
        view._ranked.pop()
        view._reload_document_choices()
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
        verify_cover_a(0)

    def verify_cover_a(attempt):
        page = state["view"]._master._page
        list_covers = [
            cell.cover for cell in walk(page.list_stack)
            if isinstance(cell, BookListCell) and cell.get_mapped()
            and cell.title_label.get_text() == "Livre A"
        ]
        detail_covers = [
            cover for cover in walk(page.detail)
            if isinstance(cover, CoverView) and cover.get_mapped()
        ]
        if not (
            len(list_covers) == len(detail_covers) == 1
            and all(cover.picture.get_paintable() is not None
                    for cover in list_covers + detail_covers)
        ):
            assert attempt < 20, "La couverture n’est pas chargée dans la liste et le détail"
            later(100, lambda: verify_cover_a(attempt + 1))
            return
        assert image_requests == [COVER_URL]
        report["checks"]["shared_cover_loader_list_and_detail"] = True
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
        covers = [
            cover for cover in walk(view._master._page.detail)
            if isinstance(cover, CoverView) and cover.get_mapped()
        ]
        assert len(covers) == 1 and covers[0].placeholder.get_visible()
        assert covers[0].picture.get_paintable() is None
        assert image_requests == [COVER_URL]
        report["checks"]["missing_cover_uses_shared_placeholder"] = True
        capture("02-selected-b")
        # Retained callback from old A selection must be inert even though
        # the original button object still has its Python reference.
        state["old_action"].emit("clicked")
        assert len(state["searches"]) == 1
        report["checks"]["stale_action_after_fast_selection"] = True
        view._query_controls.entry.set_text("ABSENT-THIS-TITLE")
        view._query_controls.entry.emit("activate")
        later(170, no_match)

    def no_match():
        view = state["view"]
        assert view._master._page.store.get_n_items() == 0
        assert view._master._page.selection.get_selected_item() is None
        assert view._master._page.empty_page.get_title() == "Aucun résultat pour ces filtres"
        state["old_action"].emit("clicked")
        assert len(state["searches"]) == 1
        capture("03-no-match")
        report["checks"]["zero_results_revoke_detail_and_actions"] = True
        view._query_controls.entry.set_text("")
        view._query_controls.entry.emit("activate")
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
        updated = replace(view._ranked[0], score=96)
        view._ranked = [updated]
        view._render()
        assert view._master._page.selection.get_selected() == 0
        assert view._exact_notice_state["ranked"] is updated
        assert len(state["searches"]) == 1
        report["checks"]["retained_selection_refreshes_detail"] = True
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
        panel = view._master._page.detail
        # The native adaptive action bar intentionally promotes the exact
        # in-place route (#4664) at narrow widths. The old Search redirect
        # remains a normal GTK action, accessible under "Plus d’actions".
        # Inspect the bar's owned buttons, not only mapped descendants:
        # overflow popovers are outside the regular widget walk until shown.
        buttons = [
            widget for widget in panel._action_buttons
            if isinstance(widget, Gtk.Button)
            and str(getattr(widget, "_cifereca_action_id", "")).startswith(
                "recommendation.loaded-notice."
            )
        ]
        report["p1_1354_phase"] = "exact_button"
        assert len(buttons) == 1, "Loaded exact Search route is missing"
        button = buttons[0]
        if not button.get_mapped():
            bar = panel.action_scroll
            report["p1_1354_phase"] = "exact_overflow"
            assert button.get_parent() is bar.overflow_box
            assert bar.overflow_button.get_visible()
            assert bar.overflow_button.get_mapped()
            # #1216/#3687: mapped is not enough. The menu's entire
            # rectangle must actually fit the scrolled action viewport.
            inside, menu_box = bar.overflow_button.compute_bounds(bar)
            assert inside and menu_box.get_x() >= -1
            assert menu_box.get_x() + menu_box.get_width() <= bar.get_width() + 1
            # At 1024 px this button lives in a horizontal ScrolledWindow.
            # A mapped widget can be partly clipped, and the native-surface
            # centre from compute_bounds(native) need not be GTK's hit target.
            # Locate a *pick-verified* point inside the allocated toplevel
            # before dispatching physical XTEST. Do not emit "clicked", call
            # popup() or treat an inaccessible overflow as a passing test.
            menu = bar.overflow_button
            window = state["window"]
            ok, bounds = menu.compute_bounds(window)
            assert ok
            target = None
            for h in (0.5, 0.3, 0.7):
                for v in (0.5, 0.3, 0.7):
                    x = bounds.get_x() + bounds.get_width() * h
                    y = bounds.get_y() + bounds.get_height() * v
                    if not (0 <= x < window.get_width() and
                            0 <= y < window.get_height()):
                        continue
                    picked = window.pick(x, y, Gtk.PickFlags.DEFAULT)
                    while picked is not None and picked is not menu:
                        picked = picked.get_parent()
                    if picked is menu:
                        target = state["device"].window_point(window, x, y)
                        break
                if target is not None:
                    break
            # The existing maintenance probe has already validated a
            # vertically wrapped GTK pick fallback at 920 px. Use it here
            # when nested scroller bounds are inaccurate; still no click
            # unless the *same* menu button is picked by the toplevel.
            if target is None:
                from maintenance_hit_target import choose_verified_point
                try:
                    x, y = choose_verified_point(
                        window, menu, bounds, Gtk.PickFlags.DEFAULT,
                    )
                except RuntimeError:
                    pass
                else:
                    target = state["device"].window_point(window, x, y)
            if target is None:
                # Closed numeric geometry only. A menu wholly outside its
                # viewport is an actual UX failure, never a reason to force
                # a GTK popup or silently skip the narrow-width scenario.
                bar_ok, bar_bounds = bar.compute_bounds(window)
                if bar_ok:
                    def rect_dims(rect):
                        return tuple(int(round(value)) for value in (
                            rect.get_x(), rect.get_y(),
                            rect.get_width(), rect.get_height(),
                        ))
                    values = rect_dims(bounds) + rect_dims(bar_bounds)
                    report["p1_774_overflow_geometry"] = dict(zip(
                        ("menu_x", "menu_y", "menu_width", "menu_height",
                         "bar_x", "bar_y", "bar_width", "bar_height"), values,
                    ))
            assert target is not None, "Overflow button has no accessible GTK hit target"
            state["device"].click(*target)
            later(320, lambda: exact_overflow_ready(button))
            return
        exact_button_ready(button)

    def exact_overflow_ready(button):
        report["p1_1354_phase"] = "exact_overflow_ready"
        bar = state["view"]._master._page.detail.action_scroll
        assert bar.overflow_popover.get_mapped()
        assert button.get_mapped() and button.get_sensitive()
        report["checks"]["P1_4664_narrow_exact_redirect_in_accessible_overflow"] = True
        exact_button_ready(button)

    def exact_button_ready(button):
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
        # This action may be inside a real GTK overflow popover. Preserve
        # keyboard activation in both wide and narrow layouts.
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
        # Closing the overflow is a GUI-only transition; the Search
        # navigation proof below still uses the real action callback.
        bar = view._master._page.detail.action_scroll
        if bar.overflow_popover.get_mapped():
            bar.overflow_popover.popdown()
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
        # Continue in the SAME production plugin and actual GTK detail panel:
        # the old #1354 assertion only proved a redirect to Search.
        later(160, in_place_setup)

    def in_place_setup():
        report["p1_4664_phase"] = "setup"
        view = state["view"]
        window = state["window"]
        assert view.widget.get_mapped()
        assert view.selected_identity_id() == "alice"
        session = NS(
            provider_id="fixture", credential_id="fixture-public",
            connected=True,
        )
        service = window.service
        service.storage = NS(
            identity_for_connection=lambda provider, credential:
                "alice" if (provider, credential) == (
                    "fixture", "fixture-public",
                ) else None,
            provider_connections=lambda **kw: [{
                "identity_id": "alice", "provider_id": "fixture",
                "credential_id": "fixture-public", "enabled": True,
            }] if kw.get("identity_id") == "alice" else [],
        )
        service._session_for_source = lambda provider, credential: (
            session if (provider, credential) == (
                "fixture", "fixture-public",
            ) else None
        )
        service.searchable_providers = lambda: [("fixture", "Fixture")]
        service._source_context_epoch = 4
        service.current_identity = NS(identity_id="alice")
        service.provider_display_name = lambda provider: "Fixture"
        service.content_type_for = lambda _item: "ebook"
        service.availability_display_label = (
            lambda _item, _detail: "Disponible"
        )
        service.identities = [NS(identity_id="alice", label="Alice")]
        service.local_placement_for_source = lambda **_kw: None
        window._add_catalogue_action = lambda *_a: None
        window._identity_context_generation = 8
        window._search_generation = 9
        window._shell_page_rebuild_token = object()
        window._last_search_query = "édition"
        window._account_context_generation = 2
        window.plugin_manager = None
        window.action_button = action_button
        window.can_borrow_item = lambda _item: False
        window._add_local_book_actions = (
            MainWindow._add_local_book_actions.__get__(window)
        )
        window.download_direct_item = (
            MainWindow.download_direct_item.__get__(window)
        )
        window._activity_result = state["in_place_activity"].append
        service.obtain_search_item = lambda item: (
            state["in_place_obtain"].append(item) or
            NS(path=None, acquisition=NS(action="command"))
        )
        window.runner = NS(submit=lambda *a, **kw:
                           state["in_place_jobs"].append((a, kw)))
        item = SearchResult(
            id="core-loaded-1", record_id="edition-reopened",
            docbase="BOOKS", medium="digital", title="Livre Réactivé",
            author="Autrice Attestée", type="book", format="epub",
            publisher=None, published_at=None,
            description="Notice publique exacte.", thumbnail_url=None,
            friendly_url=None, scenario="BOOKS",
            source_provider="fixture", source_credential="fixture-public",
            source_collection="catalog",
        )
        detail = ItemDetail(
            record_id="edition-reopened", docbase="BOOKS",
            title="Livre Réactivé", author="Autrice Attestée", type="book",
            year=None, thumbnail_url=None,
            availability_status="available",
            availability_label="Disponible", holdings=[],
            source_provider="fixture", source_credential="fixture-public",
        )
        state["in_place_item"] = item
        state["in_place_detail"] = detail
        service.can_direct_download = lambda candidate: candidate is item
        service.holdings = lambda candidate, _query, *, force=False: (
            state["in_place_holdings"].append(candidate) or detail
        )
        window._loaded_search_context = (
            "édition", "title", "all", "alice",
        )
        window._search_mode = "global"
        window.search_controls = NS(
            state=lambda: NS(text="édition", field="title", scope="all"),
        )
        window._remote_search_results = [item]
        key = "fixture:core-loaded-1"
        window.search_objects = {key: item}
        window.search_page = NS(
            store=NS(
                get_n_items=lambda: 1,
                get_item=lambda _index: NS(key=key),
            ),
            select_key=lambda target: target == key,
        )
        window.can_open_loaded_search_notice = lambda **coords: (
            core_can_open_exact(window, **coords)
        )
        window.can_open_known_search_notice = lambda **_coords: False
        view._ranked = [ranked(
            "edition-reopened", "Livre Réactivé", "9780306406157",
            score=90,
        )]
        view._render()
        assert view._master._page.store.get_n_items() == 1
        view._master._page.selection.set_selected(0)
        later(160, in_place_ready)

    def in_place_button():
        view = state["view"]
        buttons = [
            widget for widget in walk(view._master._page.detail)
            if isinstance(widget, Gtk.Button)
            and str(getattr(widget, "_cifereca_action_id", "")).startswith(
                "recommendation.loaded-in-place."
            )
        ]
        assert len(buttons) == 1, "No exact in-place action in GTK"
        return buttons[0]

    def core_download_button():
        panel = state["view"]._master._page.detail
        buttons = [
            widget for widget in walk(panel)
            if isinstance(widget, Gtk.Button)
            and str(getattr(widget, "_cifereca_action_label", "")).startswith(
                "Télécharger"
            )
        ]
        assert len(buttons) == 1, "Core's direct Search download action missing"
        return buttons[0]

    def in_place_ready():
        report["p1_4664_phase"] = "button"
        view = state["view"]
        button = in_place_button()
        report["p1_4664_phase"] = "button_mapped"
        assert button.get_mapped() and button.get_sensitive()
        report["p1_4664_phase"] = "button_selection"
        assert view._master._page.selection.get_selected() == 0
        report["p1_4664_phase"] = "button_jobs"
        assert not state["in_place_jobs"]
        assert not state["in_place_holdings"]
        assert not state["in_place_obtain"]
        report["p1_4664_phase"] = "button_focus"
        assert button.grab_focus() is not False
        later(170, in_place_press)

    def in_place_press():
        report["p1_4664_phase"] = "key"
        button = in_place_button()
        assert button.get_mapped() and button.has_focus()
        state["device"].key("Return", True)
        state["device"].key("Return", False)
        later(220, in_place_deliver)

    def in_place_deliver():
        report["p1_4664_phase"] = "holdings"
        jobs = state["in_place_jobs"]
        assert len(jobs) == 1 and jobs[0][1]["kind"] == "holdings"
        assert state["in_place_holdings"] == []
        args, opts = jobs[0]
        detail = args[1]()
        assert detail is state["in_place_detail"]
        assert state["in_place_holdings"] == [state["in_place_item"]]
        args[2](detail)
        panel = state["view"]._master._page.detail
        # The direct-download-only Search renderer intentionally returns
        # before caching a holdings detail when no individual holding exists.
        # Verify the actual visible book and admitted action instead of
        # treating that optional internal cache as a publication receipt.
        assert any(
            isinstance(widget, Gtk.Label)
            and widget.get_text() == detail.title
            for widget in walk(panel)
        )
        assert state["view"]._master._page.selection.get_selected() == 0
        assert not state["exact_navigations"][1:]
        assert any(
            isinstance(widget, Adw.ActionRow)
            and widget.get_title() == "Affinité"
            for widget in walk(panel)
        ), "Recommendation reasons disappeared from exact Core detail"
        download = core_download_button()
        assert download.get_mapped() and download.get_sensitive()
        assert state["in_place_obtain"] == []
        report["checks"]["P1_4664_gtk_in_place_core_holdings_and_reasons"] = True
        report["checks"]["P1_4664_no_mutation_on_in_place_render"] = True
        capture("07-exact-in-place-core-actions")
        state["in_place_download"] = download
        assert download.grab_focus() is not False
        later(170, in_place_download_press)

    def in_place_download_press():
        report["p1_4664_phase"] = "download"
        button = state["in_place_download"]
        assert button.get_mapped() and button.has_focus()
        state["device"].key("Return", True)
        state["device"].key("Return", False)
        later(200, in_place_download_work)

    def in_place_download_work():
        report["p1_4664_phase"] = "obtain"
        jobs = state["in_place_jobs"]
        assert len(jobs) == 2 and jobs[1][1]["kind"] == "obtain"
        assert state["in_place_obtain"] == []  # Click schedules, no implicit POST
        args, _opts = jobs[1]
        result = args[1]()
        assert state["in_place_obtain"] == [state["in_place_item"]]
        args[2](result)
        assert state["in_place_activity"] == [
            "Commande d’obtention terminée"
        ]
        report["checks"]["P1_4664_gtk_real_core_download_handler"] = True
        # Redraw the same recommendation; an old direct-download button
        # is now detached. It must be inert, even if a GTK reference lives.
        view = state["view"]
        row = view._master._page.selection.get_selected_item()
        assert row is not None
        view._selected_row(row)
        state["in_place_download"].emit("clicked")
        assert len(jobs) == 2
        later(140, in_place_failed_holdings)

    def in_place_failed_holdings():
        report["p1_4664_phase"] = "unavailable"
        button = in_place_button()
        assert button.get_mapped()
        button.emit("clicked")
        jobs = state["in_place_jobs"]
        assert len(jobs) == 3 and jobs[2][1]["kind"] == "holdings"
        assert len(state["in_place_holdings"]) == 1
        error = RuntimeError("token=PRIVATE account=/home/private")
        jobs[2][0][3](error)
        panel = state["view"]._master._page.detail
        assert any(
            isinstance(widget, Adw.ActionRow)
            and widget.get_title() == "Disponibilités non actualisées"
            for widget in walk(panel)
        )
        assert core_download_button().get_mapped()
        assert any(
            isinstance(widget, Adw.ActionRow)
            and widget.get_title() == "Affinité"
            for widget in walk(panel)
        )
        assert state["toasts"][-1] == (
            "Disponibilités indisponibles pour cette notice."
        )
        assert not any("PRIVATE" in value for value in state["toasts"])
        assert len(state["in_place_obtain"]) == 1
        report["checks"]["P1_4664_gtk_failure_preserves_core_actions"] = True
        report["checks"]["P1_4664_no_provider_error_leak"] = True
        capture("08-exact-in-place-unverified-holdings")
        view = state["view"]
        view._master._page.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        jobs[2][0][2](state["in_place_detail"])
        jobs[2][0][3](error)
        assert view._master._page.selection.get_selected_item() is None
        assert not state["in_place_obtain"][1:]
        report["checks"]["P1_4664_gtk_stale_holdings_callback_inert"] = True
        state["plugin"].deactivate()
        report["status"] = "PASS"
        save()
        app.quit()

    def activate(_app):
        nonlocal cover_loader
        window = Adw.ApplicationWindow(application=app)
        window.set_default_size(args.width - 40, {1024: 720, 1366: 850, 1600: 930}[args.width])
        window._closed = False
        cover_loader = CoverLoader(max_workers=2)
        cover_loader.client.close()
        cover_loader.client = httpx.Client(
            transport=httpx.MockTransport(image_response), trust_env=False,
        )
        window.covers = cover_loader
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
        if cover_loader is not None:
            cover_loader.close()
        save()
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
