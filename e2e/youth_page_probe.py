#!/usr/bin/env python3
"""P1 #758: real Jeunesse package/page on the GTK4 window, driven under Xvfb.

The actual youth-catalog package is installed into the isolated Core plugin
store. Only the catalog's local cache is seeded; no provider is simulated as
available and no remote collection is triggered by opening or filtering.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import zipfile
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "environnements" / "openai-chat-debian"))
sys.path.insert(0, str(ROOT / "simulation"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from PIL import ImageGrab
from x11_input import X11Input
from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow


def install_real_youth(core: CiferecaCore, work: Path, *, with_booktrust: bool = False) -> None:
    packages = [
        ("youth-catalog", ("cifereca-plugin.json", "youth_catalog.py", "youth_page.py")),
    ]
    if with_booktrust:
        packages.append(("youth-source-booktrust", ("cifereca-plugin.json", "booktrust_youth.py")))
    for name, files in packages:
        source = ROOT / "src/plugins" / name
        archive = work / f"{name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for filename in files:
                bundle.write(source / filename, filename)
        core.plugins.install(archive)
    statuses = {status.descriptor.id: status for status in core.plugins.statuses()}
    plugins = ("catalog.youth", "ui.page.youth")
    if with_booktrust:
        plugins += ("youth.source.booktrust",)
    for plugin_id in plugins:
        assert statuses[plugin_id].state in {"ready", "degraded"}, (
            f"{plugin_id} not activated: {statuses[plugin_id]}"
        )


def records():
    def book(key, title, age=None, *, kind="recommended", hi=None, isbn=""):
        return {
            "record_id": key, "source_item_id": key, "title": title,
            "authors": ["Autrice UX"], "isbn": isbn,
            "language": "fr", "formats": ["epub"], "book_type": "fiction",
            "age_ranges": ([] if age is None else [{
                "age_min": age, "age_max": hi, "age_kind": kind,
                "source_label": f"{age}–{hi} ans" if hi is not None else f"{age}+",
                "source_id": "booktrust", "confidence": 0.8,
            }]),
        }
    return [
        book("zero", "Petit nuage", 0, hi=6),
        book("interest-eight", "Forest aventure", 8, hi=12, kind="interest", isbn="9780306406157"),
        book("reading-ten", "Lecture autonome", 10, hi=12, kind="reading"),
        book("seventeen", "Grand roman", 17),
        book("inferred", "Estimation prudente", 8, hi=12, kind="inferred"),
        book("unknown", "Age inconnu"),
    ], [book("alex-only", "Livre prive Alex", 8, hi=12)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", required=True, type=int, choices=(1024, 1366))
    parser.add_argument("--booktrust", action="store_true", help="Use the real installed BookTrust source with offline HTTP fixtures")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Real GTK4/X11 display required")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    result = {
        "scenario": "youth-gtk", "git_sha": sha,
        "backend": "GTK4/X11/Xvfb", "screen_width": args.width,
        "real_package": "src/plugins/youth-catalog",
        "booktrust_source_installed": args.booktrust,
        "steps": [], "status": "RUNNING",
    }
    start = monotonic()
    rc = [1]
    window = None
    device = None
    core = CiferecaCore.open(
        no_plugins=False,
        force_plugins=(
            ("catalog.youth", "ui.page.youth", "youth.source.booktrust")
            if args.booktrust else ("catalog.youth", "ui.page.youth")
        ),
        activate_plugins=False,
    )
    work = tempfile.TemporaryDirectory(prefix="youth-gtk-package-")
    install_real_youth(core, Path(work.name), with_booktrust=args.booktrust)
    catalog = core.plugins.service("catalog.youth")
    page = core.plugins.service("ui.page.youth")
    assert catalog is not None and page is not None

    requests = []
    fault = {"unavailable": False}
    if args.booktrust:
        import httpx
        from youth_sources.pseudo_youth_sources_service import booktrust_listing

        booktrust = core.plugins.service("youth.source.booktrust")
        assert booktrust is not None, "Installed BookTrust source not available"
        # Only transport responses are simulated; the installed provider
        # still validates canonical BookTrust URLs and parses real HTML.
        def respond(request):
            assert request.url.host == "www.booktrust.org.uk", request.url
            requests.append(str(request.url))
            if fault["unavailable"]:
                return httpx.Response(503, text="Simulated BookTrust outage", request=request)
            return httpx.Response(
                200, content=booktrust_listing(),
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )

        booktrust.client.close()
        booktrust.client = httpx.Client(
            transport=httpx.MockTransport(respond), trust_env=False, follow_redirects=False
        )

    def write_report():
        result["duration_seconds"] = round(monotonic() - start, 2)
        (out / "screen.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )

    def capture(name):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).convert("RGB")
        assert image.width == args.width, f"wrong virtual screen: {image.width}"
        assert len(image.getcolors(maxcolors=2) or []) != 1, "blank GTK screenshot"
        path = out / f"{name}.png"
        image.save(path)
        shutil.copyfile(path, out / "screen.png")  # Canonical E2E screenshot, including on failure.
        result["steps"].append({
            "stage": name, "image": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "route": window.view_stack.get_visible_child_name(),
            "window_width": window.get_width(),
            "visible_rows": sorted(page.view.rows),
            "filters": str(page.view.filter_state()),
        })

    def fail(reason):
        if result["status"] != "RUNNING":
            return
        result["status"] = "FAIL"
        result["error"] = reason
        if window is not None:
            try:
                capture("failure")
            except Exception:
                pass
        write_report()
        app.quit()

    def later(ms, fn):
        def invoke():
            try:
                fn()
            except Exception as exc:
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, invoke)

    def keys(name):
        device.key(name, True)
        device.key(name, False)

    def assert_rows(*expected):
        actual = {book.record_id for book in page.view.rows.values()}
        assert actual == set(expected), f"expected {expected}, observed {actual}"

    def detail_rows():
        """Read actual libadwaita detail rows, not only catalog data."""
        found = []

        def visit(widget):
            if isinstance(widget, Adw.ActionRow):
                found.append((str(widget.get_title() or ""), str(widget.get_subtitle() or "")))
            child = widget.get_first_child()
            while child is not None:
                visit(child)
                child = child.get_next_sibling()

        visit(page.view.master._page.detail)
        return found

    def start_ui():
        assert window.view_stack.get_visible_child_name() == "youth"
        assert page.view is not None, "Real ui.page.youth factory not called"
        assert page.view.widget.get_mapped(), "Youth view not mapped on screen"
        assert window.get_width() <= args.width, "Youth window overflows Xvfb viewport"
        assert page.view.identity.get_model().get_n_items() == 2
        assert page.view.age.get_model().get_n_items() == 19, "Only 0..17 are selectable, not 18"
        if args.booktrust:
            source_start_ui()
            return
        assert len(page.view.sources) == 0, "No external youth source should activate in this fixture"
        assert_rows()
        capture("00-empty-cache")
        camille, alex = records()
        assert catalog.ingest("camille", "booktrust", camille).accepted == 6
        milan = dict(
            camille[1],
            record_id="milan-forest",
            source_item_id="milan-forest",
            age_ranges=[{
                "age_min": 8, "age_max": 12, "age_kind": "publisher",
                "source_label": "8–12 ans (Milan)", "source_id": "milan",
                "confidence": 0.9,
            }],
        )
        assert catalog.ingest("camille", "milan", [milan]).accepted == 1
        assert catalog.ingest("alex", "booktrust", alex).accepted == 1
        merged = catalog.search_cached("camille", "Forest")
        assert len(merged) == 1 and set(merged[0].provenance) == {"booktrust", "milan"}
        page.view.render_cached()
        later(200, cached_camille)


    def collect_button():
        def walk(widget):
            if isinstance(widget, Gtk.Button) and widget.get_label() == "Collecter cette source":
                return widget
            child = widget.get_first_child()
            while child is not None:
                found = walk(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None
        button = walk(page.view.widget)
        assert button is not None and button.get_mapped(), "Collect action not mapped"
        return button

    def source_start_ui():
        assert len(page.view.sources) == 1, "Installed BookTrust source missing from real GTK"
        assert page.view.sources[0].plugin_id == "youth.source.booktrust"
        assert page.view.source.get_model().get_n_items() == 2
        assert_rows()
        assert not requests, "Opening page triggered unsolicited HTTP"
        capture("00-source-installed-cache-empty")
        page.view.source.set_selected(1)
        later(180, source_collect)

    def source_collect():
        assert page.view.selected_source_plugin() == "youth.source.booktrust"
        assert not requests, "Selecting source triggered unsolicited HTTP"
        assert_rows()
        device.click_widget(collect_button())
        later(120, source_success)

    def source_success():
        if len(page.view.rows) != 2 and monotonic() - start < 16:
            later(130, source_success)
            return
        assert len(requests) == 1, f"Expected one explicit request: {requests!r}"
        assert requests[0].startswith("https://www.booktrust.org.uk/book-recommendations/bookfinder/")
        assert {book.record_id for book in page.view.rows.values()} == {
            "atlas-des-lucioles", "jardin-des-mots"
        }, f"Unexpected BookTrust results: {page.view.rows!r}"
        status = page.view.status.get_text()
        assert "2 notice(s) reçue(s) de BookTrust Bookfinder" in status, status
        assert len(catalog.search_cached("camille")) == 2
        capture("01-source-success-cache")
        result["booktrust_requests_before_outage"] = list(requests)
        fault["unavailable"] = True
        device.click_widget(collect_button())
        later(130, source_failure)

    def source_failure():
        status = page.view.status.get_text()
        if "Échec de la source" not in status and monotonic() - start < 23:
            later(130, source_failure)
            return
        assert len(requests) == 2, f"Outage requires second explicit click: {requests!r}"
        assert "Échec de la source. Le cache précédent est conservé." in status, status
        assert {book.record_id for book in page.view.rows.values()} == {
            "atlas-des-lucioles", "jardin-des-mots"
        }, "Outage destroyed the visible cache"
        assert len(catalog.search_cached("camille")) == 2
        result["booktrust_requests_after_outage"] = list(requests)
        result["outage_message"] = status
        capture("02-source-unavailable-cache-retained")
        page.view.age.set_selected(18)  # 17 ans, no matching BookTrust cache
        later(180, source_local_empty)

    def source_local_empty():
        assert_rows()
        assert len(requests) == 2, "Local age filter unexpectedly triggered HTTP"
        assert page.view.master._page.list_stack.get_visible_child_name() == "empty"
        capture("03-local-empty-distinct-from-outage")
        result["status"] = "PASS"
        write_report()
        rc[0] = 0
        app.quit()

    def cached_camille():
        assert_rows("zero", "interest-eight", "reading-ten", "seventeen", "inferred", "unknown")
        capture("01-cached-camille")
        page.view.age.set_selected(9)  # 0 = any, 1 = age 0, 9 = age 8
        later(200, age_eight)

    def age_eight():
        assert page.view.filter_state().age == 8
        assert_rows("interest-eight")
        capture("02-age-8-interest")
        page.view.age.set_selected(13)  # age 12
        assert_rows("interest-eight", "reading-ten")
        page.view.age_kind.set_selected(3)  # reading only (kind index 3)
        later(200, age_twelve_reading)

    def age_twelve_reading():
        assert_rows("reading-ten")
        capture("03-age-12-reading")
        page.view.age_kind.set_selected(0)
        page.view.age.set_selected(18)  # age 17; age 18 is not exposed
        later(200, age_seventeen)

    def age_seventeen():
        assert_rows("seventeen")
        capture("04-age-17")
        page.view.age.set_selected(1)  # age 0
        assert_rows("zero")
        page.view.age.set_selected(9)
        page.view.inferred.set_active(True)
        assert_rows("interest-eight", "inferred")
        page.view.inferred.set_active(False)
        assert_rows("interest-eight")
        page.view.identity.set_selected(1)
        later(200, profile_alex)

    def profile_alex():
        assert_rows("alex-only")
        capture("05-identity-alex")
        page.view.identity.set_selected(0)
        assert_rows("interest-eight")
        later(200, keyboard_search)

    def keyboard_search():
        # Real mouse + XTEST keyboard events, not Gtk.Entry.set_text().
        device.click_widget(page.view.query)
        assert page.view.query.get_mapped(), "Keyboard search entry not mapped"
        device.type_text("Forest")
        later(220, finish_keyboard)

    def finish_keyboard():
        assert page.view.query.get_text() == "Forest"
        assert_rows("interest-eight")
        page.view.master._page.selection.set_selected(0)
        later(250, finish)

    def finish():
        assert page.view.selected_book is not None
        assert page.view.selected_book.record_id == "interest-eight"
        # The selected book must preserve the real source age label.
        assert page.view.selected_book.age_ranges[0].source_label == "8–12 ans"
        assert set(page.view.selected_book.provenance) == {"booktrust", "milan"}
        shown = detail_rows()
        assert any(
            "source booktrust" in subtitle and "8–12 ans" in subtitle
            for _, subtitle in shown
        ), f"BookTrust age not rendered in GTK detail: {shown!r}"
        assert any(
            "source milan" in subtitle and "8–12 ans (Milan)" in subtitle
            for _, subtitle in shown
        ), f"Milan age not rendered in GTK detail: {shown!r}"
        assert any(
            title == "Sources" and "booktrust" in subtitle and "milan" in subtitle
            for title, subtitle in shown
        ), f"Multi-source provenance not rendered in GTK detail: {shown!r}"
        result["detail_provenance"] = shown
        capture("06-keyboard-search-detail")
        later(160, click_collect_without_source)

    def click_collect_without_source():
        """XTEST no-source action: explicit feedback, no implicit remote request."""
        assert not page.view.sources, "No remote youth provider should be installed"

        def find_collect(widget):
            if isinstance(widget, Gtk.Button) and widget.get_label() == "Collecter cette source":
                return widget
            child = widget.get_first_child()
            while child is not None:
                button = find_collect(child)
                if button is not None:
                    return button
                child = child.get_next_sibling()
            return None

        button = find_collect(page.view.widget)
        assert button is not None and button.get_mapped(), "Collect action is not visible"
        result["no_source_click"] = device.click_widget(button)
        later(200, verify_no_source)

    def verify_no_source():
        message = page.view.status.get_text()
        assert "Choisissez une source Jeunesse avant de collecter." in message, message
        assert_rows("interest-eight")
        assert page.view.selected_book is not None
        assert page.view.selected_book.record_id == "interest-eight"
        result["no_source_feedback"] = message
        capture("07-no-source-feedback")
        page.view.age.set_selected(0)
        page.view.query.set_text("Age inconnu")
        later(200, select_unknown_age)

    def select_unknown_age():
        assert_rows("unknown")
        page.view.master._page.selection.set_selected(0)
        later(200, verify_unknown_age)

    def verify_unknown_age():
        assert page.view.selected_book is not None
        assert page.view.selected_book.record_id == "unknown"
        shown = detail_rows()
        assert any(
            title == "Âge" and "Aucune tranche d’âge attestée" in subtitle
            for title, subtitle in shown
        ), f"Unknown age not described in GTK detail: {shown!r}"
        capture("08-unknown-age-detail")
        result["status"] = "PASS"
        write_report()
        rc[0] = 0
        app.quit()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.YouthE2E")
        def do_activate(self):
            nonlocal window, device
            window = MainWindow(self, core.config, core.library, core.diagnostics)
            window.set_default_size(args.width - 40, 780)
            window.present()
            device = X11Input()
            later(400, lambda: (window.navigate("youth"), later(400, start_ui)))
        def do_shutdown(self):
            if device:
                device.close()
            core.close()
            work.cleanup()
            Gio.Application.do_shutdown(self)

    app = Application()
    def timed_out():
        if rc[0]:
            fail("GTK Youth probe timed out (30s)")
        return False
    GLib.timeout_add_seconds(45 if args.booktrust else 30, timed_out)
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        result.update(status="FAIL", error="GTK closed before probe completed")
        write_report()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
