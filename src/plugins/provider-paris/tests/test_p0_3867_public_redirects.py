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


def test_signed_supplier_redirect_cycle_stops_before_third_get(monkeypatch):
    """A replay of the original signed URL is forbidden even below hop limit."""
    client = ParisClient("https://bibliotheques.paris.fr")
    calls = []

    def respond(url, **_kwargs):
        calls.append(url)
        assert len(calls) <= 2, "redirect cycle must not repeat a signed GET"
        target = FOREIGN if url == START else START
        return httpx.Response(
            302, headers={"Location": target},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(client, "_get_pnb_response", respond)
    try:
        with pytest.raises(Exception, match="Boucle de redirections PNB") as error:
            client._resolve_pnb_loan_to_acsm(START)
        assert calls == [START, FOREIGN]
        assert "ticket=" not in str(error.value)
        assert "key=" not in str(error.value)
    finally:
        client.close()


def test_paris_selected_delivery_redirect_cycle_never_publishes(tmp_path, monkeypatch):
    """The selected-right staging path must enforce the same no-replay rule."""
    from types import SimpleNamespace as NS

    from cifereca.plugin_api import ProviderConfigurationError
    from tests.test_p0_3252_paris_download_scope import SIGNED, prepare

    session, account, loan, client, calls = prepare(tmp_path, monkeypatch)
    foreign = "https://new-delivery.example/file?token=SYNTHETIC_SECRET"

    def respond(url):
        calls.append(url)
        assert len(calls) <= 2, "selected candidate must not repeat a signed GET"
        return NS(
            status_code=307, url=url,
            headers={"location": foreign if url == SIGNED else SIGNED},
        )

    client._get_pnb_response = respond
    try:
        with pytest.raises(ProviderConfigurationError, match="Boucle de redirections PNB") as error:
            session.download_digital(loan, account, tmp_path)
        assert calls == [SIGNED, foreign]
        assert "SYNTHETIC_SECRET" not in str(error.value)
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_public_supplier_200_is_bounded_while_preserving_candidate_bytes(monkeypatch):
    client = ParisClient("https://bibliotheques.paris.fr")
    from cifereca_paris_client import _MAX_PNB_PUBLIC_BODY_BYTES
    public_requests = []

    def factory(*, producer, timeout):
        def respond(request):
            public_requests.append(request)
            return httpx.Response(
                200, headers={"Content-Type": "application/vnd.adobe.adept+xml"},
                content=b"<fulfillment>synthetic</fulfillment>",
            )
        return httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)

    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", factory)
    try:
        response = client._get_pnb_response(FOREIGN)
        assert response.status_code == 200
        assert response.content == b"<fulfillment>synthetic</fulfillment>"
        assert str(response.url) == FOREIGN
        assert len(public_requests) == 1
        assert _MAX_PNB_PUBLIC_BODY_BYTES == 128 * 1024 * 1024
    finally:
        client.close()


def test_public_supplier_rejects_oversize_stream_without_leaking_signed_url(monkeypatch):
    import cifereca_paris_client as module
    client = ParisClient("https://bibliotheques.paris.fr")
    signed = "https://new-supplier.example/file.acsm?token=PRIVATE_SIGNATURE"

    def factory(*, producer, timeout):
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"X" * 17)
        ), trust_env=False)

    monkeypatch.setattr(module, "_MAX_PNB_PUBLIC_BODY_BYTES", 16)
    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", factory)
    try:
        with pytest.raises(module.ParisError, match="trop volumineuse") as error:
            client._get_pnb_response(signed)
        assert "PRIVATE_SIGNATURE" not in str(error.value)
        assert "new-supplier.example" not in str(error.value)
    finally:
        client.close()


def test_public_supplier_refuses_announced_oversize_before_reading_body(monkeypatch):
    import cifereca_paris_client as module
    client = ParisClient("https://bibliotheques.paris.fr")
    seen = []

    class MustNotRead(httpx.SyncByteStream):
        def __iter__(self):
            seen.append("read")
            raise AssertionError("oversized response body must not be consumed")
        def close(self):
            pass

    def factory(*, producer, timeout):
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Length": "17"}, stream=MustNotRead(),
            )
        ), trust_env=False)

    monkeypatch.setattr(module, "_MAX_PNB_PUBLIC_BODY_BYTES", 16)
    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", factory)
    try:
        with pytest.raises(module.ParisError, match="trop volumineuse"):
            client._get_pnb_response(FOREIGN)
        assert seen == []
    finally:
        client.close()


