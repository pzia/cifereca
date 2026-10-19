from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from xml.etree import ElementTree as ET
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
for path in (SIM, REPO / "src/plugins/literary-criticism"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from editorial.pseudo_editorial_service import EditorialFakeApp, Handler
from criticism_feeds import FeedSource, discover_url, parse_feed
import datasetctl
import plugin_scenarios


PUBLIC_FEED_BASE = "https://editorial-fixtures.test"


class Serving:
    def __init__(self, app):
        Handler.app = app
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.root = "http://127.0.0.1:" + str(self.server.server_port)
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def body(self, path):
        with urlopen(self.root + path, timeout=3) as response:
            return response.status, response.headers, response.read()

    def fake_fetch(self, url):
        # The real feed plugin rejects localhost by design. Expose a public-
        # shaped HTTPS URL to the plugin, and route it to this isolated fixture
        # only inside the explicit test fetch callback (never through DNS).
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "editorial-fixtures.test":
            raise AssertionError("unapproved simulated feed URL")
        local = self.root + parsed.path + (("?" + parsed.query) if parsed.query else "")
        with urlopen(local, timeout=3) as response:
            return url, response.read()


class EditorialHttpServiceTests(unittest.TestCase):
    def test_profiles_include_edited_service_without_auto_activating_plugins(self):
        catalog = datasetctl.load_catalog()
        service = catalog["editorial"]
        self.assertEqual(service["plugins"], [])
        self.assertEqual(service["fixture_packages"],
                         ["literary-criticism", "constellations"])
        from plugin_coverage import build_report
        matrix = {r["package"]: r for r in build_report()["packages"]}
        for package in service["fixture_packages"]:
            self.assertIn("editorial", matrix[package]["http_fixtures"])
            self.assertNotIn("editorial", matrix[package]["services"])
        for name in ("ux-small", "ux-all", "ux-errors"):
            _, profile = datasetctl.load_profile(name)
            self.assertIn("editorial", datasetctl.enabled_service_configs(profile))
            self.assertNotIn("criticism.source.rss", datasetctl.derived_plugins(profile, catalog))

    def test_real_feed_source_consumes_rss_and_json_feed_on_loopback(self):
        with tempfile.TemporaryDirectory() as tmp, Serving(
            EditorialFakeApp(Path(tmp), 20261008)
        ) as server:
            status, headers, health = server.body("/health")
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("Cache-Control"), "no-store")
            data = json.loads(health)
            self.assertTrue(data["ok"] and data["synthetic"])
            self.assertEqual(data["counts"]["youth"], 7)

            for suffix in ("/criticism/rss.xml", "/criticism/jsonfeed.json"):
                with self.subTest(suffix=suffix):
                    url = PUBLIC_FEED_BASE + suffix
                    source = FeedSource(url, fetch=server.fake_fetch)
                    self.assertTrue(source.source_info()["source_id"].startswith("feed."))
                    result = source.collect(limit=50)
                    self.assertGreater(len(result["items"]), 0)
                    self.assertEqual(result["items"][0]["book"]["isbns"], [plugin_scenarios.ISBN_A])
                    self.assertGreater(result["fresh_for_seconds"], 0)

            candidates = discover_url(PUBLIC_FEED_BASE + "/criticism/index.html",
                                      fetch=server.fake_fetch)
            urls = [item.url for item in candidates]
            self.assertIn(PUBLIC_FEED_BASE + "/criticism/rss.xml", urls)
            self.assertIn(PUBLIC_FEED_BASE + "/criticism/jsonfeed.json", urls)

    def test_exposes_bounded_jsons_and_deterministic_error_cases(self):
        with tempfile.TemporaryDirectory() as tmp, Serving(
            EditorialFakeApp(Path(tmp), 20261008)
        ) as server:
            for path, key, size in (
                ("/youth/records.json", "records", 7),
                ("/constellations/records.json", "records", 10),
                ("/criticism/cases.json", "sources", 4),
                ("/youth/discovery.json", "search_results", 7),
                ("/youth/enrichment.json", "cases", 6),
            ):
                status, headers, data = server.body(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers.get_content_type(), "application/json")
                self.assertEqual(len(json.loads(data)[key]), size)

            _, _, empty = server.body("/criticism/empty.xml")
            self.assertEqual(parse_feed(empty, server.root + "/criticism/empty.xml"), [])
            _, _, malformed = server.body("/criticism/invalid.xml")
            with self.assertRaises(ET.ParseError):
                parse_feed(malformed, server.root + "/criticism/invalid.xml")
            for path, status in (
                ("/criticism/unavailable", 503),
                ("/unknown", 404),
                ("/../../etc/passwd", 404),
            ):
                with self.subTest(path=path), self.assertRaises(HTTPError) as raised:
                    server.body(path)
                self.assertEqual(raised.exception.code, status)

    def test_generation_rebuild_and_manifest_stay_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            a = EditorialFakeApp(directory, 20261008)
            before = dict(a.manifest["files"])
            (a.data_dir / "youth.json").write_text("DANGER", encoding="utf-8")
            restored = EditorialFakeApp(directory, 20261008, rebuild=True)
            self.assertEqual(restored.manifest["files"], before)
            self.assertNotEqual((restored.data_dir / "youth.json").read_text(), "DANGER")
            changed = EditorialFakeApp(directory, 20261009, rebuild=True)
            self.assertNotEqual(changed.manifest["files"]["optimizer-valid.epub"],
                                before["optimizer-valid.epub"])


if __name__ == "__main__":
    unittest.main()
