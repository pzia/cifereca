"""#2797: exact authenticated readBook dwld link is selectable via Core only.

The six real Numilog EPUBs are NOT used here. No live provider connection.
"""
from dataclasses import asdict
import json

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from tests.test_p1_3252_targeted_delivery_offer import rig
from tests.test_p1_3252_rights_observation_snapshot import bound_account
from cifereca_numilog_html import parse_library_page

LIBRARY = "https://www.numilog.com/Pages/Compte/MesLivres.aspx"


def card(lid="12", *, mode="dwld", count=1, title=True, extra=""):
    return (
        '<div class="owned-book">'
        + ('<span class="pTitle">Synthetic purchased book</span>' if title else '')
        + ''.join(
            '<a class="LnkDownloadBook" href="/Pages/Compte/'
            'readBook.ashx?lid=' + lid + '&amp;m=' + mode
            + '">Download</a>'
            for _ in range(count)
        ) + extra + '</div>'
    )


def page(*cards):
    return '<html><table id="myTable">' + ''.join(cards) + '</table></html>'


def chosen_offer(session):
    account = bound_account(session)
    observed = session.rights_observation_snapshot(account)
    assert len(observed.rights) == 1
    right = observed.rights[0]
    assert right.delivery == "unknown" and right.delivery_offer_id == ""
    selected = session.rights_selected_delivery_offer_snapshot(
        account, edition_id=right.edition_id, right_id=right.right_id,
        kind="purchase", branch="meslivres", explicit_user_action=True,
    )
    return account, right, selected.rights[0]


def test_selected_dwld_offer_from_one_real_owned_card_never_exports_href():
    html = page(card())
    session, broker, reads = rig(html)
    try:
        account, right, offered = chosen_offer(session)
        assert offered.delivery == "available"
        assert offered.delivery_offer_id.startswith("numilog.readbook:")
        assert offered.provider_attested
        assert reads == [session.library_url] * 3
        exported = json.dumps(asdict(offered))
        assert 'readBook.ashx' not in exported
        assert 'lid=12' not in exported
        assert 'dwld' not in exported
        assert broker.owner == "identity-A"
    finally:
        session.close()


def test_selected_readbook_revalidates_exact_href_before_one_staged_candidate(
    tmp_path,
):
    session, broker, reads = rig(page(card()))
    try:
        account, right, offered = chosen_offer(session)
        item = session.library()[0]
        staged = tmp_path / "candidate"
        staged.mkdir()
        attempted = []
        def fake_download(start, destination, **kwargs):
            attempted.append((start, kwargs))
            return staged / "fake.acsm", session.library_url
        session._download = fake_download
        result = session.download_direct(
            item, staged, expected_offer_id=offered.delivery_offer_id,
            expected_identity_id=account.identity_id, stage_acsm_only=True,
        )
        assert result[0] == staged / "fake.acsm"
        assert len(attempted) == 1
        assert attempted[0][0] == (
            "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=12&m=dwld"
        )
        assert attempted[0][1]["source"] == "readbook"
        assert attempted[0][1]["authorized_link"] is None
        assert attempted[0][1]["stage_acsm_only"] is True
        assert attempted[0][1]["expected_readbook_url"] == attempted[0][0]
        assert reads == [session.library_url] * 5
    finally:
        session.close()


def test_refresh_with_new_readbook_href_never_reuses_old_selected_offer(tmp_path):
    session, broker, reads = rig(page(card()))
    try:
        account, right, offered = chosen_offer(session)
        item = session.library()[0]
        calls = []
        session._download = lambda *args, **kwargs: calls.append((args, kwargs))
        # Same edition identity in the legacy listing, but a new exact
        # owned readBook query: the opaque offer MUST be refreshed.
        session._page = lambda url, **kwargs: (
            url, page(card(mode="dwld&amp;extra=1")),
        )
        with pytest.raises(ProviderCapabilityError, match="numilog_offer_changed"):
            session.download_direct(
                item, tmp_path, expected_offer_id=offered.delivery_offer_id,
                expected_identity_id=account.identity_id,
            )
        assert calls == []
    finally:
        session.close()


@pytest.mark.parametrize("markup", (
    card(mode="stream"),
    card(mode="download"),
    card(count=2),
    card(title=False),
    card(extra="<script>window.book={ProductId:9001};</script>"),
    card(extra='<a onclick="ModalDownload(9001,1)">Alternate</a>'),
    card(mode="dwld&amp;m=stream"),
))
def test_readbook_unqualified_modes_and_ambiguous_cards_cannot_mint_offer(markup):
    s, _, _ = rig(page(markup))
    try:
        account = bound_account(s)
        observed = s.rights_observation_snapshot(account)
        if len(observed.rights) != 1:
            assert len(observed.rights) == 0
            return
        chosen = observed.rights[0]
        try:
            selected = s.rights_selected_delivery_offer_snapshot(
                account, edition_id=chosen.edition_id,
                right_id=chosen.right_id, kind="purchase",
                branch="meslivres", explicit_user_action=True,
            )
        except ProviderError:
            # Multiple download links with the same scoped lid are already
            # rejected by the parser as competing cards on selected reads.
            assert markup.count("LnkDownloadBook") > 1
        else:
            assert selected.rights[0].delivery == "unknown"
            assert selected.rights[0].delivery_offer_id == ""
    finally:
        s.close()


def test_six_readbook_cards_emit_six_distinct_selected_only_offers():
    html = page(*(card(str(lid)) for lid in range(11, 17)))
    s, _, _ = rig(html)
    try:
        account = bound_account(s)
        observed = s.rights_observation_snapshot(account)
        assert len(observed.rights) == 6
        assert all(x.delivery == "unknown" for x in observed.rights)
        offers = []
        for right in observed.rights:
            result = s.rights_selected_delivery_offer_snapshot(
                account, edition_id=right.edition_id,
                right_id=right.right_id, kind="purchase",
                branch="meslivres", explicit_user_action=True,
            )
            offers.append(result.rights[0].delivery_offer_id)
        assert len(set(offers)) == 6
        assert all(offer.startswith("numilog.readbook:") for offer in offers)
    finally:
        s.close()


def test_readbook_structural_diagnostic_counts_only_selected_actions():
    html = page(card("12"), card("34", mode="stream"))
    diag = {}
    observed, _ = parse_library_page(
        html, delivery_evidence=True, delivery_diagnostics=diag,
        delivery_selector=lambda row: row["lid"] == "12",
    )
    assert len(observed) == 2
    assert observed[0].get("readbook_dwld_offer") is True
    assert "readbook_dwld_offer" not in observed[1]
    assert diag["cards"] == 2
    assert diag["js_shapes_in_cards"] == 0
    assert diag["bound_links"] == 0
    assert diag["readbook_dwld_offers"] == 1
    assert "12" not in repr(diag) and "34" not in repr(diag)
