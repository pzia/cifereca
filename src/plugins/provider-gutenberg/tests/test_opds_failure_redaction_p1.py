"""#1917: raw Gutenberg HTTP/XML failures never enter public messages or logs.

Offline MockTransport and malformed synthetic XML only. No purchase/download.
"""
from types import SimpleNamespace
import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_gutenberg import GutenbergSession

SECRET = "PRIVATE_TOKEN_203948"
SIGNED = "https://www.gutenberg.org/ebooks/42.opds?token=" + SECRET


class Diagnostics:
    def __init__(self):
        self.events = []

    def add(self, *args, **kwargs):
        self.events.append((args, kwargs))


def session_with_handler(handler=None):
    diag = Diagnostics()
    session = GutenbergSession(
        credential_id="public", label="", credentials={},
        options={"contact": "catalog-tests@example.test"},
        config={}, diagnostics=diag,
    )
    session.client.close()
    if handler is not None:
        session.client = httpx.Client(
            transport=httpx.MockTransport(handler), trust_env=False,
        )
    return session, diag


def item_with_private_url():
    return SimpleNamespace(
        metadata={"opds_detail_url": SIGNED},
        download_url=None,
        record_id="42",
    )


def assert_redacted(exc, diagnostics, caplog):
    text = str(exc.value) + repr(diagnostics.events) + caplog.text
    assert SECRET not in text
    assert "token=" not in text
    assert "PRIVATE" not in text
    assert exc.value.__cause__ is None


def test_bad_search_xml_has_closed_error_and_no_raw_url_or_error_details(caplog):
    session, diag = session_with_handler(
        lambda req: httpx.Response(
            200, content=b'<feed><bad a="' + SECRET.encode() + b'"',
        ),
    )
    try:
        with caplog.at_level("WARNING", logger="cifereca.plugin.provider.gutenberg"):
            with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
                session.search("book")
        assert_redacted(caught, diag, caplog)
        assert diag.events
        details = diag.events[-1][1].get("detail")
        assert details["reason"] == "invalid_opds_feed"
        assert "error" not in details and "base_url" not in details
    finally:
        session.close()


@pytest.mark.parametrize(("status", "expected"), [
    (401, "auth_required"),
    (403, "access_denied"),
    (429, "rate_limited"),
    (503, "service_unavailable"),
])
def test_detail_http_denial_is_typed_and_redacted(status, expected, caplog):
    session, diag = session_with_handler(
        lambda req: httpx.Response(status, text="token=" + SECRET),
    )
    try:
        with caplog.at_level("WARNING", logger="cifereca.plugin.provider.gutenberg"):
            with pytest.raises(ProviderError, match=expected) as caught:
                session._resolve_epub_url(item_with_private_url())
        assert_redacted(caught, diag, caplog)
    finally:
        session.close()


def test_invalid_detail_xml_does_not_expose_parser_or_signed_url(caplog):
    session, diag = session_with_handler(
        lambda req: httpx.Response(
            200, content=b'<feed><item href="token=' + SECRET.encode(),
        ),
    )
    try:
        with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
            session._resolve_epub_url(item_with_private_url())
        assert_redacted(caught, diag, caplog)
    finally:
        session.close()


def test_search_transport_error_does_not_retain_private_httpx_exception():
    def handler(request):
        raise httpx.ConnectError("PRIVATE_TOKEN_203948 " + str(request.url))

    session, diag = session_with_handler(handler)
    try:
        with pytest.raises(ProviderError, match="service_unavailable") as caught:
            session.search("book")
        assert caught.value.__cause__ is None
        assert SECRET not in str(caught.value)
    finally:
        session.close()
