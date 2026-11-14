"""P1 #6002 — never attribute linked/primary loan counts to ambiguous borrowers.

The Home projection must fail closed *per connection*, preserving another
credential's positive observations without provider calls or writes.
"""
from __future__ import annotations

import pytest

from cifereca.core.digital_loan_availability import digital_loan_availability
from tests.test_core_p1_6002_digital_loan_availability import (
    account, active_loan, configured, runtime, spec,
)


@pytest.mark.parametrize("ambiguous", ["linked_without_id", "two_primaries"])
def test_ambiguous_borrower_cardinality_never_exposes_a_false_scope(ambiguous):
    if ambiguous == "linked_without_id":
        # The empty account ID is the legacy primary alias. It cannot safely
        # identify a different, linked borrower even without another row.
        bad = [account("demo", "ambiguous", "", primary=False)]
    else:
        bad = [
            account("demo", "ambiguous", "owner-1", primary=True),
            account("demo", "ambiguous", "owner-2", primary=True),
        ]
    healthy = account("demo", "healthy", "reader")
    loans = [active_loan(a, "loan-" + str(i)) for i, a in enumerate(bad)]
    loans.append(active_loan(healthy, "genuine-loan"))
    state = runtime(
        bad + [healthy],
        [configured("demo", "ambiguous"), configured("demo", "healthy")],
        [spec("demo", linked=True)],
        loans,
    )
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2
    by_credential = {row.credential_id: row for row in rows}

    invalid = by_credential["ambiguous"]
    assert invalid.status == "unknown"
    assert invalid.reason == "scope_ambiguous"
    assert invalid.borrower_id is None
    assert invalid.observed_loans is None
    assert invalid.personal_ceiling is None
    assert invalid.remaining is None

    valid = by_credential["healthy"]
    assert valid.borrower_id == "reader"
    assert valid.observed_loans == 1
    assert valid.personal_ceiling == 4
    assert valid.remaining is None


def test_one_primary_and_distinct_linked_borrowers_remain_independent():
    primary = account("demo", "one", "", primary=True)
    linked = account("demo", "one", "linked-1", primary=False)
    state = runtime(
        [primary, linked], [configured("demo", "one")],
        [spec("demo", linked=True)],
        [active_loan(primary, "primary-loan"), active_loan(linked, "linked-loan")],
    )
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2
    by_borrower = {row.borrower_id: row for row in rows}
    assert set(by_borrower) == {"", "linked-1"}
    assert all(row.observed_loans == 1 for row in rows)
    assert all(row.reason == "inventory_unverified" for row in rows)
    assert all(row.status == "unknown" and row.remaining is None for row in rows)
