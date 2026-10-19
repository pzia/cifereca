"""#1917 P1 — streaming COPS OPDS never buffers unbounded remote payloads.

Synthetic public Atom via httpx.MockTransport; no live network, credentials,
download, personal library, redirects to another origin or acquisition.
"""
from __future__ import annotations

import gzip

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_ebooksgratuits import (
    EbooksSession, OPDS_MAX_FEED_BYTES, OPDS_MAX_ELEMENTS, OPDS_MAX_DEPTH,
    _opds_fetch,
)

HOST = "https://opds2.ebooksgratuits.org"
ROOT = HOST + "/index.php/feed"
XML = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Public</title></feed>'


class ProbeStream(httpx.SyncByteStream):
    def __init__(self, chunks=(), *, forbid_read=False):
        self.chunks = chunks
        self.forbid_read = forbid_read
        self.chunks_read = 0
        self.closed = False

    def __iter__(self):
        if self.forbid_read:
            pytest.fail("HTTP body must never be read")
        for chunk in self.chunks:
            self.chunks_read += 1
            yield chunk

    def close(self):
        self.closed = True


def session(handler):
    seen = []

    def instrument(request):
        seen.append((str(request.url), request.headers.get("accept-encoding")))
        return handler(request)

    s = EbooksSession(
        credential_id="__public__", label="Public", credentials={},
        options={}, config={}, diagnostics=None, transport=None,
    )
    s.client.close()
    s.client = httpx.Client(
        transport=httpx.MockTransport(instrument), trust_env=False,
    )
    s.connect()
    return s, seen


def test_normal_feed_is_read_once_with_identity_http_encoding():
    body = ProbeStream([XML[:12], XML[12:]])
    s, seen = session(lambda req: httpx.Response(200, stream=body, request=req))
    try:
        root, url = _opds_fetch(s, ROOT)
        assert root.tag == "{http://www.w3.org/2005/Atom}feed"
        assert url == ROOT
        assert seen == [(ROOT, "identity")]
        assert body.chunks_read == 2 and body.closed
    finally:
        s.close()


def test_oversized_feed_fails_while_streaming_not_after_full_buffering():
    # Deliberately advertise a short Content-Length. Do not trust it.
    body = ProbeStream(
        [b"x" * (512 * 1024)] * 5
        + [b"PRIVATE_END_OF_STREAM_SHOULD_NOT_BE_READ"]
    )
    s, seen = session(lambda req: httpx.Response(
        200, stream=body, request=req, headers={"content-length": "3"},
    ))
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
            _opds_fetch(s, ROOT)
        assert body.chunks_read == 5  # Fail before the 6th source chunk.
        assert body.closed
        assert "PRIVATE" not in str(caught.value)
        assert seen == [(ROOT, "identity")]
    finally:
        s.close()


def test_exact_budget_is_not_rejected_before_xml_validation():
    prefix = b'<feed xmlns="http://www.w3.org/2005/Atom">'
    suffix = b'</feed>'
    body = prefix + b" " * (OPDS_MAX_FEED_BYTES - len(prefix) - len(suffix)) + suffix
    assert len(body) == OPDS_MAX_FEED_BYTES
    s, _seen = session(lambda req: httpx.Response(
        200, stream=ProbeStream([body]), request=req,
    ))
    try:
        root, url = _opds_fetch(s, ROOT)
        assert root.tag.endswith("feed") and url == ROOT
    finally:
        s.close()


def test_prebuffered_mock_response_still_enforces_xml_size_bound():
    # Several long-lived Core/Plugin tests construct Response(content=...)
    # rather than a streaming body. They are trusted test fixtures, NOT a
    # network buffering strategy, and still must respect the XML cap.
    payload = b"x" * (OPDS_MAX_FEED_BYTES + 1)
    s, seen = session(lambda req: httpx.Response(
        200, content=payload, request=req,
    ))
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed"):
            _opds_fetch(s, ROOT)
        assert seen == [(ROOT, "identity")]
    finally:
        s.close()


def test_gzip_response_is_rejected_without_decompressing_any_bytes():
    # Even a few encoded bytes could inflate far above the 2 MiB XML cap.
    compressed = gzip.compress(XML + b" " * (OPDS_MAX_FEED_BYTES + 1))
    body = ProbeStream([compressed], forbid_read=True)
    s, seen = session(lambda req: httpx.Response(
        200, stream=body, request=req,
        headers={"content-encoding": "gzip"},
    ))
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed"):
            _opds_fetch(s, ROOT)
        assert body.chunks_read == 0
        assert body.closed
        assert seen == [(ROOT, "identity")]
    finally:
        s.close()


