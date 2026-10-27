#!/usr/bin/env python3
"""P0 #771: real GTK Search on explicit 429/503 and recovery from local HTTP.

The *real* public EbooksGratuits adapter talks to an actual loopback fake
service; a narrowly scoped Core progressive-batch bridge supplies the real
Search controller without an installed provider or external account.
This is an isolated UI error-surface acceptance, not end-to-end provider
registration or a real site. No acquisition or passive remote search.
All public evidence is from a closed schema; never persist error text, book
titles, headers, URLs, tokens or raw exceptions.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "src/plugins/ebooksgratuits",
                  ROOT / "src/plugins/provider-kobo", ROOT / "src/plugins/baen",
                  ROOT / "environnements/openai-chat-debian"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
import httpx
from PIL import ImageGrab

from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow
from cifereca.gui.query_model import QueryState
from cifereca.plugin_api import CatalogSearchBatch, CatalogSearchSummary
from cifereca_ebooksgratuits import EbooksSession
from cifereca_kobo import KoboSession, DESCRIPTOR as KOBO_DESCRIPTOR
from cifereca_kobo_vault import KoboVaultClient
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_baen import BaenSession
from simulation.tests.test_fake_services_contract import load_module, RunningServer
from x11_input import X11Input


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--provider", choices=("ebooksgratuits", "kobo", "baen"),
                        default="ebooksgratuits")
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Real GTK/XTEST/Xvfb required")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    is_kobo = args.provider == "kobo"
    is_baen = args.provider == "baen"
    work = TemporaryDirectory(prefix="ux2332-offline-" if is_baen else "ux771-offline-")
    if is_kobo:
        fake = load_module("ux771_kobo_http", "kobo/pseudo_kobo_services.py")
        cache = fake.DatasetCache(Path(work.name) / "catalog", 30, 6, 20261020, True)
        fake_app = fake.KoboFakeApp(
            cache, service="combined", token="kvt_e2e_only",
            accept_any_token=False,
        )
        catalog = RunningServer(fake.Handler, fake_app)
        query = str(cache.search_catalog("", "all", 0, 1)[1][0]["title"])
    elif is_baen:
        fake = load_module("ux2332_baen_http", "baen/pseudo_baen_service.py")
        fake_app = fake.BaenFakeApp(Path(work.name) / "catalog")
        catalog = RunningServer(fake.Handler, fake_app)
        query = "Firefly"
        original_epub_digest = hashlib.sha256(fake_app.epub_path.read_bytes()).hexdigest()
    else:
        fake = load_module("ux771_ebooks_http", "ebooksgratuits/pseudo_ebooksgratuits_service.py")
        cache = fake.DatasetCache(Path(work.name) / "catalog", 8, 20261020, True)
        fake_app = fake.EbooksGratuitsApp(cache)
        catalog = RunningServer(
            type("UX771EbooksHandler", (fake.Handler,), {"app": fake_app}),
            fake_app,
        )
        first = cache.get(1)
        assert first is not None
        query = str(first["title"])
    catalog.__enter__()
    fault = ["429"]
    http_statuses = []
    requested_paths = []
    worker_count = [0]
    ui = [None]
    device = [None]
    current_stage = ["initial"]
    last_click = [0]
    screenshots = []
    report = {
        "scenario": ("baen-fault-search-1366" if is_baen else
                     "kobo-fault-search-1366" if is_kobo else "public-fault-search-1366"),
        "status": "RUNNING",
        "scope": (
            "real GTK Search + real Kobo catalogue adapter + local HTTP and isolated Core batch bridge"
            if is_kobo else
            "real GTK Search + real Baen adapter + local synthetic HTTP and isolated Core batch bridge"
            if is_baen else
            "real GTK Search + real EbooksGratuits adapter + local synthetic HTTP and isolated Core batch bridge"
        ),
        "checks": {},
        "snapshots": [],
    }
    rc = [1]

    class Diagnostics:
        def add(self, *args, **kwargs):
            return None

    def forward(request: httpx.Request) -> httpx.Response:
        # Both real adapters use only local routes; no arbitrary host or
        # redirect may be requested. Fake Bearer data never enters evidence.
        path = request.url.path
        valid_kobo_paths = {
            "/api/v2/status", "/api/v2/account", "/api/v2/catalog/search",
        }
        if is_baen:
            if (request.url.scheme != "https" or request.url.host != "www.baen.com"
                    or request.method != "GET"
                    or path != "/allbooks/category/index/id/2012"):
                raise AssertionError("Unexpected Baen outbound route")
            probe_request = True
        elif (request.url.host != "127.0.0.1"
              or request.url.port != catalog.server.server_port):
            raise AssertionError("Unexpected outbound host")
        if is_kobo:
            if path not in valid_kobo_paths or request.method != "GET":
                raise AssertionError("Kobo catalogue must not fetch private data")
            probe_request = path == "/api/v2/catalog/search"
        elif not is_baen:
            if path != "/ebooks.php" or request.method not in {"GET", "POST"}:
                raise AssertionError("Unexpected outbound request")
            probe_request = True
        target = (
            request.url.copy_set_param("q", "rate-limit" if fault[0] == "429" else "down")
            if is_baen and fault[0] else
            request.url.copy_set_param("ux_fault", fault[0])
            if fault[0] and probe_request else request.url
        )
        post = request.method == "POST"
        headers = (
            dict(request.headers) if is_kobo else
            {"Content-Type": "application/x-www-form-urlencoded"} if post else {}
        )
        if is_baen:
            # The official HTTPS host is NEVER rewritten by production.
            # Only this injected test transport maps its fixed Baen route to
            # the loopback fixture. No credentials or external Baen traffic.
            local = catalog.base + target.raw_path.decode("ascii")
            requested_paths.append(path)
        else:
            local = str(target)
        outbound = Request(
            local, data=request.content if post else None,
            headers=headers, method=request.method,
        )
        try:
            with urlopen(outbound, timeout=4) as response:
                status, response_headers, payload = response.status, dict(response.headers), response.read()
        except HTTPError as caught:
            status, response_headers, payload = caught.code, dict(caught.headers), caught.read()
        if probe_request:
            http_statuses.append(status)
        return httpx.Response(status, headers=response_headers, content=payload, request=request)

    if is_kobo:
        session = KoboSession(
            descriptor=KOBO_DESCRIPTOR, credential_id="kobo-e2e", label="",
            credentials={"api_token": "kvt_e2e_only"},
            options={"base_url": catalog.base}, config={}, storage={},
            diagnostics=Diagnostics(),
        )
        session.client.close()
        session.client = KoboVaultClient(
            catalog.base, "kvt_e2e_only", transport=httpx.MockTransport(forward),
        )
        session.catalog = KoboCatalogService(
            session.client, credential_id=session.credential_id,
            provider_id=session.provider_id,
        )
    elif is_baen:
        session = BaenSession(
            credential_id="baen-public-fixture", label="Baen offline",
            credentials={}, options={}, config={}, diagnostics=Diagnostics(),
            transport=None,
        )
        session.client.close()
        session.client = httpx.Client(
            transport=httpx.MockTransport(forward), trust_env=False,
        )
    else:
        session = EbooksSession(
            credential_id="public", label="", credentials={},
            options={"base_url": catalog.base + "/", "search_page_size": 5,
                     "acquisition_backend": "html"},
            config={}, diagnostics=Diagnostics(), transport=None,
        )
        session.client.close()
        session.client = httpx.Client(
            transport=httpx.MockTransport(forward), trust_env=False,
        )
    session.connect()
    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)

    def search_progressive(text, scope="all", force=False, *, provider_ids=(),
                           field="all", content_types=(), on_batch=None, control=None):
        # All result/error data originates in the REAL adapter's HTTP call.
        # Only this E2E bridge replaces Core federation/installed plugin
        # discovery, and uses Core's published batch/result_status schema.
        assert force and text == query
        assert field == ("all" if (is_kobo or is_baen) else "title")
        assert not provider_ids and not content_types
        worker_count[0] += 1
        try:
            books = tuple(session.search(
                text, scope="catalog", field="all",
            ) if is_kobo else session.search(text, scope="all", field="all")
            if is_baen else session.search(text, field="title"))
            status = ("partial" if is_baen and session.last_search_warnings
                      else "live")
            warnings = ()
        except Exception:
            books = ()
            status = "unavailable"
            warnings = ("provider unavailable",)
        core.library.last_search = list(books)
        if on_batch is not None:
            on_batch(CatalogSearchBatch(
                provider_id=("baen" if is_baen else "kobo" if is_kobo else "ebooksgratuits"),
                provider_label=("Baen" if is_baen else "Kobo" if is_kobo else "Ebooks gratuits"),
                results=books, result_status=status,
            ))
        return CatalogSearchSummary(
            query=text, result_count=len(books), providers_total=1,
            providers_completed=1, providers_failed=int(status == "unavailable"),
            warnings=warnings,
        )

    core.library.search_progressive = search_progressive

    def record_image(name):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert image.size == (1366, 900), "Unexpected virtual screen size"
        assert image.getbbox() is not None, "Empty GTK screenshot"
        file = out / (name + ".png")
        image.save(file)
        shutil.copyfile(file, out / "screen.png")
        screenshots.append({"stage": name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})

    def save():
        report["screenshots"] = screenshots
        (out / "screen.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def fail():
        if report["status"] != "RUNNING":
            return
        report["status"] = "FAIL"
        report["failure_stage"] = current_stage[0]  # fixed closed label only
        save()
        if ui[0] is not None:
            app.quit()

    def later(delay_ms, callback):
        def dispatch():
            try:
                callback()
            except Exception:
                fail()  # no raw exception/providing provider content on disk
            return False
        GLib.timeout_add(delay_ms, dispatch)

    def count_label():
        return ui[0].search_page.count_label.get_label()

    def no_implicit_download():
        if is_baen:
            assert requested_paths and all(
                path == "/allbooks/category/index/id/2012" for path in requested_paths
            ), "Baen never fetches a product/download on passive search"
            assert hashlib.sha256(fake_app.epub_path.read_bytes()).hexdigest() == original_epub_digest
        else:
            assert not list(cache.epub_dir.glob("*.epub")), "Passive acquisition occurred"
        if is_kobo:
            assert not list(cache.acsm_dir.glob("*.acsm")), "Passive ACSM occurred"
        report["checks"]["no_implicit_download"] = True

    def public_status_only():
        caption = count_label()
        tooltip = ui[0].search_page.count_label.get_tooltip_text() or ""
        assert ("UNTRUSTED_" not in caption + tooltip and query not in caption + tooltip)
        assert "127.0.0.1" not in caption + tooltip
        assert "ebooksgratuits" not in caption + tooltip
        assert "kobo" not in (caption + tooltip).casefold()
        assert "kvt_e2e_only" not in caption + tooltip
        assert "baen" not in (caption + tooltip).casefold()
        report["checks"]["public_status_only"] = True

    def wait_until(predicate, callback, *, deadline=None):
        end = monotonic() + 9 if deadline is None else deadline
        def poll():
            if predicate():
                callback()
            elif monotonic() >= end:
                fail()
            else:
                later(100, poll)
        later(150, poll)

    def click_global(stage):
        current_stage[0] = stage
        assert ui[0].search_global_button.get_sensitive()
        assert ui[0].search_global_button.get_mapped()
        last_click[0] += 1
        device[0].click_widget(ui[0].search_global_button)
        wait_until(
            lambda: worker_count[0] >= last_click[0]
                and ui[0].search_global_button.get_sensitive()
                and ("indisponible" in count_label()
                     if fault[0] else
                     "résultats partiels" in count_label() if is_baen
                     else "en ligne" in count_label()),
            lambda: verify(stage),
        )

    def verify(stage):
        caption = count_label()
        expected = ("indisponible" if fault[0] else
                    "résultats partiels" if is_baen else "en ligne")
        assert expected in caption, "Missing closed public source status"
        assert all(code in (200, 429, 503) for code in http_statuses)
        public_status_only()
        no_implicit_download()
        if fault[0]:
            assert ui[0].search_page.store.get_n_items() == 0
            assert http_statuses[-1] == int(fault[0])
        else:
            assert ui[0].search_page.store.get_n_items() >= 1
            assert http_statuses[-1] == 200
            if is_baen:
                assert session.last_search_warnings
                assert ui[0].search_page.store.get_item(0).title == "The Firefly Atlas"
        report["checks"][stage] = True
        report["snapshots"].append({
            "stage": stage, "result_count": ui[0].search_page.store.get_n_items(),
            "http_status": http_statuses[-1], "worker_calls": worker_count[0],
        })
        record_image(stage)
        if stage == "429":
            fault[0] = "503"
            later(220, lambda: click_global("503"))
        elif stage == "503":
            fault[0] = ""
            later(220, lambda: click_global("recovery"))
        else:
            assert worker_count[0] == 3
            assert http_statuses == [429, 503, 200]
            report["status"] = "PASS"
            save()
            rc[0] = 0
            app.quit()

    def begin():
        ui[0].navigate("search")
        ui[0].search_controls.set_state(
            QueryState(text=query, field=("all" if (is_kobo or is_baen) else "title"),
                       scope="all"), notify=False,
        )
        # The actual GTK button is clicked only after the stack has settled.
        later(350, lambda: click_global("429"))

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id=(
                "org.cifereca.UX2332BaenFault" if is_baen else
                "org.cifereca.UX771KoboFault" if is_kobo
                else "org.cifereca.UX771PublicFault"
            ))

        def do_activate(self):
            window = MainWindow(self, core.config, core.library, core.diagnostics)
            ui[0] = window
            window.set_default_size(1326, 780)
            window.present()
            device[0] = X11Input()
            later(550, begin)

        def do_shutdown(self):
            if device[0] is not None:
                device[0].close()
            try:
                session.close()
            finally:
                catalog.__exit__(None, None, None)
                work.cleanup()
                core.close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(34, lambda: (fail(), False)[1] if report["status"] == "RUNNING" else False)
    try:
        app.run([sys.argv[0]])
    except Exception:
        fail()
    if not (out / "screen.json").is_file():
        report.update(status="FAIL", failure_stage="startup")
        save()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
