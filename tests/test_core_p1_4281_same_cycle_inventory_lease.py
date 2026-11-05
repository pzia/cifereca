"""P1 #4281 — Core can reuse one exact opening inventory only during that phase.

All data and upstream readers are synthetic. No supplier HTTP, no right writes,
no persistent cache and no opening of another borrower or source.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.rights_snapshot_read import read_account_rights_snapshot
from cifereca.core.same_cycle_loan_inventory import (
    opening_scoped_loan_inventory, current_scoped_loan_inventory,
)
from cifereca.plugin_api.loan_inventory import (
    LoanInventoryScope, LoanInventorySnapshot, LoanBranchCoverage,
)
from cifereca.plugin_api.downloadable_resources import from_legacy_rights_snapshot
from tests.test_core_p0_3171_rights_snapshot_read import rig, ProviderSession


def _rig():
    service, account, _ = rig()
    class SharingProvider(ProviderSession):
        def __init__(self):
            super().__init__(account.provider_id, account.credential_id)
            self.accounts = [account]
            self.reuse = 0

        def downloadable_resource_snapshot_from_loan_inventory(
            self, selected, *, loan_inventory,
        ):
            self.reuse += 1
            assert selected is account
            assert loan_inventory.scope == LoanInventoryScope.for_account(account)
            # Model the provider's *pure* response projection without
            # calling its ordinary/private network reader.
            evidence = ProviderSession(
                account.provider_id, account.credential_id,
            ).rights_observation_snapshot(selected)
            return from_legacy_rights_snapshot(evidence)

    session = SharingProvider()
    service._sessions[(account.provider_id, account.credential_id)] = session
    return service, account, session


def _inventory(account, *, status="success", branch="complete", borrower=None):
    scope = LoanInventoryScope.for_account(borrower or account)
    return LoanInventorySnapshot(
        scope=scope, loans=(), status=status,
        branches=(LoanBranchCoverage(
            upstream="sample", medium="digital",
            completeness=branch,
            pagination_complete=True if branch == "complete" else None,
        ),),
    )


def test_same_cycle_only_uses_provider_pure_projection_and_never_caches_rights():
    service, account, session = _rig()
    inventory = _inventory(account)
    with opening_scoped_loan_inventory(service, account, session, inventory):
        assert current_scoped_loan_inventory(service, account, session) is inventory
        observed = read_account_rights_snapshot(service, account)
        assert len(observed.rights) == 1
        assert observed.scope.identity_id == account.identity_id
        assert session.reuse == 1 and session.calls == 0
    assert current_scoped_loan_inventory(service, account, session) is None
    # Any independent rights action makes a fresh ordinary provider read.
    ordinary = read_account_rights_snapshot(service, account)
    assert len(ordinary.rights) == 1
    assert session.calls == 1 and session.reuse == 1


@pytest.mark.parametrize("invalid", ["partial", "branch_partial", "other_borrower"])
def test_incomplete_or_foreign_inventory_falls_back_to_fresh_upstream(invalid):
    service, account, session = _rig()
    inventory = (
        _inventory(account, status="partial") if invalid == "partial"
        else _inventory(account, branch="partial") if invalid == "branch_partial"
        else _inventory(account, borrower=replace(account, unique_id="foreign"))
    )
    with opening_scoped_loan_inventory(service, account, session, inventory):
        report = read_account_rights_snapshot(service, account)
        assert len(report.rights) == 1
    assert session.reuse == 0 and session.calls == 1


def test_rebind_epoch_and_service_account_identity_prevent_foreign_reuse():
    service, account, session = _rig()
    snapshot = _inventory(account)
    session._epoch = "epoch-one"
    with opening_scoped_loan_inventory(service, account, session, snapshot):
        session._epoch = "epoch-two"
        assert current_scoped_loan_inventory(service, account, session) is None
        assert current_scoped_loan_inventory(service, replace(account), session) is None
        assert read_account_rights_snapshot(service, account).status == "partial"
        assert session.reuse == 0 and session.calls == 1
    with opening_scoped_loan_inventory(service, account, session, snapshot):
        service.accounts.append(replace(account))
        refused = read_account_rights_snapshot(service, account)
        assert refused.status == "failed" and not refused.rights
        assert session.reuse == 0 and session.calls == 1


def test_offered_projection_failure_never_retries_private_reader():
    service, account, session = _rig()

    def failing(selected, *, loan_inventory):
        session.reuse += 1
        raise RuntimeError("upstream data invalid; do not start another GET")

    session.downloadable_resource_snapshot_from_loan_inventory = failing
    with opening_scoped_loan_inventory(service, account, session, _inventory(account)):
        report = read_account_rights_snapshot(service, account)
    assert report.status == "failed" and not report.rights
    assert session.reuse == 1 and session.calls == 0


def test_cancelled_or_disabled_source_never_consumes_offered_snapshot():
    service, account, session = _rig()
    with opening_scoped_loan_inventory(service, account, session, _inventory(account)):
        cancelled = read_account_rights_snapshot(service, account, cancelled=lambda: True)
        assert cancelled.status == "cancelled"
        service.sync_source_enabled = lambda _account: False
        disabled = read_account_rights_snapshot(service, account)
        assert disabled.status == "failed"
    assert session.reuse == session.calls == 0
