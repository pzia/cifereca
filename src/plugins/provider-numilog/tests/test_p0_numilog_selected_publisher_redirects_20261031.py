"""#2797: Core-selected staging follows an attested publisher redirect chain.

Reproduces the 31-Oct-2026 delivery trace: the signed Numilog handler
responds with 302 to Dilicom or Eden, then returns an ACSM. No real network,
publisher credentials, file publication or ADEPT fulfillment.
"""
import httpx
import pytest

import cifereca_numilog as provider
from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from tests.test_numilog_owned_js_delivery_p0 import SIGNED
from tests.test_numilog_signed_acsm_transport_p0 import ACSM
from tests.test_p1_3252_selected_delivery_candidate import setup, selected
from tests.pinned_public_https_fixture import mock_selected_public_client

DILICOM = "https://hub-dilicom.centprod.com/v3//link/SYNTHETIC/receipt"
EDEN = "https://www.edenlivres.fr/organisations/75/customers/SYNTHETIC/transactions/receipt"
FINAL = "https://publisher-delivery.example.org/fulfill/acsm"


@pytest.mark.parametrize("publisher", [DILICOM, EDEN])
@pytest.mark.parametrize("with_second_redirect", [False, True])
def test_core_selected_acsm_follows_attested_publisher_redirects(
    tmp_path, monkeypatch, publisher, with_second_redirect,
):
    session, account, broker, reads, right = setup()
    requested = []
    def handler(request):
        requested.append(request)
        url = str(request.url)
        if url == SIGNED:
            return httpx.Response(302, headers={"location": publisher})
        if url == publisher:
            if with_second_redirect:
                return httpx.Response(302, headers={"location": FINAL})
            return httpx.Response(
                200, content=ACSM,
                headers={"content-type": "application/vnd.adobe.adept+xml"},
            )
        if url == FINAL and with_second_redirect:
            return httpx.Response(
                200, content=ACSM,
                headers={"content-type": "application/vnd.adobe.adept+xml"},
            )
        pytest.fail("unexpected or replayed signed GET")
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False, trust_env=False,
    )
    public_clients = mock_selected_public_client(monkeypatch, provider, handler)
    monkeypatch.setattr(provider, "publish", lambda *a, **kw: pytest.fail(
        "Core staging must not publish an EPUB",
    ))
    monkeypatch.setattr(
        provider, "publish_artifact_file",
        lambda *a, **kw: pytest.fail("Core staging must not publish"),
    )
    try:
        staged, provenance = session.rights_selected_delivery_candidate(
            account, **selected(right, tmp_path),
        )
        assert staged.is_file()
        assert staged.parent == tmp_path
        assert staged.suffix == ".acsm"
        assert staged.read_bytes() == ACSM
        assert provenance == session.library_url
        assert [str(req.url) for req in requested] == (
            [SIGNED, publisher, FINAL] if with_second_redirect
            else [SIGNED, publisher]
        )
        assert not list(tmp_path.glob("*.part"))
        assert not list(tmp_path.glob("*.epub"))
        # The request-specific blank Cookie must not disclose a Numilog
        # session to any supplier in the signed downstream chain.
        assert all(not req.headers.get("cookie") for req in requested)
        assert all(not req.headers.get("authorization") for req in requested)
        assert all("MesLivres" not in req.headers.get("referer", "")
                   for req in requested)
        assert len(public_clients) == 1
        assert public_clients[0][2] == (
            [SIGNED, publisher, FINAL] if with_second_redirect
            else [SIGNED, publisher]
        )
        # All signed hops are driven by the Core transport; static DNS
        # preflight is not security proof of the actual connected TCP peer.
    finally:
        session.close()


@pytest.mark.parametrize("unsafe", [
    "http://publisher-delivery.example.org/acsm",
    "https://127.0.0.1/private",
    "https://username:secret@publisher-delivery.example.org/acsm",
    "https://publisher-delivery.example.org/acsm#fragment",
])
def test_selected_publisher_redirect_rejects_unsafe_target_without_second_get(
    tmp_path, monkeypatch, unsafe,
):
    session, account, broker, reads, right = setup()
    requested = []
    def handler(request):
        requested.append(str(request.url))
        assert str(request.url) == SIGNED
        return httpx.Response(302, headers={"location": unsafe})
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False, trust_env=False,
    )
    public_clients = mock_selected_public_client(monkeypatch, provider, handler)
    try:
        with pytest.raises(ProviderCapabilityError, match="numilog_candidate_transport_refused"):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert requested == [SIGNED]
        assert len(public_clients) == 1
        assert public_clients[0][2] == [SIGNED]
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


def test_selected_redirect_owner_change_blocks_next_supplier_get(tmp_path, monkeypatch):
    session, account, broker, reads, right = setup()
    requested = []
    def handler(request):
        requested.append(str(request.url))
        assert str(request.url) == SIGNED
        broker.owner = "identity-B"
        return httpx.Response(302, headers={"location": DILICOM})
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False, trust_env=False,
    )
    public_clients = mock_selected_public_client(monkeypatch, provider, handler)
    try:
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert requested == [SIGNED]
        assert len(public_clients) == 1
        assert public_clients[0][2] == [SIGNED]
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


def test_selected_stream_revocation_removes_unpublished_candidate(
    tmp_path, monkeypatch,
):
    """A changed owner between chunks cannot leave an acquired receipt."""
    session, account, broker, reads, right = setup()
    requested = []

    class RevokeDuringBody(httpx.SyncByteStream):
        def __iter__(self):
            yield ACSM[:12]
            broker.owner = "identity-B"
            yield ACSM[12:]

    def handler(request):
        requested.append(str(request.url))
        assert str(request.url) == SIGNED
        return httpx.Response(
            200, stream=RevokeDuringBody(),
            headers={"content-type": "application/vnd.adobe.adept+xml"},
        )

    # A signed card starts immediately on an external URL; self.client is not
    # allowed to perform this GET or see the stream.
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("selected supplier GET used legacy client")
        ), trust_env=False, follow_redirects=False,
    )
    public_clients = mock_selected_public_client(monkeypatch, provider, handler)
    try:
        with pytest.raises(
            ProviderCapabilityError, match="numilog_candidate_transport_refused"
        ):
            session.rights_selected_delivery_candidate(
                account, **selected(right, tmp_path),
            )
        assert requested == [SIGNED]
        assert len(public_clients) == 1
        assert public_clients[0][2] == [SIGNED]
        assert not list(tmp_path.iterdir())
    finally:
        session.close()
