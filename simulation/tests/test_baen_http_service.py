"""Contrats Baen : vrai plugin Baen -> HTTPX injectable -> serveur loopback.

Aucun appel réseau vers baen.com et aucun jeton, tout est synthétique.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
for path in (SIM, REPO / "src", REPO / "src/plugins/baen"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import httpx
import datasetctl
from plugin_coverage import build_report
from baen.pseudo_baen_service import BaenFakeApp, Handler
from cifereca_baen import BaenSession, ProviderError


class LocalBaenServer:
    def __init__(self, cache_dir: Path):
        Handler.app = BaenFakeApp(cache_dir)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.root = f"http://127.0.0.1:{self.httpd.server_port}"
        self.calls: list[str] = []

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def get(self, url: str):
        self.calls.append(url)
        request = httpx.URL(url)
        if request.scheme != "https" or request.host != "www.baen.com":
            raise AssertionError("Le transport de test refuse les domaines autres que Baen")
        local = self.root + request.raw_path.decode("ascii")
        try:
            with urlopen(Request(local), timeout=3) as result:
                return result.status, dict(result.headers), result.read()
        except HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def client(self, *, timeout_on_query: bool = False):
        def fetch(request):
            # A real socket stall would make this test timing-dependent.
            # Inject ReadTimeout at the trusted transport boundary instead,
            # without contacting a remote host or even the local server.
            if timeout_on_query and request.url.params.get("q") == "timeout":
                self.calls.append(str(request.url))
                raise httpx.ReadTimeout("PRIVATE_SYNTHETIC_TIMEOUT", request=request)
            status, headers, content = self.get(str(request.url))
            return httpx.Response(status, headers=headers, content=content, request=request)
        return httpx.Client(transport=httpx.MockTransport(fetch), trust_env=False)


class RecordingTransport:
    """Only for tests: mimic the transport.http download into a local file."""
    def __init__(self, server: LocalBaenServer):
        self.server = server
        self.calls: list[dict] = []

    def fetch(self, source, destination):
        source = dict(source)
        self.calls.append(source)
        status, headers, content = self.server.get(source["url"])
        if status != 200:
            raise RuntimeError("HTTP download failed")
        if headers.get("Content-Type", "").split(";")[0] not in source["allowed_content_types"]:
            raise RuntimeError("Unexpected MIME")
        with zipfile.ZipFile(__import__("io").BytesIO(content)) as archive:
            if archive.read("mimetype") != b"application/epub+zip":
                raise RuntimeError("Invalid EPUB")
        dest = Path(destination)
        if len(content) > source["max_bytes"]:
            raise RuntimeError("Too large")
        dest.write_bytes(content)
        return dest


def make_session(server: LocalBaenServer, *, transport):
    session = BaenSession(
        credential_id="fake-baen-public", label="Baen fictif",
        credentials={}, options={"max_download_bytes": 1_000_000},
        config={}, diagnostics=None, transport=transport,
    )
    session.client.close()
    session.client = server.client()
    session.connect()
    return session


class BaenContractTests(unittest.TestCase):
    def test_registered_as_fixture_not_live_provider(self):
        catalog = datasetctl.load_catalog()
        service = catalog["baen"]
        self.assertEqual(service["plugins"], [])
        self.assertEqual(service["fixture_packages"], ["baen"])
        row = next(item for item in build_report()["packages"] if item["package"] == "baen")
        self.assertEqual(row["services"], [])
        self.assertIn("baen", row["http_fixtures"])
        self.assertEqual(row["status"], "partial")
        self.assertFalse(row["e2e_gtk_verified"])
        for name in ("ux-small", "ux-all", "ux-errors"):
            _, profile = datasetctl.load_profile(name)
            self.assertIn("baen", datasetctl.enabled_service_configs(profile))
            self.assertNotIn("provider.baen", datasetctl.derived_plugins(profile, catalog))

    def test_real_plugin_search_pages_filters_details_and_download(self):
        with tempfile.TemporaryDirectory() as tmp, LocalBaenServer(Path(tmp)) as server:
            transport = RecordingTransport(server)
            session = make_session(server, transport=transport)
            try:
                session.page_size = 2
                one = session.search("", page=0)
                self.assertEqual([i.title for i in one], ["The Firefly Atlas", "The Garden of Words"])
                self.assertTrue(all(i.source_provider == "baen" for i in one))
                two = session.search("", page=1)
                self.assertEqual([i.title for i in two], ["The Last Shore"])
                self.assertEqual(session.search("", page=2), [])
                self.assertEqual([i.title for i in session.search("Garden")], ["The Garden of Words"])
                self.assertEqual(session.search("nothing"), [])
                self.assertEqual(session.last_search_warnings, [],
                                 "attested 200 zero is not partial")
                self.assertIn("page=2", server.calls[1])
                self.assertIn("pageSize=2", server.calls[1])

                item = one[0]
                detail = session.holdings(item)
                self.assertEqual(detail.author, "Camille Example")
                self.assertEqual(item.metadata["isbns"], ["9780140328721"])
                self.assertEqual(item.metadata["metadata_evidence"], "baen.public_product_page")

                with tempfile.TemporaryDirectory() as dest:
                    final, reference = session.download_direct(item, Path(dest))
                    self.assertTrue(final.exists())
                    self.assertEqual(reference, item.friendly_url)
                    self.assertEqual(len(transport.calls), 1)
                    self.assertEqual(transport.calls[0]["expected_format"], "epub")
                    self.assertTrue(transport.calls[0]["url"].startswith("https://www.baen.com/downloads/"))

                with self.assertRaisesRegex(ProviderError, "aucun lien EPUB"):
                    session.download_direct(one[1], Path(tmp))
            finally:
                session.close()

    def test_actual_plugin_propagates_simulated_catalog_failures(self):
        with tempfile.TemporaryDirectory() as tmp, LocalBaenServer(Path(tmp)) as server:
            session = make_session(server, transport=RecordingTransport(server))
            try:
                # Provider #1917 now exposes stable, redacted failure
                # categories rather than the former generic error string.
                from cifereca.search_failures import search_failure_code
                from cifereca.core.known_catalog_fallback import public_service_unavailable
                for query, code, core_code in (
                    ("auth-required", "auth_required", "SearchAuthenticationRequired"),
                    ("access-denied", "access_denied", "SearchAccessDenied"),
                    ("rate-limit", "rate_limited", "SearchTemporarilyUnavailable"),
                    ("down", "service_unavailable", "SearchTemporarilyUnavailable"),
                ):
                    with self.subTest(query=query):
                        before = len(server.calls)
                        with self.assertRaisesRegex(ProviderError, f"^Baen : {code}$") as raised:
                            session.search(query)
                        self.assertEqual(len(server.calls), before + 1)
                        self.assertEqual(search_failure_code(raised.exception), core_code)
                        self.assertEqual(
                            public_service_unavailable(raised.exception),
                            core_code == "SearchTemporarilyUnavailable",
                        )
                        self.assertIsNone(raised.exception.__cause__)
                self.assertEqual(session.search("nothing"), [])
            finally:
                session.close()

    def test_deterministic_read_timeout_preserves_outage_and_next_success(self):
        from cifereca.search_failures import search_failure_code
        from cifereca.core.known_catalog_fallback import public_service_unavailable
        with tempfile.TemporaryDirectory() as tmp, LocalBaenServer(Path(tmp)) as server:
            session = make_session(server, transport=RecordingTransport(server))
            session.client.close()
            session.client = server.client(timeout_on_query=True)
            try:
                with self.assertRaisesRegex(
                    ProviderError, "^Baen : service_unavailable$",
                ) as raised:
                    session.search("timeout")
                self.assertEqual(len(server.calls), 1)
                self.assertIn("q=timeout", server.calls[0])
                self.assertEqual(
                    search_failure_code(raised.exception),
                    "SearchTemporarilyUnavailable",
                )
                self.assertTrue(public_service_unavailable(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertNotIn("PRIVATE_SYNTHETIC_TIMEOUT", str(raised.exception))

                # Recovery is observable: no permanent error state/cache and
                # no retry of the timed-out query.
                self.assertEqual([r.title for r in session.search("Garden")],
                                 ["The Garden of Words"])
                self.assertEqual(len(server.calls), 2)
                self.assertEqual(session.last_search_warnings, [])
            finally:
                session.close()

    def test_unknown_success_markup_is_partial_not_an_attested_zero(self):
        with tempfile.TemporaryDirectory() as tmp, LocalBaenServer(Path(tmp)) as server:
            session = make_session(server, transport=RecordingTransport(server))
            try:
                self.assertEqual(session.search("layout-change"), [])
                self.assertTrue(any(
                    "partial_results" in warning
                    for warning in session.last_search_warnings
                ))
                self.assertEqual(len(server.calls), 1)
                self.assertEqual(session.search("nothing"), [])
                self.assertFalse(session.last_search_warnings)
                self.assertEqual(len(server.calls), 2)
            finally:
                session.close()

    def test_health_and_epub_are_deterministic_and_invalid_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, LocalBaenServer(Path(tmp)) as server:
            with urlopen(server.root + "/health", timeout=3) as response:
                health = json.load(response)
            self.assertTrue(health["ok"] and health["synthetic"])
            self.assertEqual(health["catalog"], 3)
            a = server.get("https://www.baen.com/downloads/firefly-atlas.epub")
            b = server.get("https://www.baen.com/downloads/firefly-atlas.epub")
            self.assertEqual(a[2], b[2])
            self.assertTrue(a[2].startswith(b"PK"))
            with self.assertRaises(zipfile.BadZipFile):
                zipfile.ZipFile(__import__("io").BytesIO(server.get(
                    "https://www.baen.com/downloads/firefly-atlas.epub?fault=invalid-epub")[2])).namelist()


if __name__ == "__main__":
    unittest.main()
