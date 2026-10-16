"""#1917: Baen distinguishes public search failures without exposing response details.

All requests are handled by httpx.MockTransport. No live Baen HTTP, account,
signed URL, acquisition or file write.
"""
import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_baen import BaenSession, FREE


def session_with_transport(handler):
    session = BaenSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler))
    session.connect()
    return session


@pytest.mark.parametrize("status,code", (
    (401, "auth_required"),
    (403, "access_denied"),
    (404, "service_unavailable"),
    (429, "rate_limited"),
    (500, "service_unavailable"),
    (503, "service_unavailable"),
))
def test_http_errors_have_stable_codes_without_leaking_response(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="PRIVATE_BEARER_TOKEN https://example.test/?access_token=PRIVATE",
        )

    session = session_with_transport(handler)
    try:
        with pytest.raises(ProviderError) as caught:
            session.search("Private query", field="all")
        assert str(caught.value) == f"Baen : {code}"
        assert "PRIVATE" not in str(caught.value)
        assert len(calls) == 1
        assert str(calls[0].url).startswith(FREE)
        assert calls[0].url.params["q"] == "Private query"
    finally:
        session.close()


def test_transport_failure_is_service_unavailable_and_no_secret_is_exposed():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("PRIVATE_NETWORK_DETAILS", request=request)

    session = session_with_transport(handler)
    try:
        with pytest.raises(ProviderError) as caught:
            session.search("book", field="all")
        assert str(caught.value) == "Baen : service_unavailable"
        assert len(calls) == 1
    finally:
        session.close()


def test_empty_success_is_not_confused_with_service_unavailable():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="<html><body>Aucun résultat</body></html>")

    session = session_with_transport(handler)
    try:
        assert session.search("absent", field="all") == []
        assert len(calls) == 1
    finally:
        session.close()