def test_same_origin_redirect_never_reads_untrusted_redirect_body():
    redirect_body = ProbeStream([b"PRIVATE"], forbid_read=True)
    atom_body = ProbeStream([XML])
    paths = []

    def handler(req):
        paths.append(req.url.path)
        if req.url.path == "/index.php/feed":
            return httpx.Response(
                302, stream=redirect_body, request=req,
                headers={"location": "/index.php/feed/books"},
            )
        assert req.url.path == "/index.php/feed/books"
        return httpx.Response(200, stream=atom_body, request=req)

    s, seen = session(handler)
    try:
        root, final_url = _opds_fetch(s, ROOT)
        assert root.tag.endswith("feed")
        assert final_url == ROOT + "/books"
        assert paths == ["/index.php/feed", "/index.php/feed/books"]
        assert len(seen) == 2
        assert all(headers == "identity" for _, headers in seen)
        assert redirect_body.chunks_read == 0
        assert redirect_body.closed and atom_body.closed
    finally:
        s.close()


def test_redirect_to_foreign_host_is_rejected_without_reading_body():
    redirect_body = ProbeStream([b"PRIVATE"], forbid_read=True)
    s, seen = session(lambda req: httpx.Response(
        302, stream=redirect_body, request=req,
        headers={"location": "https://evil.invalid/index.php/feed"},
    ))
    try:
        with pytest.raises(ProviderError, match="invalid_opds_link") as caught:
            _opds_fetch(s, ROOT)
        assert "evil.invalid" not in str(caught.value)
        assert redirect_body.chunks_read == 0 and redirect_body.closed
        assert seen == [(ROOT, "identity")]
    finally:
        s.close()


@pytest.mark.parametrize(("status", "code"), [
    (401, "auth_required"), (403, "access_denied"),
    (429, "rate_limited"), (500, "service_unavailable"),
])
def test_status_errors_do_not_stream_or_expose_untrusted_response(status, code):
    body = ProbeStream([b"token=PRIVATE"], forbid_read=True)
    s, _seen = session(lambda req: httpx.Response(
        status, stream=body, request=req,
    ))
    try:
        with pytest.raises(ProviderError, match=code) as caught:
            _opds_fetch(s, ROOT)
        assert "PRIVATE" not in str(caught.value)
        assert body.chunks_read == 0 and body.closed
    finally:
        s.close()


def test_dtd_still_rejected_after_safe_bounded_read():
    payload = b'<!DOCTYPE feed [<!ENTITY secret "PRIVATE">]><feed/>'
    body = ProbeStream([payload])
    s, _seen = session(lambda req: httpx.Response(200, stream=body, request=req))
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
            _opds_fetch(s, ROOT)
        assert "PRIVATE" not in str(caught.value)
        assert body.closed
    finally:
        s.close()



@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-be"])
def test_internal_xml_entity_is_forbidden_at_parser_level_even_in_utf16(encoding):
    # In UTF-16, raw b'<!doctype' scans do NOT see a DTD. ElementTree's
    # default parser otherwise expands this internal entity.
    text = ('<?xml version="1.0" encoding="' + encoding + '"?>'
            '<!DOCTYPE feed [<!ENTITY secret "PRIVATE_ENTITY">]>'
            '<feed>&secret;</feed>')
    body = text.encode(encoding)
    s, _seen = session(lambda req: httpx.Response(
        200, stream=ProbeStream([body]), request=req,
    ))
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
            _opds_fetch(s, ROOT)
        assert "PRIVATE" not in str(caught.value)
    finally:
        s.close()


@pytest.mark.parametrize("kind", ["deep", "wide"])
def test_xml_structure_rejected_before_tree_becomes_disproportionate(kind):
    if kind == "deep":
        payload = b"<feed>" + b"<n>" * OPDS_MAX_DEPTH + b"</n>" * OPDS_MAX_DEPTH + b"</feed>"
    else:
        payload = b"<feed>" + b"<n/>" * OPDS_MAX_ELEMENTS + b"</feed>"
    assert len(payload) < OPDS_MAX_FEED_BYTES
    stream = ProbeStream([payload])
    s, _seen = session(lambda req: httpx.Response(
        200, stream=stream, request=req,
    ))
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
            _opds_fetch(s, ROOT)
        assert stream.closed
        assert "PRIVATE" not in str(caught.value)
    finally:
        s.close()


def test_structural_budgets_accept_ordinary_root_navigation():
    body = (b'<feed xmlns="http://www.w3.org/2005/Atom">'
            b'<entry><id>urn:book:1</id><title>Livre public</title>'
            b'</entry></feed>')
    s, _seen = session(lambda req: httpx.Response(200, content=body, request=req))
    try:
        root, url = _opds_fetch(s, ROOT)
        assert len(root.findall("{http://www.w3.org/2005/Atom}entry")) == 1
        assert url == ROOT
    finally:
        s.close()
