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
import re
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
    "public_product_metadata": {
        "brand": "Marque commerciale", "pagination_label": "228 pages",
        "selected_format_label": "PDF sans DRM",
        "rights": "SECRET_NE_JAMAIS_AFFICHER",
    },
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
    parser.add_argument("--width", required=True, type=int, choices=(1300, 1366, 1600))
    parser.add_argument("--geometry-1300", action="store_true", help="UX #611: measure native X11 position, shell and both split views at exact 1300×768")
    parser.add_argument("--component-contracts", action="store_true", help="P0 #1124/#1125: real widget lifecycle, summaries, overflow and stable selection")
    parser.add_argument("--short-component-actions", action="store_true", help="Reproduce the pre-existing boundary overflow case with eight shorter labels")
    args = parser.parse_args()
    if args.geometry_1300 and args.width != 1300:
        parser.error("--geometry-1300 requires --width 1300")
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
        if args.geometry_1300:
            assert picture.height == 768, picture.size
        filename = out / f"{name}.png"
        picture.save(filename)
        shutil.copyfile(filename, out / "screen.png")
        report["steps"].append({
            "stage": name, "file": filename.name,
            "sha256": hashlib.sha256(filename.read_bytes()).hexdigest(),
            "route": window.view_stack.get_visible_child_name(),
        })

    def geometry_1300(route):
        """Collect numeric-only native geometry beside each private screenshot.

        The X11 window name is constant fixture data. Never write a raw
        xwininfo dump or a title/account/pointer to the report.
        """
        assert args.geometry_1300 and route in {"search", "library"}
        surface = window.get_surface()
        assert surface is not None
        # Native X11 position/size is not a GTK requested/default size.
        report["ux611_probe_stage"] = "xwininfo_lookup"
        native_raw = subprocess.check_output(
            ["xwininfo", "-name", "Cifereca geometry E2E 1300"],
            text=True, timeout=3,
        )
        def native_int(key):
            found = re.search(r"^\s*" + re.escape(key) + r":\s*(-?\d+)\s*$",
                              native_raw, re.MULTILINE)
            assert found, ("Missing native X11 geometry field", key)
            return int(found.group(1))
        report["ux611_probe_stage"] = "xwininfo_parse"
        native = {
            "x": native_int("Absolute upper-left X"),
            "y": native_int("Absolute upper-left Y"),
            "width": native_int("Width"), "height": native_int("Height"),
        }
        report["ux611_probe_stage"] = "monitor_and_paned"
        monitor = Gdk.Display.get_default().get_monitors().get_item(0)
        assert monitor is not None
        monitor_rect = monitor.get_geometry()
        page = window.search_page if route == "search" else window.library_page
        pane = page.paned
        sample = {
            "display": {"width": monitor_rect.width, "height": monitor_rect.height},
            "requested": {"width": 1300, "height": 768},
            "native": native,
            "gtk_toplevel": {"width": window.get_width(), "height": window.get_height()},
            "gdk_surface": {"width": surface.get_width(), "height": surface.get_height()},
            "shell": {"width": window.shell_root.get_width(), "height": window.shell_root.get_height()},
            "shell_after_sidebar": {"width": window.view_stack.get_width(),
                                    "height": window.view_stack.get_height()},
            "page": {"width": page.get_width(), "height": page.get_height()},
            "sidebar": {"visible": bool(window.sidebar.get_visible()),
                        "mapped": bool(window.sidebar.get_mapped()),
                        "width": window.sidebar.get_width()},
            "scale_factor": int(window.get_scale_factor()),
            "paned": {
                "maximum": int(pane.get_property("max-position")),
                "position": int(pane.get_position()),
                "master_width": int(pane.get_start_child().get_width()),
                "detail_width": int(pane.get_end_child().get_width()),
            },
            "book_selected": page.selection.get_selected_item() is not None,
        }
        report.setdefault("native_1300_geometry", {})[route] = sample
        report["ux611_probe_stage"] = "strict_native_geometry"
        assert sample["display"] == {"width": 1300, "height": 768}, sample["display"]
        # Client-side decorations reserve a GTK content inset (5px per side
        # with Adwaita on the CI X11 runtime). GDK surface and native X11 are
        # the actual outer window, *not* Gtk.Window's content allocation.
        for geom in (native, sample["gdk_surface"]):
            assert (geom["width"], geom["height"]) == (1300, 768), geom
        inset_width = native["width"] - sample["gtk_toplevel"]["width"]
        inset_height = native["height"] - sample["gtk_toplevel"]["height"]
        sample["gtk_content_insets"] = {
            "horizontal_total": inset_width, "vertical_total": inset_height,
        }
        assert 0 <= inset_width <= 32 and 0 <= inset_height <= 32, (
            "Native outer window and GTK content differ beyond decorations",
            inset_width, inset_height,
        )
        assert 0 < sample["shell_after_sidebar"]["width"] <= 1300
        assert sample["page"]["width"] <= sample["shell_after_sidebar"]["width"]
        report["ux611_probe_stage"] = "paned_allocations"
        assert sample["paned"]["maximum"] > 0
        assert sample["paned"]["master_width"] > 0
        assert sample["paned"]["detail_width"] > 0
        assert sample["book_selected"]
        report["ux611_probe_stage"] = "checked"
        report["checks"]["P2_611_native_1300_" + route] = True

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
                                 "Catalogue · Éditeur", "Fiche publique · Marque",
                                 "Fiche publique · Pagination déclarée (support non précisé)",
                                 "Fiche publique · Format commercial sélectionné"}
        ]
        return expanded[0], rows

    def read_rows(rows):
        return [(str(x.get_title()), str(x.get_subtitle())) for x in rows]

    def buttons_for(row):
        buttons = [x for x in walk(row) if isinstance(x, Gtk.Button)]
        return {str(x.get_tooltip_text() or ""): x for x in buttons}

    def search_initial():
        if args.geometry_1300:
            report["ux611_probe_stage"] = "search_ready"
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
        if args.geometry_1300:
            report["ux611_probe_stage"] = "selected_search"
            geometry_1300("search")
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
            ("Fiche publique · Marque", "Marque commerciale"),
            ("Fiche publique · Pagination déclarée (support non précisé)", "228 pages"),
            ("Fiche publique · Format commercial sélectionné", "PDF sans DRM"),
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
        if args.geometry_1300:
            report["ux611_probe_stage"] = "selected_library"
            geometry_1300("library")
        expander, rows = detail_info("library")
        shown = read_rows(rows)
        for expected in (
            ("Auteur", "Alice Exemple"), ("Auteur", "Bob Exemple"),
            ("Sujet", "nature"), ("ISBN déclaré", "9780306406157"),
            ("Fiche publique · Format commercial sélectionné", "PDF sans DRM"),
        ):
            assert expected in shown, f"Bibliothèque n'utilise pas le détail partagé : {shown!r}"
        report["checks"]["library_metadata"] = True
        report["library_rows"] = shown
        report["library_expanded"] = expander.get_expanded()
        capture("03-library-shared-detail")
        assert len(report["online_calls"]) == 1
        begin_owned_book_action()

    owned_action = {}

    def begin_owned_book_action(provider="kobo"):
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
        label = "Numilog" if provider == "numilog" else "Kobo"
        product = "gtk-owned-product"
        if provider == "numilog":
            scope = hashlib.sha256(b"gtk-owned-credential").hexdigest()[:20]
            product = "numilog:" + scope + ":fictional:" + "a"*16
        account = Account(display_name=label + " fictif ciblé", unique_id="gtk-owned-account",
                          provider_id=provider, credential_id="gtk-owned-credential", identity_id=owner)
        owned_action.update(accounts=list(service.accounts), sessions=dict(service._sessions),
                            obtain=service.obtain_owned_library_item, refresh=window.refresh_library,
                            recent=window.refresh_recent, calls=[], refreshed=[], recents=[],
                            provider=provider, product=product, resume=False)
        service.storage.provider_connection_put({"provider_id": provider, "credential_id": account.credential_id,
            "identity_id": owner, "label": label + " fictif ciblé"})
        service.storage.entitlement_put({"entitlement_id": "gtk-owned-right", "provider_id": provider,
            "credential_id": account.credential_id, "account_id": account.unique_id, "identity_id": owner,
            "item_id": product, "revision_id": "r1", "entitlement_type": "owned", "state": "active",
            "metadata": {"title": TITLE, **({"source":"numilog.authenticated_library"}
                         if provider == "numilog" else {"media_format":"epub"})}})
        service.accounts = [*service.accounts, account]
        service._sessions[service._session_key(provider, account.credential_id)] = NS(
            provider_id=provider, credential_id=account.credential_id, connected=True,
            capabilities=NS(backup=True, personal_library=True, direct_download=True), accounts=[account])
        def observe(target):
            assert target.identity_id == owner and target.item_id == product
            owned_action["calls"].append((target.identity_id, target.item_id))
            return NS(path=out / "synthetic-boundary-only.epub")
        service.obtain_owned_library_item = observe
        window.refresh_library = lambda *a, **kw: owned_action["refreshed"].append(a)
        window.refresh_recent = lambda *a, **kw: owned_action["recents"].append(a)
        possession = {"identity_id": owner, "provider_id": provider, "item_id": product,
                      "kind": "owned", "state": "active", "entitlement_id": "gtk-owned-right"}
        window._library_entries_cache = [dict(entry, key="gtk-owned-row-" + provider, provider_id=provider,
            item_id=product, local=False, state="remote_owned", source="entitlement",
            entitlement_id="gtk-owned-right", possessions=[possession])]
        # Different purchases need different canonical GTK row keys; reusing
        # a key would intentionally exercise the progressive append fast path
        # and leave the preceding provider's row materialized.
        assert service.owned_library_download_target(window._library_entries_cache[0]) is not None
        window._render_library()
        later(160, owned_book_select)

    def begin_numilog_index_resume():
        """Real receipt -> GTK/XTEST -> worker -> offline Core index/commit."""
        from types import SimpleNamespace as NS
        import zipfile
        from cifereca.core.numilog_local_preview import _identity_root
        from cifereca.core.numilog_materialization_checkpoint import record
        from cifereca.locking import InterProcessLock
        from cifereca.paths import acquisition_lock_file
        service = window.service
        owner = service.current_identity.identity_id
        root = _identity_root(service, owner)[0] / 'numilog'
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'gtk-receipt-fictional.epub'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('mimetype', 'application/epub+zip')
            archive.writestr('META-INF/container.xml', '<container/>')
            archive.writestr('content.xhtml', '<html><body>Fictional receipt</body></html>')
        item = NS(record_id=owned_action['product'], source_credential='gtk-owned-credential')
        with InterProcessLock(acquisition_lock_file()):
            aid = record(service, item, path, root, owner, 'gtk-owned-account', validate=lambda:None)
        owned_action.update(resume=True, calls=[], refreshed=[], recents=[], receipt=aid, path=path)
        # Resume must work without an online account or provider session.
        service.accounts = []
        service._sessions = {}
        def resume_exact(target):
            assert target.operation == 'resume_index' and target.acquisition_id == aid
            owned_action['calls'].append((target.identity_id, target.item_id))
            return owned_action['obtain'](target)
        service.obtain_owned_library_item = resume_exact
        row = window._library_entries_cache[0]
        window._library_entries_cache = [dict(row, key='gtk-numilog-resume-receipt')]
        assert service.owned_library_download_target(window._library_entries_cache[0]).operation == 'resume_index'
        window._render_library()
        later(160, owned_book_select)

    def owned_book_select():
        assert window.library_page.store.get_n_items() == 1
        window.library_page.selection.set_selected(0)
        later(260, owned_book_action_visible)

    def owned_book_action_visible():
        buttons = [node for node in walk(panel("library"))
                   if isinstance(node, Gtk.Button) and getattr(node, "_cifereca_action_id", "") == ("resume-index-numilog" if owned_action["resume"] else "download-owned-" + owned_action["provider"])]
        assert len(buttons) == 1, "Action individuelle du livre possédé absente"
        button = buttons[0]
        assert button.get_mapped() and button.get_sensitive()
        ok, bounds = button.compute_bounds(window)
        assert ok and bounds.get_width() > 0 and bounds.get_height() > 0
        assert 0 <= bounds.get_x() and bounds.get_x() + bounds.get_width() <= window.get_width()
        assert 0 <= bounds.get_y() and bounds.get_y() + bounds.get_height() <= window.get_height()
        assert not owned_action["calls"] and not owned_action["refreshed"]
        report["checks"]["P0_1905_numilog_resume_passive_until_physical_click" if owned_action["resume"] else "P0_1347_owned_library_action_passive_until_physical_click" if owned_action["provider"] == "kobo" else "P0_2024_owned_numilog_passive_until_physical_click"] = True
        capture("03a-owned-" + owned_action["provider"] + ("-resume-index-action" if owned_action["resume"] else "-single-book-action"))
        report.setdefault("pointer_actions", []).append(device.click_widget(button))
        owned_action["deadline"] = monotonic() + 6
        later(100, owned_book_action_completed)

    def owned_book_action_completed():
        if not owned_action["refreshed"]:
            assert monotonic() < owned_action["deadline"], "Action individuelle GTK non terminée"
            later(100, owned_book_action_completed)
            return
        assert owned_action["calls"] == [("camille", owned_action["product"])]
        assert len(owned_action["refreshed"]) == len(owned_action["recents"]) == 1
        assert len(report["online_calls"]) == 1, "Téléchargement individuel devenu une recherche distante"
        if owned_action['resume']:
            receipt = window.service.storage.acquisition_get(owned_action['receipt'])
            assert receipt['status'] == 'complete' and receipt['metadata']['phase'] == 'indexed'
            placement = window.service.storage.placement_for_path(owned_action['path'])
            assert placement['identity_id'] == 'camille' and placement['source_item_id'] == owned_action['product']
            assert not window.service.accounts and not window.service._sessions
            report['checks']['P0_1905_numilog_resume_XTEST_real_offline_index_complete'] = True
            report['numilog_resume_scope'] = 'synthetic delivered file; real receipt/GTK/XTEST/TaskRunner/index/SQLite, no provider session'
        else:
            report["checks"]["P0_1347_owned_library_XTEST_exact_single_target" if owned_action["provider"] == "kobo" else "P0_2024_owned_numilog_XTEST_exact_single_target"] = True
        report["owned_download_scope"] = "real GTK/TaskRunner + stored ownership; acquisition boundary observed, no live provider"
        capture("03b-owned-" + owned_action["provider"] + ("-resume-index-complete" if owned_action["resume"] else "-targeted-click-complete"))
        if owned_action['provider'] == 'numilog' and not owned_action['resume']:
            later(160, begin_numilog_index_resume)
            return
        service = window.service
        service.accounts = owned_action["accounts"]
        service._sessions = owned_action["sessions"]
        service.obtain_owned_library_item = owned_action["obtain"]
        window.refresh_library = owned_action["refresh"]
        window.refresh_recent = owned_action["recent"]
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM entitlements WHERE entitlement_id='gtk-owned-right'")
            db.execute("DELETE FROM provider_connections WHERE provider_id=? AND credential_id='gtk-owned-credential'", (owned_action["provider"],))
        if owned_action["provider"] == "kobo":
            later(160, lambda: begin_owned_book_action("numilog"))
            return
        window._library_entries_cache = [dict(entry, key="ux942-library")]
        window._render_library()
        window.library_page.selection.set_selected(0)
        # Every Core book-detail E2E width executes #1018's real GTK
        # allocation/overflow checks, not only the optional component suite.
        later(160, component_summaries)

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
        # #1018: a >700-character description may occupy only one *visual*
        # line. Combining grapheme joiners are invisible but retained by the
        # description sanitizer; old length heuristics displayed a fake button.
        p._show_description("Résumé compact " + "\u034f" * 710)
        later(200, component_summary_short)

    def component_summary_short():
        p = panel("library")
        groups = [w for w in walk(p.content)
                  if isinstance(w, Adw.PreferencesGroup) and w.get_title() == "Résumé"]
        assert len(groups) == 1
        label = next(w for w in walk(groups[0]) if isinstance(w, Gtk.Label)
                     and w.get_text().startswith("Résumé compact"))
        toggle = next(w for w in walk(groups[0]) if isinstance(w, Gtk.Button)
                      and w.get_label() == "Lire la suite")
        assert label.get_width() > 0, "Résumé non alloué"
        assert label.get_lines() == -1, "Texte intégral artificiellement tronqué"
        assert not toggle.get_visible() and not toggle.get_mapped(), (
            "Bouton trompeur alors que le résumé est intégralement visible"
        )
        report["checks"]["P2_1018_long_text_without_visual_overflow_no_toggle"] = True

        # Six explicit visual lines are *not* overflow, including a newline.
        p.empty("Résumé six lignes", "Contrat d'overflow")
        p._show_description("Une\nDeux\nTrois\nQuatre\nCinq\nSix")
        later(200, component_summary_six_lines)

    def component_summary_six_lines():
        p = panel("library")
        toggle = next(w for w in walk(p.content) if isinstance(w, Gtk.Button)
                      and w.get_label() == "Lire la suite")
        assert not toggle.get_visible()
        report["checks"]["P2_1018_six_explicit_lines_no_toggle"] = True

        p.empty("Résumé sept lignes", "Contrat d'overflow")
        p._show_description("Une\nDeux\nTrois\nQuatre\nCinq\nSix\nSept")
        later(200, component_summary_seven_lines)

    def component_summary_seven_lines():
        p = panel("library")
        toggle = next(w for w in walk(p.content) if isinstance(w, Gtk.Button)
                      and w.get_label() == "Lire la suite")
        assert toggle.get_visible() and toggle.get_mapped(), (
            "Sept lignes sans possibilité de déplier le résumé"
        )
        report["checks"]["P2_1018_seven_explicit_lines_toggle"] = True
        # #1916: exercise the real GTK count label without any provider call.
        # Visible text is made solely of closed Core status counts, even if a
        # provider key resembles a token/URL, and old identity state vanishes.
        state = window.search_controls.state()
        identity = getattr(window.service, "current_identity", None)
        identity_id = getattr(identity, "identity_id", None)
        prior_mode = window._search_mode
        prior_context = getattr(window, "_online_source_context", None)
        prior_statuses = getattr(window, "_online_source_statuses", None)
        try:
            window._search_mode = "global"
            window._online_source_context = (
                state.text, state.field, state.scope, identity_id,
            )
            window._online_source_statuses = {
                "https://private.invalid/?token=HIDDEN_1916": "known_catalog_stale",
                "provider.public": "unavailable",
            }
            window._update_search_count(0)
            actual = window.search_page.count_label.get_label()
            assert "secours local" in actual and "indisponible" in actual
            assert "HIDDEN_1916" not in actual and "private.invalid" not in actual
            window._online_source_context = (
                state.text, state.field, state.scope, "outdated-identity",
            )
            window._update_search_count(0)
            assert "secours local" not in window.search_page.count_label.get_label()
            report["checks"]["P1_1916_search_status_closed_and_identity_scoped"] = True
        finally:
            window._search_mode = prior_mode
            window._online_source_context = prior_context
            window._online_source_statuses = prior_statuses or {}
            window._update_search_count(window.search_page.store.get_n_items())
        if not args.component_contracts:
            # #908: the same Core E2E checks a real GTK 36-module inventory
            # at 1024/1366 pixels without touching installed plugins.
            from plugin_manager_scroll_contract import verify_plugin_scroll
            verify_plugin_scroll(
                window, later, finish,
                width=1024 if args.width == 1366 else 1366,
            )
            return

        p.empty("Résumé réellement long", "Contrat d'overflow")
        p._show_description("Résumé long de la fixture. " * 80)
        toggle = next(w for w in walk(p.content) if isinstance(w, Gtk.Button)
                      and w.get_label() == "Lire la suite")
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
            if args.geometry_1300:
                window.set_title("Cifereca geometry E2E 1300")
                window.set_default_size(1300, 768)
            else:
                window.set_default_size(args.width - 40, 880 if args.width == 1600 else 780)
            window.present()
            if args.geometry_1300:
                report["ux611_probe_stage"] = "probe_start"
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
