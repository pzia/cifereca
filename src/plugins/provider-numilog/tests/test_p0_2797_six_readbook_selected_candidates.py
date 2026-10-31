"""#2797: six card-bound readBook dwld purchases remain six independent GETs.

Synthetic MesLivres HTML, HTTPX MockTransport and ACSM bytes. This validates
the provider-side selected delivery contract; it is NOT a real six-EPUB test,
nor evidence of Core B publications or ADEPT rights.
"""
import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p0_2797_selected_readbook_dwld_offer import card, page, rig
from tests.test_p1_3252_rights_observation_snapshot import bound_account
from tests.test_numilog_signed_acsm_transport_p0 import SIGNED, ACSM


LIBRARY = "https://www.numilog.com/Pages/Compte/MesLivres.aspx"
LIDS = tuple(range(11, 17))


def setup_six():
    # The private page is one authenticated response and never exposes an
    # action from outside the six card descendants.
    session, broker, private_reads = rig(page(*(card(str(i)) for i in LIDS)))
    account = bound_account(session)
    observed = session.downloadable_resource_snapshot(account)
    resources = observed.resources
    assert len(resources) == 6
    assert observed.status == "partial"
    assert not observed.authorizes_acquisition
    assert all(x.capability.offer_id == "" for x in resources)
    assert all(x.source.scope.identity_id == "identity-A" for x in resources)
    return session, broker, private_reads, account, resources


def test_six_selected_readbooks_stage_six_distinct_acsm_candidates_no_cross_book_get(
    tmp_path,
):
    session, broker, private_reads, account, resources = setup_six()
    readbook = {
        lid: (f"https://www.numilog.com/Pages/Compte/readBook.ashx?"
              f"lid={lid}&m=dwld")
        for lid in LIDS
    }
    signed = {
        lid: SIGNED.replace("FICTIONAL", f"FICTIONAL{lid}")
        for lid in LIDS
    }
    received = []

    def handler(request):
        target = str(request.url)
        received.append(target)
        for lid in LIDS:
            if target == readbook[lid]:
                return httpx.Response(302, headers={"location": signed[lid]})
            if target == signed[lid]:
                return httpx.Response(
                    200, content=ACSM,
                    headers={"content-type": "application/vnd.adobe.adept+xml"},
                )
        raise AssertionError("GET outside the six owned card actions")

    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False, trust_env=False,
    )
    offers = []
    candidates = []
    try:
        # Every selected offer is qualified independently, never borrowed
        # from a sibling card or inferred from its title or source SHA.
        for resource in resources:
            key = resource.source
            result = session.downloadable_selected_offer_snapshot(
                account, edition_id=key.edition_id,
                resource_id=key.resource_id, kind="purchase",
                branch="meslivres", explicit_user_action=True,
            )
            assert len(result.resources) == 1
            selected = result.resources[0]
            assert selected.source == key
            assert selected.capability.state == "available"
            offers.append(selected.capability.offer_id)
        assert len(set(offers)) == 6
        assert all(value.startswith("numilog.readbook:") for value in offers)
        # The preflight above performs only MesLivres private inventory GETs.
        assert received == []

        for index, (resource, offer_id) in enumerate(zip(resources, offers)):
            scope = resource.source
            staging = tmp_path / ("Core-staging-" + str(index))
            staging.mkdir()
            candidate = session.downloadable_selected_delivery_candidate(
                account, edition_id=scope.edition_id,
                resource_id=scope.resource_id, kind="purchase",
                branch="meslivres", selected_offer_id=offer_id,
                target_dir=staging, explicit_user_action=True,
            )
            assert candidate.path.parent == staging
            assert candidate.path.suffix == ".acsm"
            assert candidate.path.read_bytes() == ACSM
            assert candidate.source == scope
            assert candidate.selected_offer_id == offer_id
            assert not candidate.authorizes_publication
            candidates.append(candidate.path)

        assert len(candidates) == 6
        assert len({str(path) for path in candidates}) == 6
        assert received == [
            target
            for lid in LIDS
            for target in (readbook[lid], signed[lid])
        ]
        assert broker.owner == "identity-A"
        assert session.acquisition_broker is None
        assert all(path.is_file() for path in candidates)
        assert all(url.startswith("https://") for url in received)
        assert len(private_reads) >= 1
        assert all(url == LIBRARY for url in private_reads)
    finally:
        session.close()


def test_foreign_book_selected_offer_cannot_stage_other_owned_readbook(
    tmp_path,
):
    session, broker, _reads, account, resources = setup_six()
    first, second = resources[:2]
    offered = session.downloadable_selected_offer_snapshot(
        account, edition_id=first.source.edition_id,
        resource_id=first.source.resource_id, kind="purchase",
        branch="meslivres", explicit_user_action=True,
    ).resources[0].capability.offer_id
    staged = tmp_path / "Core-staging"
    staged.mkdir()
    seen_network = []

    def no_delivery_get(request):
        seen_network.append(str(request.url))
        raise AssertionError("wrong-book action reached HTTPX")

    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(no_delivery_get),
        follow_redirects=False, trust_env=False,
    )
    try:
        with pytest.raises(ProviderCapabilityError, match="numilog_offer_changed"):
            session.downloadable_selected_delivery_candidate(
                account, edition_id=second.source.edition_id,
                resource_id=second.source.resource_id, kind="purchase",
                branch="meslivres", selected_offer_id=offered,
                target_dir=staged, explicit_user_action=True,
            )
        assert seen_network == []
        assert list(staged.iterdir()) == []
    finally:
        session.close()
