"""#2703: fail closed before capability projection on ambiguous loan branches.

The loan inventory v1 source contract rejects repeated medium/upstream keys.
Projection only consumes valid snapshots; it cannot silently pick one among
contradictory observations or amplify duplicate provider claims.
"""
import pytest

from cifereca.plugin_api import (
    LoanBranchCoverage, project_existing_capabilities,
)
from tests.test_core_p1_2703_existing_capability_projection import (
    _descriptor, _loan_snapshot,
)


@pytest.mark.parametrize("second", (
    LoanBranchCoverage("pnblive", "digital", "partial", False),
    LoanBranchCoverage("pnblive", "digital", "complete", True),
))
def test_duplicate_branch_is_rejected_at_source_contract(second):
    branches = (
        LoanBranchCoverage("pnblive", "digital", "complete", True),
        second,
    )
    with pytest.raises(ValueError, match="^ambiguous_loan_inventory_branches$"):
        _loan_snapshot(branches=branches)


def test_distinct_branches_keep_exact_scopes_and_partial_parent():
    branches = (
        LoanBranchCoverage("pnblive", "digital", "complete", True),
        LoanBranchCoverage("pnblive", "physical", "partial", False),
    )
    projected = project_existing_capabilities(
        _descriptor(), identity_id="alice",
        loan_snapshot=_loan_snapshot(branches=branches),
    )
    parent = projected.exact(
        "loan.inventory", "account", identity_id="alice",
        account_id="card-a", credential_id="vault-a",
    )
    assert parent.status == "partial"
    assert parent.evidence_ref == ""
    digital = projected.exact(
        "loan.inventory.branch", "digital", field="pnblive",
        identity_id="alice", account_id="card-a", credential_id="vault-a",
    )
    physical = projected.exact(
        "loan.inventory.branch", "physical", field="pnblive",
        identity_id="alice", account_id="card-a", credential_id="vault-a",
    )
    assert digital.status == "supported"
    assert physical.status == "partial"
    assert physical.evidence_ref == ""
    assert projected.exact(
        "loan.inventory.branch", "digital", field="pnblive",
        identity_id="bob", account_id="card-a", credential_id="vault-a",
    ) is None
    assert all(row.authorizes_mutation is False for row in projected.observations)
