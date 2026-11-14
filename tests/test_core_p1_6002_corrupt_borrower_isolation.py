"""#6002 — malformed borrower data cannot blank another Home lender.

Offline typed Accounts with deliberately corrupted mutable legacy fields.
No provider/SQLite/GTK I/O or quota inference.
"""
from __future__ import annotations

import pytest

from cifereca.core.digital_loan_availability import digital_loan_availability
from cifereca.plugin_api import digital_loan_availability_lines
from tests.test_core_p1_6002_digital_loan_availability import (
    account, active_loan, configured, runtime, spec,
)


@pytest.mark.parametrize("bad_id", [
    None, [], 42, True, "reader\x00private", "a" * 1025,
])
def test_invalid_borrower_id_isolated_to_its_own_connection(bad_id):
    corrupt = account("demo", "bad", "not-renderable")
    corrupt.unique_id = bad_id
    good = account("demo", "good", "reader-exact")
    state = runtime(
        [corrupt, good],
        [configured("demo", "bad"), configured("demo", "good")],
        [spec("demo")],
        [active_loan(good, "exact-loan")],
    )
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2
    by_credential = {row.credential_id: row for row in rows}
    invalid, valid = by_credential["bad"], by_credential["good"]
    assert invalid.status == "unknown"
    assert invalid.reason == "scope_ambiguous"
    assert invalid.borrower_id is None
    assert invalid.observed_loans is None
    assert invalid.personal_ceiling is None
    assert invalid.remaining is None
    assert valid.borrower_id == "reader-exact"
    assert valid.observed_loans == 1
    assert valid.personal_ceiling == 4
    assert valid.remaining is None
    visible = "\n".join(digital_loan_availability_lines(rows, identity_id="alice"))
    assert "Reste inconnu" in visible
    assert "not-renderable" not in visible
    assert "exact-loan" not in visible


@pytest.mark.parametrize("bad_primary", [None, "yes", 1, [], object()])
def test_invalid_legacy_is_primary_does_not_select_another_account(bad_primary):
    corrupt = account("demo", "bad", "reader-corrupt")
    corrupt.is_primary = bad_primary
    good = account("demo", "good", "reader-good")
    state = runtime(
        [corrupt, good],
        [configured("demo", "bad"), configured("demo", "good")],
        [spec("demo")],
        [active_loan(good, "genuine")],
    )
    rows = {row.credential_id: row for row in digital_loan_availability(state, "alice")}
    assert rows["bad"].reason == "scope_ambiguous"
    assert rows["bad"].borrower_id is None
    assert rows["good"].observed_loans == 1


def test_oversized_single_connection_does_not_hide_independent_lender():
    many = [
        account("demo", "large", f"reader-{index}")
        for index in range(33)
    ]
    ordinary = account("demo", "ordinary", "reader-other")
    state = runtime(
        [*many, ordinary],
        [configured("demo", "large"), configured("demo", "ordinary")],
        [spec("demo")],
        [active_loan(ordinary, "real")],
    )
    rows = {row.credential_id: row for row in digital_loan_availability(state, "alice")}
    assert len(rows) == 2
    assert rows["large"].reason == "scope_ambiguous"
    assert rows["large"].borrower_id is None
    assert rows["large"].personal_ceiling is None
    assert rows["ordinary"].borrower_id == "reader-other"
    assert rows["ordinary"].observed_loans == 1
    assert rows["ordinary"].remaining is None
