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
    parser.add_argument("--width", required=True, type=int, choices=(1024, 1366))
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
        # Essentiel/Bibliothèque, hors écran dans la première capture 1024px.
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
        report["status"] = "PASS"
        save()
        rc[0] = 0
        app.quit()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.BookDetailUXE2E")
        def do_activate(self):
            nonlocal window, device
            window = MainWindow(self, core.config, core.library, core.diagnostics)
            window.set_default_size(args.width - 40, 780)
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
