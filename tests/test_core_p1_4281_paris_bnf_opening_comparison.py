"""#4281: compare the same opening observations against real Paris/BnF adapters.

Synthetic HTTP/HTML and synthetic Core owner; no live account, login network,
ACSM or download. This is a request-count comparison, NOT wall-clock data.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from cifereca.core.sync_coordination import _sync_loan_inventory_observation
from tests.test_core_p1_4281_paris_opening_observer_cost import _session as paris

BNF_DIR = Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret"
if str(BNF_DIR) not in sys.path:
    sys.path.insert(0, str(BNF_DIR))

from cifereca_bnf_pret import BnfProviderSession  # noqa: E402


class _BnfClient:
    def __init__(self, digital: int):
        self.calls = []
        rows = "".join(
            '<div class="js-loan">'
            f'<a href="/resources/edition-{i}">Synthetic title {i}</a>'
            f'<a href="/loans/loan-{i}">Synthetic loan</a>'
            '</div>'
            for i in range(digital)
        )
        self.html = '<section class="active-loans">' + rows + '</section>'

    def login(self, _username, _password):
        self.calls.append("login")
        return '<section class="active-loans"></section>'

    def activity(self):
        self.calls.append("activity")
        return self.html

    def clear(self):
        self.calls.append("clear")

    def close(self):
        self.calls.append("close")


class _BnfOwner:
    def accounts(self, identity_id=None):
        if identity_id != "identity-A":
            return []
        return [SimpleNamespace(
            provider_id="bnf-pret",
            account_id="credential-A", identity_id="identity-A",
        )]


def _bnf(digital):
    client = _BnfClient(digital)
    session = BnfProviderSession(
        client=client, credential_id="credential-A",
        label="Synthetic BnF", credentials={
            "username": "synthetic", "password": "synthetic",
        }, options={}, identity_broker=_BnfOwner(),
    )
    (account,) = session.connect()
    account.identity_id = "identity-A"
    return session, account, client


@pytest.mark.parametrize("digital", [0, 1, 4, 20])
def test_paris_vs_bnf_two_opening_readers_have_independent_get_budgets(digital):
    """Paris has two branches per observation; BnF has one HTML activity.

    Rights must be re-read within the canonical phase; no persistent loan
    cache may be treated as fresh A, and the two authors/providers are scoped.
    """
    paris_session, paris_account, paris_client = paris(digital)
    bnf_session, bnf_account, bnf_client = _bnf(digital)
    try:
        paris_loan_snapshot, paris_loans = _sync_loan_inventory_observation(
            None, paris_account, paris_session,
        )
        paris_resources = paris_session.downloadable_resource_snapshot(paris_account)

        bnf_loan_snapshot, bnf_loans = _sync_loan_inventory_observation(
            None, bnf_account, bnf_session,
        )
        bnf_resources = bnf_session.downloadable_resource_snapshot(bnf_account)

        assert paris_loan_snapshot.status == "success"
        assert len(paris_loans) == len(paris_resources.resources) == digital
        assert paris_client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
        assert bnf_loan_snapshot.status == ("success" if digital else "partial")
        assert len(bnf_loans) == len(bnf_resources.resources) == digital
        assert bnf_client.calls == ["login", "activity", "activity"]
        assert {row.identity_id for row in paris_loans} <= {"identity-A"}
        assert {row.identity_id for row in bnf_loans} <= {"identity-A"}
        assert all(row.source.scope.provider_id == "bnf-pret"
                   for row in bnf_resources.resources)
        assert all(row.source.scope.provider_id == "paris"
                   for row in paris_resources.resources)
    finally:
        paris_session.close()
        bnf_session.close()
