"""P1 #4281: reuse one complete Paris ListLoans pair within one Core sync.

Synthetic scoped loans, no provider HTTP or rights mutation. Independent rights
reads and selected delivery actions continue to use fresh source observations.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.plugin_api import ProviderCapabilityError, read_loan_inventory
from cifereca.plugin_api.downloadable_resources import ResourceSnapshot
from tests.test_p1_3252_rights_observation_snapshot import fixture, upstream


@pytest.mark.parametrize("digital,physical", [
    (0, 0), (1, 12), (4, 3), (20, 0),
])
def test_same_cycle_complete_inventory_projects_rights_with_two_gets(digital, physical):
    session, account, client = fixture(
        digital=tuple(upstream(f"d-{i}", f"ed-{i}") for i in range(digital)),
        physical=tuple(upstream(f"p-{i}", f"ep-{i}") for i in range(physical)),
    )
    try:
        loan_inventory = read_loan_inventory(session, account)
        assert loan_inventory.status == "success"
        assert loan_inventory.branch_claims_complete("dilicom", "digital")
        assert loan_inventory.branch_claims_complete("syracuse", "physical")
        assert len(loan_inventory.loans) == digital + physical
        assert client.calls == [
            ("DILICOM", account.unique_id), ("SYRACUSE", account.unique_id),
        ]
        resources = session.downloadable_resource_snapshot_from_loan_inventory(
            account, loan_inventory=loan_inventory,
        )
        assert type(resources) is ResourceSnapshot
        assert resources.status == "success"
        assert len(resources.resources) == digital
        assert {(r.source.scope.account_id, r.source.edition_id, r.source.resource_id)
                for r in resources.resources} == {
            (account.unique_id, f"ed-{i}", f"d-{i}") for i in range(digital)
        }
        assert len(client.calls) == 2  # previously 4 on the same opening sync

        # A second use is refused rather than minting rights from old rows.
        with pytest.raises(ProviderCapabilityError, match="paris_same_cycle_inventory_stale"):
            session.downloadable_resource_snapshot_from_loan_inventory(
                account, loan_inventory=loan_inventory,
            )
        fresh = session.downloadable_resource_snapshot(account)
        assert fresh.status == "success"
        assert len(fresh.resources) == digital
        assert client.calls == [
            ("DILICOM", account.unique_id), ("SYRACUSE", account.unique_id),
            ("DILICOM", account.unique_id), ("SYRACUSE", account.unique_id),
        ]
    finally:
        session.close()


@pytest.mark.parametrize("alter", [
    "replace_account", "duplicate_account", "change_epoch",
    "replace_account_list", "disconnect", "new_inventory",
])
def test_inflight_scope_or_new_read_invalidates_one_shot_inventory(alter):
    session, account, client = fixture(digital=(upstream("d-1", "ed-1"),))
    try:
        loan_inventory = read_loan_inventory(session, account)
        assert len(client.calls) == 2
        if alter == "replace_account":
            session._accounts = [replace(account)]
        elif alter == "duplicate_account":
            session._accounts.append(replace(account))
        elif alter == "change_epoch":
            session._epoch = "new-session-epoch"
        elif alter == "replace_account_list":
            session._accounts = [account]
        elif alter == "disconnect":
            session.connected = False
        else:
            # A newer source request makes earlier evidence stale.
            next_inventory = read_loan_inventory(session, account)
            assert next_inventory.status == "success"
        with pytest.raises(ProviderCapabilityError, match="paris_same_cycle_inventory_stale"):
            session.downloadable_resource_snapshot_from_loan_inventory(
                account, loan_inventory=loan_inventory,
            )
        assert len(client.calls) == (4 if alter == "new_inventory" else 2)
    finally:
        session.close()


def test_partial_dilicom_cannot_be_reused_to_positively_attest_rights():
    session, account, client = fixture(digital=(upstream("d-1", "ed-1"),))
    prior = client.loans

    def failed(branch, borrower):
        if branch == "DILICOM":
            client.calls.append((branch, borrower))
            raise RuntimeError("synthetic private branch outage")
        return prior(branch, borrower)

    client.loans = failed
    try:
        inventory = read_loan_inventory(session, account)
        assert inventory.status == "partial"
        assert len(client.calls) == 2
        with pytest.raises(ProviderCapabilityError, match="paris_same_cycle_inventory_unverified"):
            session.downloadable_resource_snapshot_from_loan_inventory(
                account, loan_inventory=inventory,
            )
        assert len(client.calls) == 2  # no fallback GET after rejection
    finally:
        session.close()


def test_missing_core_owner_cannot_project_reused_inventory():
    session, account, client = fixture(digital=(upstream("d-1", "ed-1"),))
    try:
        inventory = read_loan_inventory(session, account)
        session.identity_broker = None
        with pytest.raises(ProviderCapabilityError, match="paris_rights_owner_unverified"):
            session.downloadable_resource_snapshot_from_loan_inventory(
                account, loan_inventory=inventory,
            )
        assert len(client.calls) == 2
    finally:
        session.close()
