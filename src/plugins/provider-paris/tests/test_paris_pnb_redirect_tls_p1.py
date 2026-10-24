"""PNB: redirects to known hosts must never downgrade signed tokens to HTTP.

No network is opened: each hop is captured by a fake response. The discovery
of the initial loan link is deliberately unchanged (#2914).
"""
from __future__ import annotations

import httpx
import pytest

from cifereca_paris_client import ParisClient, ParisRedirectError


@pytest.fixture
def client():
    session = ParisClient("https://bibliotheques.paris.fr")
    try:
        yield session
    finally:
        session.close()


@pytest.mark.parametrize("host", (
    "bibliotheques.paris.fr",
    "marketplace.syracuse.cloud",
    "example.syracuse.cloud",
    "pnb-dilicom.centprod.com",
    "numilog.com",
    "www.numilog.com",
    "www.edenlivres.fr",
    "www.cantook.net",
    "acs4.edelivres.fr",
    "acs4.edenlivres.fr",
    "acs4.cantook.net",
))
def test_known_redirect_hosts_require_https_with_standard_port(client, host):
    for suffix in ("", ":443"):
        client._validate_pnb_redirect_url(
            f"https://{host}{suffix}/some/signed/path?token=PRIVATE"
        )
    for origin in ("http", "https"):
        for port in (81, 8080, 444, 8443):
            with pytest.raises(ParisRedirectError) as exc:
                client._validate_pnb_redirect_url(
                    f"{origin}://{host}:{port}/private/path?token=PRIVATE"
                )
            assert "PRIVATE" not in str(exc.value)
    with pytest.raises(ParisRedirectError):
        client._validate_pnb_redirect_url(
            f"http://{host}/private/path?token=PRIVATE"
        )


@pytest.mark.parametrize("url", (
    "https://user:secret@bibliotheques.paris.fr/ASSARedirect.ashx",
    "https://user:secret@pnb-dilicom.centprod.com/LOAN/private",
    "https://evil.example/ASSARedirect.ashx",
    "https://bibliotheques.paris.fr.evil.example/ASSARedirect.ashx",
    "ftp://numilog.com/path",
    "https://www.numilog.com:invalid/path",
))
def test_rejected_host_credentials_scheme_and_port_do_not_leak_or_navigate(client, url):
    with pytest.raises(ParisRedirectError) as exc:
        client._validate_pnb_redirect_url(url)
    assert "secret" not in str(exc.value).lower()
    assert "/LOAN/" not in str(exc.value)


@pytest.mark.parametrize("next_url", (
    "http://pnb-dilicom.centprod.com/LOAN/PRIVATE_TOKEN",
    "http://numilog.com/path/PRIVATE_TOKEN",
    "https://numilog.com:8080/path/PRIVATE_TOKEN",
    "https://marketplace.syracuse.cloud:444/PRIVATE_TOKEN",
))
def test_untrusted_second_hop_blocked_before_next_http_request(client, monkeypatch, next_url):
    first = "https://bibliotheques.paris.fr/numerique/ASSARedirect.ashx?url=signed"
    requested = []
    def respond(url, **_kwargs):
        requested.append(url)
        return httpx.Response(
            302, headers={"Location": next_url},
            request=httpx.Request("GET", url),
        )
    monkeypatch.setattr(client, "_get_pnb_response", respond)
    with pytest.raises(ParisRedirectError):
        client._resolve_pnb_loan_to_acsm(first)
    assert requested == [first]


def test_canonical_https_dilicom_acsm_second_hop_remains_valid(client, monkeypatch):
    first = "https://bibliotheques.paris.fr/numerique/ASSARedirect.ashx?url=signed"
    end = "https://pnb-dilicom.centprod.com/LOAN/PRIVATE/urllink.acsm"
    requested = []
    def respond(url, **_kwargs):
        requested.append(url)
        return httpx.Response(
            302, headers={"Location": end}, request=httpx.Request("GET", url),
        )
    monkeypatch.setattr(client, "_get_pnb_response", respond)
    assert client._resolve_pnb_loan_to_acsm(first) == end
    assert requested == [first]  # Only next-hop URL is returned, never GETed.
