"""#6002: globally reused provider/credential keys cannot be a scoped lender.

The Core session registry is keyed by provider and credential, not identity.
No provider calls, storage writes or remaining-quota estimates are allowed.
"""
from __future__ import annotations

import pytest

from cifereca.core.digital_loan_availability import digital_loan_availability
from cifereca.plugin_api import digital_loan_availability_lines
from tests.test_core_p1_6002_digital_loan_availability import (
    account, active_loan, configured, runtime, spec,
)


def test_shared_credential_across_profiles_never_inherits_other_session():
    alice_shared = account("demo", "shared", "alice-reader", "alice")
    bob_shared = account("demo", "shared", "bob-reader", "bob")
    alice_healthy = account("demo", "alice-own", "other-reader", "alice")
    state = runtime(
        [alice_shared, bob_shared, alice_healthy],
        [
            configured("demo", "shared", "alice", options={"primary_digital": "99"}),
            configured("demo", "alice-own", "alice", options={"primary_digital": "4"}),
            configured("demo", "shared", "bob", options={"primary_digital": "0"}),
        ],
        [spec("demo")],
        [
            active_loan(alice_shared, "alice-shared"),
            active_loan(bob_shared, "bob-shared"),
            active_loan(alice_healthy, "alice-real"),
        ],
    )
    for identity in ("alice", "bob"):
        rows = digital_loan_availability(state, identity)
        shared, = (row for row in rows if row.credential_id == "shared")
        assert shared.status == "unknown"
        assert shared.reason == "scope_ambiguous"
        assert shared.borrower_id is None
        assert shared.observed_loans is None
        assert shared.personal_ceiling is None
        assert shared.remaining is None
        visible = " ".join(digital_loan_availability_lines(rows, identity_id=identity))
        assert "alice-shared" not in visible
        assert "bob-shared" not in visible

    alice = {row.credential_id: row for row in digital_loan_availability(state, "alice")}
    assert len(alice) == 2
    assert alice["alice-own"].borrower_id == "other-reader"
    assert alice["alice-own"].observed_loans == 1
    assert alice["alice-own"].personal_ceiling == 4
    assert alice["alice-own"].remaining is None


def test_different_provider_or_different_credential_preserves_scope():
    alice = account("demo", "same", "reader-a")
    bob = account("other", "same", "reader-b", "bob")
    bob2 = account("demo", "different", "reader-c", "bob")
    state = runtime(
        [alice, bob, bob2],
        [
            configured("demo", "same", "alice"),
            configured("other", "same", "bob"),
            configured("demo", "different", "bob"),
        ],
        [spec("demo"), spec("other")],
        [active_loan(alice, "a"), active_loan(bob, "b"), active_loan(bob2, "c")],
    )
    first, = digital_loan_availability(state, "alice")
    assert first.reason == "inventory_unverified"
    assert first.observed_loans == 1
    second = digital_loan_availability(state, "bob")
    assert {(row.provider_id, row.credential_id) for row in second} == {
        ("other", "same"), ("demo", "different"),
    }
    assert all(row.reason == "inventory_unverified" for row in second)
    assert all(row.observed_loans == 1 for row in second)


def test_registry_key_collision_is_case_insensitive_for_provider_only():
    alice = account("demo", "shared", "alice-reader")
    state = runtime(
        [alice],
        [
            configured("demo", "shared", "alice"),
            configured("DEMO", "shared", "bob"),
        ],
        [spec("demo")],
        [active_loan(alice, "positive")],
    )
    one, = digital_loan_availability(state, "alice")
    assert one.reason == "scope_ambiguous"
    assert one.borrower_id is None
    assert one.observed_loans is None
    assert one.personal_ceiling is None


@pytest.mark.parametrize("bad_owner", [None, "", 123])
@pytest.mark.parametrize("alternate_provider_case", [False, True])
@pytest.mark.parametrize("reverse_order", [False, True])
def test_malformed_connection_owner_cannot_hide_duplicate_session_registry_key(
    bad_owner, alternate_provider_case, reverse_order,
):
    """Every saved row with this key counts, including an ownerless/corrupt one."""
    shared = account("demo", "shared", "sensitive-reader")
    healthy = account("demo", "healthy", "independent-reader")
    legitimate = configured(
        "demo", "shared", options={"primary_digital": "4"},
    )
    malformed = configured(
        "DEMO" if alternate_provider_case else "demo",
        "shared", identity="bob",
        options={"primary_digital": "99"},
    )
    if bad_owner is None:
        del malformed["identity_id"]
    else:
        malformed["identity_id"] = bad_owner
    colliding = [legitimate, malformed]
    if reverse_order:
        colliding.reverse()
    rows = digital_loan_availability(
        runtime(
            [shared, healthy],
            [*colliding, configured(
                "demo", "healthy", options={"primary_digital": "7"},
            )],
            [spec("demo")],
            [active_loan(shared, "private"), active_loan(healthy, "public")],
        ),
        "alice",
    )
    assert len(rows) == 2
    by_credential = {record.credential_id: record for record in rows}
    bad = by_credential["shared"]
    assert bad.reason == "scope_ambiguous"
    assert bad.borrower_id is None
    assert bad.observed_loans is None
    assert bad.personal_ceiling is None
    assert bad.remaining is None
    good = by_credential["healthy"]
    assert good.reason == "inventory_unverified"
    assert good.observed_loans == 1 and good.personal_ceiling == 7
    assert good.remaining is None
    visible = " ".join(digital_loan_availability_lines(rows, identity_id="alice"))
    assert "plafond personnel 99" not in visible
    assert "CREDENTIAL_SECRET" not in visible


def test_invalid_owner_on_different_registry_key_does_not_poison_valid_account():
    reader = account("demo", "personal", "reader")
    malformed = configured("DEMO", "other", identity="bob")
    malformed["identity_id"] = None
    rows = digital_loan_availability(
        runtime(
            [reader],
            [configured("demo", "personal"), malformed],
            [spec("demo")],
            [active_loan(reader, "legitimate")],
        ),
        "alice",
    )
    one, = rows
    assert one.reason == "inventory_unverified"
    assert one.observed_loans == 1
    assert one.personal_ceiling == 4
    assert one.remaining is None
