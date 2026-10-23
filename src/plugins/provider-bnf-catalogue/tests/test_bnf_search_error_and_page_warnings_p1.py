"""#1917 BnF: SRU failures and page-local facets are not false global zeros.

httpx MockTransport only: no live catalogue, credentials or acquisitions.
"""
from types import SimpleNamespace

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_bnf_catalogue import BnfCatalogueSession

EMPTY_SRU = b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><numberOfRecords>0</numberOfRecords></searchRetrieveResponse>'


def _session(handler):
    events = []
    session = BnfCatalogueSession(
        credential_id="public", label="", credentials={},
        options={"sru_url": "https://catalogue.example/api/SRU"},
        diagnostics=SimpleNamespace(add=lambda *a, **kw: events.append((a, kw))),
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler))
    return session, events


@pytest.mark.parametrize("status,code", (
    (401, "auth_required"),
    (403, "access_denied"),
    (429, "rate_limited"),
    (500, "service_unavailable"),
    (503, "service_unavailable"),
))
def test_sru_http_failures_are_typed_and_do_not_echo_upstream(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="Bearer PRIVATE_TOKEN https://sru.test/?token=SECRET",
        )

    session, events = _session(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            session.search("Doyle", field="author")
        assert str(raised.value) == f"BnF : catalogue temporairement indisponible ({code})"
        assert len(calls) == 1
        assert session.last_search_warnings == []
        event = next(
            detail["detail"] for args, detail in events
            if args[:3] == ("BNF", "SRU search", "error")
        )
        assert event["http_status"] == status
        assert event["error"] == "HTTPStatusError"
        assert "PRIVATE" not in str(event)
        assert "token=" not in str(event)
    finally:
        session.close()


@pytest.mark.parametrize("field,scope", (
    ("series", "all"),
    ("collection", "all"),
    ("title", "digital"),
    ("title", "physical"),
))
def test_local_postfilter_reports_partial_page_even_on_zero(field, scope):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=EMPTY_SRU)

    session, _ = _session(handler)
    try:
        assert session.search("unmatched", field=field, scope=scope, page=2) == []
        assert len(calls) == 1
        assert calls[0].url.params["startRecord"] == "101"
        assert session.last_search_warnings == [
            "partial_results : post-filtre local limité à la page SRU demandée"
        ]
        # A later native field/all-scope search must clear the warning.
        assert session.search("unmatched", field="author", scope="all") == []
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_unsupported_request_resets_warning_and_never_sends_http():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=EMPTY_SRU)

    session, _ = _session(handler)
    try:
        session.search("unmatched", field="series")
        assert session.last_search_warnings
        calls.clear()
        with pytest.raises(ProviderCapabilityError, match="unsupported_field"):
            session.search("unmatched", field="language")
        assert calls == []
        assert session.last_search_warnings == []
        assert session.search("x", field="series") == []
        assert calls == []
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_full_sru_zero_is_not_confused_with_an_outage():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=EMPTY_SRU)

    session, _ = _session(handler)
    try:
        assert session.search("Missing title", field="title", scope="all") == []
        assert len(calls) == 1
        assert session.last_search_warnings == []
    finally:
        session.close()



def test_invalid_page_clears_stale_partial_warning_without_network():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=EMPTY_SRU)

    session, _ = _session(handler)
    try:
        session.search("Doyle", scope="physical")
        assert session.last_search_warnings
        calls.clear()
        with pytest.raises(ProviderCapabilityError, match="invalid_page"):
            session.search("Doyle", scope="physical", page=-1)
        assert calls == []
        assert session.last_search_warnings == []
    finally:
        session.close()


@pytest.mark.parametrize("status,core_code", (
    (400, None), (404, None),
    (401, "SearchAuthenticationRequired"), (403, "SearchAccessDenied"),
    (408, "SearchTemporarilyUnavailable"),
    (429, "SearchTemporarilyUnavailable"),
    (503, "SearchTemporarilyUnavailable"),
))
def test_sru_failure_preserves_core_refusal_and_stale_policy(status, core_code):
    from cifereca.search_failures import search_failure_code
    from cifereca.core.known_catalog_fallback import public_service_unavailable

    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, text="PRIVATE_TOKEN https://catalogue.example/?token=SECRET"
        )
    session, events = _session(handler)
    try:
        with pytest.raises(ProviderError) as caught:
            session.search("Doyle", field="author")
        assert len(calls) == 1  # Never retry an equivalent SRU request.
        assert search_failure_code(caught.value) == core_code
        assert public_service_unavailable(caught.value) is (
            core_code == "SearchTemporarilyUnavailable"
        )
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__ is True
        assert "PRIVATE" not in str(caught.value)
        assert "SECRET" not in str(caught.value)
        assert "PRIVATE" not in repr(events)
    finally:
        session.close()


def test_sru_transport_or_decoding_is_not_an_attested_zero():
    from cifereca.search_failures import search_failure_code
    for error in (httpx.ReadTimeout("PRIVATE_TOKEN"),
                  httpx.DecodingError("PRIVATE_TOKEN")):
        def handler(request):
            raise error
        session, _ = _session(handler)
        try:
            with pytest.raises(ProviderError) as caught:
                session.search("Doyle")
            assert search_failure_code(caught.value) == "SearchTemporarilyUnavailable"
            assert caught.value.__cause__ is None
            assert "PRIVATE_TOKEN" not in str(caught.value)
        finally:
            session.close()


def test_sru_200_invalid_envelope_never_claims_legitimate_zero():
    from cifereca.search_failures import search_failure_code
    cases = [
        b"<searchRetrieveResponse",
        b"<html><body>PRIVATE_TOKEN</body></html>",
        b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"/>',
    ]
    for payload in cases:
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=payload)
        session, _ = _session(handler)
        try:
            with pytest.raises(ProviderError, match="invalid_sru_response") as caught:
                session.search("Doyle")
            assert len(calls) == 1
            assert search_failure_code(caught.value) == "SearchTemporarilyUnavailable"
            assert caught.value.__cause__ is None
            assert "PRIVATE_TOKEN" not in str(caught.value)
        finally:
            session.close()
