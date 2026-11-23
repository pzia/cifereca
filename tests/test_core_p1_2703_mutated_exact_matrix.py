"""#2703: public exact lookup must reject mutated frozen capability evidence."""
import pytest

from cifereca.plugin_api import CapabilityEvidence, CapabilityMatrix


def _matrix():
    row = CapabilityEvidence(
        "synthetic", "loan.borrow", "account", source_kind="code",
        status="unverified", implementation_state="implemented",
    )
    return CapabilityMatrix("synthetic", (row,)), row


@pytest.mark.parametrize(("target", "key", "value"), [
    ("row", "status", "supported"),
    ("row", "source_kind", "forged"),
    ("row", "scope", "other\nprivate"),
    ("row", "provider_id", "another"),
    ("matrix", "schema_version", True),
    ("matrix", "observations", [1, 2]),
])
def test_exact_rejects_forged_frozen_snapshot(target, key, value):
    matrix, row = _matrix()
    object.__setattr__(matrix if target == "matrix" else row, key, value)
    with pytest.raises(ValueError):
        matrix.exact("loan.borrow", "account")


def test_matrix_rejects_evidence_mutated_before_assembly():
    _, row = _matrix()
    object.__setattr__(row, "status", "supported")
    with pytest.raises(ValueError, match="capability_unverified_support"):
        CapabilityMatrix("synthetic", (row,))


def test_valid_exact_remains_read_only_and_scope_specific():
    matrix, row = _matrix()
    assert matrix.exact("loan.borrow", "account") is row
    assert matrix.exact("loan.borrow", "public") is None
    assert not row.authorizes_mutation
