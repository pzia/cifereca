"""Contract tests: real youth plugins -> HTTPX injection -> real loopback server.

External hosts remain allowlisted by the plugins; nothing reaches the internet.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
for p in (SIM, REPO / "src/plugins/youth-source-booktrust",
          REPO / "src/plugins/youth-bibliography-openlibrary"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import httpx
from youth_sources.pseudo_youth_sources_service import Handler, YouthSourcesApp
from booktrust_youth import BookTrustSource, BOOKFINDER
from openlibrary_youth import OpenLibraryBibliography
import datasetctl


class LoopbackServer:
    def __init__(self):
        Handler.app = YouthSourcesApp(seed=20261009)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.received = []

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def get(self, path):
        with urlopen(self.url + path, timeout=3) as response:
            return response.status, response.headers, response.read()

    def injected_httpx(self):
        def adapter(request):
            self.received.append(str(request.url))
            host = request.url.host
            if host in ("booktrust.org.uk", "www.booktrust.org.uk"):
                prefix = "/booktrust"
            elif host == "openlibrary.org":
                prefix = "/openlibrary"
            else:
                raise AssertionError(f"Unapproved external host: {host}")
            url = self.url + prefix + request.url.raw_path.decode("ascii")
            try:
                with urlopen(Request(url, headers={"Accept": "application/json, text/html"}), timeout=3) as res:
                    status, headers, content = res.status, dict(res.headers), res.read()
            except HTTPError as error:
                status, headers, content = error.code, dict(error.headers), error.read()
            return httpx.Response(status, headers=headers, content=content, request=request)
        return httpx.Client(transport=httpx.MockTransport(adapter), trust_env=False)


class YouthSourcesContractTests(unittest.TestCase):
    def test_simulator_registered_but_does_not_force_real_plugins(self):
        catalog = datasetctl.load_catalog()
        self.assertEqual(catalog["youth_sources"]["plugins"], [])
        self.assertEqual(
            set(catalog["youth_sources"]["fixture_packages"]),
            {"youth-source-booktrust", "youth-bibliography-openlibrary"})
        for profile_name in ("ux-small", "ux-all", "ux-errors"):
            _, profile = datasetctl.load_profile(profile_name)
            self.assertIn("youth_sources", datasetctl.enabled_service_configs(profile))
            forced = datasetctl.derived_plugins(profile, catalog)
            self.assertNotIn("youth.source.booktrust", forced)

    def test_http_health_and_explicit_empty_404(self):
        with LoopbackServer() as server:
            status, _, body = server.get("/health")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["books"], 3)
            with self.assertRaises(HTTPError) as raised:
                server.get("/booktrust/book-recommendations/bookfinder/no-such-book/")
            self.assertEqual(raised.exception.code, 404)

    def test_real_booktrust_search_pagination_age_filters_detail_and_error(self):
        with LoopbackServer() as server, server.injected_httpx() as client:
            plugin = BookTrustSource(client=client)
            selected = plugin.browse(age=10, themes=["Adventure"], cursor="1")
            self.assertEqual([i["title"] for i in selected["items"]], ["The Firefly Atlas"])
            self.assertEqual(selected["next_cursor"], "", "Une unique fiche filtrée ne doit pas inventer une seconde page")
            self.assertIn("ageGroups=9+to+11+years", server.received[0])
            self.assertIn("bookfinderCategories%5B%5D=Adventure", server.received[0])
            page = plugin.browse(cursor="1")
            self.assertEqual([i["title"] for i in page["items"]],
                             ["The Firefly Atlas", "The Garden of Words"])
            self.assertEqual(page["next_cursor"], "2")
            two = plugin.browse(cursor=page["next_cursor"])
            self.assertEqual([i["title"] for i in two["items"]], ["The Last Shore"])
            last = plugin.browse(cursor=two["next_cursor"])
            self.assertEqual(last["items"], [])
            self.assertEqual(last["next_cursor"], "")
            record = plugin.detail(BOOKFINDER + "atlas-des-lucioles/")
            self.assertEqual(record["interest_age"], "8 to 10")
            self.assertEqual(record["reading_age"], "9 to 11")
            self.assertEqual(record["metadata"]["age_evidence"], "booktrust.detail.explicit")
            self.assertEqual([i["title"] for i in plugin.search("garden")["items"]], ["The Garden of Words"])
            with self.assertRaises(ValueError):
                plugin.detail("https://example.org/elsewhere")
            # Passing explicit fault simulates the site's HTTP error; do not
            # change the real plugin's domain or allowlist to test it.
            with self.assertRaises(RuntimeError):
                plugin._get_text(BOOKFINDER + "bookfinder-recommendations/?fault=unavailable")

    def test_real_openlibrary_isbn_olid_pagination_cache_rate_limits_and_ages(self):
        with LoopbackServer() as server, server.injected_httpx() as client:
            plugin = OpenLibraryBibliography(
                client=client, clock=lambda: 100, sleeper=lambda _: None
            )
            rows = plugin.search("atlas", limit=50, page=1)
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row.age_ranges == () for row in rows))
            self.assertTrue(all(row.metadata["age_evidence"] == "none" for row in rows))
            self.assertIn("limit=20", server.received[0])
            matched = plugin.lookup_isbn("0140328726")
            self.assertEqual([record.title for record in matched], ["The Firefly Atlas"])
            self.assertEqual(plugin.lookup_isbn("invalid"), ())
            self.assertEqual(plugin.search("atlas", page=2), ())
            work = plugin.lookup_olid("OL45804W")
            self.assertEqual(work[0].record_id, "OL45804W")
            self.assertEqual(work[0].age_ranges, ())
            self.assertEqual(plugin.lookup_olid("OL45804W"), work)
            edition = plugin.lookup_olid("OL7353617M")
            self.assertEqual(edition[0].isbns, ("9780140328721",))
            self.assertEqual(plugin.lookup_olid("OL999999W"), ())
            with self.assertRaisesRegex(RuntimeError, "429"):
                plugin.search("rate-limit")
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                plugin.search("broken-json")

    def test_http_simulation_is_identical_for_same_seed(self):
        with LoopbackServer() as server:
            _, _, first = server.get("/openlibrary/search.json?q=atlas&limit=20&page=1")
            _, _, second = server.get("/openlibrary/search.json?q=atlas&limit=20&page=1")
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
