#!/usr/bin/env python3
"""UX #942: real GTK4 Search/Library detail, multivalued bibliographic rows.

Only the real Core GUI and local indexed rows are used. Online clicks are
observed at the UI action boundary and never reach remote providers.
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
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "environnements/openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from PIL import ImageGrab
from x11_input import X11Input
from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow

TITLE = "Camille, le détail UX"
METADATA = {
    "authors": ["Alice Exemple", "Bob Exemple"],
    "subjects": ["écologie", "nature"],
    "isbn": "9780306406157",
    "ean": "4006381333931",
    "publisher": "Éditions UX",
    "catalogue_metadata": {"publishers": ["Maison A", "Maison B"]},
    "access_token": "SECRET_NE_JAMAIS_AFFICHER",
}


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", required=True, type=int, choices=(1366, 1600))
    parser.add_argument("--component-contracts", action="store_true", help="P0 #1124/#1125: real widget lifecycle, summaries, overflow and stable selection")
    parser.add_argument("--short-component-actions", action="store_true", help="Reproduce the pre-existing boundary overflow case with eight shorter labels")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Xvfb/GDK_BACKEND=x11 requis")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)
    entry = {
        "catalog_entry_id": "ux942-book", "item_id": "ux942-book",
        "identity_id": "camille", "provider_id": "local",
        "title": TITLE, "author": "Alice Exemple", "local_state": "remote_catalog",
        "media_format": "epub", "metadata": METADATA,
    }
    core.storage.catalog_entry_put(entry)
    report = {
        "status": "RUNNING", "scenario": "book-detail-gtk",
        "git_sha": sha, "width": args.width, "backend": "GTK4/X11/XTEST",
        "steps": [], "online_calls": [], "checks": {},
    }
    started = monotonic()
    window = None
    device = None
    rc = [1]
    original_search_books = [None]
    original_known_search = core.library.search_known_catalog
    known_calls = []

    def count_local(*a, **kw):
        known_calls.append({"args": repr(a), "kwargs": repr(kw)})
        return original_known_search(*a, **kw)

    core.library.search_known_catalog = count_local

    def save():
        report["duration_seconds"] = round(monotonic() - started, 2)
        (out / "screen.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def capture(name):
        picture = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert picture.width == args.width, picture.size
        filename = out / f"{name}.png"
        picture.save(filename)
        shutil.copyfile(filename, out / "screen.png")
        report["steps"].append({
            "stage": name, "file": filename.name,
            "sha256": hashlib.sha256(filename.read_bytes()).hexdigest(),
            "route": window.view_stack.get_visible_child_name(),
        })

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        report["status"] = "FAIL"
        report["error"] = str(exc)
        if window:
            try:
                capture("failure")
            except Exception:
                pass
        save()
        app.quit()

    def later(ms, fn):
        def run():
            try:
                fn()
            except Exception as exc:
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, run)

    def panel(route):
        return window.search_page.detail if route == "search" else window.library_page.detail

    def detail_info(route):
        p = panel(route)
        expanded = [x for x in walk(p.content) if isinstance(x, Adw.ExpanderRow)
                    and x.get_title() == "Détail"]
        assert len(expanded) == 1, f"Expander Détail GTK absent ({route}): {len(expanded)}"
        rows = [
            x for x in walk(p.content)
            if isinstance(x, Adw.ActionRow) and x is not expanded[0]
            and x.get_title() in {"Titre", "Auteur", "Sujet", "ISBN déclaré",
                                 "EAN déclaré", "Identifiant déclaré", "Éditeur",
                                 "Catalogue · Éditeur"}
        ]
        return expanded[0], rows

    def read_rows(rows):
        return [(str(x.get_title()), str(x.get_subtitle())) for x in rows]

    def buttons_for(row):
        buttons = [x for x in walk(row) if isinstance(x, Gtk.Button)]
        return {str(x.get_tooltip_text() or ""): x for x in buttons}

    def search_initial():
        assert window.service.current_identity.identity_id == "camille"
        # La branche #954 précède #955. Utiliser l\x27API locale déjà stable pour
        # charger une vraie fiche GTK ; le clic loupe est intercepté séparément.
        from cifereca.gui.query_model import QueryState
        window.search_controls.set_state(
            QueryState(text="Camille", field="title", scope="all"), notify=False,
        )
        window.navigate("search")
        window.run_search(False)
        later(180, wait_search)

    def wait_search():
        if window.search_page.store.get_n_items() != 1 and monotonic() - started < 12:
            later(160, wait_search)
            return
        assert window.search_page.store.get_n_items() == 1, "Notice du catalogue connu absente"
        assert len(known_calls) == 1, f"Recherche locale non unique : {known_calls!r}"
        # Spy only *after* starting the local search but *before* building suffix buttons.
        original_search_books[0] = getattr(window, "search_books", None)
        def observe(query, *, field="title", online=False, identity_id=None):
            report["online_calls"].append({
                "query": query, "field": field, "online": online, "identity_id": identity_id,
            })
            # Never perform a remote search during an offline UX probe.
            return True
        window.search_books = observe
        window.search_page.selection.set_selected(0)
        later(280, search_detail)

    def search_detail():
        expander, rows = detail_info("search")
        assert not expander.get_expanded(), "Détail doit être replié initialement"
        assert report["online_calls"] == [], "Recherche déclenchée par affichage"
        # Les sous-titres des Adw.ActionRows repliées ne sont pas encore
        # matérialisés en GTK : tester les valeurs *après* ouverture.
        report["checks"]["initially_collapsed"] = True
        capture("00-search-detail-closed")
        expander.set_expanded(True)
        # Le volet droit est scrollable ; expander situé sous les groupes
        # Essentiel/Bibliothèque, hors écran avant le défilement.
        scroller = panel("search").body_scroll
        adjustment = scroller.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        later(400, expanded_search)

    def expanded_search():
        expander, rows = detail_info("search")
        assert expander.get_expanded()
        shown = read_rows(rows)
        report["search_rows"] = shown
        report["search_row_diagnostics"] = [
            {"title": str(row.get_title()), "subtitle": str(row.get_subtitle()),
             "tooltip": str(row.get_tooltip_text() or ""),
             "mapped": bool(row.get_mapped())}
            for row in rows
        ]
        capture("01-search-detail-expanded-gtk")
        for expected in (
            ("Auteur", "Alice Exemple"), ("Auteur", "Bob Exemple"),
            ("Sujet", "écologie"), ("Sujet", "nature"),
            ("ISBN déclaré", "9780306406157"),
            ("EAN déclaré", "4006381333931"),
            ("Catalogue · Éditeur", "Maison B"),
        ):
            assert expected in shown, f"Champ {expected!r} absent du véritable panneau GTK déplié : {shown!r}"
        assert not any("SECRET_NE_JAMAIS_AFFICHER" in v for _, v in shown)
        assert report["online_calls"] == [], "Recherche déclenchée par ouverture du détail"
        report["checks"]["search_metadata"] = True
        authors = [x for x in rows if x.get_title() == "Auteur"]
        assert len(authors) == 2
        report["author_copy_tooltips"] = [
            next((k for k in buttons_for(row) if k.startswith("Copier ")), "")
            for row in authors
        ]
        # Record an accessibility discrepancy, but do not turn the entire
        # functional E2E red on a pre-existing UX issue.
        report["duplicate_author_tooltips"] = (
            len(set(report["author_copy_tooltips"])) != len(authors)
        )
        subject = next(x for x in rows if x.get_title() == "Sujet" and x.get_subtitle() == "nature")
        copy = buttons_for(subject).get("Copier Sujet")
        assert copy is not None, "Copie individuelle de sujet absente"
        assert copy.get_mapped(), "Action Copier de la ligne sélectionnée non mappée"
        assert copy.grab_focus() is not False
        capture("01-search-detail-expanded")
        later(230, lambda: click_copy(copy))

    def click_copy(button):
        report["copy_pointer"] = device.click_widget(button)
        later(210, read_clipboard)

    def read_clipboard():
        clipboard = Gdk.Display.get_default().get_clipboard()
        def done(source, task):
            try:
                value = source.read_text_finish(task)
                assert value == "nature", f"Copie incorrecte : {value!r}"
                report["checks"]["copy_individual_subject"] = True
                later(120, search_isbn)
            except Exception as exc:
                fail(f"Clipboard: {type(exc).__name__}: {exc}")
        clipboard.read_text_async(None, done)

    def search_isbn():
        _, rows = detail_info("search")
        isbn = next(x for x in rows if x.get_title() == "ISBN déclaré")
        buttons = buttons_for(isbn)
        search = next((b for k, b in buttons.items() if k.startswith("Rechercher en ligne")), None)
        assert search is not None, "Loupe ISBN absente"
        assert search.grab_focus() is not False
        later(250, lambda: click_isbn(search))

    def click_isbn(button):
        report["isbn_pointer"] = device.click_widget(button)
        later(180, verify_isbn)

    def verify_isbn():
        assert report["online_calls"] == [{
            "query": "9780306406157", "field": "isbn",
            "online": True, "identity_id": "camille",
        }], f"Recherche ISBN typée incorrecte : {report['online_calls']!r}"
        report["checks"]["typed_isbn_click_only"] = True
        capture("02-search-isbn-action")
        # Switch route without remote collection. Reuse the actual Library
        # master/detail renderer with the same locally indexed book.
        window._library_entries_cache = [dict(entry, key="ux942-library")]
        window.navigate("library")
        later(260, library_select)

    def library_select():
        assert window.library_page.store.get_n_items() == 1
        window.library_page.selection.set_selected(0)
        later(270, verify_library)

    component = {}

    def verify_library():
        expander, rows = detail_info("library")
        shown = read_rows(rows)
        for expected in (
            ("Auteur", "Alice Exemple"), ("Auteur", "Bob Exemple"),
            ("Sujet", "nature"), ("ISBN déclaré", "9780306406157"),
        ):
            assert expected in shown, f"Bibliothèque n'utilise pas le détail partagé : {shown!r}"
        report["checks"]["library_metadata"] = True
        report["library_rows"] = shown
        report["library_expanded"] = expander.get_expanded()
        capture("03-library-shared-detail")
        assert len(report["online_calls"]) == 1
        begin_owned_book_action()

    owned_action = {}

    def begin_owned_book_action():
        """#1347: real Library renderer/TaskRunner and physical targeted click.

        Ownership/source eligibility uses real Core storage. The acquisition
        boundary is observed, never connected to a provider or DRM runtime;
        tests/test_owned_library_download.py verifies the engine/file boundary.
        """
        from types import SimpleNamespace as NS
        from cifereca.models import Account
        service = window.service
        owner = service.current_identity.identity_id
        assert owner == "camille"
        account = Account(display_name="Kobo fictif ciblé", unique_id="gtk-owned-account",
                          provider_id="kobo", credential_id="gtk-owned-credential", identity_id=owner)
        owned_action.update(accounts=list(service.accounts), sessions=dict(service._sessions),
                            obtain=service.obtain_owned_library_item, refresh=window.refresh_library,
                            recent=window.refresh_recent, calls=[], refreshed=[], recents=[])
        service.storage.provider_connection_put({"provider_id": "kobo", "credential_id": account.credential_id,
            "identity_id": owner, "label": "Kobo fictif ciblé"})
        service.storage.entitlement_put({"entitlement_id": "gtk-owned-right", "provider_id": "kobo",
            "credential_id": account.credential_id, "account_id": account.unique_id, "identity_id": owner,
            "item_id": "gtk-owned-product", "revision_id": "r1", "entitlement_type": "owned", "state": "active",
            "metadata": {"title": TITLE, "media_format": "epub"}})
        service.accounts = [*service.accounts, account]
        service._sessions[service._session_key("kobo", account.credential_id)] = NS(
            provider_id="kobo", credential_id=account.credential_id, connected=True,
            capabilities=NS(backup=True), accounts=[account])
        def observe(target):
            assert target.identity_id == owner and target.item_id == "gtk-owned-product"
            owned_action["calls"].append((target.identity_id, target.item_id))
            return NS(path=out / "synthetic-boundary-only.epub")
        service.obtain_owned_library_item = observe
        window.refresh_library = lambda *a, **kw: owned_action["refreshed"].append(a)
        window.refresh_recent = lambda *a, **kw: owned_action["recents"].append(a)
        possession = {"identity_id": owner, "provider_id": "kobo", "item_id": "gtk-owned-product",
                      "kind": "owned", "state": "active", "entitlement_id": "gtk-owned-right"}
        window._library_entries_cache = [dict(entry, key="gtk-owned-row", provider_id="kobo",
            item_id="gtk-owned-product", local=False, state="remote_owned", source="entitlement",
            entitlement_id="gtk-owned-right", possessions=[possession])]
        window._render_library()
        later(160, owned_book_select)

    def owned_book_select():
        assert window.library_page.store.get_n_items() == 1
        window.library_page.selection.set_selected(0)
        later(260, owned_book_action_visible)

    def owned_book_action_visible():
        buttons = [node for node in walk(panel("library"))
                   if isinstance(node, Gtk.Button) and getattr(node, "_cifereca_action_id", "") == "download-owned-kobo"]
        assert len(buttons) == 1, "Action individuelle du livre possédé absente"
        button = buttons[0]
        assert button.get_mapped() and button.get_sensitive()
        ok, bounds = button.compute_bounds(window)
        assert ok and bounds.get_width() > 0 and bounds.get_height() > 0
        assert 0 <= bounds.get_x() and bounds.get_x() + bounds.get_width() <= window.get_width()
        assert 0 <= bounds.get_y() and bounds.get_y() + bounds.get_height() <= window.get_height()
        assert not owned_action["calls"] and not owned_action["refreshed"]
        report["checks"]["P0_1347_owned_library_action_passive_until_physical_click"] = True
        capture("03a-owned-kobo-single-book-action")
        report.setdefault("pointer_actions", []).append(device.click_widget(button))
        owned_action["deadline"] = monotonic() + 6
        later(100, owned_book_action_completed)

    def owned_book_action_completed():
        if not owned_action["refreshed"]:
            assert monotonic() < owned_action["deadline"], "Action individuelle GTK non terminée"
            later(100, owned_book_action_completed)
            return
        assert owned_action["calls"] == [("camille", "gtk-owned-product")]
        assert len(owned_action["refreshed"]) == len(owned_action["recents"]) == 1
        assert len(report["online_calls"]) == 1, "Téléchargement individuel devenu une recherche distante"
        report["checks"]["P0_1347_owned_library_XTEST_exact_single_target"] = True
        report["owned_download_scope"] = "real GTK/TaskRunner + stored ownership; acquisition boundary observed, no live provider"
        capture("03b-owned-kobo-targeted-click-complete")
        service = window.service
        service.accounts = owned_action["accounts"]
        service._sessions = owned_action["sessions"]
        service.obtain_owned_library_item = owned_action["obtain"]
        window.refresh_library = owned_action["refresh"]
        window.refresh_recent = owned_action["recent"]
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM entitlements WHERE entitlement_id='gtk-owned-right'")
            db.execute("DELETE FROM provider_connections WHERE provider_id='kobo' AND credential_id='gtk-owned-credential'")
        window._library_entries_cache = [dict(entry, key="ux942-library")]
        window._render_library()
        window.library_page.selection.set_selected(0)
        if args.component_contracts:
            later(160, component_summaries)
        else:
            finish()

    def finish():
        later(80, audit_mixed_security_values)

    def reset_library_detail_for_security_audit():
        """Only clear the probe's panel view; no product code or remote service."""
        p = panel("library")
        child = p.content.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            p.content.remove(child)
            child = next_child
        return p

    def security_detail_expanders():
        return [
            widget for widget in walk(panel("library").content)
            if isinstance(widget, Adw.ExpanderRow) and widget.get_title() == "Détail"
        ]

    def audit_mixed_security_values():
        """Real GTK: the final sink must not count fields rejected as secrets."""
        assert report["online_calls"] and len(report["online_calls"]) == 1
        p = reset_library_detail_for_security_audit()
        p._group("Détail", [
            ("Auteur", "Alice Exemple"),
            ("Lien signé", "https://example.test/notice?token=do-not-display"),
            ("Auteur", "Bob Exemple"),
            ("Sujet", "nature"),
            ("Authentification", "Bearer must-never-display"),
        ])
        expanded = security_detail_expanders()
        assert len(expanded) == 1, "The three public rows require a visible GTK detail group"
        expanded[0].set_expanded(True)
        adj = p.body_scroll.get_vadjustment()
        adj.set_value(max(0, adj.get_upper() - adj.get_page_size()))
        later(260, audit_mixed_security_values_rendered)

    def audit_mixed_security_values_rendered():
        expanded = security_detail_expanders()
        assert len(expanded) == 1
        expander = expanded[0]
        rows = [
            widget for widget in walk(expander)
            if isinstance(widget, Adw.ActionRow) and widget.get_title() != "Détail"
        ]
        public = [(str(widget.get_title()), str(widget.get_subtitle())) for widget in rows]
        prohibited = (
            "do-not-display", "must-never-display",
            "Lien signé", "Authentification",
        )
        rendered_text = repr(public)
        report["security_detail_mixed_942"] = {
            "source_fields": 5,
            "allowed_fields": 3,
            "rendered_fields": len(rows),
            "announced_subtitle": str(expander.get_subtitle()),
            "public_rows": public,
            "private_visible": any(word in rendered_text for word in prohibited),
            "unprompted_online_calls": len(report["online_calls"]) != 1,
        }
        capture("04-library-detail-mixed-safety-gtk")
        assert not report["security_detail_mixed_942"]["private_visible"], (
            "Sensitive URL/token must never appear in GTK detail"
        )
        assert public == [
            ("Auteur", "Alice Exemple"), ("Auteur", "Bob Exemple"),
            ("Sujet", "nature"),
        ], f"Safe multivalued metadata differs from source: {public!r}"
        later(80, audit_all_private_values)

    def audit_all_private_values():
        """Real GTK: no empty expander should reveal an all-rejected section."""
        p = reset_library_detail_for_security_audit()
        p._group("Détail", [
            ("Lien signé", "https://example.test/notice?token=do-not-display"),
            ("Authentification", "Bearer must-never-display"),
        ])
        later(250, audit_all_private_values_rendered)

    def audit_all_private_values_rendered():
        expanded = security_detail_expanders()
        report["security_detail_all_private_942"] = {
            "source_fields": 2,
            "allowed_fields": 0,
            "expander_count": len(expanded),
            "announced_subtitles": [str(widget.get_subtitle()) for widget in expanded],
            "unprompted_online_calls": len(report["online_calls"]) != 1,
        }
        capture("05-library-detail-all-private-gtk")
        mixed = report["security_detail_mixed_942"]
        private = report["security_detail_all_private_942"]
        assert len(report["online_calls"]) == 1, "Detail creation triggered network search"
        assert mixed["rendered_fields"] == mixed["allowed_fields"]
        assert mixed["announced_subtitle"] == "3 champ(s) bibliographiques", (
            "The GTK detail announced raw fields instead of visible safe rows: "
            f"{mixed['announced_subtitle']!r}"
        )
        assert private["expander_count"] == 0, (
            "All-rejected private metadata created a misleading empty detail"
        )
        report["checks"]["security_detail_visible_counter_942"] = True
        report["checks"]["security_detail_all_private_hidden_942"] = True
        begin_stale_public_detail()

    def begin_stale_public_detail():
        from cifereca.models import SearchResult
        from cifereca.gui.query_model import QueryState
        from datetime import datetime, timezone
        service = window.service
        def forbidden(*_args, **_kwargs):
            raise AssertionError("Stale public detail requested download, holdings or plugin decoration")
        # Assert the final GTK detail boundary independently of the Core's own
        # refusal: no capability lookup/decorator/holdings may be requested.
        service.can_direct_download = forbidden
        service.holdings = forbidden
        panel("search")._decorate = forbidden
        window.navigate("search")
        window.search_controls.set_state(QueryState(text="Camille", field="title"), notify=False)
        item = SearchResult(
            id="known-stale:fixture", record_id="fixture", docbase="CATALOG",
            medium="digital", title=TITLE + " connu", author="Autrice fictive",
            type="Livre numérique", format="epub", publisher=None,
            published_at=None, description="Notice publique déjà collectée.",
            thumbnail_url=None, friendly_url=None, scenario="KNOWN_CATALOG_STALE",
            source_provider="gallica", source_credential="known-public",
            source_collection="catalog", owned=False, downloadable=False,
            metadata={"search_provenance": "known_catalog_stale",
                      "mirrored_at": int(datetime(2026, 10, 16, 12, tzinfo=timezone.utc).timestamp()),
                      "access_token": "STALE_SECRET_NEVER_DISPLAY"},
        )
        window._search_local_objects = {}
        window._remote_search_results = [item]
        window._populate_search([item], announce=False)
        assert window.search_page.store.get_n_items() == 1
        window.search_page.selection.set_selected(0)
        later(280, stale_public_detail_rendered)

    def stale_public_detail_rendered():
        p = panel("search")
        fields = [(row.get_title(), row.get_subtitle()) for row in walk(p.content)
                  if isinstance(row, Adw.ActionRow)]
        assert ("Recherche en ligne", "Indisponible") in fields
        texts = [node.get_text() for node in walk(p.content) if isinstance(node, Gtk.Label)]
        assert "Résultat connu · hors ligne" in texts
        assert not any("STALE_SECRET_NEVER_DISPLAY" in text for text in texts)
        assert not any(
            isinstance(node, Gtk.Button)
            and str(getattr(node, "_cifereca_action_label", "")).startswith(("Télécharger", "Sauvegarder"))
            for node in walk(p)
        )
        assert len(report["online_calls"]) == 1
        report["checks"]["P1_1916_stale_public_detail_readonly_without_acquisition"] = True
        capture("06-stale-public-detail-readonly")
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

    def component_summaries():
        p = panel("library")
        p.empty("Sans couverture", "Fixture locale sans URL")
        p._show_description("")
        assert not any(isinstance(w, Adw.PreferencesGroup) and w.get_title() == "Résumé" for w in walk(p.content))
        p._show_description("Résumé long de la fixture. " * 80)
        toggle = next(w for w in walk(p.content) if isinstance(w, Gtk.Button) and w.get_label() == "Lire la suite")
        component["summary_toggle"] = toggle
        later(200, lambda: component_expand(toggle))

    def component_expand(toggle):
        assert toggle.get_mapped()
        report["summary_pointer"] = device.click_widget(toggle)
        later(180, component_collapse)

    def component_collapse():
        toggle = component["summary_toggle"]
        assert toggle.get_label() == "Réduire"
        labels = [w for w in walk(panel("library").content) if isinstance(w, Gtk.Label) and str(w.get_text()).startswith("Résumé long")]
        assert labels and labels[0].get_lines() == -1
        adjustment = panel("library").body_scroll.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        later(180, component_click_collapse)

    def component_click_collapse():
        toggle = component["summary_toggle"]
        assert toggle.get_mapped(), "Résumé: bouton Réduire non mappé"
        report["collapse_pointer"] = device.click_widget(toggle)
        later(180, component_overflow)

    def component_overflow():
        from cifereca.gui.main_window import action_button
        toggle = component["summary_toggle"]
        assert toggle.get_label() == "Lire la suite", "Résumé: clic Réduire sans effet"
        report["checks"]["empty_and_long_summary"] = True
        capture("04-summary-collapsed")
        p = panel("library")
        p.empty("Actions de la fixture", "Sans couverture distante")
        component["action_calls"] = []
        buttons = []
        for i in range(8):
            label = f"Action longue {i}" if args.short_component_actions else f"Action de la fixture depuis le contexte du document {i}"
            button = action_button(label, "view-more-symbolic")
            button.connect("clicked", lambda _button, index=i: component["action_calls"].append(index))
            buttons.append(button)
        report["component_action_labels"] = [button._cifereca_action_label for button in buttons]
        p.add_actions(buttons)
        later(250, component_open_overflow)

    def component_open_overflow():
        bar = panel("library").action_scroll
        assert bar.overflow_button.get_visible() and bar.overflow_button.get_mapped()
        report["overflow_pointer"] = device.click_widget(bar.overflow_button)
        later(200, component_click_hidden)

    def component_click_hidden():
        bar = panel("library").action_scroll
        assert bar.overflow_popover.get_mapped(), "Overflow: menu non ouvert par le clic XTEST"
        button = next(w for w in walk(bar.overflow_box) if isinstance(w, Gtk.Button))
        component["expected_action"] = int(button._cifereca_action_label.rsplit(" ", 1)[1])
        report["hidden_action_pointer"] = device.click_widget(button)
        later(160, component_lists)

    def component_lists():
        assert component["action_calls"] == [component["expected_action"]]
        panel("library").action_scroll.overflow_popover.popdown()
        report["checks"]["overflow_action"] = True
        from cifereca.gui.main_window import BookListItem
        page = window.library_page
        page.clear_rows()
        assert page.store.get_n_items() == 0 and page.selection.get_selected_item() is None
        page.show_rows([BookListItem("one", "Un document")])
        assert page.store.get_n_items() == 1 and page.selection.get_selected_item() is None
        # No business callback may interpret these deliberately synthetic keys.
        callbacks = page._selected_callbacks
        page._selected_callbacks = []
        try:
            assert page.select_key("one")
            page.append_rows([BookListItem("two", "Deux documents"), BookListItem("three", "Trois documents")])
            assert page.selection.get_selected_item().key == "one"
            page.show_rows([page.store.get_item(2), page.store.get_item(0)])
            assert page.selection.get_selected_item().key == "one"
            page.show_rows([page.store.get_item(0)])
            assert page.selection.get_selected_item() is None
            assert not page.select_key("missing")
        finally:
            page._selected_callbacks = callbacks
        report["checks"]["zero_one_many_stable_selection"] = True
        # Real key input must clear selection and return focus to the list.
        page._selected_callbacks = []
        component["callbacks"] = callbacks
        assert page.select_key("three")
        page.listview.grab_focus()
        device.key("Escape", True); device.key("Escape", False)
        later(180, component_identity)

    def component_identity():
        page = window.library_page
        assert page.selection.get_selected_item() is None
        page._selected_callbacks = component.pop("callbacks")
        report["checks"]["keyboard_escape"] = True
        # Model a pending metadata observer; the real identity transition must
        # cancel it, clear old rows and leave no stale detail in the real widget.
        class Observer:
            cancelled = False
            def cancel(self): self.cancelled = True
        observer = Observer()
        page.detail._meta_request = observer
        original_runner = window.runner
        submitted = []
        class Queue:
            def submit(self, *args, **kwargs): submitted.append((args, kwargs))
        window.runner = Queue()
        try:
            window._activate_identity("alex")
            assert observer.cancelled and page.detail._meta_request is None
            assert page.store.get_n_items() == 0
            assert window.service.current_identity.identity_id == "alex"
            assert submitted
        finally:
            window.runner = original_runner
        report["checks"]["identity_cancels_pending_observer"] = True
        report["pending_observer_scope"] = "controlled observer/scheduler; real MainWindow identity transition and GTK widgets"
        later(220, component_identity_rendered)

    def component_identity_rendered():
        assert window.service.current_identity.identity_id == "alex", "Identité changée de nouveau avant rendu"
        assert window.library_page.store.get_n_items() == 0, "Anciennes lignes restaurées avant rendu"
        report["identity_rendered"] = {
            "identity": window.service.current_identity.identity_id,
            "rows": window.library_page.store.get_n_items(),
            "selected": window.library_page.selection.get_selected_item() is not None,
        }
        capture("05-new-identity-cleared-detail")
        finish()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.BookDetailUXE2E")
        def do_activate(self):
            nonlocal window, device
            window = MainWindow(self, core.config, core.library, core.diagnostics)
            window.set_default_size(args.width - 40, 880 if args.width == 1600 else 780)
            window.present()
            device = X11Input()
            later(450, search_initial)
        def do_shutdown(self):
            if window is not None:
                if original_search_books[0] is not None:
                    window.search_books = original_search_books[0]
                elif "search_books" in vars(window):
                    delattr(window, "search_books")
            core.library.search_known_catalog = original_known_search
            if device is not None:
                device.close()
            core.close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(40, lambda: (fail("GTK détail livre : timeout 40s"), False)[1] if rc[0] else False)
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        report.update(status="FAIL", error="GTK arrêté sans rapport")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
