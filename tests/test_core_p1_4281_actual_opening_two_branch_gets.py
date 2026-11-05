"""#4281: actual Core opening loop reuses typed Paris ListLoans once per borrower.

This is a regression of the real wiring (#4526/#4527), not a mocked pair of
independent reads. No Paris HTTP, external credentials or acquisition.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.core import canonical_sync_stage
from cifereca.core.same_cycle_loan_inventory import current_scoped_loan_inventory
from cifereca.models import Account
from tests.test_sync_state_contract import _service
from tests.test_core_p1_4281_paris_opening_observer_cost import (
    _Diagnostics, _LoansClient,
)
from cifereca_paris import ParisProviderSession


class _TwoReaders:
    def accounts(self, identity_id=None):
        if identity_id != "default":
            return []
        return [
            NS(provider_id="paris", account_id=borrower, identity_id="default")
            for borrower in ("reader-A", "reader-B")
        ]


def test_actual_opening_reuses_two_listloans_per_scoped_paris_borrower(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    readers = [
        Account(
            display_name=borrower, provider_id="paris",
            identity_id="default", unique_id=borrower,
            credential_id="card-A",
        )
        for borrower in ("reader-A", "reader-B")
    ]
    service.accounts = readers
    session = ParisProviderSession(
        credential_id="card-A", label="Paris",
        credentials={}, options={}, config={},
        diagnostics=_Diagnostics(), identity_broker=_TwoReaders(),
    )
    session.client.close()
    session.client = _LoansClient(0)
    session._enrich_digital_loan_metadata = lambda _loan: None
    session.connected = True
    session._accounts = readers
    service._sessions[service._session_key("paris", "card-A")] = session
    rights = []

    def observe(source, account, *, snapshot_sink=None, **_options):
        assert source is service
        assert account in readers
        # The Core bridge is active ONLY for this exact account and session,
        # and the plugin projection does not perform another supplier GET.
        lease = current_scoped_loan_inventory(source, account, session)
        assert lease is not None
        assert lease.scope.account_id == account.unique_id
        assert lease.status == "success"
        result = session.downloadable_resource_snapshot_from_loan_inventory(
            account, loan_inventory=lease,
        )
        assert result.status == "success"
        assert result.scope.account_id == account.unique_id
        rights.append(account.unique_id)
        return {
            "status": "success", "observed": 0,
            "present_attested": 0, "source_references_changed": 0,
            "pending": 0,
        }

    monkeypatch.setattr(canonical_sync_stage, "observe_canonical_rights_in_sync", observe)
    try:
        outcome = service.opening_sync()
        assert rights == ["reader-A", "reader-B"]
        assert session.client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
            ("DILICOM", "reader-B"), ("SYRACUSE", "reader-B"),
        ]
        assert current_scoped_loan_inventory(service, readers[0], session) is None
        assert current_scoped_loan_inventory(service, readers[1], session) is None
        assert outcome["identities"]
        # Independent action outside the opening lease remains fresh.
        another = session.downloadable_resource_snapshot(readers[0])
        assert another.status == "success"
        assert session.client.calls[-2:] == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        service.close()
