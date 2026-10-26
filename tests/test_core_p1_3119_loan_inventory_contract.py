"""P1 #3119: immutable scoped loan inventory claims, no invented absence."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, Loan
from cifereca.plugin_api import (
    INVENTORY_CONTRACT, LoanInventoryScope, LoanBranchCoverage,
    LoanInventorySnapshot, legacy_loan_inventory, read_loan_inventory,
    Cancelled,
)


def account(*, provider="paris", credential="p1", borrower="", identity="alice"):
    return Account(
        provider_id=provider, credential_id=credential,
        unique_id=borrower, identity_id=identity,
    )


def loan(a: Account, *, uid="loan-1", medium="digital", account_id=None):
    return Loan(
        id=uid, provider=a.provider_id, medium=medium, record_id="record-1",
        title="Livre", source_provider=a.provider_id,
        source_credential=a.credential_id,
        source_account_id=a.unique_id if account_id is None else account_id,
        identity_id=a.identity_id, raw={"source": {"page": 1}},
    )


def branch(medium="digital", upstream="dilicom", *, complete=False):
    return LoanBranchCoverage(
        upstream=upstream, medium=medium,
        completeness="complete" if complete else "partial",
        pagination_complete=True if complete else None,
    )


def test_public_api_scope_primary_empty_and_defensive_positive_rows():
    a = account()
    row = loan(a)
    snapshot = LoanInventorySnapshot(
        scope=LoanInventoryScope.for_account(a), loans=(row,),
        status="success", branches=(branch(complete=True),),
        observed_at="2026-10-25T07:00:00+00:00",
    )
    assert snapshot.contract == INVENTORY_CONTRACT
    assert snapshot.scope.account_id == ""
    assert snapshot.branch_claims_complete("dilicom", "digital")
    assert not snapshot.branch_claims_complete("syracuse", "physical")
    row.raw["source"]["page"] = 99
    row.title = "Mutated"
    assert snapshot.loans[0].raw["source"]["page"] == 1
    assert snapshot.loans[0].title == "Livre"
    with pytest.raises(FrozenInstanceError):
        snapshot.status = "failed"


def test_paris_primary_alias_is_narrow_not_a_wildcard():
    a = account()
    assert len(legacy_loan_inventory(a, [loan(a, account_id="primary")]).loans) == 1
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        legacy_loan_inventory(a, [loan(a, account_id="linked")])
    linked = account(borrower="linked")
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        legacy_loan_inventory(linked, [loan(linked, account_id="primary")])


@pytest.mark.parametrize("modified", [
    {"provider": "bnf-pret"}, {"credential": "p2"},
    {"identity": "bob"}, {"borrower": "linked"},
])
def test_foreign_provider_credential_identity_and_borrower_are_rejected(modified):
    local = account()
    foreign = account(**modified)
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        legacy_loan_inventory(local, [loan(foreign)])


def test_legacy_positive_observations_never_implicitly_claim_zero_complete():
    a = account(provider="bnf-pret", borrower="bnf-user")
    empty = legacy_loan_inventory(a, [])
    assert empty.loans == ()
    assert empty.status == "partial"
    assert empty.reason_code == "legacy_unverified"
    assert not empty.branch_claims_complete("bnf_html", "digital")
    observed = legacy_loan_inventory(a, [loan(a), loan(a, uid="loan-2")])
    assert len(observed.loans) == 2 and observed.branches == ()
    assert not observed.branch_claims_complete("bnf_html", "digital")


def test_branch_completion_requires_explicit_upstream_and_pagination_end():
    with pytest.raises(ValueError, match="unproven"):
        LoanBranchCoverage("dilicom", "digital", "complete")
    with pytest.raises(ValueError, match="unproven"):
        LoanBranchCoverage("dilicom", "digital", "complete", False)
    a = account()
    snap = LoanInventorySnapshot(
        scope=LoanInventoryScope.for_account(a), loans=(),
        status="success",
        branches=(branch(complete=True),
                  branch("physical", "syracuse", complete=False)),
    )
    assert snap.branch_claims_complete("dilicom", "digital")
    assert not snap.branch_claims_complete("syracuse", "physical")
    with pytest.raises(ValueError, match="ambiguous"):
        LoanInventorySnapshot(
            scope=snap.scope, loans=(),
            branches=(branch(complete=True), branch(complete=True)),
        )


@pytest.mark.parametrize("status", ["partial", "failed", "cancelled"])
def test_even_a_complete_branch_claim_has_no_negative_authority_on_non_success(status):
    a = account()
    snap = LoanInventorySnapshot(
        scope=LoanInventoryScope.for_account(a), loans=(),
        status=status, branches=(branch(complete=True),),
    )
    assert not snap.branch_claims_complete("dilicom", "digital")


def test_timestamp_bounds_and_closed_reason_codes():
    a = account()
    for timestamp in ("2026-10-25T06:00:00", "2026-10-25T06:00:00secret",
                      "1999-01-01T01:00:00+00:00"):
        with pytest.raises(ValueError, match="timestamp"):
            LoanInventorySnapshot(scope=LoanInventoryScope.for_account(a),
                                  loans=(), observed_at=timestamp)
    with pytest.raises(ValueError, match="reason"):
        LoanInventorySnapshot(scope=LoanInventoryScope.for_account(a),
                              loans=(), reason_code="secret://url")


def session(a: Account, *, connected=True, enabled=True, reader=None, hook=None):
    calls = []
    def legacy(arg):
        calls.append(("legacy", arg))
        return reader(arg) if callable(reader) else [loan(a)]
    ns = NS(
        connected=connected, provider_id=a.provider_id,
        credential_id=a.credential_id,
        capabilities=NS(loans=enabled), loans=legacy,
    )
    if hook is not None:
        def typed(arg):
            calls.append(("typed", arg))
            return hook(arg)
        ns.loan_inventory_snapshot = typed
    return ns, calls


def test_legacy_reader_is_explicit_and_returns_partial_even_on_success():
    a = account(borrower="linked")
    s, calls = session(a)
    assert calls == []
    result = read_loan_inventory(s, a)
    assert calls == [("legacy", a)]
    assert len(result.loans) == 1
    assert result.status == "partial"
    assert result.reason_code == "legacy_unverified"


@pytest.mark.parametrize("connected,enabled", [(False, True), (True, False)])
def test_unavailable_session_never_starts_loan_read(connected, enabled):
    a = account()
    s, calls = session(a, connected=connected, enabled=enabled)
    result = read_loan_inventory(s, a)
    assert result.status == "failed" and result.reason_code == "unavailable"
    assert calls == []


def test_wrong_session_credential_never_starts_loan_read():
    a = account()
    s, calls = session(account(credential="p2"))
    assert read_loan_inventory(s, a).status == "failed"
    assert calls == []


def test_typed_reader_positive_case_and_mixed_account_rejected():
    a = account(provider="bnf-pret", borrower="one")
    b = account(provider="bnf-pret", borrower="other")
    def good(_):
        return LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(a), loans=(loan(a),),
            status="partial", reason_code="inventory_not_exhaustive",
            branches=(LoanBranchCoverage("bnf_html", "digital", "partial"),),
        )
    s, calls = session(a, hook=good)
    snapshot = read_loan_inventory(s, a)
    assert calls == [("typed", a)]
    assert snapshot.status == "partial" and snapshot.reason_code == "inventory_not_exhaustive"
    assert len(snapshot.loans) == 1 and not snapshot.branch_claims_complete("bnf_html", "digital")
    s.loan_inventory_snapshot = lambda _: LoanInventorySnapshot(
        scope=LoanInventoryScope.for_account(b), loans=(loan(b),),
        status="success", branches=(branch(complete=True),),
    )
    failed = read_loan_inventory(s, a)
    assert failed.status == "failed" and failed.loans == ()
    assert failed.reason_code == "read_failed"


def test_forged_or_hostile_provider_output_fails_closed_without_its_text():
    a = account()
    s, calls = session(a, reader=lambda _: [loan(account(identity="other"))])
    assert read_loan_inventory(s, a).reason_code == "read_failed"
    assert len(calls) == 1

    def boom(_):
        raise RuntimeError("https://signed.example/secret?token=PRIVATE")
    s, _ = session(a, reader=boom)
    failed = read_loan_inventory(s, a)
    assert failed.status == "failed"
    assert failed.reason_code == "read_failed"
    assert "PRIVATE" not in repr(failed)


def test_session_disconnected_during_legacy_read_discards_positive_result():
    a = account()
    s, calls = session(a)

    def interrupted(_):
        s.connected = False
        return [loan(a)]

    s.loans = interrupted
    result = read_loan_inventory(s, a)
    assert result.status == "failed"
    assert result.reason_code == "scope_changed"
    assert result.loans == ()
    assert calls == []


def test_identity_or_credential_switch_during_typed_read_discards_all_results():
    for change in ("identity", "credential"):
        a = account()
        s, _ = session(a)

        def mutated(_):
            result = LoanInventorySnapshot(
                scope=LoanInventoryScope.for_account(a),
                loans=(loan(a),), status="success",
                branches=(branch(complete=True),),
            )
            if change == "identity":
                a.identity_id = "bob"
            else:
                s.credential_id = "p2"
            return result

        s.loan_inventory_snapshot = mutated
        result = read_loan_inventory(s, a)
        assert result.status == "failed"
        assert result.reason_code == "scope_changed"
        assert result.loans == ()



@pytest.mark.parametrize("kind", ["typed", "legacy"])
def test_provider_cancellation_is_not_misclassified_as_inventory_read_failure(kind):
    a = account(provider="paris")
    def stopped(_):
        raise Cancelled("signed://PRIVATE/reader?token=SECRET")
    if kind == "typed":
        s, calls = session(a, hook=stopped)
    else:
        s, calls = session(a, reader=stopped)

    result = read_loan_inventory(s, a)
    assert result.status == "cancelled" and result.reason_code == "cancelled"
    assert result.scope == LoanInventoryScope.for_account(a)
    assert result.loans == () and result.branches == ()
    assert not result.branch_claims_complete("dilicom", "digital")
    assert calls == [(kind, a)]
    assert "SECRET" not in repr(result) and "PRIVATE" not in repr(result)


def test_arbitrary_provider_exception_still_failed_not_cancelled():
    a = account()
    s, calls = session(a, hook=lambda _: (_ for _ in ()).throw(
        RuntimeError("failure in typed inventory"),
    ))
    result = read_loan_inventory(s, a)
    assert result.status == "failed" and result.reason_code == "read_failed"
    assert calls == [("typed", a)]
