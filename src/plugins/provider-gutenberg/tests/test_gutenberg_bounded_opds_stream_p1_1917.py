"""P1 #1917: Gutenberg search reads a bounded raw Atom HTTP response.

All data is synthetic, using httpx.MockTransport. No live account, search,
download, signed link or user-identifying diagnostic is involved.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca.search_failures import search_failure_code
from cifereca_gutenberg import GutenbergSession, MAX_SEARCH_OPDS_BYTES


ATOM_ZERO = b'<feed xmlns="http://www.w3.org/2005/Atom"/>'


class ForbiddenRead(httpx.SyncByteStream):
    def __iter__(self):
        raise AssertionError("Content-Length should reject before body read")
        yield b""


class CountedChunks(httpx.SyncByteStream):
    def __init__(self):
        self.sent = 0
    def __iter__(self):
        for _ in range(100):
            self.sent += 1
            yield b"X" * 65536


def session(handler):
    obj = GutenbergSession(
        credential_id="public", label="", credentials={},
        options={"contact": "offline-tests@example.invalid"},
        config={}, diagnostics=None,
    )
    obj.client.close()
    obj.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        trust_env=False, follow_redirects=True,
    )
    return obj


@pytest.mark.parametrize("mode", ["length", "raw-chunks", "compression"])
def test_200_oversized_or_unsupported_encoding_is_closed_outage(mode):
    requests = []
    streamed = CountedChunks()

    def respond(request):
        requests.append(request)
        if mode == "length":
            return httpx.Response(
                200,
                headers={"Content-Length": str(MAX_SEARCH_OPDS_BYTES + 1)},
                stream=ForbiddenRead(),
            )
        if mode == "raw-chunks":
            return httpx.Response(200, stream=streamed)
        return httpx.Response(
            200, stream=httpx.ByteStream(ATOM_ZERO),
            headers={"Content-Encoding": "gzip"},
        )

    obj = session(respond)
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed") as error:
            obj.search("Livre public")
        assert search_failure_code(error.value) == "SearchTemporarilyUnavailable"
        assert error.value.retryable is False
        assert error.value.__cause__ is None
        assert len(requests) == 1
        assert requests[0].headers["accept-encoding"] == "identity"
        if mode == "raw-chunks":
            assert 32 < streamed.sent < 100
        # A failed search must not register a fresh successful page URL.
        assert all(not any(row) for row in obj._page_urls.values())
        assert obj.last_search_warnings == []
    finally:
        obj.close()


def test_valid_small_atom_remains_legitimate_zero_and_single_request():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=ATOM_ZERO)
    obj = session(respond)
    try:
        assert obj.search("Livre public") == []
        assert obj.last_search_warnings == []
        assert len(requests) == 1
    finally:
        obj.close()


def test_untrusted_content_length_is_not_authority_and_raw_limit_still_applies():
    requests = []
    counted = CountedChunks()
    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"Content-Length": "not-a-number"},
            stream=counted,
        )
    obj = session(respond)
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed"):
            obj.search("Livre public")
        assert len(requests) == 1
        assert 32 < counted.sent < 100
    finally:
        obj.close()
