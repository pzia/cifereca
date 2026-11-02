"""#4118: BnF catalogue -> shared Core cover loader, two independent images.

Offline integration. Synthetic DOM and image bytes only; never contacts the
reader account, Feedbooks CDN or a remote image service. This is NOT a live
GTK/portal acceptance test.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from unittest import TestCase
from unittest.mock import patch
import struct
import sys
import zlib

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1]))

from cifereca_bnf_pret_web import ORIGIN, parse_detail, parse_search_page
from cifereca.gui import cover_loader


COVER_A = "https://covers.feedbooks.net/item/synthetic-alpha.jpg?size=medium&t=1762000000"
COVER_B = "https://covers.feedbooks.net/item/synthetic-beta.jpg?size=small&t=1762000001"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    data = kind + payload
    return struct.pack(">I", len(payload)) + data + struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)


def _png(rgb: tuple[int, int, int]) -> bytes:
    # Valid 1x1 RGB PNG, decodable by the same GTK image codec as normal covers.
    row = bytes((0, *rgb))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(row))
        + _chunk(b"IEND", b"")
    )


def _card(resource: str, title: str, cover: str) -> str:
    return (
        '<li class="list-details__item"><article class="view-details" itemscope '
        'itemtype="https://schema.org/Book">'
        f'<a href="/resources/{resource}"><h3 class="details-content__title">{title}</h3></a>'
        f'<img class="cover" src="{cover}"></article></li>'
    )


class TwoCoverPipelineTests(TestCase):
    def test_two_scoped_catalogue_covers_download_cache_and_detail_match(self):
        html = '<ul class="list-details">' + (
            _card("synthetic-alpha", "Alpha", COVER_A)
            + _card("synthetic-beta", "Beta", COVER_B)
        ) + '</ul>'
        records = parse_search_page(html).records
        self.assertEqual(
            [(r.resource_id, r.thumbnail_url) for r in records],
            [("synthetic-alpha", COVER_A), ("synthetic-beta", COVER_B)],
        )
        for resource, cover in (("synthetic-alpha", COVER_A), ("synthetic-beta", COVER_B)):
            detail = (
                '<article itemscope itemtype="https://schema.org/Book">'
                f'<h1 itemprop="name">{resource}</h1>'
                f'<img class="cover" itemprop="image" src="{cover}"></article>'
            )
            result, = parse_detail(detail, page_url=ORIGIN + "/resources/" + resource).records
            self.assertEqual(result.thumbnail_url, cover)

        observed: list[str] = []
        images = {COVER_A: _png((220, 0, 0)), COVER_B: _png((0, 0, 220))}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            observed.append(url)
            self.assertEqual(request.method, "GET")
            self.assertIn(url, images)
            return httpx.Response(200, headers={"Content-Type": "image/png"}, content=images[url])

        with TemporaryDirectory() as root, patch.object(cover_loader, "cache_dir", return_value=Path(root)):
            loader = cover_loader.CoverLoader(base_url=ORIGIN, max_workers=2)
            old_client = loader.client
            loader.client = httpx.Client(transport=httpx.MockTransport(handler))
            old_client.close()
            try:
                done = Event()
                mutex = Lock()
                found: dict[str, Path | None] = {}

                def callback_for(url: str):
                    def complete(path):
                        with mutex:
                            found[url] = path
                            if len(found) == 2:
                                done.set()
                    return complete

                for record in records:
                    loader.load(record.thumbnail_url, callback_for(record.thumbnail_url))
                self.assertTrue(done.wait(5.0), "both independent cover callbacks must complete")
                self.assertEqual(set(observed), set(images))
                self.assertEqual(loader.stats()["download_ok"], 2)
                self.assertEqual(len({str(p) for p in found.values()}), 2)
                for url, path in found.items():
                    self.assertIsNotNone(path)
                    self.assertEqual(path.read_bytes(), images[url])

                cached = []
                for record in records:
                    loader.load(record.thumbnail_url, cached.append)
                self.assertEqual(cached, [found[COVER_A], found[COVER_B]])
                self.assertEqual(len(observed), 2, "second projection must be cache-only")
                self.assertEqual(loader.stats()["cache_hit_valid"], 2)

                unknown = []
                loader.load(None, unknown.append)
                self.assertEqual(unknown, [None], "absent image must remain placeholder")
                self.assertEqual(len(observed), 2)
            finally:
                loader.close()
