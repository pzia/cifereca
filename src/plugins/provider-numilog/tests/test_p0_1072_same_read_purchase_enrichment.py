"""#1072: same authenticated MesLivres read emits only exact direct candidates.

Real HTML parser, synthetic provider/account and private HTML. Never HTTP
to the actual provider, public product, Readzis, or delivery endpoint.
"""
import pytest

from cifereca.plugin_api import ProviderCapabilityError, PurchaseEnrichmentObservation
from tests.test_p1_3252_rights_observation_snapshot import session, bound_account
import cifereca_numilog_public_client as client

DIRECT = "https://www.numilog.com/123/ouvrage.ebook"
OTHER = "https://www.numilog.com/456/autre.ebook"
SHARE = "https://www.numilog.com/Pages/Readzis/Recommand.ashx?LivreId=45&amp;SN=FB"


def card(lid, href=None, *, title="Même titre", extra_title="", extra_link=""):
    title = '<span class="pTitle">' + title + '</span>'
    if href is not None:
        title = '<a href="' + href + '">' + title + '</a>'
    return (
        '<tr><td>' + title + extra_title +
        f'<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid={lid}&amp;m=dwld">Get</a>'
        + extra_link + '</td></tr>'
    )


def page(*cards):
    return '<html><body><table id="myTable"><tbody>' + "".join(cards) + '</tbody></table></body></html>'


def forbid(*args, **kwargs):
    pytest.fail("unexpected public, tracking, or delivery request")


def prepare(monkeypatch, html):
    s, broker = session()
    requests = []
    def private_page(url, **kwargs):
        assert url == s.library_url
        requests.append(url)
        return url, html
    s._page = private_page
    monkeypatch.setattr(client, "fetch_public_detail", forbid)
    monkeypatch.setattr(client, "fetch_public_search", forbid)
    monkeypatch.setattr(client, "resolve_readzis_recommendation", forbid)
    return s, broker, requests


def test_one_read_two_homonymous_exact_cards_without_tracking_or_delivery(monkeypatch):
    s, broker, reads = prepare(monkeypatch, page(
        card(42, DIRECT), card(43, OTHER), card(44),
        card(45, SHARE), card(46, DIRECT + "?trackid=private"),
        card(47, "https://www.numilog.com/livres/legacy"),
    ))
    try:
        response = s.purchase_enrichment_observation(bound_account(s))
        assert type(response) is PurchaseEnrichmentObservation
        assert reads == [s.library_url]
        assert len(response.inventory.resources) == 6
        assert response.inventory.coverage == "complete"
        a, b = response.candidates
        assert (a.public_locator, b.public_locator) == (DIRECT, OTHER)
        assert a.title == b.title == "Même titre"
        assert a.source != b.source and a.source.scope == b.source.scope
        assert {item.source for item in response.candidates} == {
            item.source for item in response.inventory.resources[:2]
        }
        assert {item.source for item in response.omissions} == {
            item.source for item in response.inventory.resources[2:]
        }
        assert all(item.reason == "direct_locator_unattested" and not item.authorizes_fetch
                   for item in response.omissions)
        assert all("trackid" not in repr(item) for item in response.omissions)
        assert not response.authorizes_fetch
        assert all(resource.capability.state == "unknown" for resource in response.inventory.resources)
        assert "private" not in repr(response)
        # Ordinary inventory remains the original DTO and does not visit links.
        plain = s.downloadable_resource_snapshot(bound_account(s))
        assert [r.source for r in plain.resources] == [r.source for r in response.inventory.resources]
        assert reads == [s.library_url, s.library_url]
    finally:
        s.close()


def test_duplicate_exact_card_does_not_offer_enrichment_for_ambiguous_purchase(monkeypatch):
    s, _, reads = prepare(monkeypatch, page(
        card(42, DIRECT), card(42, OTHER), card(43, OTHER),
    ))
    try:
        report = s.purchase_enrichment_observation(bound_account(s))
        assert reads == [s.library_url]
        assert report.inventory.coverage == "partial"
        assert len(report.candidates) == 1
        candidate = report.candidates[0]
        assert candidate.public_locator == OTHER
        assert candidate.source == report.inventory.resources[-1].source
    finally:
        s.close()


@pytest.mark.parametrize("change", ["owner", "broker", "account"])
def test_rebind_during_same_read_never_returns_candidate(monkeypatch, change):
    s, broker, reads = prepare(monkeypatch, page(card(42, DIRECT)))
    original = s._page
    from dataclasses import replace
    def rebind(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "owner":
            broker.owner = "other"
        elif change == "broker":
            from tests.test_p1_3252_rights_observation_snapshot import _candidate_broker
            s.identity_broker = _candidate_broker(s, ["identity-A"])
        else:
            s._accounts = [replace(s._accounts[0])]
        return result
    s._page = rebind
    try:
        with pytest.raises(ProviderCapabilityError):
            s.purchase_enrichment_observation(bound_account(s))
        assert reads == [s.library_url]
    finally:
        s.close()
