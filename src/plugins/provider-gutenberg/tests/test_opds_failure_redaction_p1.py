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
                session._parse_feed(
                    b'<feed><bad a="' + SECRET.encode() + b'"', SIGNED,
                )
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


@pytest.mark.parametrize(("status", "public_marker", "machine_code", "core_code"), [
    (400, "service_unavailable", None, None),
    (401, "auth_required", "search_authentication_required",
     "SearchAuthenticationRequired"),
    (403, "access_denied", "search_access_denied", "SearchAccessDenied"),
    (408, "service_unavailable", "search_temporarily_unavailable",
     "SearchTemporarilyUnavailable"),
    (429, "rate_limited", "search_temporarily_unavailable",
     "SearchTemporarilyUnavailable"),
    (503, "service_unavailable", "search_temporarily_unavailable",
     "SearchTemporarilyUnavailable"),
])
def test_search_http_codes_map_to_closed_core_status_without_retry(
    status, public_marker, machine_code, core_code, caplog,
):
    from cifereca.search_failures import search_failure_code

    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(status, text="PRIVATE_TOKEN_203948 " + str(request.url))

    session, diag = session_with_handler(handler)
    try:
        with caplog.at_level("WARNING", logger="cifereca.plugin.provider.gutenberg"):
            with pytest.raises(ProviderError, match=public_marker) as caught:
                session.search("FICTIONAL_BOOK", page=0)
        assert getattr(caught.value, "code", None) == machine_code
        assert search_failure_code(caught.value) == core_code
        assert calls == ["GET"]  # no variant/retry on a failed OPDS endpoint
        assert_redacted(caught, diag, caplog)
    finally:
        session.close()


def test_search_transport_failure_publishes_closed_outage_without_raw_cause():
    from cifereca.search_failures import search_failure_code

    calls = []
    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("PRIVATE_TOKEN_203948")

    session, diag = session_with_handler(handler)
    try:
        with pytest.raises(ProviderError, match="service_unavailable") as caught:
            session.search("FICTIONAL_BOOK")
        assert caught.value.__cause__ is None
        assert caught.value.code == "search_temporarily_unavailable"
        assert search_failure_code(caught.value) == "SearchTemporarilyUnavailable"
        assert len(calls) == 1
        assert SECRET not in repr(diag.events) + str(caught.value)
    finally:
        session.close()


def test_malformed_search_feed_is_typed_outage_not_attested_zero(caplog):
    """Actual search performs one GET; broken XML is not a zero-result proof."""
    from cifereca.search_failures import search_failure_code
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b'<feed><bad token="' + SECRET.encode())
    session, diag = session_with_handler(handler)
    try:
        with caplog.at_level("WARNING", logger="cifereca.plugin.provider.gutenberg"):
            with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
                session.search("book", page=0, field="all")
        assert len(calls) == 1
        assert caught.value.code == "search_temporarily_unavailable"
        assert caught.value.retryable is False
        assert search_failure_code(caught.value) == "SearchTemporarilyUnavailable"
        assert_redacted(caught, diag, caplog)
    finally:
        session.close()


def test_malformed_pagination_feed_is_outage_not_missing_page():
    from cifereca.search_failures import search_failure_code
    with pytest.raises(ProviderError, match="invalid_opds_feed") as caught:
        GutenbergSession._next_page_url(b"<feed><unclosed", SIGNED)
    assert caught.value.code == "search_temporarily_unavailable"
    assert search_failure_code(caught.value) == "SearchTemporarilyUnavailable"
    assert caught.value.__cause__ is None
    assert SECRET not in str(caught.value)


def test_unverified_next_link_does_not_authorize_offline_fallback():
    from cifereca.search_failures import search_failure_code
    feed = (
        b'<feed xmlns="http://www.w3.org/2005/Atom">'
        b'<link rel="next" href="https://private.invalid/?token=SECRET"/>'
        b"</feed>"
    )
    with pytest.raises(ProviderError, match="opds_next_link_unverified") as caught:
        GutenbergSession._next_page_url(feed, SIGNED)
    assert getattr(caught.value, "code", None) is None
    assert search_failure_code(caught.value) is None
    assert SECRET not in str(caught.value)
