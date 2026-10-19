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
