from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from cifereca.plugin_api import Conflict, TransportService, UnsupportedOperation
from cifereca_http_transport import HttpTransport


PAYLOAD = b"PK\x03\x04" + b"x" * 64


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/book.epub")
            self.end_headers()
            return
        if self.path == "/html":
            body = b"<html>not a book</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/large":
            body = b"x" * 128
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/epub+zip")
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *_args):
        return


class Context:
    config = {"timeout_seconds": 2, "max_download_bytes": 1024 * 1024, "max_redirects": 4}
    logger = logging.getLogger("test.transport.http")


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=httpd.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    host, port = httpd.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_contract_and_redirected_streaming(server, tmp_path):
    transport = HttpTransport(Context())
    try:
        assert isinstance(transport, TransportService)
        destination = tmp_path / "book.epub"
        result = transport.fetch(
            {
                "url": server + "/redirect",
                "max_bytes": 1024,
                "allowed_content_types": ["application/epub+zip"],
            },
            destination,
        )
        assert result == destination
        assert destination.read_bytes() == PAYLOAD
    finally:
        transport.close()


def test_html_and_size_limits_are_rejected(server, tmp_path):
    transport = HttpTransport(Context())
    try:
        with pytest.raises(UnsupportedOperation, match="HTML"):
            transport.fetch({"url": server + "/html"}, tmp_path / "bad.epub")
        with pytest.raises(UnsupportedOperation, match="volumineux"):
            transport.fetch({"url": server + "/large", "max_bytes": 64}, tmp_path / "large.epub")
        assert not (tmp_path / "bad.epub").exists()
        assert not (tmp_path / "large.epub").exists()
    finally:
        transport.close()


def test_existing_different_destination_is_not_overwritten(server, tmp_path):
    transport = HttpTransport(Context())
    destination = tmp_path / "book.epub"
    destination.write_bytes(b"old")
    try:
        with pytest.raises(Conflict):
            transport.fetch({"url": server + "/book.epub"}, destination)
        assert destination.read_bytes() == b"old"
    finally:
        transport.close()


def test_matches_rejects_credential_urls():
    transport = HttpTransport(Context())
    try:
        assert transport.matches({"url": "https://example.test/book.epub"})
        assert not transport.matches({"url": "ftp://example.test/book.epub"})
        assert not transport.matches({"url": "https://user:secret@example.test/book.epub"})
    finally:
        transport.close()


def test_expected_format_validates_before_publish(server, tmp_path):
    transport = HttpTransport(Context())
    destination = tmp_path / "invalid.epub"
    try:
        with pytest.raises(UnsupportedOperation, match="artefact invalide"):
            transport.fetch(
                {
                    "url": server + "/book.epub",
                    "expected_format": "epub",
                    "allowed_content_types": ["application/epub+zip"],
                },
                destination,
            )
        assert not destination.exists()
    finally:
        transport.close()


def test_core_orchestration_requires_normalized_protection_proof(server, tmp_path):
    transport = HttpTransport(Context())
    try:
        core_source = {
            "url": server + "/book.epub",
            "handling": "http_plain",
            "drm": "none",
        }
        with pytest.raises(UnsupportedOperation, match="preuve de protection Core requise"):
            transport.fetch(core_source, tmp_path / "missing-proof.epub")

        bad = {
            **core_source,
            "protection": {
                "handling": "acsm_adept",
                "drm": "none",
                "protected": False,
                "attested": True,
            },
        }
        with pytest.raises(UnsupportedOperation, match="handling Core non http_plain"):
            transport.fetch(bad, tmp_path / "bad-proof.epub")

        contradictory = {
            **core_source,
            "drm": "adobe",
            "protection": {
                "handling": "http_plain",
                "drm": "none",
                "protected": False,
                "attested": True,
            },
        }
        with pytest.raises(UnsupportedOperation, match="preuve DRM Core incohérente"):
            transport.fetch(contradictory, tmp_path / "contradictory.epub")

        good = {
            **core_source,
            "protection": {
                "handling": "http_plain",
                "drm": "none",
                "protected": False,
                "attested": True,
            },
        }
        destination = tmp_path / "core-proof.epub"
        assert transport.fetch(good, destination) == destination
        assert destination.read_bytes() == PAYLOAD
    finally:
        transport.close()


def test_direct_provider_call_without_core_policy_remains_supported(server, tmp_path):
    transport = HttpTransport(Context())
    try:
        destination = tmp_path / "direct.epub"
        assert transport.fetch(
            {
                "url": server + "/book.epub",
                "allowed_content_types": ["application/epub+zip"],
            },
            destination,
        ) == destination
    finally:
        transport.close()
