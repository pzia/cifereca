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



@pytest.mark.parametrize("status,core_code", (
    (400, None),
    (401, "SearchAuthenticationRequired"),
    (403, "SearchAccessDenied"),
    (404, None),
    (408, "SearchTemporarilyUnavailable"),
    (429, "SearchTemporarilyUnavailable"),
    (503, "SearchTemporarilyUnavailable"),
))
def test_baen_closed_core_code_does_not_authorize_stale_fallback_on_refusals(
    status, core_code,
):
    from cifereca.search_failures import search_failure_code
    from cifereca.core.known_catalog_fallback import public_service_unavailable

    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(
            status, text="PRIVATE_ACCESS_TOKEN https://signed.invalid/?token=SECRET",
        )
    session = session_with_transport(handler)
    try:
        with pytest.raises(ProviderError) as caught:
            session.search("book", page=0)
        assert len(requests) == 1
        assert search_failure_code(caught.value) == core_code
        assert public_service_unavailable(caught.value) is (
            core_code == "SearchTemporarilyUnavailable"
        )
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__ is True
        assert "PRIVATE" not in str(caught.value)
        assert "SECRET" not in str(caught.value)
    finally:
        session.close()


@pytest.mark.parametrize("failure,core_code", (
    (httpx.ReadTimeout("PRIVATE_TOKEN"), "SearchTemporarilyUnavailable"),
    (httpx.ConnectError("PRIVATE_TOKEN"), "SearchTemporarilyUnavailable"),
    (httpx.DecodingError("PRIVATE_TOKEN"), "SearchTemporarilyUnavailable"),
    (httpx.TooManyRedirects("PRIVATE_TOKEN"), None),
))
def test_baen_failed_public_get_has_one_attempt_and_no_cause_leak(
    failure, core_code,
):
    from cifereca.search_failures import search_failure_code
    from cifereca.core.known_catalog_fallback import public_service_unavailable

    requests = []
    def handler(req):
        requests.append(req)
        raise failure
    session = session_with_transport(handler)
    try:
        with pytest.raises(ProviderError, match="^Baen : service_unavailable$") as caught:
            session.search("book", page=0)
        assert len(requests) == 1
        assert search_failure_code(caught.value) == core_code
        assert public_service_unavailable(caught.value) is (
            core_code == "SearchTemporarilyUnavailable"
        )
        assert caught.value.__cause__ is None
        assert "PRIVATE_TOKEN" not in str(caught.value)
    finally:
        session.close()
