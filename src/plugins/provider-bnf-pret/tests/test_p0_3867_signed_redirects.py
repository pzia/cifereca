"""P0 #3867: signed BnF ACSM redirects use Core HTTPS, not private SSO HTTP.

All HTTP responses and ACSM bytes are synthetic. No provider network.
"""
import httpx
import pytest

from bnf_token_fixture import TOKEN
from cifereca.plugin_api import HTTPX_TRANSPORT
from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError


SIGNED = (
    "https://pret.bnf.fr/loans/synthetic-loan?"
    "device_type=reader&drm=acs4&nonce=syntheticNonce123&"
    "timestamp=2026-10-26T00%3A00%3A00Z&"
    "token=0123456789abcdef0123456789abcdef01234567"
)
CDN_A = "https://new-cdn.example/download?token=opaque-a"
CDN_B = "https://another-cdn.example/token.acsm?key=opaque-b"


def _mock_public(monkeypatch, handler):
    seen = []

    def factory(*, producer, timeout):
        assert producer == "provider.bnf-pret.delivery"
        assert 0 < timeout <= 15

        def record(request):
            seen.append(request)
            return handler(request)

        return httpx.Client(
            transport=httpx.MockTransport(record),
            follow_redirects=False, trust_env=False,
        )

    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", factory)
    return seen


def test_fresh_signed_bnf_get_can_redirect_twice_to_unknown_https_hosts(monkeypatch):
    initial = []

    def private(request):
        initial.append(request)
        assert str(request.url) == SIGNED
        return httpx.Response(302, headers={
            "Location": CDN_A, "Set-Cookie": "bnf_private=secret; Path=/",
        })

    def public(request):
        if str(request.url) == CDN_A:
            return httpx.Response(307, headers={"location": CDN_B})
        assert str(request.url) == CDN_B
        return httpx.Response(200, content=TOKEN, headers={
            "content-type": "application/vnd.adobe.adept+xml",
        })

    seen = _mock_public(monkeypatch, public)
    client = ReaderHTTPClient(transport=httpx.MockTransport(private))
    try:
        assert client.loan_token("synthetic-loan", SIGNED,
                                 admission_current=lambda: True) == TOKEN
        assert len(initial) == 1
        assert [str(request.url) for request in seen] == [CDN_A, CDN_B]
        for request in seen:
            for private_header in ("cookie", "authorization", "referer", "x-csrf-token"):
                assert private_header not in request.headers
    finally:
        client.close()


@pytest.mark.parametrize("destination", [
    "http://new-cdn.example/token.acsm",
    "https://127.0.0.1/token.acsm",
    "file:///etc/passwd",
    "https://someone:secret@new-cdn.example/token.acsm",
])
def test_untrusted_delivery_redirect_never_follows_url(monkeypatch, destination):
    seen = _mock_public(monkeypatch, lambda request: pytest.fail(
        "unsafe redirect must not issue a public GET"
    ))
    client = ReaderHTTPClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": destination})
    ))
    try:
        with pytest.raises(ReaderHTTPError, match="^download_redirect_refused$"):
            client.loan_token("synthetic-loan", SIGNED)
        assert seen == []
    finally:
        client.close()


def test_revocation_after_authenticated_redirect_stops_public_get(monkeypatch):
    state = {"admitted": True}
    seen = _mock_public(monkeypatch, lambda request: pytest.fail(
        "revoked account must not trigger any redirected GET"
    ))

    def private(request):
        state["admitted"] = False
        return httpx.Response(302, headers={"location": CDN_A})

    client = ReaderHTTPClient(transport=httpx.MockTransport(private))
    try:
        with pytest.raises(ReaderHTTPError, match="^download_redirect_refused$"):
            client.loan_token("synthetic-loan", SIGNED,
                              admission_current=lambda: state["admitted"])
        assert seen == []
    finally:
        client.close()


def test_same_origin_ss0_activity_redirect_is_never_a_file(monkeypatch):
    seen = _mock_public(monkeypatch, lambda request: pytest.fail(
        "HTML or SSO must not become an ACSM download"
    ))
    client = ReaderHTTPClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(302, headers={
            "location": "https://pret.bnf.fr/my_profile/activity",
        })
    ))
    try:
        with pytest.raises(ReaderHTTPError, match="^download_redirect_refused$"):
            client.loan_token("synthetic-loan", SIGNED)
        assert seen == []
    finally:
        client.close()
