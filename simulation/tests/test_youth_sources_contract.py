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
for p in (SIM, REPO / "src/plugins/youth-bibliography-openlibrary"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import httpx
from youth_sources.pseudo_youth_sources_service import Handler, YouthSourcesApp
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
            if host == "openlibrary.org":
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
            {"youth-bibliography-openlibrary"})
        for profile_name in ("ux-small", "ux-all", "ux-errors"):
            _, profile = datasetctl.load_profile(profile_name)
            self.assertIn("youth_sources", datasetctl.enabled_service_configs(profile))
            forced = datasetctl.derived_plugins(profile, catalog)

    def test_http_health_and_explicit_empty_404(self):
        with LoopbackServer() as server:
            status, _, body = server.get("/health")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["books"], 3)
            with self.assertRaises(HTTPError) as raised:
                server.get("/openlibrary/works/no-such-book.json")
            self.assertEqual(raised.exception.code, 404)

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
