"""Numilog P0: only the private-library origin can certify owned books.

The signed ADEPT download allowlist includes extra partner/CDN hosts.
Those domains must never become authenticated MesLivres, even if an
HTTP redirect gives them the exact same library path and HTML layout.
All account data and URLs here are fictional and never used on the network.
"""
import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog import NumilogSession, url_origin

HOME = "https://www.numilog.com/"
LIBRARY = HOME + "Pages/Compte/MesLivres.aspx"
PRIVATE = "SYNTHETIC_PRIVATE_SENTINEL"
OTHER_ORIGINS = (
    "https://services.numilog.com/",
    "https://www.edenlivres.fr/",
    "https://storage.googleapis.com/",
)
PAGE = '<html><table id="myTable"></table></html>'
PAGE_NEXT = (
    '<html><table id="myTable"></table>'
    '<a rel="next" href="/Pages/Compte/MesLivres.aspx?page=2">Next</a>'
    '</html>'
)


class RedactedDiag:
    def __init__(self):
        self.events = []

    def add(self, module, event, status, *, detail=None, **kwargs):
        self.events.append((module, event, status, detail))


def make_session(handler, *, base_url=None, diagnostics=None):
    session = NumilogSession(
        credential_id=PRIVATE, label="Fictional account",
        credentials={"email": "fiction@example.invalid", "password": PRIVATE},
        options={"base_url": base_url} if base_url else {},
        config={}, diagnostics=diagnostics,
    )
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False,
    )
    return session


@pytest.mark.parametrize("partner", OTHER_ORIGINS)
@pytest.mark.parametrize("stage", ("first", "second"))
def test_library_partner_redirect_never_queries_external_host_or_creates_owned_right(
    partner, stage,
):
    visited = []
    partner_target = partner + "Pages/Compte/MesLivres.aspx?page=2"

    def handler(request):
        url = str(request.url)
        visited.append(url)
        assert url_origin(url) == url_origin(HOME), "Unexpected external request"
        if url == LIBRARY:
            return (
                httpx.Response(302, headers={"location": partner_target})
                if stage == "first"
                else httpx.Response(200, text=PAGE_NEXT)
            )
        if url == LIBRARY + "?page=2":
            return httpx.Response(302, headers={"location": partner_target})
        pytest.fail("Unexpected Numilog library route")

    diag = RedactedDiag()
    session = make_session(handler, diagnostics=diag)
    session.connected = True
    try:
        with pytest.raises(ProviderError, match="origine de bibliothèque inattendue"):
            session.library()
        expected = [LIBRARY] if stage == "first" else [
            LIBRARY, LIBRARY + "?page=2",
        ]
        assert visited == expected
        assert session.connected is True  # rejected origin != proven expiry
        assert "owned" not in repr(diag.events)
        assert PRIVATE not in repr(diag.events)
        assert partner_target not in repr(diag.events)
        assert "auth=" not in repr(diag.events)
    finally:
        session.close()


@pytest.mark.parametrize("partner", OTHER_ORIGINS)
def test_connect_does_not_accept_fake_meslivres_after_redirect(partner):
    visited = []
    target = partner + "Pages/Compte/MesLivres.aspx"

    def handler(request):
        url = str(request.url)
        visited.append(url)
        assert url_origin(url) == url_origin(HOME)
        if url.endswith("/Login.Aspx/AuthUser"):
            assert request.method == "POST"
            return httpx.Response(200, json={"d": {"AuthSuccess": True}})
        if url == LIBRARY:
            return httpx.Response(302, headers={"location": target})
        if url == HOME:
            return httpx.Response(200, text="fictional home")
        pytest.fail("No GET permitted for partner's forged owned catalogue")

    diag = RedactedDiag()
    session = make_session(handler, diagnostics=diag)
    try:
        with pytest.raises(ProviderError, match="origine de bibliothèque inattendue"):
            session.connect()
        assert session.connected is False and not session.accounts
        assert [url_origin(url) for url in visited] == [url_origin(HOME)] * 3
        assert PRIVATE not in repr(diag.events)
        assert target not in repr(diag.events)
    finally:
        session.close()


@pytest.mark.parametrize("base_url", (
    "http://127.0.0.1:8094/",
    "http://localhost:8094/",
))
def test_loopback_library_still_rejects_real_numilog_as_origin(base_url):
    visited = []
    host_origin = url_origin(base_url)

    def handler(request):
        url = str(request.url)
        visited.append(url)
        assert url_origin(url) == host_origin
        return httpx.Response(302, headers={"location": LIBRARY})

    session = make_session(handler, base_url=base_url)
    session.connected = True
    try:
        with pytest.raises(ProviderError, match="origine de bibliothèque inattendue"):
            session.library()
        assert len(visited) == 1
    finally:
        session.close()


def test_same_origin_meslivres_pagination_and_redirect_continue_to_work():
    visited = []

    def handler(request):
        url = str(request.url)
        visited.append(url)
        if url == LIBRARY:
            return httpx.Response(200, text=PAGE_NEXT)
        if url == LIBRARY + "?page=2":
            return httpx.Response(302, headers={
                "location": "/Pages/Compte/MesLivres.aspx?page=3",
            })
        if url == LIBRARY + "?page=3":
            return httpx.Response(200, text=PAGE)
        pytest.fail("Unexpected request")

    session = make_session(handler)
    session.connected = True
    try:
        assert session.library() == []
        assert visited == [LIBRARY, LIBRARY + "?page=2", LIBRARY + "?page=3"]
        assert session.connected is True
    finally:
        session.close()
