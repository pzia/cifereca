"""P1 #6002 — exact per-borrower advisory digital headroom (offline).

No provider method, GET/POST, backend DB write or global-profile fallback.
Real descriptor and loan-scope types; all accounts/loans are synthetic.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import IdentityBrokerImpl
from cifereca.core.digital_loan_availability import digital_loan_availability
from cifereca.models import Account, Loan
from cifereca.plugin_api import (
    AccountOptionField, BorrowerScopedOptionField, ProviderCapabilities,
    ProviderDescriptor, DigitalLoanAvailability, digital_loan_availability_lines,
)


def spec(provider, *, linked=False):
    return ProviderDescriptor(
        id=provider, display_name=provider.upper(), description="synthetic",
        state="ready",
        capabilities=ProviderCapabilities(loans=True, linked_accounts=linked),
        account_options=(
            AccountOptionField(
                "primary_digital", "Plafond numérique", default="4",
                value_type="unsigned_integer", min_value=0, max_value=99,
                advisory_loan_medium="digital",
            ),
            AccountOptionField(
                "primary_physical", "Plafond physique", default="9",
                value_type="unsigned_integer", min_value=0, max_value=99,
                advisory_loan_medium="physical",
            ),
        ),
        borrower_scoped_options=(
            BorrowerScopedOptionField(
                "linked_limits", "Liés",
                branches=(("digital", "DILICOM"), ("physical", "SYRACUSE")),
                advisory_loan_limit=True,
            ),
        ) if linked else (),
    )


def configured(provider, credential, identity="alice", *, options=None, enabled=True):
    return {
        "provider": provider, "id": credential,
        "identity_id": identity, "label": "Connexion de test",
        "enabled": enabled, "options": options or {},
        "credentials": {"password": "CREDENTIAL_SECRET"},
    }


def account(provider, credential, borrower, identity="alice", *, primary=True):
    return Account(
        display_name="Principal" if primary else "Lié",
        unique_id=borrower, is_primary=primary,
        provider_id=provider, credential_id=credential,
        identity_id=identity,
    )


def active_loan(a, loan_id, *, medium="digital", state="active"):
    return Loan(
        id=loan_id, provider=a.provider_id.upper(), medium=medium,
        record_id="edition-" + loan_id, title="Livre secret non destiné à l’accueil",
        source_provider=a.provider_id, source_credential=a.credential_id,
        source_account_id=a.unique_id, identity_id=a.identity_id, state=state,
    )


def runtime(accounts, stored, specs, loans=(), *, disconnected=()):
    state = NS(
        accounts=list(accounts), last_loans=list(loans),
        config=NS(provider_accounts=lambda: stored),
        provider_descriptors=lambda: specs,
        _session_key=lambda provider, credential: (provider, credential),
    )
    state._sessions = {
        (a.provider_id, a.credential_id): NS(
            provider_id=a.provider_id, credential_id=a.credential_id,
            connected=(a.credential_id not in disconnected),
            # A provider method is NOT a passive snapshot.
            loan_inventory_snapshot=lambda *_: pytest.fail("network/inventory forbidden"),
        )
        for a in accounts
    }
    return state


def test_multi_connection_primary_linked_branch_and_identity_are_exact():
    alice_a = account("paris", "paris-a", "", primary=True)
    alice_l = account("paris", "paris-a", "linked-1", primary=False)
    alice_b = account("paris", "paris-b", "", primary=True)
    bnf = account("bnf", "bnf-1", "reader-1")
    bob = account("paris", "paris-bob", "", identity="bob")
    stored = [
        configured("paris", "paris-a", options={
            "primary_digital": "0", "primary_physical": "99",
            "linked_limits": {"linked-1": {"digital": "99", "physical": "0"}},
        }),
        configured("paris", "paris-b", options={"primary_digital": "4"}),
        configured("bnf", "bnf-1"),
        configured("paris", "paris-bob", "bob", options={"primary_digital": "7"}),
    ]
    books = [
        active_loan(alice_a, "one"), active_loan(alice_a, "one"),
        active_loan(alice_a, "other-medium", medium="physical"),
        active_loan(alice_l, "two"),
        active_loan(alice_b, "three"),
        active_loan(alice_b, "already-returned", state="returned"),
        active_loan(bnf, "bnf-positive"),
        active_loan(bob, "foreign"),
    ]
    state = runtime(
        [alice_a, alice_l, alice_b, bnf, bob], stored,
        [spec("paris", linked=True), spec("bnf")], books,
    )
    original = deepcopy(stored)
    rows = IdentityBrokerImpl(state).digital_loan_availability("alice")
    assert len(rows) == 4
    assert all(type(row) is DigitalLoanAvailability for row in rows)
    assert all(row.status == "unknown" and row.remaining is None
               and row.observed_at is None and row.eligibility == "not_verified"
               for row in rows)
    by_key = {(r.provider_id, r.credential_id, r.borrower_id): r for r in rows}
    assert (by_key["paris", "paris-a", ""].personal_ceiling,
            by_key["paris", "paris-a", ""].observed_loans) == (0, 1)
    assert (by_key["paris", "paris-a", "linked-1"].personal_ceiling,
            by_key["paris", "paris-a", "linked-1"].observed_loans) == (99, 1)
    assert (by_key["paris", "paris-b", ""].personal_ceiling,
            by_key["paris", "paris-b", ""].observed_loans) == (4, 1)
    assert (by_key["bnf", "bnf-1", "reader-1"].personal_ceiling,
            by_key["bnf", "bnf-1", "reader-1"].observed_loans) == (4, 1)
    assert all(r.identity_id == "alice" for r in rows)
    assert [r.personal_ceiling for r in IdentityBrokerImpl(state).digital_loan_availability("bob")] == [7]
    assert stored == original
    report = "\n".join(digital_loan_availability_lines(rows, identity_id="alice"))
    assert "Reste inconnu" in report and "Total non calculable" in report
    assert "CREDENTIAL_SECRET" not in report and "Livre secret" not in report
    assert "disponibles : 4" not in report and "4 prêt(s) disponibles" not in report


def test_partial_bnf_and_complete_biblio_claim_not_durable_and_no_subtraction():
    a = account("bnf", "card", "reader")
    b = account("biblio", "sso", "reader")
    state = runtime([a, b], [
        configured("bnf", "card", options={"primary_digital": "4"}),
        configured("biblio", "sso", options={"primary_digital": "4"}),
    ], [spec("bnf"), spec("biblio")], [
        active_loan(a, "loan-1"), active_loan(a, "loan-2"),
        active_loan(b, "loan-1"),
    ])
    state.last_loan_inventory_evidence = NS(
        status="success", branches=("complete",), remaining_loans=40,
    )
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2
    assert all(row.status == "unknown" and row.remaining is None for row in rows)
    assert [row.observed_loans for row in rows] == [2, 1]
    assert [row.personal_ceiling for row in rows] == [4, 4]


@pytest.mark.parametrize("bad", ["-1", "+4", "04", "100", "NaN", "", True, None, 4, [], {}])
def test_corrupt_saved_manual_ceiling_never_defaults_or_grants_headroom(bad):
    a = account("demo", "one", "reader")
    state = runtime([a], [
        configured("demo", "one", options={"primary_digital": bad}),
    ], [spec("demo")])
    record, = digital_loan_availability(state, "alice")
    assert record.personal_ceiling is None and record.remaining is None
    assert record.status == "unknown"


def test_no_connected_borrower_and_disabled_source_are_unknown_not_zero():
    stored = [configured("demo", "one"), configured("demo", "two", enabled=False)]
    state = runtime([], stored, [spec("demo")])
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2
    assert all(row.borrower_id is None and row.personal_ceiling is None
               and row.observed_loans is None for row in rows)
    assert {row.reason for row in rows} == {"borrower_unverified", "account_disabled"}
    assert all(row.remaining is None for row in rows)


def test_disconnected_or_unknown_provider_and_zero_observed_never_claim_zero():
    one = account("demo", "one", "reader")
    state = runtime([one], [configured("demo", "one")], [spec("demo")],
                    disconnected=("one",))
    rec, = digital_loan_availability(state, "alice")
    assert rec.reason == "account_disconnected"
    assert rec.personal_ceiling == 4 and rec.observed_loans is None
    assert rec.remaining is None
    assert digital_loan_availability(state, "bob") == ()
    state.provider_descriptors = lambda: []
    rec, = digital_loan_availability(state, "alice")
    assert rec.reason == "provider_unverified"
    assert rec.personal_ceiling is None


def test_duplicate_borrower_scope_never_splits_one_observation_twice():
    a = account("demo", "one", "same")
    state = runtime([a, account("demo", "one", "same")],
                    [configured("demo", "one")], [spec("demo")],
                    [active_loan(a, "loan")])
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 2
    assert all(r.reason == "scope_ambiguous" and r.observed_loans is None
               and r.personal_ceiling is None and r.remaining is None for r in rows)


@pytest.mark.parametrize("identity", ["", None, 1, True, "alice\x00bob"])
def test_absent_or_malformed_identity_cannot_inherit_navigation(identity):
    state = runtime([], [configured("demo", "one")], [spec("demo")])
    with pytest.raises(ValueError, match="identity_id_required"):
        digital_loan_availability(state, identity)


def test_no_source_side_effects_and_non_lender_catalogues_skipped():
    demo = account("demo", "one", "reader")
    catalogue = ProviderDescriptor(
        id="store", display_name="Book store", description="synthetic",
        state="ready", capabilities=ProviderCapabilities(loans=False),
    )
    stored = [configured("demo", "one"), configured("store", "shop")]
    state = runtime([demo], stored, [spec("demo"), catalogue])
    state.synchronize = lambda *_: pytest.fail("sync must never run")
    state.connect = lambda *_: pytest.fail("connect must never run")
    rows = digital_loan_availability(state, "alice")
    assert len(rows) == 1 and rows[0].provider_id == "demo"
    assert rows[0].status == "unknown"


def test_borrower_advisory_schema_is_explicit_opt_in_and_append_only():
    """An old linked-borrower setting must not become a digital quota."""
    old = BorrowerScopedOptionField(
        "linked_limits", "Ancien plafond",
        (("digital", "Numérique"), ("physical", "Physique")),
        "4", 0, 99, "Configuration locale",
    )
    assert old.advisory_loan_limit is False
    assert BorrowerScopedOptionField(
        "linked_limits", "Nouveau plafond",
        branches=(("digital", "Numérique"),),
        advisory_loan_limit=True,
    ).advisory_loan_limit is True

    a = account("demo", "one", "linked", primary=False)
    linked = spec("demo", linked=True)
    inactive = ProviderDescriptor(
        id="demo", display_name="Démo", description="fictif",
        state="ready", capabilities=ProviderCapabilities(loans=True),
        borrower_scoped_options=(old,),
    )
    configured_account = configured("demo", "one", options={
        "linked_limits": {"linked": {"digital": "99"}},
    })
    state = runtime([a], [configured_account], [inactive])
    row, = digital_loan_availability(state, "alice")
    assert row.personal_ceiling is None and row.remaining is None
    state.provider_descriptors = lambda: [linked]
    row, = digital_loan_availability(state, "alice")
    assert row.personal_ceiling == 99 and row.remaining is None
