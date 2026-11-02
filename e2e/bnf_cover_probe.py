#!/usr/bin/env python3
"""P1 #4118: real GTK BnF Search/Detail cover acceptance, with zero external I/O.

The authentic BnF HTML parser and session projections feed the real MainWindow
Search controller. HTTPX MockTransport provides two valid, distinct PNG covers.
Neither an authenticated BnF account nor the public Feedbooks CDN is contacted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
from time import monotonic
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "src/plugins/provider-bnf-pret",
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
from cifereca.gui.book_widgets import CoverView
from cifereca.gui.main_window import MainWindow
from cifereca.gui.query_model import QueryState
from cifereca.plugin_api import CatalogSearchBatch, CatalogSearchSummary
import cifereca_bnf_pret as bnf
from cifereca_bnf_pret_web import ORIGIN, parse_detail, parse_search_page
from x11_input import X11Input

COVERS = (
    "https://covers.feedbooks.net/item/synthetic-alpha.jpg?size=medium&t=1762000000",
    "https://covers.feedbooks.net/item/synthetic-beta.jpg?size=small&t=1762000001",
)
RESOURCES = ("synthetic-alpha", "synthetic-beta", "synthetic-missing")


def card(resource: str, cover: str | None) -> str:
    image = f'<img class="cover" src="{cover}">' if cover else ""
    return (
        '<li class="list-details__item"><article class="view-details" itemscope '
        'itemtype="https://schema.org/Book">'
        f'<a href="/resources/{resource}"><h3 class="details-content__title">'
        f'Synthetic {resource}</h3></a>{image}</article></li>'
    )


SEARCH_HTML = '<ul class="list-details">' + "".join(
    card(resource, COVERS[i] if i < 2 else None)
    for i, resource in enumerate(RESOURCES)
) + "</ul>"


def detail_html(resource: str) -> str:
    image = f'<img class="cover" itemprop="image" src="{COVERS[RESOURCES.index(resource)]}">' if resource != RESOURCES[2] else ""
    return (
        '<article itemscope itemtype="https://schema.org/Book">'
        f'<h1 itemprop="name">Synthetic {resource}</h1>{image}</article>'
    )


def png(rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, value: bytes) -> bytes:
        body = kind + value
        return (struct.pack(">I", len(value)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))
    width, height = 32, 48
    row = bytes([0] + list(rgb) * width)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height))
            + chunk(b"IEND", b""))


def walk(widget: Gtk.Widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("GTK4/X11/Xvfb required")

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)

    class Client:
        def clear(self): pass
        def close(self): pass

    session = bnf.BnfProviderSession(
        client=Client(), credential_id="bnf-e2e", label="BnF synthetic",
        credentials={"username": "synthetic", "password": "synthetic"},
        options={},
    )
    parsed = parse_search_page(SEARCH_HTML).records
    assert len(parsed) == 3
    assert tuple(row.thumbnail_url for row in parsed) == (*COVERS, None)
    reads: list[str] = []
    details: list[str] = []
    applied_details: list[str] = []
    image_gets: list[str] = []
    images = {COVERS[0]: png((225, 0, 0)), COVERS[1]: png((0, 0, 225))}
    stages = ["startup"]
    patch_search = patch.object(
        bnf, "read_search",
        side_effect=lambda _client, query, *, is_current: (
            reads.append("search") or parsed, ()
        ),
    )
    patch_detail = patch.object(
        bnf, "read_detail",
        side_effect=lambda _client, resource: (
            details.append(resource)
            or parse_detail(
                detail_html(resource), page_url=ORIGIN + "/resources/" + resource,
            ).records[0]
        ),
    )
    patch_search.start()
    patch_detail.start()

    def image_response(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method != "GET" or url not in images:
            raise AssertionError("Unexpected outbound image request")
        image_gets.append(url)
        return httpx.Response(
            200, headers={"Content-Type": "image/png"},
            content=images[url], request=request,
        )

    def search_progressive(text, scope="all", force=False, *, provider_ids=(),
                           field="all", content_types=(), on_batch=None,
                           control=None):
        assert force is True and text == "Synthetic"
        assert not provider_ids and not content_types
        results = tuple(session.search(text, scope="all", field="all"))
        if on_batch is not None:
            on_batch(CatalogSearchBatch(
                provider_id="bnf-pret", provider_label="BnF",
                results=results, result_status="live",
            ))
        return CatalogSearchSummary(
            query=text, result_count=len(results), providers_total=1,
            providers_completed=1, providers_failed=0, warnings=(),
        )

    core.library.search_progressive = search_progressive
    core.library.holdings = lambda item, _query="", *, force=False: session.holdings(item)
    report = {
        "scenario": "bnf-covers-1366", "status": "RUNNING",
        "backend": "GTK4/X11, BnF HTML parser + provider adapter, HTTPX MockTransport",
        "checks": {}, "snapshots": [],
    }
    app_window = [None]
    device = [None]
    result = [1]
    started = monotonic()
    original_client = [None]

    def save():
        report["duration_seconds"] = round(monotonic() - started, 2)
        (out / "screen.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )

    def capture(stage):
        screenshot = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert screenshot.size == (1366, 900), "Unexpected Xvfb geometry"
        assert screenshot.getbbox() is not None, "Blank GTK surface"
        filename = out / (stage + ".png")
        screenshot.save(filename)
        shutil.copyfile(filename, out / "screen.png")
        report["snapshots"].append({
            "stage": stage, "sha256": hashlib.sha256(filename.read_bytes()).hexdigest(),
        })

    def fail():
        if report["status"] != "RUNNING":
            return
        report["status"] = "FAIL"
        report["last_stage"] = stages[-1]
        save()
        if app_window[0] is not None:
            app.quit()

    def later(milliseconds, callback):
        def safe():
            try:
                callback()
            except Exception:
                fail()  # No raw URL, file or token in public failure artifacts.
            return False
        GLib.timeout_add(milliseconds, safe)

    def await_condition(label, predicate, then, *, timeout=11.0):
        stages.append(label)
        deadline = monotonic() + timeout
        def poll():
            if predicate():
                then()
            elif monotonic() >= deadline:
                fail()
            else:
                later(110, poll)
        later(180, poll)

    def visible(url):
        assert app_window[0] is not None
        return [cover for cover in walk(app_window[0].search_page)
                if isinstance(cover, CoverView) and cover._url == url
                and cover.get_mapped() and cover.picture.get_paintable() is not None
                and not cover.placeholder.get_visible()]

    def selected_detail():
        return [cover for cover in walk(app_window[0].search_page.detail)
                if isinstance(cover, CoverView)]

    def search_ready():
        window = app_window[0]
        assert tuple(window.search_page.store.get_item(i).thumbnail_url
                     for i in range(3)) == (*COVERS, None)
        assert len(reads) == 1
        await_condition(
            "two_distinct_list_covers",
            lambda: bool(visible(COVERS[0])) and bool(visible(COVERS[1])),
            first_detail,
        )

    def first_detail():
        window = app_window[0]
        assert len(set(image_gets)) == 2 and set(image_gets) == set(COVERS)
        report["checks"]["two_real_gtk_search_images"] = True
        capture("01-bnf-search-two-covers")
        window.search_page.selection.set_selected(0)
        await_condition(
            "first_detail", lambda: bool(selected_detail())
            and applied_details and applied_details[-1] == RESOURCES[0]
            and selected_detail()[0].get_mapped()
            and selected_detail()[0]._url == COVERS[0]
            and selected_detail()[0].picture.get_paintable() is not None,
            second_detail,
        )

    def second_detail():
        report["checks"]["first_real_gtk_detail_cover"] = True
        capture("02-bnf-first-detail")
        app_window[0].search_page.selection.set_selected(1)
        await_condition(
            "second_detail", lambda: bool(selected_detail())
            and applied_details and applied_details[-1] == RESOURCES[1]
            and selected_detail()[0].get_mapped()
            and selected_detail()[0]._url == COVERS[1]
            and selected_detail()[0].picture.get_paintable() is not None,
            missing_detail,
        )

    def missing_detail():
        report["checks"]["second_real_gtk_detail_cover"] = True
        capture("03-bnf-second-detail")
        app_window[0].search_page.selection.set_selected(2)
        await_condition(
            "missing_placeholder",
            lambda: bool(selected_detail())
            and applied_details and applied_details[-1] == RESOURCES[2]
            and selected_detail()[0].get_mapped()
            and selected_detail()[0]._url is None
            and selected_detail()[0].picture.get_paintable() is None
            and selected_detail()[0].placeholder.get_visible(),
            finish,
        )

    def finish():
        capture("04-bnf-missing-cover-placeholder")
        assert set(image_gets) == set(COVERS)
        assert len(image_gets) == 2, "Search and detail must share cached public images"
        assert details[-3:] == list(RESOURCES), "Provider detail scoped to each resource"
        assert applied_details[-3:] == list(RESOURCES), "Real GTK applied all scoped details"
        assert app_window[0].covers.stats()["ui_applied"] >= 4
        report["checks"]["missing_cover_no_image_get"] = True
        report["checks"]["cache_shared_search_detail"] = True
        report["checks"]["no_credential_or_token_network"] = True
        report["status"] = "PASS"
        result[0] = 0
        save()
        app.quit()

    def click_search():
        window = app_window[0]
        assert window.search_global_button.get_mapped()
        assert window.search_global_button.get_sensitive()
        device[0].click_widget(window.search_global_button)
        await_condition(
            "search_results",
            lambda: window.search_page.store.get_n_items() == 3,
            search_ready,
        )

    def begin():
        window = app_window[0]
        window.navigate("search")
        window.search_controls.set_state(
            QueryState(text="Synthetic", field="title", scope="all"),
            notify=False,
        )
        # Search route uses a real GTK stack transition: do not XTEST click
        # while its button is still unmapped by the crossfade.
        later(350, lambda: await_condition(
            "global_button_visible",
            lambda: window.search_global_button.get_mapped()
            and window.search_global_button.get_sensitive(),
            click_search,
        ))

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.BnfCovers4118")

        def do_activate(self):
            window = MainWindow(self, core.config, core.library, core.diagnostics)
            app_window[0] = window
            # Do not accept an initial search thumbnail as proof that the
            # provider's real holdings response reached the GTK detail sink.
            actual_apply = window._apply_search_detail

            def traced_apply(*args, **kwargs):
                actual_apply(*args, **kwargs)
                if (len(args) >= 2
                        and window._selected_search_id == args[0]):
                    applied_details.append(args[1].record_id)

            window._apply_search_detail = traced_apply
            window.set_default_size(1326, 780)
            # Install the test transport before the first GTK frame can bind
            # a list cell: even initial fixtures cannot start a real cover GET.
            original_client[0] = window.covers.client
            window.covers.client = httpx.Client(
                transport=httpx.MockTransport(image_response), trust_env=False,
            )
            original_client[0].close()
            window.present()
            device[0] = X11Input()
            later(500, begin)

        def do_shutdown(self):
            if device[0] is not None:
                device[0].close()
            patch_detail.stop()
            patch_search.stop()
            try:
                session.close()
            finally:
                core.close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(
        28, lambda: (fail(), False)[1]
        if report["status"] == "RUNNING" else False,
    )
    try:
        app.run([sys.argv[0]])
    except Exception:
        fail()
    if not (out / "screen.json").is_file():
        report["status"] = "FAIL"
        report["last_stage"] = "startup"
        save()
    return result[0]


if __name__ == "__main__":
    raise SystemExit(main())
