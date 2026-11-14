"""P1 #6002: duplicate linked borrowers cannot yield duplicate capacity rows."""
from __future__ import annotations

import pytest

from cifereca.core.digital_loan_availability import digital_loan_availability
from tests.test_core_p1_6002_digital_loan_availability import (
    account, active_loan, configured, runtime, spec,
)


@pytest.mark.parametrize("same_primary", [False, True])
def test_duplicate_upstream_borrower_is_one_unknown_connection(same_primary):
    first = account("demo", "duplicate", "linked-1", primary=same_primary)
    duplicate = account("demo", "duplicate", "linked-1", primary=same_primary)
    independent = account("demo", "other", "reader", primary=True)
    state = runtime(
        [first, duplicate, independent],
        [configured("demo", "duplicate"), configured("demo", "other")],
        [spec("demo", linked=True)],
        [active_loan(first, "not-to-attribute"),
         active_loan(independent, "genuine")],
    )
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2
    by_credential = {row.credential_id: row for row in rows}
    ambiguous = by_credential["duplicate"]
    assert ambiguous.reason == "scope_ambiguous"
    assert ambiguous.borrower_id is None
    assert ambiguous.observed_loans is None
    assert ambiguous.personal_ceiling is None
    assert ambiguous.remaining is None
    healthy = by_credential["other"]
    assert healthy.observed_loans == 1
    assert healthy.personal_ceiling == 4
    assert healthy.remaining is None


def test_distinct_primary_and_two_linked_borrowers_keep_exact_cache_counts():
    people = [
        account("demo", "card", "", primary=True),
        account("demo", "card", "linked-a", primary=False),
        account("demo", "card", "linked-b", primary=False),
    ]
    state = runtime(
        people, [configured("demo", "card")], [spec("demo", linked=True)],
        [active_loan(person, str(index)) for index, person in enumerate(people)],
    )
    rows = digital_loan_availability(state, "alice")
    assert {row.borrower_id for row in rows} == {"", "linked-a", "linked-b"}
    assert all(row.observed_loans == 1 for row in rows)
    assert all(row.reason == "inventory_unverified" for row in rows)
    assert all(row.status == "unknown" and row.remaining is None for row in rows)