def test_public_supplier_redirect_body_is_never_read(monkeypatch):
    client = ParisClient("https://bibliotheques.paris.fr")
    seen = []

    class MustNotRead(httpx.SyncByteStream):
        def __iter__(self):
            seen.append("read")
            raise AssertionError("redirect body must not be consumed")
        def close(self):
            pass

    def factory(*, producer, timeout):
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                307, headers={"Location": FINAL}, stream=MustNotRead(),
            )
        ), trust_env=False)

    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", factory)
    try:
        response = client._get_pnb_response(FOREIGN)
        assert response.status_code == 307
        assert response.headers["location"] == FINAL
        assert response.content == b""
        assert seen == []
    finally:
        client.close()


@pytest.mark.parametrize("malformed_location", [
    "https://cdn.example/file.acsm\rPRIVATE",
    "https://cdn.example/file.acsm\nPRIVATE",
    "https://cdn.example/file.acsm\tPRIVATE",
    "https://cdn.example/file.acsm\\\\PRIVATE",
    "https://cdn.example/file.acsm#FRAGMENT",
])
def test_raw_signed_location_refused_before_urljoin_or_second_get(
    monkeypatch, malformed_location,
):
    # HTTP response headers from untrusted suppliers must never be repaired
    # by urllib before they are authorized as the next signed GET.
    from types import SimpleNamespace
    client = ParisClient("https://bibliotheques.paris.fr")
    calls = []

    def respond(url, **_kwargs):
        calls.append(url)
        assert calls == [START]
        return SimpleNamespace(
            status_code=302, url=START,
            headers={"location": malformed_location},
        )

    monkeypatch.setattr(client, "_get_pnb_response", respond)
    monkeypatch.setattr(client, "_pnb_trace_response", lambda *_a, **_kw: None)
    try:
        with pytest.raises(ParisRedirectError) as error:
            client._resolve_pnb_loan_to_acsm(START)
        assert calls == [START]
        assert "PRIVATE" not in str(error.value)
        assert "FRAGMENT" not in str(error.value)
    finally:
        client.close()


def test_same_origin_relative_location_still_valid(monkeypatch):
    client = ParisClient("https://bibliotheques.paris.fr")
    calls = []
    relative = "/numerique/another-hop"

    def respond(url, **_kwargs):
        calls.append(url)
        return httpx.Response(
            302, headers={
                "location": relative if len(calls) == 1 else FINAL,
            }, request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(client, "_get_pnb_response", respond)
    try:
        assert client._resolve_pnb_loan_to_acsm(START) == FINAL
        assert calls == [
            START,
            "https://bibliotheques.paris.fr/numerique/another-hop",
        ]
    finally:
        client.close()


@pytest.mark.parametrize("signed_url", (FOREIGN, START))
def test_guarded_paris_pnb_stream_stops_when_core_lease_revoked_mid_body(
    monkeypatch, signed_url,
):
    from cifereca_paris_client import ParisError
    client = ParisClient("https://bibliotheques.paris.fr")
    live = [True]
    seen = []

    class RevokingBody(httpx.SyncByteStream):
        def __iter__(self):
            seen.append("first-chunk")
            live[0] = False
            yield b"A" * (64 * 1024)
            seen.append("forbidden-second-chunk")
            yield b"not-allowed"
        def close(self):
            pass

    def supplier(request):
        seen.append("request")
        return httpx.Response(
            200, headers={"Content-Type": "application/vnd.adobe.adept+xml"},
            stream=RevokingBody(),
        )

    def factory(*, producer, timeout):
        return httpx.Client(
            transport=httpx.MockTransport(supplier),
            trust_env=False, follow_redirects=False,
        )

    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", factory)
    if signed_url == START:
        # The authenticated same-origin hop must also be streamed under
        # a live Core lease rather than using eager, uncancellable get().
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(supplier),
            trust_env=False, follow_redirects=False,
        )
    try:
        with pytest.raises(ParisError, match="Core admission revoked"):
            client._get_pnb_response(
                signed_url, admission_current=lambda: live[0],
            )
        assert seen == ["request", "first-chunk"]
    finally:
        client.close()


def test_guarded_paris_pnb_refuses_revoked_lease_before_any_https_request(monkeypatch):
    from cifereca_paris_client import ParisError
    client = ParisClient("https://bibliotheques.paris.fr")
    monkeypatch.setattr(
        HTTPX_TRANSPORT, "public_client",
        lambda **_kwargs: pytest.fail("revoked Core must not construct HTTP client"),
    )
    try:
        with pytest.raises(ParisError, match="Core admission revoked"):
            client._get_pnb_response(FOREIGN, admission_current=lambda: False)
    finally:
        client.close()
