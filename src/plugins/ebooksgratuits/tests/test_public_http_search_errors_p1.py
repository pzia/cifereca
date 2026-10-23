"""#1917: public errors must not confuse a refusal, throttle, outage or true zero.

All HTTP responses are synthetic MockTransport fixtures; no external network.
"""
import importlib.util
from pathlib import Path

import httpx
import pytest

from cifereca.plugin_api import ProviderError


MODULE_PATH = Path(__file__).resolve().parents[1] / "cifereca_ebooksgratuits.py"
spec = importlib.util.spec_from_file_location("ebooksgratuits_http_p1", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

FORM = '<select name="id_auteur"><option value="17">Doyle</option></select>'


def _session(handler):
    session = mod.EbooksSession(
        credential_id="public", label="", credentials={}, options={"acquisition_backend":"html"},
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler))
    session.connected = True
    return session


@pytest.mark.parametrize("status,code", [
    (401, "auth_required"),
    (403, "access_denied"),
    (429, "rate_limited"),
    (500, "service_unavailable"),
    (503, "service_unavailable"),
])
@pytest.mark.parametrize("route", ["form", "filter", "text"])
def test_failures_have_stable_codes_and_never_echo_upstream_secrets(status, code, route):
    requests = []

    def handle(request):
        requests.append(request)
        if route == "filter" and len(requests) == 1:
            return httpx.Response(200, text=FORM)
        return httpx.Response(status, text="PRIVATE_PROVIDER_URL?token=SECRET")

    session = _session(handle)
    try:
        args = (
            dict(field="author", query="Doyle")
            if route in {"form", "filter"}
            else dict(field="title", query="Doyle")
        )
        with pytest.raises(ProviderError, match=code) as exc:
            session.search(**args)
        assert str(exc.value) == f"Ebooks libres et gratuits : {code}"
        for secret in ("PRIVATE", "SECRET", "token=", "Doyle"):
            assert secret not in str(exc.value)
        assert len(requests) == (2 if route == "filter" else 1)
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_transport_failure_is_service_unavailable_without_provider_details():
    def handle(request):
        raise httpx.ConnectError("PRIVATE_SUPPLIER_URL", request=request)

    session = _session(handle)
    try:
        with pytest.raises(ProviderError, match="service_unavailable") as exc:
            session.search("Example", field="all")
        assert "PRIVATE" not in str(exc.value)
    finally:
        session.close()


@pytest.mark.parametrize("field", ("author", "subject", "genre", "category"))
def test_blank_selector_request_is_legitimate_empty_without_http(field):
    requests = []

    def handle(request):
        requests.append(request)
        pytest.fail("Blank selector query must not fetch a public page")

    session = _session(handle)
    try:
        assert session.search("   ", field=field) == []
        assert requests == []
        assert session.last_search_warnings == []
    finally:
        session.close()


@pytest.mark.parametrize("status,public_status", (
    (400, None),
    (401, "SearchAuthenticationRequired"),
    (403, "SearchAccessDenied"),
    (404, None),
    (408, "SearchTemporarilyUnavailable"),
    (429, "SearchTemporarilyUnavailable"),
    (500, "SearchTemporarilyUnavailable"),
))
@pytest.mark.parametrize("backend", ("html", "opds"))
def test_wrapped_public_search_keeps_exact_core_refusal_and_outage(
    status, public_status, backend,
):
    from cifereca.search_failures import search_failure_code
    from cifereca.core.known_catalog_fallback import public_service_unavailable
    requests = []
    def handler(request):
        requests.append(request.method)
        return httpx.Response(status, text="PRIVATE_TOKEN?secret=SIGNED")
    session = mod.EbooksSession(
        credential_id="public", label="", credentials={},
        options={"acquisition_backend": backend},
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    session.connected = True
    try:
        with pytest.raises(ProviderError) as caught:
            session.search("Doyle", field="title")
        assert search_failure_code(caught.value) == public_status
        assert public_service_unavailable(caught.value) is (
            public_status == "SearchTemporarilyUnavailable"
        )
        assert requests == (["POST"] if backend == "html" else ["GET"])
        assert caught.value.__cause__ is None
        assert "PRIVATE" not in str(caught.value)
        assert "SIGNED" not in str(caught.value)
    finally:
        session.close()


def test_public_redirect_loop_is_not_fallback_authorization():
    from cifereca.search_failures import search_failure_code
    failure = mod._public_search_error(httpx.TooManyRedirects("PRIVATE_TOKEN"))
    assert search_failure_code(failure) is None
    assert getattr(failure, "code", None) is None
    assert "PRIVATE_TOKEN" not in str(failure)


def test_public_decoding_error_is_offline_outage_not_catalogue_zero():
    from cifereca.search_failures import search_failure_code
    failure = mod._public_search_error(httpx.DecodingError("PRIVATE_TOKEN"))
    assert search_failure_code(failure) == "SearchTemporarilyUnavailable"
    assert failure.retryable is False
    assert "PRIVATE_TOKEN" not in str(failure)
