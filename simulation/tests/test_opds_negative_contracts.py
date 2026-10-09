"""OPDS negative-case contracts on the genuine Gallica/Gutenberg adapters."""
from __future__ import annotations

import importlib.util
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen, Request

import httpx

SIM = Path(__file__).resolve().parents[1]
ROOT = SIM.parent
for folder in (SIM, ROOT / "src", ROOT / "src/plugins/provider-gutenberg",
               ROOT / "src/plugins/provider-gallica",
               ROOT / "src/plugins/transport-http"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from cifereca_gallica import GallicaSession
from cifereca_gutenberg import GutenbergSession
from cifereca_http_transport import HttpTransport
from cifereca.plugin_api import TemporaryUnavailable, UnsupportedOperation
import datasetctl

spec = importlib.util.spec_from_file_location("ux_opds_negative_server", SIM / "opds/pseudo_opds_server.py")
assert spec and spec.loader
opds = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = opds
spec.loader.exec_module(opds)


class Diagnostics:
    def add(self, *args, **kwargs):
        return None


class TransportContext:
    config = {"timeout_seconds": 3, "max_download_bytes": 1_000_000, "max_redirects": 3}
    import logging
    logger = logging.getLogger("ux.opds.errors")


class LocalOPDS:
    def __init__(self, cache_dir: Path):
        cache = opds.DatasetCache(cache_dir, 4, 20261009, True)
        opds.Handler.app = opds.FakeOPDSApp(cache)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), opds.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def injected_client(self, fault: str) -> httpx.Client:
        # Both real providers pass params=... to HTTPX, which replaces query
        # components on configured URLs. Inject the fault on the outgoing
        # request instead, strictly inside the test, without touching plugins.
        def forward(request: httpx.Request) -> httpx.Response:
            if request.url.scheme != "http" or request.url.host != "127.0.0.1":
                raise AssertionError("Outbound network blocked in OPDS contract tests")
            url = request.url
            if url.path.rstrip("/") in {
                "/gutenberg/ebooks/search.opds",
                "/gallica/services/engine/search/opds",
            }:
                url = url.copy_set_param("ux_fault", fault)
            try:
                with urlopen(Request(str(url)), timeout=4) as response:
                    status, headers, body = response.status, dict(response.headers), response.read()
            except HTTPError as exc:
                status, headers, body = exc.code, dict(exc.headers), exc.read()
            return httpx.Response(status, headers=headers, content=body, request=request)
        return httpx.Client(
            transport=httpx.MockTransport(forward),
            follow_redirects=True, trust_env=False,
        )

    def gutenberg(self, fault: str) -> GutenbergSession:
        session = GutenbergSession(
            credential_id="synthetic",
            label="Gutenberg fictif", credentials={},
            options={
                "contact": "test-opds@example.test",
                "opds_search_url": self.base + "/gutenberg/ebooks/search.opds/",
            },
            config={}, diagnostics=Diagnostics(),
        )
        session.client.close()
        session.client = self.injected_client(fault)
        return session

    def gallica(self, fault: str) -> GallicaSession:
        session = GallicaSession(
            credential_id="synthetic", label="Gallica fictif",
            credentials={},
            options={
                "opds_url": self.base + "/gallica/opds",
                "epub_search_url": self.base + "/gallica/services/engine/search/opds",
                "include_audio": False,
            },
            config={}, diagnostics=Diagnostics(),
        )
        session.client.close()
        session.client = self.injected_client(fault)
        return session


class OPDSNegativeCasesTests(unittest.TestCase):
    def test_catalogue_faults_are_opt_in_and_profile_is_local(self):
        _, profile = datasetctl.load_profile("ux-errors")
        self.assertTrue(datasetctl.enabled_service_configs(profile)["opds"]["enabled"])
        self.assertGreater(datasetctl.enabled_service_configs(profile)["opds"]["broken_assets_every"], 0)
        with tempfile.TemporaryDirectory() as temp, LocalOPDS(Path(temp)) as srv:
            with urlopen(srv.base + "/gutenberg/ebooks/search.opds/?query=NoSuchBook") as response:
                body = response.read()
            self.assertIn(b"<feed", body)
            self.assertIn(b"totalResults", body)
            self.assertNotIn(b"<entry>", body)
            for path, expected in (
                ("/gutenberg/ebooks/search.opds/?ux_fault=rate-limit", 429),
                ("/gallica/services/engine/search/opds?ux_fault=unavailable", 503),
            ):
                with self.subTest(path=path), self.assertRaises(HTTPError) as ctx:
                    urlopen(srv.base + path)
                self.assertEqual(ctx.exception.code, expected)

    def test_gutenberg_real_parser_propagates_429_503_invalid_xml(self):
        with tempfile.TemporaryDirectory() as temp, LocalOPDS(Path(temp)) as srv:
            for fault, fragment in (
                ("rate-limit", "indisponible"),
                ("unavailable", "indisponible"),
                ("malformed", "XML invalide"),
            ):
                with self.subTest(fault=fault):
                    session = srv.gutenberg(fault)
                    try:
                        with self.assertRaisesRegex(Exception, fragment):
                            session.search("fictional")
                    finally:
                        session.close()

    def test_gutenberg_explicit_empty_and_unreachable_next_page(self):
        with tempfile.TemporaryDirectory() as temp, LocalOPDS(Path(temp)) as srv:
            session = srv.gutenberg("empty")
            try:
                self.assertEqual(session.search("fictional"), [])
                self.assertEqual(session.search("fictional", page=1), [])
            finally:
                session.close()
            session = srv.gutenberg("broken-next")
            try:
                self.assertEqual(session.search("fictional", page=0), [])
                with self.assertRaisesRegex(Exception, "indisponible"):
                    session.search("fictional", page=1)
            finally:
                session.close()

    def test_gallica_real_parser_rejects_bad_xml_and_handles_empty(self):
        with tempfile.TemporaryDirectory() as temp, LocalOPDS(Path(temp)) as srv:
            for fault, expected in (
                ("unavailable", "temporairement indisponible"),
                ("malformed", "temporairement indisponible"),
                ("wrong-root", "temporairement indisponible"),
            ):
                with self.subTest(fault=fault):
                    session = srv.gallica(fault)
                    try:
                        with self.assertRaisesRegex(Exception, expected):
                            session.search("fictional", field="title", content_types=("epub",))
                    finally:
                        session.close()
            session = srv.gallica("empty")
            try:
                self.assertEqual(session.search("fictional", field="title", content_types=("epub",)), [])
                self.assertEqual(session.last_search_warnings, [])
            finally:
                session.close()

    def test_corrupt_opds_epub_is_rejected_by_real_http_transport(self):
        with tempfile.TemporaryDirectory() as temp, LocalOPDS(Path(temp)) as srv:
            transport = HttpTransport(TransportContext())
            try:
                for fault, expected in (("invalid-epub", "artefact invalide"),
                                        ("wrong-mime", "HTML")):
                    with self.subTest(fault=fault):
                        output = Path(temp) / (fault + ".epub")
                        with self.assertRaisesRegex(UnsupportedOperation, expected):
                            transport.fetch({
                                "url": srv.base + f"/gutenberg/epub/1.epub?ux_fault={fault}",
                                "expected_format": "epub",
                                "allowed_content_types": ["application/epub+zip"],
                            }, output)
                        self.assertFalse(output.exists())
            finally:
                transport.close()


if __name__ == "__main__":
    unittest.main()
