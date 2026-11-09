"""#5135: three rights, two already present, third exact selected delivery.

Synthetic local simulation of a Core A→B plan, not a live Paris claim.
A positive typed inventory is published once for one Core source Sync.
"""
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p0_3252_paris_download_scope import prepare, SIGNED
from tests.test_p1_3252_paris_selected_right_freshness import raw_loan


def _candidate(session, account, root, right):
    return session.rights_selected_delivery_candidate(
        account, edition_id=right.edition_id, right_id=right.right_id,
        kind="loan", branch="dilicom",
        delivery_offer_id=right.delivery_offer_id,
        target_dir=root, explicit_user_action=True,
    )


def test_three_rights_two_present_only_missing_third_gets_content(tmp_path, monkeypatch):
    session, account, _loan, client, contents = prepare(tmp_path, monkeypatch)
    client.digital = [
        raw_loan(right="loan-1", edition="edition-1"),
        raw_loan(right="loan-2", edition="edition-2"),
        raw_loan(right="loan-3", edition="edition-3"),
    ]
    original = client.loans
    digital = {"reads": 0}

    def flaky(upstream, borrower):
        if upstream == "DILICOM":
            digital["reads"] += 1
            if digital["reads"] > 1:
                raise TimeoutError("SYNTHETIC_LISTLOANS_TIMEOUT_SECRET")
        return original(upstream, borrower)

    client.loans = flaky
    try:
        with session.source_sync_inventory(account):
            observed = session.rights_observation_snapshot(account)
            assert observed.status == "success"
            assert len(observed.rights) == 3
            for index in (1, 2):
                earlier = observed.exact(f"edition-{index}", f"loan-{index}", "loan")
                assert earlier is not None and earlier.delivery_attested
            # Core's existing local-present decisions skip those two. Only
            # the third positive right is dispatched to selected delivery.
            third = observed.exact("edition-3", "loan-3", "loan")
            assert third is not None and third.delivery_attested
            offer = session.rights_selected_delivery_offer_snapshot(
                account, edition_id="edition-3", right_id="loan-3",
                kind="loan", branch="dilicom", explicit_user_action=True,
            )
            assert offer.rights[0].delivery_offer_id == third.delivery_offer_id
            path, hint = _candidate(session, account, tmp_path, third)
            assert path.is_file() and hint == ""
            assert path.read_bytes().startswith(b"%PDF-")
            assert contents == [SIGNED]
            assert digital["reads"] == 1
        # Next independent source cycle has no inherited proof or URL.
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_offer_snapshot(
                account, edition_id="edition-3", right_id="loan-3",
                kind="loan", branch="dilicom", explicit_user_action=True,
            )
        assert digital["reads"] == 2
        assert contents == [SIGNED]
    finally:
        session.close()


def test_expired_content_offer_does_not_become_loan_retry(tmp_path, monkeypatch):
    session, account, _loan, client, contents = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    original_get = client._get_pnb_response
    posts = []
    client.pnb_preferences = lambda: posts.append("PREFS")
    client.download_digital_document = lambda *_a, **_kw: posts.append("BORROW")
    # No success, no content bytes and no later authorization from an
    # expired attested link. The next user action reads fresh DILICOM.
    client._get_pnb_response = lambda _url: (_ for _ in ()).throw(
        TimeoutError("SYNTHETIC_EXPIRED_TOKEN"),
    )
    try:
        with session.source_sync_inventory(account):
            prior = session.rights_observation_snapshot(account).rights[0]
            with pytest.raises(Exception):
                _candidate(session, account, tmp_path, prior)
            assert posts == [] and contents == []
        client._get_pnb_response = original_get
        recent = session.rights_selected_delivery_offer_snapshot(
            account, edition_id="edition-1", right_id="loan-1",
            kind="loan", branch="dilicom", explicit_user_action=True,
        ).rights[0]
        path, hint = _candidate(session, account, tmp_path, recent)
        assert path.is_file() and hint == ""
        assert contents == [SIGNED]
        assert posts == []
    finally:
        session.close()
