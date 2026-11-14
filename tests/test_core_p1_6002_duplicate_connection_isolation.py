"""P1 #6002 — duplicate configured connections fail closed per credential."""
from __future__ import annotations

from cifereca.core.digital_loan_availability import digital_loan_availability
from tests.test_core_p1_6002_digital_loan_availability import (
    account, active_loan, configured, runtime, spec,
)


def test_duplicate_connection_never_hides_other_credentials_or_uses_first_quota():
    duplicated = account("demo", "duplicate", "borrower")
    independent = account("demo", "independent", "reader")
    stored = [
        configured("demo", "duplicate", options={"primary_digital": "99"}),
        configured("demo", "independent", options={"primary_digital": "4"}),
        configured("demo", "duplicate", options={"primary_digital": "1"}),
    ]
    state = runtime(
        [duplicated, independent], stored, [spec("demo")],
        [active_loan(duplicated, "untrusted"), active_loan(independent, "real")],
    )
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2
    by_credential = {row.credential_id: row for row in rows}
    bad = by_credential["duplicate"]
    assert bad.status == "unknown"
    assert bad.reason == "scope_ambiguous"
    assert bad.borrower_id is None
    assert bad.personal_ceiling is None
    assert bad.observed_loans is None
    assert bad.remaining is None
    good = by_credential["independent"]
    assert good.borrower_id == "reader"
    assert good.personal_ceiling == 4
    assert good.observed_loans == 1
    assert good.remaining is None


def test_duplicate_connection_does_not_inherit_other_profile_or_provider():
    a = account("demo", "duplicate", "a")
    other = account("other", "same", "b")
    foreign = account("demo", "foreign", "secret", identity="bob")
    state = runtime(
        [a, other, foreign],
        [
            configured("demo", "duplicate"),
            configured("other", "same"),
            configured("demo", "duplicate"),
            configured("demo", "foreign", identity="bob"),
        ],
        [spec("demo"), spec("other")],
        [active_loan(other, "independent"), active_loan(foreign, "private")],
    )
    alice = digital_loan_availability(state, "alice")
    assert len(alice) == 2
    assert {(r.provider_id, r.credential_id) for r in alice} == {
        ("demo", "duplicate"), ("other", "same"),
    }
    assert next(r for r in alice if r.provider_id == "demo").reason == "scope_ambiguous"
    assert next(r for r in alice if r.provider_id == "other").observed_loans == 1
    bob, = digital_loan_availability(state, "bob")
    assert bob.credential_id == "foreign"
    assert bob.observed_loans == 1
    assert bob.remaining is None


def test_duplicate_connection_without_other_accounts_still_unknown():
    state = runtime(
        [], [configured("demo", "same"), configured("demo", "same")],
        [spec("demo")],
    )
    row, = digital_loan_availability(state, "alice")
    assert row.reason == "scope_ambiguous"
    assert row.borrower_id is None
    assert row.personal_ceiling is None
    assert row.remaining is None
