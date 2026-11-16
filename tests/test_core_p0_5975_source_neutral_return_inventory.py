"""P0 #5975: typed return inventory can attest any exact installed source.

Private read-only verification: never sends a return POST, invents a loan
right or considers an unscoped/incomplete inventory proof of absence.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, Loan
from cifereca.core.loan_return_verification import (
    _paris_inventory_exact_borrower, _read_paris_return_presence,
)
from cifereca.plugin_api.loan_inventory import (
    LoanInventoryScope, LoanBranchCoverage, LoanInventorySnapshot,
)


def _rig(*, provider="future-lender", docbase="PBNB",
         coverage="complete", status="success", rows=()):
    account = Account(
        identity_id="identity-A", provider_id=provider,
        credential_id="credential-A", unique_id="reader-1", is_primary=False,
    )
    target = Loan(
        id="loan-A", provider=docbase, medium="digital",
        source_provider=provider, source_credential="credential-A",
        source_account_id="reader-1", identity_id="identity-A",
        record_id="edition-A",
    )
    calls = []
    session = NS(
        provider_id=provider, credential_id="credential-A", connected=True,
        capabilities=NS(loans=True),
    )

    def inventory(selected):
        calls.append(selected)
        return LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(selected),
            loans=tuple(rows), status=status, branches=(
                LoanBranchCoverage(
                    docbase.casefold(), "digital", coverage,
                    pagination_complete=True if coverage == "complete" else None,
                ),
            ),
        )

    session.loan_inventory_snapshot = inventory
    session.loans = lambda _: pytest.fail("legacy GET after typed snapshot")
    session.posts = []
    service = NS(
        accounts=[account],
        _sessions={f"{provider}::credential-A": session},
        _account_context_epoch=0,
        _session_key=lambda source, credential: f"{source}::{credential}",
    )
    return service, account, session, target, calls


@pytest.mark.parametrize("provider,branch", [
    ("future-lender", "PBNB"),
    ("biblioaccess.la-rochelle", "LOCAL_LIBRARY"),
    ("paris", "DILICOM"),
])
def test_complete_exact_source_can_attest_absence_without_supplier_branch(
        provider, branch):
    service, account, session, target, calls = _rig(
        provider=provider, docbase=branch,
    )
    assert _read_paris_return_presence(service, account, session, target) is False
    assert calls == [account]
    assert session.posts == []


@pytest.mark.parametrize("coverage,status", [
    ("unknown", "success"), ("partial", "success"),
    ("complete", "partial"),
])
def test_partial_or_unknown_source_never_proves_absence(coverage, status):
    service, account, session, target, calls = _rig(
        coverage=coverage, status=status,
    )
    with pytest.raises(ValueError, match="return_inventory_not_exhaustive"):
        _read_paris_return_presence(service, account, session, target)
    assert len(calls) == 1
    assert session.posts == []


def test_sibling_branch_coverage_cannot_attest_target_absence():
    service, account, session, target, calls = _rig(docbase="OTHER_BRANCH")
    target = replace(target, provider="TARGET_BRANCH")
    with pytest.raises(ValueError, match="return_inventory_not_exhaustive"):
        _read_paris_return_presence(service, account, session, target)
    assert len(calls) == 1
    assert session.posts == []


def test_typed_positive_exact_loan_keeps_presence_without_complete_pagination():
    service, account, session, loan, calls = _rig(coverage="unknown")
    session.loan_inventory_snapshot = lambda selected: (
        LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(selected),
            loans=(loan,), status="partial",
            branches=(LoanBranchCoverage("p b n b".replace(" ", ""), "digital"),),
        )
    )
    assert _read_paris_return_presence(service, account, session, loan) is True
    assert session.posts == []


@pytest.mark.parametrize("field,foreign", [
    ("source_provider", "another-lender"),
    ("source_credential", "foreign-credential"),
    ("source_account_id", "another-reader"),
    ("identity_id", "foreign-identity"),
])
def test_foreign_rows_never_attest_borrower_scope(field, foreign):
    service, account, session, loan, calls = _rig()
    row = replace(loan, id="another-loan", **{field: foreign})
    assert _paris_inventory_exact_borrower([row], account) is False
    # The typed producer may not construct a foreign-scoped snapshot;
    # the consumer refuses its failed/invalid response without a second GET.
    session.loan_inventory_snapshot = lambda selected: LoanInventorySnapshot(
        scope=LoanInventoryScope.for_account(selected),
        loans=(row,), status="success", branches=(
            LoanBranchCoverage("pbnb", "digital", "complete", pagination_complete=True),
        ),
    )
    with pytest.raises(ValueError, match="return_inventory_unavailable"):
        _read_paris_return_presence(service, account, session, loan)
    assert session.posts == []


def test_generic_exact_reader_rejects_reserved_and_malformed_source_ids():
    _service, account, _session, loan, _calls = _rig()
    assert _paris_inventory_exact_borrower([loan], account)
    for invalid in ("all", "other", "bad..provider", "../escape"):
        assert not _paris_inventory_exact_borrower(
            [replace(loan, source_provider=invalid)],
            replace(account, provider_id=invalid),
        )
