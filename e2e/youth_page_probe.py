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
import shlex
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


def install_real_youth(core: CiferecaCore, work: Path, *, with_booktrust: bool = False, with_milan: bool = False) -> None:
    packages = [
        ("youth-catalog", ("cifereca-plugin.json", "youth_catalog.py", "youth_page.py")),
    ]
    if with_booktrust:
        packages.append(("youth-source-booktrust", ("cifereca-plugin.json", "booktrust_youth.py")))
    if with_milan:
        packages.append(("youth-source-milan", ("cifereca-plugin.json", "milan_youth.py")))
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
    if with_milan:
        plugins += ("youth.source.milan",)
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
    provider = parser.add_mutually_exclusive_group()
    provider.add_argument("--booktrust", action="store_true", help="Use installed BookTrust with offline HTTP fixtures")
    provider.add_argument("--milan", action="store_true", help="Use installed Milan with offline HTML fixtures")
    provider.add_argument("--editions", action="store_true", help="Offline same-title editions and conflicting provenance on real GTK")
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
        "milan_source_installed": args.milan,
        "conflicting_editions": args.editions,
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
            if args.booktrust else
            ("catalog.youth", "ui.page.youth", "youth.source.milan")
            if args.milan else ("catalog.youth", "ui.page.youth")
        ),
        activate_plugins=False,
    )
    work = tempfile.TemporaryDirectory(prefix="youth-gtk-package-")
    install_real_youth(core, Path(work.name), with_booktrust=args.booktrust, with_milan=args.milan)
    catalog = core.plugins.service("catalog.youth")
    page = core.plugins.service("ui.page.youth")
    assert catalog is not None and page is not None

    requests = []
    fault = {"unavailable": False}
    notice_sink_path = out / "local-https-handler.jsonl"
    if args.milan:
        # Exercise the real Gtk.LinkButton -> Gio HTTPS handler dispatch while
        # making an external browser/network request impossible. The runner
        # supplies a fresh XDG_CONFIG_HOME/XDG_DATA_HOME for every scenario.
        assert os.environ.get("XDG_DATA_HOME") and os.environ.get("XDG_CONFIG_HOME"), (
            "Refuse to install fake handler outside isolated XDG fixture"
        )
        sink_script = out / "local-https-handler.py"
        sink_script.write_text(
            "#!/usr/bin/python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"target = Path({str(notice_sink_path)!r})\n"
            "with target.open('a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps({'argv': sys.argv[1:]}) + '\\n')\n",
            encoding="utf-8",
        )
        sink_script.chmod(0o700)
        handler_name = "Cifereca E2E Offline HTTPS Notice Handler"
        handler = Gio.AppInfo.create_from_commandline(
            shlex.quote(str(sink_script)),
            handler_name,
            Gio.AppInfoCreateFlags.SUPPORTS_URIS,
        )
        assert handler is not None, "Could not create offline URI sink"
        assert handler.set_as_default_for_type("x-scheme-handler/https"), (
            "HTTPS scheme not safely mapped to offline URI sink"
        )
        default_https = Gio.AppInfo.get_default_for_uri_scheme("https")
        assert default_https is not None and default_https.get_name() == handler_name, (
            f"Refuse to click external HTTPS link without isolated safe handler: {default_https!r}"
        )
        result["notice_launch_handler"] = handler_name
    if args.milan:
        import httpx
        from milan.pseudo_milan_service import listing, detail

        milan = core.plugins.service("youth.source.milan")
        assert milan is not None, "Installed Milan provider unavailable"

        def milan_respond(request):
            assert request.url.host == "www.editionsmilan.com", request.url
            assert request.url.scheme == "https", request.url
            requests.append(str(request.url))
            if fault["unavailable"]:
                return httpx.Response(503, text="Simulated Milan outage", request=request)
            uri = request.url.path
            if uri == "/livres/":
                payload = listing(1)
            elif uri == "/livres/page/2/":
                payload = listing(2)
            elif uri.startswith("/livres/"):
                payload = detail(uri.rstrip("/").split("/")[-1])
            else:
                raise AssertionError(f"Unexpected Milan URL: {uri}")
            assert payload is not None, f"No fixture for {uri}"
            return httpx.Response(
                200, content=payload,
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )

        milan.client.close()
        milan.client = httpx.Client(
            transport=httpx.MockTransport(milan_respond),
            trust_env=False, follow_redirects=False,
        )

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
        if args.milan:
            milan_start_ui()
            return
        if args.editions:
            editions_start_ui()
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
        # The same source must recover through a new deliberate click.
        page.view.age.set_selected(0)
        later(180, booktrust_retry_after_outage)

    def booktrust_retry_after_outage():
        assert_rows("atlas-des-lucioles", "jardin-des-mots")
        assert len(requests) == 2, "Clearing local filter must not fetch remote"
        assert "Échec de la source" in page.view.status.get_text()
        fault["unavailable"] = False
        device.click_widget(collect_button())
        later(140, booktrust_recovered)

    def booktrust_recovered():
        if (
            len(requests) != 3
            or "2 notice(s) reçue(s) de BookTrust Bookfinder" not in page.view.status.get_text()
        ) and monotonic() - start < 37:
            later(140, booktrust_recovered)
            return
        assert len(requests) == 3, f"Recovery requires exactly one click: {requests!r}"
        assert requests[0] == requests[1] == requests[2], "Retry changed canonical BookTrust URL"
        assert_rows("atlas-des-lucioles", "jardin-des-mots")
        assert len(catalog.search_cached("camille")) == 2
        status = page.view.status.get_text()
        assert "Échec de la source" not in status, status
        result["booktrust_requests_after_recovery"] = list(requests)
        result["booktrust_recovery_feedback"] = status
        capture("04-source-recovered-after-503")
        result["status"] = "PASS"
        write_report()
        rc[0] = 0
        app.quit()


    def milan_start_ui():
        assert len(page.view.sources) == 1, "Installed Milan provider not listed"
        assert page.view.sources[0].plugin_id == "youth.source.milan"
        assert page.view.source.get_model().get_n_items() == 2
        assert_rows()
        assert requests == [], "Opening Milan page triggered remote HTTP"
        capture("00-milan-source-no-cache")
        page.view.source.set_selected(1)
        later(180, milan_collect)

    def milan_collect():
        assert page.view.selected_source_plugin() == "youth.source.milan"
        assert not requests, "Selecting Milan triggered remote HTTP"
        device.click_widget(collect_button())
        later(170, milan_success)

    def milan_success():
        if len(page.view.rows) != 4 and monotonic() - start < 19:
            later(180, milan_success)
            return
        assert len(page.view.rows) == 4, f"Milan cache count: {page.view.rows!r}"
        assert len(requests) == 9, f"Expected listing + 8 bounded detail fetches: {requests!r}"
        assert requests[0] == "https://www.editionsmilan.com/livres/"
        assert "99993-jeunesse-neuf" not in " ".join(requests)
        status = page.view.status.get_text()
        assert "4 notice(s) reçue(s) de Éditions Milan" in status, status
        assert "page suivante disponible" in status, status
        assert len(catalog.search_cached("camille")) == 4
        # Immediate capture documents the actual empty -> list CROSSFADE.
        capture("01-milan-collected-four")
        later(1100, milan_list_settled)

    def milan_list_settled():
        # Do not mistake the temporary outgoing "Aucun résultat en cache"
        # status-page overlay for a lasting UX defect.
        stack = page.view.master._page.list_stack
        assert stack.get_visible_child_name() == "list", "Collected rows not made visible"
        assert not stack.get_transition_running(), "Empty-to-list GTK transition still running"
        assert len(page.view.rows) == 4
        capture("01b-milan-list-settled")
        page.view.master._page.selection.set_selected(0)
        later(250, milan_notice)

    def milan_notice():
        book = page.view.selected_book
        assert book is not None, "Selecting Milan book failed"
        assert set(book.provenance) == {"milan"}
        assert len(book.age_ranges) == 1
        assert book.age_ranges[0].age_kind == "publisher"
        assert "À partir de" in book.age_ranges[0].source_label
        expected_uri = book.notice_url
        assert expected_uri.startswith("https://www.editionsmilan.com/livres/")
        assert page.view.master._page.detail._rendered_document_urls == {expected_uri}
        def links(widget):
            result_links = []
            if isinstance(widget, Gtk.LinkButton):
                result_links.append(widget)
            child = widget.get_first_child()
            while child is not None:
                result_links.extend(links(child))
                child = child.get_next_sibling()
            return result_links
        rendered = links(page.view.master._page.detail)
        notice = [link for link in rendered if link.get_uri() == expected_uri]
        assert len(notice) == 1, f"Expected 1 notice link for {expected_uri}: {[(b.get_label(), b.get_uri()) for b in rendered]!r}"
        # GTK4 LinkButton may render its text in a child GtkLabel while
        # Gtk.Button.get_label() returns None. Inspect the actual widget tree.
        def widget_text(widget):
            labels = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
            child = widget.get_first_child()
            while child is not None:
                labels.extend(widget_text(child))
                child = child.get_next_sibling()
            return labels
        label = notice[0].get_label() or " ".join(widget_text(notice[0]))
        assert "Voir la notice publique" in label, f"Notice link text missing in GTK: {label!r}"
        assert notice[0].get_mapped(), "Notice link not mapped in real GTK detail"
        result["milan_notice_uri"] = expected_uri
        capture("02-milan-notice-link")
        # Real XTEST activation with an isolated local HTTPS handler. Its sole
        # executable records the URI and never touches the Internet.
        assert not notice_sink_path.exists(), "Notice URL dispatched without explicit click"
        assert len(requests) == 9, "Detail render unexpectedly fetched HTTP"
        result["milan_notice_click"] = device.click_widget(notice[0])
        later(150, milan_notice_dispatched)

    def milan_notice_dispatched():
        if not notice_sink_path.exists() and monotonic() - start < 33:
            later(160, milan_notice_dispatched)
            return
        assert notice_sink_path.exists(), "GTK LinkButton did not launch isolated HTTPS handler"
        invocations = [
            json.loads(line)["argv"]
            for line in notice_sink_path.read_text(encoding="utf-8").splitlines()
        ]
        assert invocations == [[result["milan_notice_uri"]]], (
            f"Unexpected default browser dispatch: {invocations!r}"
        )
        assert len(requests) == 9, "Clicking notice must not re-collect via source HTTP"
        assert len(page.view.rows) == 4, "Clicking notice mutated catalogue cache"
        result["milan_notice_handler_invocations"] = invocations
        result["milan_notice_dispatched_via"] = result["notice_launch_handler"]
        capture("02b-milan-notice-launched-to-offline-browser")
        def find_more(widget):
            if isinstance(widget, Gtk.Button) and widget.get_label() == "Plus de résultats":
                return widget
            child = widget.get_first_child()
            while child is not None:
                found = find_more(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None
        more = find_more(page.view.widget)
        assert more is not None and more.get_mapped(), "Milan pagination button missing"
        device.click_widget(more)
        later(170, milan_page_two)

    def milan_page_two():
        if len(page.view.rows) != 5 and monotonic() - start < 30:
            later(180, milan_page_two)
            return
        assert len(page.view.rows) == 5, f"Milan append pagination failed: {page.view.rows!r}"
        assert len(requests) == 12, f"Expected listing + 2 page-2 detail requests: {requests!r}"
        assert requests[9] == "https://www.editionsmilan.com/livres/page/2/"
        assert not page.view.next_cursor, "Milan terminal page still exposes next cursor"
        capture("03-milan-page-two-appended")
        fault["unavailable"] = True
        device.click_widget(collect_button())
        later(160, milan_outage)

    def milan_outage():
        message = page.view.status.get_text()
        if "Échec de la source" not in message and monotonic() - start < 40:
            later(170, milan_outage)
            return
        assert message == "Échec de la source. Le cache précédent est conservé.", message
        assert len(requests) == 13, f"Outage requires exactly one new explicit call: {requests!r}"
        assert len(page.view.rows) == 5
        assert len(catalog.search_cached("camille")) == 5
        result["milan_requests"] = list(requests)
        result["milan_outage_feedback"] = message
        capture("04-milan-outage-cache-preserved")
        page.view.age.set_selected(1)  # 0 years; all attested Milan ages start at 1+
        later(200, milan_empty)

    def milan_empty():
        assert_rows()
        assert len(requests) == 13, "Local age filter unexpectedly contacted Milan"
        assert page.view.master._page.list_stack.get_visible_child_name() == "empty"
        capture("05-milan-empty-without-request")
        # Exit local no-results filter; only a further XTEST click may retry.
        page.view.age.set_selected(0)
        later(200, milan_retry_after_outage)

    def milan_retry_after_outage():
        assert len(page.view.rows) == 5
        assert len(requests) == 13, "Clearing local filter must not fetch Milan"
        assert "Échec de la source" in page.view.status.get_text()
        fault["unavailable"] = False
        device.click_widget(collect_button())
        later(180, milan_recovered)

    def milan_recovered():
        status = page.view.status.get_text()
        if (len(requests) != 22 or "4 notice(s) reçue(s) de Éditions Milan" not in status) and monotonic() - start < 49:
            later(170, milan_recovered)
            return
        assert len(requests) == 22, f"Retry should fetch listing + eight details: {requests!r}"
        assert requests[13] == requests[0], "Recovery did not restart at first Milan catalogue page"
        assert len(page.view.rows) == 4, "Authoritative recovery must replace, not append stale page-two results"
        assert len(catalog.search_cached("camille")) == 4
        assert page.view.next_cursor == "2", "Recovery should expose the fresh second page"
        assert "page suivante disponible" in status and "Échec de la source" not in status, status
        result["milan_requests_after_recovery"] = list(requests)
        result["milan_recovery_feedback"] = status
        capture("06-milan-recovered-first-page")
        result["status"] = "PASS"
        write_report()
        rc[0] = 0
        app.quit()


    def editions_start_ui():
        """Three synthetic source records: same ISBN merges, distinct ISBN does not."""
        assert page.view.sources == (), "No remote provider should run in offline edition fixture"
        assert_rows()
        shared = dict(
            title="Livre miroir", authors=["Autrice commune"],
            language="fr", formats=["epub"], book_type="fiction",
            themes=["aventure"],
        )
        same_isbn = "9780306406157"
        other_isbn = "9780140328721"
        bt = dict(
            shared, record_id="mirror-bt", source_item_id="bt-mirror",
            isbn=same_isbn,
            notice_url="https://www.booktrust.org.uk/book-recommendations/bookfinder/mirror-a/",
            age_ranges=[{
                "age_min": 5, "age_max": 7, "age_kind": "interest",
                "source_id": "booktrust", "source_label": "Interest age: 5 to 7",
                "confidence": 0.85,
            }],
        )
        milan_same = dict(
            shared, record_id="mirror-milan-a", source_item_id="milan-mirror-a",
            isbn=same_isbn,
            notice_url="https://www.editionsmilan.com/livres/12345-mirror-a/",
            age_ranges=[{
                "age_min": 12, "age_max": None, "age_kind": "publisher",
                "source_id": "milan", "source_label": "À partir de 12 ans",
                "confidence": 1.0,
            }],
        )
        milan_other = dict(
            shared, record_id="mirror-milan-b", source_item_id="milan-mirror-b",
            isbn=other_isbn,
            notice_url="https://www.editionsmilan.com/livres/12346-mirror-b/",
            age_ranges=[{
                "age_min": 8, "age_max": None, "age_kind": "publisher",
                "source_id": "milan", "source_label": "À partir de 8 ans",
                "confidence": 1.0,
            }],
        )
        assert catalog.ingest("camille", "booktrust", [bt]).accepted == 1
        assert catalog.ingest("camille", "milan", [milan_same, milan_other]).accepted == 2
        merged = catalog.search_cached("camille", "Livre miroir")
        assert len(merged) == 2, f"Distinct ISBNs were incorrectly collapsed: {merged!r}"
        by_isbn = {book.identifiers.get("isbn"): book for book in merged}
        assert set(by_isbn) == {same_isbn, other_isbn}
        assert len(by_isbn[same_isbn].age_ranges) == 2
        assert set(by_isbn[same_isbn].provenance) == {"booktrust", "milan"}
        assert tuple(by_isbn[other_isbn].provenance) == ("milan",)
        assert catalog.search_cached("alex", "Livre miroir") == ()
        page.view.query.set_text("Livre miroir")
        later(230, editions_two_rows)

    def editions_two_rows():
        assert_rows("mirror-bt", "mirror-milan-b")
        master = page.view.master._page
        assert master.store.get_n_items() == 2
        assert master.list_stack.get_visible_child_name() == "list"
        assert not requests, "Offline edits made a remote request"
        # Verify both real Gtk.ListView rows, not just the catalog/projection.
        expected = {
            "mirror-bt": "9780306406157",
            "mirror-milan-b": "9780140328721",
        }
        list_items = {
            master.store.get_item(i).key: master.store.get_item(i).meta
            for i in range(master.store.get_n_items())
        }
        assert set(list_items) == set(page.view.rows)
        for key, isbn in expected.items():
            assert list_items[next(k for k in list_items if page.view.rows[k].record_id == key)].startswith(
                f"ISBN {isbn}"
            ), list_items

        def actual_gtk_cells(widget):
            cells = []
            if hasattr(widget, "_bound_key") and hasattr(widget, "meta_label"):
                cells.append(widget)
            child = widget.get_first_child()
            while child is not None:
                cells.extend(actual_gtk_cells(child))
                child = child.get_next_sibling()
            return cells

        cells = {
            cell._bound_key: cell for cell in actual_gtk_cells(master.listview)
            if cell._bound_key and cell.get_mapped()
        }
        assert len(cells) == 2, f"Expected two mapped Gtk.ListView edition rows: {list(cells)!r}"
        for key, isbn in expected.items():
            actual_key = next(k for k, book in page.view.rows.items() if book.record_id == key)
            cell = cells[actual_key]
            assert cell.meta_label.get_mapped(), f"ISBN label hidden for {key}"
            assert cell.meta_label.get_text().startswith(f"ISBN {isbn}"), cell.meta_label.get_text()
        result["gtk_edition_list_metadata"] = {
            cell._bound_key: cell.meta_label.get_text() for cell in cells.values()
        }
        capture("00-same-title-two-editions")
        page.view.master._page.selection.set_selected(0)
        later(220, editions_conflict_detail)

    def editions_conflict_detail():
        book = page.view.selected_book
        assert book and book.record_id == "mirror-bt"
        assert set(book.provenance) == {"booktrust", "milan"}
        assert [(row.age_min, row.age_max) for row in book.age_ranges] == [(5, 7), (12, None)]
        shown = detail_rows()
        assert ("ISBN", "9780306406157") in shown, f"ISBN missing from real first Gtk detail: {shown!r}"
        assert ("ISBN", "9780140328721") not in shown, f"Incorrect second ISBN leaked: {shown!r}"
        assert any("source booktrust" in sub and "Interest age: 5 to 7" in sub for _, sub in shown), shown
        assert any("source milan" in sub and "À partir de 12 ans" in sub for _, sub in shown), shown
        assert any(title == "Sources" and "booktrust" in sub and "milan" in sub for title, sub in shown), shown
        result["same_isbn_detail"] = shown
        capture("01-conflicting-age-sources-preserved")
        later(140, editions_first_isbn_in_view)

    def editions_first_isbn_in_view():
        scroller = page.view.master._page.detail.body_scroll
        adjustment = scroller.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        later(200, editions_first_isbn_scrolled)

    def editions_first_isbn_scrolled():
        assert ("ISBN", "9780306406157") in detail_rows()
        capture("01b-first-isbn-visible-after-scroll")
        page.view.master._page.selection.set_selected(1)
        later(220, editions_second_detail)

    def editions_second_detail():
        book = page.view.selected_book
        assert book and book.record_id == "mirror-milan-b", book
        assert book.identifiers.get("isbn") == "9780140328721"
        assert set(book.provenance) == {"milan"}
        shown = detail_rows()
        assert ("ISBN", "9780140328721") in shown, f"ISBN missing from second Gtk detail: {shown!r}"
        assert ("ISBN", "9780306406157") not in shown, f"First ISBN leaked to second edition: {shown!r}"
        assert any("source milan" in sub and "À partir de 8 ans" in sub for _, sub in shown), shown
        assert not any("source booktrust" in sub for _, sub in shown), shown
        result["different_isbn_detail"] = shown
        capture("02-second-edition-separated")
        later(140, editions_second_isbn_in_view)

    def editions_second_isbn_in_view():
        scroller = page.view.master._page.detail.body_scroll
        adjustment = scroller.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        later(200, editions_second_isbn_scrolled)

    def editions_second_isbn_scrolled():
        assert ("ISBN", "9780140328721") in detail_rows()
        capture("02b-second-isbn-visible-after-scroll")
        page.view.age.set_selected(7)  # âge 6: seulement BookTrust 5-7
        later(180, editions_age_six)

    def editions_age_six():
        assert_rows("mirror-bt")
        capture("03-age-six-only-first-edition")
        page.view.age.set_selected(10)  # âge 9: seulement Milan 8+
        later(180, editions_age_nine)

    def editions_age_nine():
        assert_rows("mirror-milan-b")
        capture("04-age-nine-only-second-edition")
        page.view.age_kind.set_selected(2)  # Âge d'intérêt, aucun à 9 ans
        later(180, editions_no_interest)

    def editions_no_interest():
        assert_rows()
        assert page.view.master._page.list_stack.get_visible_child_name() == "empty"
        capture("05-no-interest-age-at-nine")
        page.view.age_kind.set_selected(4)  # Âge éditeur
        page.view.age.set_selected(13)  # âge 12 : les deux éditions distinctes
        later(200, editions_publisher_twelve)

    def editions_publisher_twelve():
        assert_rows("mirror-bt", "mirror-milan-b")
        capture("06-publisher-age-twelve-keeps-both")
        page.view.identity.set_selected(1)  # Alex sans ces notices
        later(200, editions_identity_isolated)

    def editions_identity_isolated():
        assert_rows()
        assert not requests, "Identity/age filters triggered network"
        assert page.view.master._page.list_stack.get_visible_child_name() == "empty"
        capture("07-other-profile-stays-empty")
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
        # #910 exploratory observation: emit real GTK activate signals via
        # XTEST without prescribing an (unimplemented) product behavior.
        # Passing E2E means the observation is genuine, NOT that #910 is fixed.
        # Gtk.Entry contains an internal Gtk.Text focus target in GTK4;
        # Entry.has_focus() alone is not a reliable keyboard assertion.
        entry_has_focus = bool(page.view.query.has_focus())
        assert page.view.selected_source_plugin() == "", "Local fixture unexpectedly has a remote source"
        result["keyboard_enter_observation"] = {
            "status_before": page.view.status.get_text(),
            "entry_widget_has_focus": entry_has_focus,
            "query": page.view.query.get_text(),
            "identity_id": page.view.identity_id(),
            "source_plugin": page.view.selected_source_plugin(),
            "row_ids": sorted(book.record_id for book in page.view.rows.values()),
            "gtk_activate_signals": 0,
            "criterion_910_satisfied": None,
            "note": "Observational UX probe; PASS does not mean Enter works as specified in #910.",
        }
        def activated(_entry):
            result["keyboard_enter_observation"]["gtk_activate_signals"] += 1
        page.view.query.connect("activate", activated)
        keys("Return")
        later(170, inspect_return)

    def inspect_return():
        audit = result["keyboard_enter_observation"]
        assert audit["gtk_activate_signals"] == 1, (
            f"Physical Return did not activate Gtk.Entry: {audit!r}"
        )
        assert page.view.query.get_text() == audit["query"]
        assert_rows("interest-eight")
        assert requests == [], "First Enter must never contact a remote source"
        audit["return_status"] = page.view.status.get_text()
        audit["return_visible_feedback"] = audit["return_status"] != audit["status_before"]
        assert audit["return_visible_feedback"], "First Return must acknowledge the local cache search"
        assert "Recherche en cache" in audit["return_status"]
        capture("05b-youth-return-gtk-observation")
        keys("KP_Enter")
        later(170, inspect_keypad_enter)

    def inspect_keypad_enter():
        audit = result["keyboard_enter_observation"]
        assert audit["gtk_activate_signals"] == 2, (
            f"Physical keypad Enter did not activate Gtk.Entry: {audit!r}"
        )
        assert page.view.query.get_text() == audit["query"]
        assert_rows("interest-eight")
        assert requests == [], "No selected source: consecutive Enter must not contact any provider"
        audit["keypad_return_status"] = page.view.status.get_text()
        audit["keypad_visible_feedback"] = (
            audit["keypad_return_status"] != audit["return_status"]
        )
        assert audit["keypad_visible_feedback"], "Second keypad Enter must explain source selection"
        assert "Choisissez une source Jeunesse précise" in audit["keypad_return_status"]
        audit["route_after"] = window.view_stack.get_visible_child_name()
        audit["selected_source_after"] = page.view.selected_source_plugin()
        # The acceptance requirement of #910 also includes explicit remote
        # collection with a chosen provider, untested here. Do not auto-close.
        audit["criterion_910_satisfied"] = False  # Remote selected-source path needs an independent GTK fixture.
        audit["missing_no_source_feedback_observed"] = (
            not audit["keypad_visible_feedback"]
        )
        capture("05c-youth-keypad-enter-gtk-observation")
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
        # #891 fallback: if there is no valid ISBN, identify the source
        # unambiguously without pretending a source-local key is an edition.
        assert ("Référence source", "booktrust:unknown") in shown, shown
        assert not any(title == "ISBN" for title, _ in shown), shown
        capture("08-unknown-age-detail")
        later(180, open_installed_plugin_manager)

    def plugin_states():
        return {
            row.descriptor.id: (bool(row.enabled), row.state)
            for row in core.plugins.statuses()
        }

    def open_installed_plugin_manager():
        # The real installed package and UI contribution, not a mock list.
        statuses = plugin_states()
        assert statuses.get("catalog.youth", (False, ""))[0], statuses
        assert statuses.get("ui.page.youth", (False, ""))[0], statuses
        assert statuses["ui.page.youth"][1] in {"ready", "degraded"}, statuses
        result["plugin_states_before_manager"] = statuses
        assert requests == [], "Offline Jeunesse manager must not trigger HTTP"
        window.navigate("plugins")
        later(320, plugin_manager_overview)

    def plugin_manager_overview():
        assert window.view_stack.get_visible_child_name() == "plugins"
        manager = window.plugins_page
        assert manager.get_mapped(), "Installed plugin manager not rendered on GTK"
        assert manager.plugin_search.get_mapped(), "Plugin search not visible"
        assert manager.plugin_search.get_text() == "", "Unexpected plugin-manager filter"
        capture("09-real-plugin-manager-installed")
        # Search with real XTEST keyboard events on the manager, not its API.
        device.click_widget(manager.plugin_search)
        device.type_text("youth")
        later(280, plugin_manager_filtered)

    def plugin_manager_filtered():
        manager = window.plugins_page
        assert manager.plugin_search.get_text() == "youth", manager.plugin_search.get_text()
        assert manager._view_query == "youth", "Real plugin-manager search did not apply"
        assert manager.inventory_box.get_mapped()
        def visible_labels(widget):
            labels = []
            if isinstance(widget, Gtk.Label) and widget.get_mapped():
                labels.append(widget.get_text())
            child = widget.get_first_child()
            while child is not None:
                labels.extend(visible_labels(child))
                child = child.get_next_sibling()
            return labels
        texts = visible_labels(manager.inventory_box)
        assert any("Jeunesse" in label or "youth" in label.lower() for label in texts), texts
        assert any("Activation · Activé" in label for label in texts), texts
        assert any(label.startswith("État · ") for label in texts), texts
        result["plugin_manager_youth_labels"] = texts
        capture("10-real-plugin-manager-youth-search")
        assert plugin_states() == result["plugin_states_before_manager"], (
            "Opening or filtering plugin manager unexpectedly modified activation"
        )
        window.navigate("youth")
        later(320, verify_return_after_plugin_manager)

    def verify_return_after_plugin_manager():
        assert window.view_stack.get_visible_child_name() == "youth"
        assert page.view.widget.get_mapped(), "Youth page not restored after plugin manager"
        assert_rows("unknown")
        assert not requests, "Plugin manager/navigation performed unsolicited HTTP"
        assert plugin_states() == result["plugin_states_before_manager"]
        capture("11-youth-restored-after-plugin-manager")
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
    GLib.timeout_add_seconds(55 if args.milan else 45 if args.booktrust else 30, timed_out)
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        result.update(status="FAIL", error="GTK closed before probe completed")
        write_report()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
