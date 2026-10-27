"""#2703: conflicting branch observations must not produce a positive claim."""
import pytest

from cifereca.plugin_api import (
    LoanBranchCoverage, project_existing_capabilities,
)
from tests.test_core_p1_2703_existing_capability_projection import (
    _descriptor, _loan_snapshot,
)


def test_duplicate_branch_with_conflicting_coverage_is_not_attested():
    branches = (
        LoanBranchCoverage("pnblive", "digital", "complete", True),
        LoanBranchCoverage("pnblive", "digital", "partial", False),
    )
    snapshot = _loan_snapshot(branches=branches)
    # A single exact upstream/medium key cannot simultaneously be complete
    # and partial. The projection must fail closed rather than taking the
    # first observation or accidentally claiming full coverage.
    with pytest.raises(ValueError, match="capability_loan_branch_conflict"):
        project_existing_capabilities(
            _descriptor(), identity_id="alice", loan_snapshot=snapshot,
        )


def test_duplicate_identical_branch_is_not_double_counted():
    branch = LoanBranchCoverage("pnblive", "digital", "complete", True)
    snapshot = _loan_snapshot(branches=(branch, branch))
    projected = project_existing_capabilities(
        _descriptor(), identity_id="alice", loan_snapshot=snapshot,
    )
    rows = [r for r in projected.observations
            if r.operation == "loan.inventory.branch"]
    assert len(rows) == 1
    assert rows[0].status == "supported"
    assert rows[0].authorizes_mutation is False
