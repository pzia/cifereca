"""HTTP contracts of the real Milan jeunesse adapter against local-only fixtures."""
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
for path in (SIM, REPO / "src/plugins/youth-source-milan"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import httpx
import datasetctl
from plugin_coverage import build_report
from milan.pseudo_milan_service import Handler, MilanFakeApp
from milan_youth import CATALOG, MilanYouthSource


class LoopbackMilan:
    def __init__(self):
        Handler.app = MilanFakeApp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.root = f"http://127.0.0.1:{self.server.server_port}"
        self.seen = []

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def client(self):
        def forward(request):
            self.seen.append(str(request.url))
            if request.url.scheme != "https" or request.url.host not in (
                "www.editionsmilan.com", "editionsmilan.com"
            ):
                raise AssertionError(f"External host rejected: {request.url}")
            local = self.root + "/milan" + request.url.raw_path.decode("ascii")
            try:
                with urlopen(Request(local), timeout=3) as r:
                    status, headers, body = r.status, dict(r.headers), r.read()
            except HTTPError as e:
                status, headers, body = e.code, dict(e.headers), e.read()
            return httpx.Response(status, headers=headers, content=body, request=request)
        return httpx.Client(transport=httpx.MockTransport(forward), trust_env=False)


class MilanContractTests(unittest.TestCase):
    def test_registered_fixture_does_not_auto_activate_plugin(self):
        catalog = datasetctl.load_catalog()
        spec = catalog["milan"]
        self.assertEqual(spec["plugins"], [])
        self.assertEqual(spec["fixture_packages"], ["youth-source-milan"])
        for profile_name in ("ux-small", "ux-all", "ux-errors"):
            _, profile = datasetctl.load_profile(profile_name)
            self.assertIn("milan", datasetctl.enabled_service_configs(profile))
            self.assertNotIn("youth.source.milan", datasetctl.derived_plugins(profile, catalog))
        matrix = {row["package"]: row for row in build_report()["packages"]}
        self.assertIn("milan", matrix["youth-source-milan"]["http_fixtures"])
        self.assertEqual(matrix["youth-source-milan"]["status"], "partial")
        self.assertFalse(matrix["youth-source-milan"]["e2e_gtk_verified"])

    def test_true_milan_adapter_fetches_product_details_and_filters_by_attested_age(self):
        with LoopbackMilan() as srv, srv.client() as client:
            plugin = MilanYouthSource(client=client)
            all_page = plugin.browse(limit=8)
            self.assertEqual([row["title"] for row in all_page["items"]],
                             ["Moqueuse", "L'escargot", "Notice fictive sept", "Notice fictive huit"])
            # Nine distinct product links are published. The real adapter must
            # inspect *exactly* eight, including rejected sheets, plus listing.
            self.assertEqual(len(srv.seen), 9)
            self.assertEqual(srv.seen[0], CATALOG)
            self.assertEqual(len(set(srv.seen)), 9)
            self.assertTrue(srv.seen[-1].endswith("/99994-jeunesse-huit/"))
            self.assertFalse(any("99993-jeunesse-neuf" in url for url in srv.seen))
            self.assertEqual(all_page["next_cursor"], "2")
            self.assertEqual(all_page["items"][0]["authors"], ("Nathalie Somers",))
            self.assertEqual(all_page["items"][0]["isbn13"], "9782408020804")
            self.assertEqual(all_page["items"][0]["age_ranges"][0]["age_min"], 8)
            self.assertEqual(all_page["items"][0]["metadata"]["age_evidence"],
                             "milan.product.explicit")
            self.assertEqual(all_page["items"][1]["age_ranges"][0]["age_min"], 1)
            self.assertTrue(all(u.startswith(CATALOG) for u in srv.seen))
            # The no-age record is ignored; a second, attested record makes
            # genuine forward pagination observable.
            page2 = plugin.browse(cursor=all_page["next_cursor"])
            self.assertEqual([row["title"] for row in page2["items"]],
                             ["Notice fictive page deux"])
            self.assertEqual(page2["items"][0]["age_ranges"][0]["age_min"], 12)
            self.assertEqual(page2["next_cursor"], "")
            self.assertEqual([r["title"] for r in plugin.browse(age=4)["items"]],
                             ["L'escargot"])
            self.assertEqual([r["title"] for r in plugin.search("Moqueuse")["items"]],
                             ["Moqueuse"])
            self.assertEqual(plugin.search("no-matching-title")["items"], ())
            with self.assertRaises(ValueError):
                plugin.browse(cursor="NaN")
            with self.assertRaises(ValueError):
                plugin.browse(cursor="101")
            with self.assertRaises(ValueError):
                plugin._get("https://evil.example/livres/", kind="listing")

    def test_errors_are_served_and_rejected_by_real_plugin(self):
        with LoopbackMilan() as srv, srv.client() as client:
            plugin = MilanYouthSource(client=client)
            with self.assertRaisesRegex(RuntimeError, "503"):
                plugin._get(CATALOG + "?fault=unavailable", kind="listing")
            with self.assertRaisesRegex(RuntimeError, "not HTML"):
                plugin._get(CATALOG + "?fault=wrong-type", kind="listing")
            with self.assertRaisesRegex(RuntimeError, "2 MB"):
                plugin._get(CATALOG + "?fault=oversized", kind="listing")
            with self.assertRaisesRegex(RuntimeError, "404"):
                plugin._get(CATALOG + "22222-missing/", kind="product")
            with urlopen(srv.root + "/health") as response:
                health = json.load(response)
            self.assertEqual(health["products"], 11)
            self.assertTrue(health["synthetic"])
            self.assertEqual(health["seed"], 20261009)

    def test_reproducible_http_fixtures(self):
        with LoopbackMilan() as srv:
            with urlopen(srv.root + "/milan/livres/") as a:
                content = a.read()
            with urlopen(srv.root + "/milan/livres/") as b:
                self.assertEqual(content, b.read())

    def test_missing_isbn_truncated_product_and_pagination_are_distinct(self):
        with LoopbackMilan() as srv, srv.client() as client:
            plugin = MilanYouthSource(client=client)
            missing = plugin._get(CATALOG + "99997-sans-isbn/", kind="product")
            truncated = plugin._get(CATALOG + "99996-tronque/", kind="product")
            from milan_youth import parse_detail
            self.assertNotIn("Code ISBN", missing)
            self.assertIsNone(parse_detail(missing, url=CATALOG + "99997-sans-isbn/"))
            self.assertFalse(truncated.endswith("</html>"))
            self.assertIsNone(parse_detail(truncated, url=CATALOG + "99996-tronque/"))
            page2 = plugin.browse(cursor="2", age=12)
            self.assertEqual(len(page2["items"]), 1)
            self.assertEqual(page2["items"][0]["title"], "Notice fictive page deux")
            self.assertTrue(all(u.startswith(CATALOG) for u in srv.seen))


if __name__ == "__main__":
    unittest.main()
