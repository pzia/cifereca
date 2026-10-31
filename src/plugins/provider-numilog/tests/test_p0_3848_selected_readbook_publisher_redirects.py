"""#3848: an exact Core-selected MesLivres readBook offer can follow publisher 302s.

All owned-card HTML, ACSM payloads and remote addresses are synthetic.
Neither the legacy unselected downloader nor private library scraping gains
permission to follow an arbitrary external redirect.
"""
import httpx
import pytest

import cifereca_numilog as provider
from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from tests.test_p0_2797_selected_readbook_dwld_offer import card, chosen_offer, page, rig
from tests.test_numilog_signed_acsm_transport_p0 import ACSM
from tests.pinned_public_https_fixture import mock_selected_public_client


READBOOK = "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=12&m=dwld"
DILICOM = "https://hub-dilicom.centprod.com/v3/link/SYNTHETIC/receipt"
EDEN = "https://www.edenlivres.fr/organisations/75/transactions/SYNTHETIC/receipt"
NEXT = "https://unknown-publisher.example.org/delivery?token=SYNTHETIC%2FKEEP"


@pytest.mark.parametrize("publisher", [DILICOM, EDEN])
@pytest.mark.parametrize("extra_redirect", [False, True])
def test_selected_readbook_follows_external_acsm_redirect_chain(
    tmp_path, monkeypatch, publisher, extra_redirect,
):
    session, broker, _ = rig(page(card()))
    account, right, offer = chosen_offer(session)
    requests = []

    def handler(request):
        requests.append(request)
        if str(request.url) == READBOOK:
            return httpx.Response(302, headers={"location": publisher})
        if str(request.url) == publisher and extra_redirect:
            return httpx.Response(302, headers={"location": NEXT})
        if str(request.url) in {publisher, NEXT}:
            return httpx.Response(
                200, content=ACSM,
                headers={"content-type": "application/vnd.adobe.adept+xml"},
            )
        pytest.fail("unexpected supplier GET")

    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False, trust_env=False,
    )
    session.client.cookies.set("owned-session", "FIXTURE", domain="www.numilog.com")
    public_clients = mock_selected_public_client(monkeypatch, provider, handler)
    staging = tmp_path / "selected-staging"
    staging.mkdir()
    try:
        candidate = session.downloadable_selected_delivery_candidate(
            account, edition_id=right.edition_id, resource_id=right.right_id,
            kind="purchase", branch="meslivres",
            selected_offer_id=offer.delivery_offer_id,
            target_dir=staging, explicit_user_action=True,
        )
        assert candidate.path.is_file()
        assert candidate.path.parent == staging
        assert candidate.path.suffix == ".acsm"
        assert candidate.path.read_bytes() == ACSM
        assert candidate.source.scope.identity_id == account.identity_id
        assert not candidate.authorizes_publication
        assert [str(req.url) for req in requests] == (
            [READBOOK, publisher, NEXT] if extra_redirect
            else [READBOOK, publisher]
        )
        # Only the first, first-party GET may carry a Numilog session cookie.
        assert all(not req.headers.get("cookie") for req in requests[1:])
        assert all(not req.headers.get("authorization") for req in requests[1:])
        assert all("lid=" not in req.headers.get("referer", "") for req in requests[1:])
        # Exact selected delivery runs via the Core-pinned public client.
        # A preliminary socket.getaddrinfo monkeypatch is not proof of
        # TCP peer pinning; Core's transport has independent security tests.
        assert len(public_clients) == 1
        assert public_clients[0][2] == (
            [publisher, NEXT] if extra_redirect else [publisher]
        )
    finally:
        session.close()


@pytest.mark.parametrize("location", [
    "http://other.example.org/unsafe",
    "https://127.0.0.1/private",
    "https://user:password@other.example.org/unsafe",
    "https://other.example.org/x#fragment",
])
def test_selected_readbook_rejects_unsafe_first_external_redirect(
    tmp_path, location,
):
    session, _, _ = rig(page(card()))
    account, right, offer = chosen_offer(session)
    requested = []

    def handler(request):
        requested.append(str(request.url))
        assert str(request.url) == READBOOK
        return httpx.Response(302, headers={"location": location})

    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False, trust_env=False,
    )
    staging = tmp_path / "selected-staging"
    staging.mkdir()
    try:
        with pytest.raises(ProviderError, match="redirection réseau inattendue"):
            session.downloadable_selected_delivery_candidate(
                account, edition_id=right.edition_id, resource_id=right.right_id,
                kind="purchase", branch="meslivres",
                selected_offer_id=offer.delivery_offer_id,
                target_dir=staging, explicit_user_action=True,
            )
        assert requested == [READBOOK]
        assert list(staging.iterdir()) == []
    finally:
        session.close()


def test_readbook_owner_change_refuses_external_get(tmp_path):
    session, broker, _ = rig(page(card()))
    account, right, offer = chosen_offer(session)
    requested = []

    def handler(request):
        requested.append(str(request.url))
        assert str(request.url) == READBOOK
        broker.owner = "identity-B"
        return httpx.Response(302, headers={"location": DILICOM})

    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False, trust_env=False,
    )
    staging = tmp_path / "selected-staging"
    staging.mkdir()
    try:
        with pytest.raises(ProviderCapabilityError):
            session.downloadable_selected_delivery_candidate(
                account, edition_id=right.edition_id, resource_id=right.right_id,
                kind="purchase", branch="meslivres",
                selected_offer_id=offer.delivery_offer_id,
                target_dir=staging, explicit_user_action=True,
            )
        assert requested == [READBOOK]
        assert list(staging.iterdir()) == []
    finally:
        session.close()
