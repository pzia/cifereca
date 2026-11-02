"""#3867: Paris PNB signed hops use public-only transport, not host allowlists.

No real Paris account, network, loan mutation or ACSM retrieval.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca_paris_client import ParisClient, ParisRedirectError
from cifereca.plugin_api import HTTPX_TRANSPORT


START = "https://bibliotheques.paris.fr/numerique/ASSARedirect.ashx?ticket=synthetic"
FOREIGN = "https://supplier-new.example/gateway?key=synthetic"
FINAL = "https://fresh-cdn.example/deliver/token.acsm?sig=synthetic"


def test_unknown_https_supplier_is_public_isolated_and_stops_before_acsm_get(
    monkeypatch,
):
    client = ParisClient("https://bibliotheques.paris.fr")
    private_calls = []
    public_calls = []

    def private(url, **kwargs):
        private_calls.append((url, kwargs))
        assert url == START
        assert kwargs["headers"]["Referer"] == (
            "https://bibliotheques.paris.fr/numerique/"
        )
        return httpx.Response(
            302, headers={"Location": FOREIGN, "Set-Cookie": "session=private"},
            request=httpx.Request("GET", url),
        )

    def factory(*, producer, timeout):
        assert producer == "provider.paris.pnb.delivery"
        assert 0 < timeout <= 30

        def deliver(request):
            public_calls.append(request)
            assert str(request.url) == FOREIGN
            return httpx.Response(307, headers={"Location": FINAL})

        return httpx.Client(
            transport=httpx.MockTransport(deliver), follow_redirects=False,
            trust_env=False,
        )

    monkeypatch.setattr(client.client, "get", private)
    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", factory)
    try:
        assert client._resolve_pnb_loan_to_acsm(START) == FINAL
        assert len(private_calls) == len(public_calls) == 1
        assert all(
            name not in public_calls[0].headers for name in (
                "cookie", "authorization", "referer", "x-csrf-token",
                "proxy-authorization",
            )
        )
    finally:
        client.close()


@pytest.mark.parametrize("target", [
    "http://new-cdn.example/file.acsm",
    "https://127.0.0.1/file.acsm",
    "https://[::1]/file.acsm",
    "https://localhost/file.acsm",
    "https://person:secret@new-cdn.example/file.acsm",
    "https://cdn.example:8443/file.acsm",
    "https://cdn.example/file.acsm#fragment",
    "https://cdn.example/\\secret.acsm",
])
def test_invalid_redirect_never_issues_second_get_or_discloses_signed_token(
    monkeypatch, target,
):
    client = ParisClient("https://bibliotheques.paris.fr")
    private_calls = []
    public_calls = []

    def private(url, **_kwargs):
        private_calls.append(url)
        return httpx.Response(
            302, headers={"Location": target}, request=httpx.Request("GET", url),
        )

    def no_public(**kwargs):
        public_calls.append(kwargs)
        pytest.fail("unsafe URL must not open public transport")

    monkeypatch.setattr(client.client, "get", private)
    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", no_public)
    try:
        with pytest.raises(ParisRedirectError) as error:
            client._resolve_pnb_loan_to_acsm(START)
        assert private_calls == [START] and public_calls == []
        assert "secret" not in str(error.value).lower()
        assert "ticket=" not in str(error.value)
    finally:
        client.close()


def test_untrusted_host_cannot_start_paris_borrow_without_official_link(
    monkeypatch,
):
    client = ParisClient("https://bibliotheques.paris.fr")
    try:
        monkeypatch.setattr(client.client, "get", lambda *_a, **_kw:
                            pytest.fail("unattested start must not GET"))
        with pytest.raises(Exception, match="Lien PNB initial invalide"):
            client._resolve_pnb_loan_to_acsm(
                "https://fresh-cdn.example/ASSARedirect.ashx?ticket=synthetic"
            )
    finally:
        client.close()



def test_public_transport_exception_redacts_signed_supplier_url(monkeypatch):
    client = ParisClient("https://bibliotheques.paris.fr")
    private_url = "https://cdn.example/token.acsm?secret=PRIVATE_BEARER"
    def factory(*, producer, timeout):
        def fail(request):
            raise httpx.ConnectTimeout("PRIVATE_BEARER: " + str(request.url))
        return httpx.Client(
            transport=httpx.MockTransport(fail), follow_redirects=False,
            trust_env=False,
        )
    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", factory)
    try:
        with pytest.raises(Exception, match="Transport HTTPS PNB indisponible") as error:
            client._get_pnb_response(private_url)
        assert "PRIVATE_BEARER" not in str(error.value)
        assert "cdn.example" not in str(error.value)
    finally:
        client.close()



@pytest.mark.parametrize("invalid", [
    None, "", 123, "https://[invalid-ipv6/path",
    "https://cdn.example:bad/file.acsm",
])
def test_malformed_host_never_raises_unredacted_parser_exception(invalid):
    client = ParisClient("https://bibliotheques.paris.fr")
    try:
        with pytest.raises(ParisRedirectError):
            client._validate_pnb_redirect_url(invalid)
    finally:
        client.close()
