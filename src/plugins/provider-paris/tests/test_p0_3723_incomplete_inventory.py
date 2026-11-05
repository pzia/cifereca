"""An unfinished ListLoans response cannot terminate absent historical loans.

Real Paris client/provider over MockTransport; no supplier mutation or ADEPT.
"""
from types import SimpleNamespace as NS

import httpx
import pytest

from tests.test_p1_4157_listloans_completeness_matrix import scenario, loan_row


class Owner:
    def __init__(self, account):
        self.account = account

    def accounts(self, identity_id):
        a = self.account
        assert identity_id == a.identity_id
        return [NS(provider_id=a.provider_id, credential_id=a.credential_id,
                   account_id=a.unique_id, identity_id=a.identity_id, scope_version=2)]


PENDING = [
    {"HasMore": True}, {"HasMorePages": True}, {"NextPage": 2},
    {"Next": "https://example.invalid/next"},
    {"ContinuationToken": "synthetic-next"},
    {"TotalCount": 10, "PageSize": 1},
]


@pytest.mark.parametrize("extra", PENDING)
@pytest.mark.parametrize("level", ["root", "d"])
@pytest.mark.parametrize("count", [0, 1])
def test_incomplete_digital_response_cannot_claim_complete_rights(extra, level, count):
    body = {"success": True, "d": {"Loans": [loan_row("d1")] * count}}
    (body if level == "root" else body["d"]).update(extra)
    session, account, diagnostics, calls = scenario(
        digital_response=httpx.Response(200, json=body),
        physical=(loan_row("physical"),),
    )
    session.identity_broker = Owner(account)
    try:
        inventory = session.loan_inventory_snapshot(account)
        assert inventory.status == "partial"
        assert inventory.branches[0].reason_code == "branch_unavailable"
        assert [loan.provider for loan in inventory.loans] == ["SYRACUSE"]
        rights = session.rights_observation_snapshot(account)
        assert rights.status == "failed" and rights.coverage == "unknown"
        assert not rights.claims_complete_branch
        assert rights.pagination_complete is False
        assert len(calls) == 4  # two reads, no pagination guesses/retry/mutation
        assert "paris_loans_pagination_incomplete" in repr(diagnostics.events)
    finally:
        session.close()


def test_incomplete_physical_branch_does_not_invalidate_digital_rights():
    session, account, _, calls = scenario(
        digital=(loan_row("digital"),),
        physical_response=httpx.Response(200, json={
            "success": True, "d": {"Loans": [], "HasMore": True},
        }),
    )
    session.identity_broker = Owner(account)
    try:
        inventory = session.loan_inventory_snapshot(account)
        assert inventory.status == "partial"
        assert [loan.provider for loan in inventory.loans] == ["DILICOM"]
        rights = session.rights_observation_snapshot(account)
        assert rights.status == "success" and rights.claims_complete_branch
        assert [right.right_id for right in rights.rights] == ["synthetic-digital"]
        assert len(calls) == 4
    finally:
        session.close()


@pytest.mark.parametrize("extra", [{}, {
    "HasMore": False, "HasMorePages": False, "NextPage": None,
    "Next": "", "ContinuationToken": None, "TotalCount": 0,
}])
def test_valid_empty_digital_list_still_succeeds(extra):
    session, account, _, calls = scenario(digital_response=httpx.Response(
        200, json={"success": True, "d": {"Loans": [], **extra}},
    ))
    session.identity_broker = Owner(account)
    try:
        rights = session.rights_observation_snapshot(account)
        assert rights.rights == ()
        assert rights.status == "success" and rights.claims_complete_branch
        assert len(calls) == 2
    finally:
        session.close()
