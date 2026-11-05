"""P1 #4281: bound the private GET cost of the two opening-sync readers.

This is a request-count baseline, NOT a benchmark of Paris or GTK latency.
No HTTP, session login, ACSM, rights mutation or automatic acquisition occurs.
The actual opening sync calls _sync_loan_inventory_observation, then the
canonical v2 resource reader. These two entrypoints currently each perform a
fresh DILICOM/SYRACUSE inventory, even for the same borrower.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from cifereca.core.sync_coordination import _sync_loan_inventory_observation
from cifereca.models import Account, Loan

PLUGIN = Path(__file__).resolve().parents[1] / "src/plugins/provider-paris"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from cifereca_paris import ParisProviderSession  # noqa: E402


class _Diagnostics:
    def add(self, *_args, **_kwargs):
        pass


class _Broker:
    """Expose precisely one live borrower for the target Core identity."""

    def accounts(self, identity_id=None):
        return (
            [SimpleNamespace(
                provider_id="paris", account_id="reader-A", identity_id="identity-A",
            )]
            if identity_id == "identity-A" else []
        )


class _LoansClient:
    def __init__(self, digital):
        self.digital = digital
        self.fail_digital = False
        self.calls = []

    def loans(self, upstream, borrower):
        self.calls.append((upstream, borrower))
        if upstream == "DILICOM" and self.fail_digital:
            raise RuntimeError("synthetic ListLoans outage")
        if upstream == "SYRACUSE":
            return []
        return [
            Loan(
                id=f"loan-{number}", record_id=f"edition-{number}",
                title=f"Synthetic title {number}", provider="DILICOM",
                medium="digital", identity_id=None, source_account_id=None,
                raw={"Id": f"loan-{number}", "RecordId": f"edition-{number}"},
            )
            for number in range(self.digital)
        ]

    def close(self):
        pass


def _session(digital):
    session = ParisProviderSession(
        credential_id="card-A", label="Paris", credentials={},
        options={}, config={}, diagnostics=_Diagnostics(),
        identity_broker=_Broker(),
    )
    session.client.close()
    client = _LoansClient(digital)
    session.client = client
    # Isolate ListLoans from the optional EBOOKS catalogue enrichments.
    session._enrich_digital_loan_metadata = lambda _loan: None
    account = Account(
        display_name="Reader", unique_id="reader-A", identity_id="identity-A",
        provider_id="paris", credential_id="card-A",
    )
    session.connected = True
    session._accounts = [account]
    return session, account, client


@pytest.mark.parametrize("digital", [0, 1, 4, 20])
def test_opening_loans_then_v2_resources_repeat_two_scoped_branches(digital):
    """Today: two GETs for display loans and two more for canonical resources.

    This is the cost *inside* opening sync, excluding the startup connection
    refresh. Change this exact baseline when a safe single-read contract ships.
    A faster implementation must still pass the separate fresh-read fence.
    """
    session, account, client = _session(digital)
    try:
        snapshot, loans = _sync_loan_inventory_observation(None, account, session)
        assert snapshot is not None and snapshot.status == "success"
        assert len(loans) == digital
        assert all(
            (row.identity_id, row.source_account_id, row.source_provider)
            == ("identity-A", "reader-A", "paris")
            for row in loans
        )

        resources = session.downloadable_resource_snapshot(account)
        assert resources.status == "success"
        assert len(resources.resources) == digital
        assert all(
            (row.source.scope.identity_id, row.source.scope.account_id)
            == ("identity-A", "reader-A")
            for row in resources.resources
        )
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()


def test_rights_reader_does_not_reuse_previous_positive_loans_after_outage():
    """Across independent readers, stale positive loans are not fresh A."""
    session, account, client = _session(1)
    try:
        first, loans = _sync_loan_inventory_observation(None, account, session)
        assert first.status == "success" and len(loans) == 1
        client.fail_digital = True
        resources = session.downloadable_resource_snapshot(account)
        assert resources.status == "failed"
        assert resources.resources == ()
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()
