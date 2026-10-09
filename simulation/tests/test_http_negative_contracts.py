"""Check the real transport.http plugin against deterministic loopback responses."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
for directory in (SIM, REPO / "src", REPO / "src/plugins/transport-http"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import httpx
import datasetctl
from plugin_coverage import build_report
from cifereca.plugin_api import Conflict, TemporaryUnavailable, UnsupportedOperation
from cifereca_http_transport import HttpTransport
from transport.pseudo_http_failure_service import Handler, TransportFakeApp


class Context:
    config = {"timeout_seconds": 2, "max_download_bytes": 1024 * 1024, "max_redirects": 4}
    logger = logging.getLogger("simulation.transport.http")


class LocalService:
    def __init__(self, cache_dir: Path):
        Handler.app = TransportFakeApp(cache_dir)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class TransportFailureContractTests(unittest.TestCase):
    def test_profiles_register_fixture_not_a_forced_plugin(self):
        catalog = datasetctl.load_catalog()
        service = catalog["transport_failures"]
        self.assertEqual(service["plugins"], [])
        self.assertEqual(service["fixture_packages"], ["transport-http"])
        self.assertEqual(service["default_port"], 8102)
        for name in ("ux-small", "ux-all", "ux-errors"):
            _, profile = datasetctl.load_profile(name)
            self.assertIn("transport_failures", datasetctl.enabled_service_configs(profile))
        item = next(row for row in build_report()["packages"]
                    if row["package"] == "transport-http")
        self.assertIn("transport_failures", item["http_fixtures"])
        self.assertEqual(item["services"], [])
        self.assertFalse(item["e2e_gtk_verified"])

    def test_real_transport_downloads_valid_epub_via_relative_redirect(self):
        with tempfile.TemporaryDirectory() as tmp, LocalService(Path(tmp) / "cache") as fake:
            with urlopen(fake.base + "/health", timeout=3) as response:
                health = json.load(response)
            self.assertTrue(health["ok"] and health["synthetic"])
            self.assertEqual(health["seed"], 20261009)
            transport = HttpTransport(Context())
            try:
                destination = Path(tmp) / "book.epub"
                result = transport.fetch({
                    "url": fake.base + "/redirect/relative",
                    "expected_format": "epub",
                    "allowed_content_types": ["application/epub+zip"],
                    "max_bytes": 1024 * 1024,
                }, destination)
                self.assertEqual(result, destination)
                self.assertTrue(destination.read_bytes().startswith(b"PK"))
                self.assertFalse(list(destination.parent.glob(".*.part")))
            finally:
                transport.close()

    def test_status_429_and_503_are_explicit_and_atomic(self):
        with tempfile.TemporaryDirectory() as tmp, LocalService(Path(tmp) / "cache") as fake:
            transport = HttpTransport(Context())
            try:
                for status in (429, 503):
                    with self.subTest(status=status):
                        destination = Path(tmp) / f"{status}.epub"
                        with self.assertRaisesRegex(TemporaryUnavailable, "erreur réseau"):
                            transport.fetch({"url": fake.base + f"/status/{status}"}, destination)
                        self.assertFalse(destination.exists())
                self.assertFalse(list(Path(tmp).glob(".*.part")))
            finally:
                transport.close()

    def test_missing_and_cyclic_redirects_fail_without_partial_files(self):
        with tempfile.TemporaryDirectory() as tmp, LocalService(Path(tmp) / "cache") as fake:
            transport = HttpTransport(Context())
            try:
                for path, msg in (("/redirect/missing", "sans destination"),
                                  ("/redirect/loop-a", "trop de redirections")):
                    with self.subTest(path=path):
                        dest = Path(tmp) / (path.split("/")[-1] + ".epub")
                        with self.assertRaisesRegex(TemporaryUnavailable, msg):
                            transport.fetch({"url": fake.base + path, "max_redirects": 2}, dest)
                        self.assertFalse(dest.exists())
                self.assertFalse(list(Path(tmp).glob(".*.part")))
            finally:
                transport.close()

    def test_rejects_empty_html_mime_declared_and_streamed_size(self):
        with tempfile.TemporaryDirectory() as tmp, LocalService(Path(tmp) / "cache") as fake:
            transport = HttpTransport(Context())
            cases = (
                ("/empty", "réponse vide", {}),
                ("/html", "HTML", {}),
                ("/wrong-mime", "content-type refusé", {"allowed_content_types": ["application/epub+zip"]}),
                ("/declared-large", "volumineux", {"max_bytes": 1000}),
                ("/stream-large", "volumineux", {"max_bytes": 1000}),
                ("/invalid-epub", "artefact invalide", {"expected_format": "epub"}),
            )
            try:
                for path, error, extra in cases:
                    with self.subTest(path=path):
                        dest = Path(tmp) / (path.lstrip("/") + ".epub")
                        with self.assertRaisesRegex(UnsupportedOperation, error):
                            transport.fetch({"url": fake.base + path, **extra}, dest)
                        self.assertFalse(dest.exists())
                self.assertFalse(list(Path(tmp).glob(".*.part")))
            finally:
                transport.close()

    def test_cross_origin_redirect_drops_sensitive_headers_only(self):
        with tempfile.TemporaryDirectory() as tmp, LocalService(Path(tmp) / "cache") as initial, LocalService(Path(tmp) / "cache") as target:
            transport = HttpTransport(Context())
            try:
                source = {"url": initial.base + f"/redirect/loopback-other?port={target.server.server_port}",
                          "headers": {
                              "Authorization": "Bearer fake-test-value",
                              "Cookie": "fake=public-fixture",
                              "X-Test-Only": "yes",
                          }}
                destination = Path(tmp) / "header-check.json"
                self.assertEqual(transport.fetch(source, destination), destination)
                status = json.loads(destination.read_text(encoding="utf-8"))
                self.assertEqual(status, {
                    "authorization_present": False,
                    "cookie_present": False,
                    "custom_present": True,
                })
            finally:
                transport.close()

    def test_https_downgrade_is_rejected_before_second_request(self):
        seen = []
        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8102/file.epub"}, request=request)
        transport = HttpTransport(Context())
        transport.client.close()
        transport.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / "downgraded.epub"
                with self.assertRaisesRegex(UnsupportedOperation, "HTTPS vers HTTP refusée"):
                    transport.fetch({"url": "https://fake.example.test/start"}, destination)
                self.assertEqual(len(seen), 1)
                self.assertFalse(destination.exists())
        finally:
            transport.close()

    def test_timeout_is_reported_and_never_publishes(self):
        with tempfile.TemporaryDirectory() as tmp, LocalService(Path(tmp) / "cache") as fake:
            transport = HttpTransport(Context())
            try:
                destination = Path(tmp) / "slow.epub"
                with self.assertRaisesRegex(TemporaryUnavailable, "ReadTimeout"):
                    transport.fetch({"url": fake.base + "/slow", "timeout_seconds": 1}, destination)
                self.assertFalse(destination.exists())
            finally:
                transport.close()


if __name__ == "__main__":
    unittest.main()
