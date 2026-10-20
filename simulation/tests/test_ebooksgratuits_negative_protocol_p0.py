"""P0 #771: deterministic negative EbooksGratuits HTTP fixtures on loopback."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile

from simulation.tests.test_fake_services_contract import RunningServer, load_module

EBOOKS = load_module("dataset_fake_ebooksgratuits_p0", "ebooksgratuits/pseudo_ebooksgratuits_service.py")


class EbooksGratuitsNegativeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cache = EBOOKS.DatasetCache(Path(self.directory.name) / "eg", 7, 20261020, True)
        app = EBOOKS.EbooksGratuitsApp(self.cache)
        handler = type("LocalEbooksGratuitsHandler", (EBOOKS.Handler,), {"app": app})
        self.context = RunningServer(handler, app)
        self.http = self.context.__enter__()

    def tearDown(self):
        try:
            self.context.__exit__(None, None, None)
        finally:
            self.directory.cleanup()

    def request(self, route, *, form=None):
        request = Request(
            self.http.base + route,
            method="POST" if form is not None else "GET",
            data=urlencode(form).encode("utf-8") if form is not None else None,
            headers={"Content-Type": "application/x-www-form-urlencoded"} if form is not None else {},
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def test_normal_get_post_zero_one_many_and_pagination(self):
        status, _, one = self.request("/ebooks.php?offset=1&begin=0")
        self.assertEqual(status, 200)
        self.assertEqual(len(re.findall(rb"details.php\?id=\d+", one)), 1)
        _, _, first = self.request("/ebooks.php?offset=3&begin=0")
        _, _, second = self.request("/ebooks.php?offset=3&begin=3")
        a = set(re.findall(rb"details.php\?id=\d+", first))
        b = set(re.findall(rb"details.php\?id=\d+", second))
        self.assertEqual((len(a), len(b)), (3, 3))
        self.assertFalse(a & b)
        status, _, empty = self.request("/ebooks.php", form={"titre": "no-such-fixture-12345"})
        self.assertEqual(status, 200)
        self.assertIn(b"0 r\xc3\xa9sultat", empty)
        self.assertNotIn(b'class="listeb"', empty)
        _, _, normal = self.request("/ebooks.php?offset=1")
        self.assertEqual(self.request("/ebooks.php?offset=1&ux_fault=unknown")[2], normal)

    def test_get_post_faults_isolated_and_health_unaffected(self):
        _, _, healthy = self.request("/ebooks.php?offset=2")
        for method in ("GET", "POST"):
            for fault, code in (("429", 429), ("503", 503), ("login_html", 200),
                                ("malformed_html", 200)):
                with self.subTest(method=method, fault=fault):
                    status, headers, body = self.request(
                        "/ebooks.php?ux_fault=" + fault,
                        form={"titre": "anything"} if method == "POST" else None,
                    )
                    self.assertEqual(status, code)
                    self.assertNotIn(b'class="listeb"', body)
                    if fault == "429":
                        self.assertEqual(headers.get("Retry-After"), "3")
                    if fault == "login_html":
                        self.assertIn(b"id='login'", body)
        self.assertEqual(self.request("/ebooks.php?offset=2")[2], healthy)
        status, _, health = self.request("/health?ux_fault=503")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(health)["books"], 7)

    def test_details_and_download_faults_never_create_artifact(self):
        self.assertEqual(self.request("/details.php?id=1")[0], 200)
        self.assertIn(b"id='login'",
                      self.request("/details.php?id=1&ux_fault=login_html")[2])
        epub = self.cache.epub_path(1)
        self.assertFalse(epub.exists())
        for fault, expected, mime in (
            ("429", 429, "text/plain"), ("503", 503, "text/plain"),
            ("expired_link", 410, "text/plain"), ("wrong_mime", 200, "text/html"),
            ("missing_format", 200, "text/html"),
            ("invalid_epub", 200, "application/epub+zip"),
        ):
            with self.subTest(fault=fault):
                status, headers, body = self.request(
                    "/newsendbook.php?id=1&ux_fault=" + fault
                )
                self.assertEqual(status, expected)
                self.assertEqual(headers.get_content_type(), mime)
                self.assertFalse(epub.exists())
                if fault == "429":
                    self.assertEqual(headers.get("Retry-After"), "3")
                if fault == "invalid_epub":
                    self.assertEqual(body, b"not a ZIP/EPUB")
        status, headers, payload = self.request("/newsendbook.php?id=1")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/epub+zip")
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            self.assertIn("mimetype", archive.namelist())
        self.assertTrue(epub.exists())

    def test_missing_routes_and_records_still_return_404(self):
        self.assertEqual(self.request("/does-not-exist?ux_fault=429")[0], 404)
        self.assertEqual(self.request("/details.php?id=999")[0], 404)
        self.assertEqual(self.request("/newsendbook.php?id=999")[0], 404)


if __name__ == "__main__":
    unittest.main()
