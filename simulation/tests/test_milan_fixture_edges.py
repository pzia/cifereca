"""Self-tests for the Milan localhost HTTP fixture (no plugin imports)."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

SIM = Path(__file__).resolve().parents[1]
if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))

from milan.pseudo_milan_service import (
    Handler, MilanFakeApp, PAGES, PRODUCTS, detail, fictional_isbn, listing,
)


class MilanFixtureSelfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = type("MilanFixtureHandler", (Handler,), {"app": MilanFakeApp()})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.root = f"http://127.0.0.1:{cls.server.server_port}"
        cls.opener = build_opener(ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def response(self, suffix: str):
        try:
            reply = self.opener.open(self.root + suffix, timeout=3)
        except HTTPError as error:
            reply = error
        with reply:
            return reply.status, reply.headers.get("content-type", ""), reply.read()

    def test_nine_distinct_published_product_links_and_positive_page_two(self):
        self.assertEqual(len(PAGES[1]), 9)
        self.assertEqual(len(set(PAGES[1])), 9)
        self.assertEqual(len(PAGES[2]), 2)
        self.assertIn("77777-jeunesse-page-deux", PAGES[2])
        self.assertIn("88888-sans-age", PAGES[2])
        first = listing(1)
        second = listing(2)
        self.assertIn(b"/livres/page/2/", first)
        self.assertIn(b"jeunesse-neuf", first)
        self.assertIn(b"jeunesse-page-deux", second)
        self.assertNotIn(b"/livres/page/3/", second)
        self.assertEqual(self.response("/milan/livres/")[2], first)
        self.assertEqual(self.response("/milan/livres/page/2/")[2], second)
        self.assertEqual(self.response("/milan/livres/page/2/")[2], second)

    def test_absent_invalid_and_truncated_are_three_different_cases(self):
        self.assertEqual(PRODUCTS["99997-sans-isbn"]["isbn"], "")
        self.assertEqual(PRODUCTS["99998-mauvais-isbn"]["isbn"], "9780000000000")
        absent = detail("99997-sans-isbn")
        self.assertNotIn(b"Code ISBN", absent)
        self.assertIn(b"Code ISBN", detail("99998-mauvais-isbn"))
        truncated = detail("99996-tronque")
        self.assertFalse(truncated.endswith(b"</html>"))
        self.assertNotIn(b"Code ISBN", truncated)
        for slug, expected in (
            ("99997-sans-isbn", absent),
            ("99998-mauvais-isbn", detail("99998-mauvais-isbn")),
            ("99996-tronque", truncated),
        ):
            status, typ, actual = self.response(f"/milan/livres/{slug}/")
            self.assertEqual(status, 200)
            self.assertIn("html", typ)
            self.assertEqual(actual, expected)
        self.assertEqual(self.response("/milan/livres/12345-missing/")[0], 404)

    def test_synthetic_isbns_are_distinct_and_have_correct_checksums(self):
        published = [fictional_isbn(i) for i in range(6, 11)]
        self.assertEqual(len(set(published)), 5)
        for isbn in published:
            self.assertEqual(len(isbn), 13)
            self.assertTrue(isbn.isdecimal())
            total = sum(int(d) * (1 if position % 2 == 0 else 3)
                        for position, d in enumerate(isbn))
            self.assertEqual(total % 10, 0)

    def test_http_faults_are_deterministic_without_external_requests(self):
        for fault, status, mimetype in (
            ("unavailable", 503, "text/html"),
            ("wrong-type", 200, "application/json"),
            ("oversized", 200, "text/html"),
        ):
            first = self.response("/milan/livres/?fault=" + fault)
            second = self.response("/milan/livres/?fault=" + fault)
            self.assertEqual(first, second)
            self.assertEqual(first[0], status)
            self.assertIn(mimetype, first[1])
            if fault == "oversized":
                self.assertEqual(len(first[2]), 2_000_001)
        for path in ("/milan/livres/page/0/", "/milan/livres/page/101/",
                     "/milan/livres/page/NaN/"):
            self.assertEqual(self.response(path)[0], 404)


if __name__ == "__main__":
    unittest.main()
