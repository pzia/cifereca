"""#5135 Paris: one ephemeral provider-side proof during one Core Sync.

Real ParisProviderSession, typed snapshots and content staging with synthetic
DILICOM/SYRACUSE. No upstream HTTP, credentials, new borrow or Core mutation.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p0_3252_paris_download_scope import SIGNED, prepare
from tests.test_p1_3252_paris_selected_right_freshness import raw_loan


def _selected(session, account, directory, offer):
    return session.rights_selected_delivery_candidate(
        account, edition_id="edition-1", right_id="loan-1",
        kind="loan", branch="dilicom", delivery_offer_id=offer,
        target_dir=directory, explicit_user_action=True,
    )


def test_same_cycle_attested_offer_survives_intermediate_timeout_without_reborrow(
    tmp_path, monkeypatch,
):
    session, account, _loan, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    original = client.loans
    # Once A has been attested, an independent DILICOM ListLoans is broken.
    phase = {"attested": False, "attempts": 0}
    def intermittent(upstream, borrower):
        if upstream == "DILICOM":
            phase["attempts"] += 1
            if phase["attested"]:
                raise TimeoutError("SYNTHETIC_PRIVATE_TOKEN")
        return original(upstream, borrower)
    client.loans = intermittent
    try:
        with session.source_sync_inventory(account):
            observed = session.rights_observation_snapshot(account)
            assert observed.status == "success"
            right = observed.exact("edition-1", "loan-1", "loan")
            assert right is not None and right.delivery_attested
            phase["attested"] = True
            offer = session.rights_selected_delivery_offer_snapshot(
                account, edition_id="edition-1", right_id="loan-1",
                kind="loan", branch="dilicom", explicit_user_action=True,
            )
            assert offer.rights[0].delivery_offer_id == right.delivery_offer_id
            path, hint = _selected(session, account, tmp_path, right.delivery_offer_id)
            assert path.is_file() and hint == ""
            assert gets == [SIGNED]
            assert phase["attempts"] == 1  # no redundant DILICOM GET
            assert client.calls == [
                ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
            ]
        # No reuse once the source Sync exits, despite identical inputs.
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_offer_snapshot(
                account, edition_id="edition-1", right_id="loan-1",
                kind="loan", branch="dilicom", explicit_user_action=True,
            )
        assert phase["attempts"] == 2 and gets == [SIGNED]
        assert session._source_sync_offer_lease.get() is None
    finally:
        session.close()


def test_success_then_empty_full_branch_does_not_erase_proof_in_same_cycle(
    tmp_path, monkeypatch,
):
    session, account, _loan, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    try:
        with session.source_sync_inventory(account):
            first = session.rights_observation_snapshot(account)
            old = first.rights[0]
            # Outside the lease this is a valid *new* observation with no
            # entitlement; within the same committed source cycle, the
            # attested offer is pinned to its initial source-scoped evidence.
            client.digital = []
            offer = session.rights_selected_delivery_offer_snapshot(
                account, edition_id="edition-1", right_id="loan-1",
                kind="loan", branch="dilicom", explicit_user_action=True,
            )
            assert offer.rights[0].delivery_offer_id == old.delivery_offer_id
            assert len(client.calls) == 2
        with pytest.raises(ProviderCapabilityError, match="paris_selected_offer_unobserved"):
            session.rights_selected_delivery_offer_snapshot(
                account, edition_id="edition-1", right_id="loan-1",
                kind="loan", branch="dilicom", explicit_user_action=True,
            )
        assert len(client.calls) == 4 and gets == []
    finally:
        session.close()


@pytest.mark.parametrize("change", ["credential", "edition", "right", "account_replace", "epoch"])
def test_cycle_lease_fails_closed_on_cross_scope_or_rebind(
    tmp_path, monkeypatch, change,
):
    session, account, _loan, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    try:
        with session.source_sync_inventory(account):
            prior = session.rights_observation_snapshot(account).rights[0]
            assert prior.delivery_attested
            client.calls.clear()
            if change == "credential":
                account.credential_id = "other"
            elif change == "account_replace":
                session._accounts = [replace(account)]
            elif change == "epoch":
                session._epoch = getattr(session, "_epoch", 0) + 1
            edition = "wrong-edition" if change == "edition" else "edition-1"
            right = "wrong-right" if change == "right" else "loan-1"
            with pytest.raises((ProviderCapabilityError, Exception)):
                session.rights_selected_delivery_offer_snapshot(
                    account, edition_id=edition, right_id=right,
                    kind="loan", branch="dilicom", explicit_user_action=True,
                )
            assert gets == []
    finally:
        session.close()


def test_partial_cycle_never_mints_reusable_delivery_proof(tmp_path, monkeypatch):
    session, account, _loan, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    original = client.loans
    def fail_syracuse(upstream, borrower):
        if upstream == "SYRACUSE":
            raise TimeoutError("SYNTHETIC")
        return original(upstream, borrower)
    client.loans = fail_syracuse
    try:
        with session.source_sync_inventory(account):
            report = session.rights_observation_snapshot(account)
            assert report.rights and report.status == "success"
            assert session._source_sync_offer_lease.get()["rights"] is None
            before = len(client.calls)
            session.rights_selected_delivery_offer_snapshot(
                account, edition_id="edition-1", right_id="loan-1",
                kind="loan", branch="dilicom", explicit_user_action=True,
            )
            assert len(client.calls) == before + 2
            assert gets == []
    finally:
        session.close()
