"""Vrais adaptateurs jeunesse + serveur HTTP local, aucun contact Internet."""
from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
for p in (SIM, REPO / "src/plugins/youth-source-usborne",
          REPO / "src/plugins/youth-source-ecole-loisirs"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import httpx
from youth_publishers.pseudo_youth_publishers_service import Handler, FakePublishers
from usborne_youth import UsborneYouthSource, BROWSE
from ecole_loisirs_youth import EcoleLoisirsYouthSource, SEARCH
import datasetctl


class Loopback:
    def __init__(self):
        Handler.app = FakePublishers()
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

    def httpx_client(self):
        def forward(request):
            self.seen.append(str(request.url))
            if request.url.host in ("usborne.com", "www.usborne.com"):
                prefix = "/publishers/usborne"
            elif request.url.host == "www.ecoledesloisirs.fr":
                prefix = "/publishers/ecole"
            else:
                raise AssertionError("External host not in test allowlist")
            url = self.root + prefix + request.url.raw_path.decode("ascii")
            try:
                with urlopen(Request(url), timeout=3) as reply:
                    status, headers, payload = reply.status, dict(reply.headers), reply.read()
            except HTTPError as error:
                status, headers, payload = error.code, dict(error.headers), error.read()
            return httpx.Response(status, headers=headers, content=payload, request=request)
        return httpx.Client(transport=httpx.MockTransport(forward), trust_env=False)

    def health(self):
        with urlopen(self.root + "/health", timeout=3) as r:
            return json.loads(r.read())


class PublisherFixturesTests(unittest.TestCase):
    def test_profile_and_matrix_track_fixture_without_activation(self):
        catalog = datasetctl.load_catalog()
        service = catalog["youth_publishers"]
        self.assertEqual(service["plugins"], [])
        self.assertEqual(set(service["fixture_packages"]),
                         {"youth-source-usborne", "youth-source-ecole-loisirs", "youth-source-milan"})
        for name in ("ux-small", "ux-all", "ux-errors"):
            _, profile = datasetctl.load_profile(name)
            self.assertIn("youth_publishers", datasetctl.enabled_service_configs(profile))
            forced = datasetctl.derived_plugins(profile, catalog)
            self.assertNotIn("youth.source.usborne", forced)
            self.assertNotIn("youth.source.ecole-loisirs", forced)

    def test_usborne_real_adapter_filters_months_years_and_pagination(self):
        with Loopback() as server, server.httpx_client() as client:
            self.assertTrue(server.health()["synthetic"])
            plugin = UsborneYouthSource(client=client)
            p1 = plugin.browse(age=3)
            self.assertEqual([r["title"] for r in p1["items"]],
                             ["Why School Matters", "Baby Book"])
            self.assertEqual(p1["next_cursor"], "2")
            baby = p1["items"][1]
            self.assertEqual(baby["metadata"]["age_min_months"], 18)
            self.assertEqual(baby["age_ranges"][0]["age_min"], 2)
            self.assertEqual(baby["age_ranges"][0]["confidence"], 0.8)
            self.assertEqual(baby["age_ranges"][0]["source_label"], "Age: 18+ months")
            self.assertTrue(server.seen[0].startswith(BROWSE + "/3-5-years"))
            p2 = plugin.browse(cursor=p1["next_cursor"])
            self.assertEqual([r["title"] for r in p2["items"]], ["Older Book"])
            self.assertEqual(p2["next_cursor"], "")
            self.assertEqual([r["title"] for r in plugin.search("school")["items"]],
                             ["Why School Matters"])
            with self.assertRaises(ValueError):
                plugin.browse(cursor="not-a-page")
            with self.assertRaisesRegex(RuntimeError, "503"):
                plugin._get(BROWSE + "?fault=503")
            with self.assertRaisesRegex(RuntimeError, "not HTML"):
                plugin._get(BROWSE + "?fault=wrong-type")
            with self.assertRaisesRegex(RuntimeError, "2 MB"):
                plugin._get(BROWSE + "?fault=oversized")

    def test_ecole_real_adapter_preserves_explicit_unknown_age_and_pagination(self):
        with Loopback() as server, server.httpx_client() as client:
            plugin = EcoleLoisirsYouthSource(client=client)
            p1 = plugin.search("loup")
            self.assertEqual([r["title"] for r in p1["items"]],
                             ["L'histoire de Petit Loup", "Roman ado"])
            self.assertEqual(p1["next_cursor"], "2")
            first, second = p1["items"]
            self.assertEqual(first["isbn13"], "9782211348171")
            self.assertEqual(first["age_ranges"][0]["age_min"], 3)
            self.assertEqual(first["age_ranges"][0]["age_max"], 6)
            self.assertEqual(second["age_ranges"][0]["age_min"], 13)
            self.assertTrue(server.seen[0].startswith(SEARCH + "?"))
            page2 = plugin.search("loup", cursor=p1["next_cursor"])
            self.assertEqual([r["title"] for r in page2["items"]], ["Sans âge"])
            self.assertEqual(page2["items"][0]["age_ranges"], [])
            self.assertEqual(page2["items"][0]["metadata"]["age_label"], "indéterminé")
            self.assertEqual(page2["next_cursor"], "")
            self.assertEqual(plugin.search("aucun")["items"], ())
            with self.assertRaises(ValueError):
                plugin.search("loup", cursor="bad")
            with self.assertRaisesRegex(RuntimeError, "503"):
                plugin._get(SEARCH + "?fault=503")
            with self.assertRaisesRegex(RuntimeError, "not HTML"):
                plugin._get(SEARCH + "?fault=wrong-type")
            with self.assertRaisesRegex(RuntimeError, "2 MB"):
                plugin._get(SEARCH + "?fault=oversized")


if __name__ == "__main__":
    unittest.main()
