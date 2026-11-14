"""P1 #6002: saved connection duplicates follow the Core session key.

The real session registry is keyed by provider.lower() and credential. A
malformed provider spelling must not create a second apparently scoped
digital-loan capacity or leak the surviving borrower's advisory ceiling.
"""
from __future__ import annotations

import pytest

from cifereca.core.digital_loan_availability import digital_loan_availability
from cifereca.plugin_api import digital_loan_availability_lines
from tests.test_core_p1_6002_digital_loan_availability import (
    account, active_loan, configured, runtime, spec,
)


@pytest.mark.parametrize("swapped", [False, True])
def test_mixed_case_provider_connection_deduplicates_one_registry_key(swapped):
    borrowed = account("demo", "shared", "alice-reader")
    healthy = account("demo", "private", "other-reader")
    mixed = configured("DEMO", "shared",
                       options={"primary_digital": "99"})
    canonical = configured("demo", "shared",
                           options={"primary_digital": "3"})
    shared = [mixed, canonical]
    if swapped:
        shared.reverse()
    state = runtime(
        [borrowed, healthy],
        [*shared, configured("demo", "private",
                            options={"primary_digital": "4"})],
        [spec("demo")],
        [active_loan(borrowed, "sensitive"), active_loan(healthy, "healthy")],
    )
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2  # one shared-key refusal, one healthy connection
    by_credential = {row.credential_id: row for row in rows}
    bad = by_credential["shared"]
    assert bad.status == "unknown"
    assert bad.reason == "scope_ambiguous"
    assert bad.borrower_id is None
    assert bad.observed_loans is None
    assert bad.personal_ceiling is None
    assert bad.remaining is None

    good = by_credential["private"]
    assert good.reason == "inventory_unverified"
    assert good.borrower_id == "other-reader"
    assert good.observed_loans == 1
    assert good.personal_ceiling == 4
    assert good.remaining is None
    visible = "\n".join(digital_loan_availability_lines(rows, identity_id="alice"))
    assert "sensitive" not in visible
    assert "CREDENTIAL_SECRET" not in visible
    assert "plafond personnel 99" not in visible
    assert "plafond personnel 3" not in visible


def test_distinct_credentials_with_mixed_provider_case_stay_separate():
    alice = account("demo", "one", "alice-reader")
    state = runtime(
        [alice], [configured("demo", "one"),
                  configured("DEMO", "two")],
        [spec("demo")], [active_loan(alice, "real")],
    )
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2
    by_credential = {row.credential_id: row for row in rows}
    assert by_credential["one"].observed_loans == 1
    assert by_credential["one"].reason == "inventory_unverified"
    assert by_credential["two"].reason == "borrower_unverified"
    assert all(row.remaining is None for row in rows)
