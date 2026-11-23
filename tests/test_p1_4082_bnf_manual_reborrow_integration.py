"""#4082: explicit BnF reborrow after fresh complete end-of-cycle evidence.

Real SQLite CAS, fake provider read/action: no network, tokens or loan POST.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.manual_library_borrow import manual_borrow_available
from cifereca.models import Loan
from cifereca.plugin_api.loan_inventory import (
    LoanBranchCoverage, LoanInventoryScope, LoanInventorySnapshot,
)
from cifereca.providers.base import ManualBorrowPolicy
from tests.test_core_p0_2055_manual_dispatch import setup, manual_bnf_borrow


def _prepare(session, account):
    session.descriptor = replace(
        session.descriptor,
        manual_borrow_policy=ManualBorrowPolicy(
            docbase="BNF_PRET", reborrow_inventory_branch="bnf_html",
        ),
    )
    session.current_previous = False
    session.inventory_reads = 0

    def snapshot(borrower):
        session.inventory_reads += 1
        assert borrower is account
        # No empty list is privileged; the BnF producer only qualifies
        # complete coverage when an independent active loan was observed.
        current = (
            "loan-synthetic" if session.current_previous
            else "unrelated-loan"
        )
        edition = "book-1" if session.current_previous else "other-book"
        return LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(account),
            loans=(Loan(
                id=current, record_id=edition, provider="BNF_PRET",
                medium="digital", title="Other local loan", state="active",
                source_provider=account.provider_id,
                source_credential=account.credential_id,
                source_account_id=account.unique_id,
                identity_id=account.identity_id,
            ),),
            status="success", branches=(LoanBranchCoverage(
                "bnf_html", "digital", "complete", True,
            ),), observed_at=datetime.now(timezone.utc).isoformat(),
        )
    session.loan_inventory_snapshot = snapshot


def test_confirmed_end_allows_second_manual_cycle_with_original_journal(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        _prepare(session, account)
        key = borrow_claim_key(account, item)
        first = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True,
        )
        assert first["status"] == "confirmed"
        original = service.storage.metadata_get(key)
        # Passive UI never reads the provider and cannot grant a loan.
        assert manual_borrow_available(
            service, item, provider_id="bnf-pret", docbase="BNF_PRET",
            identity_id="default",
        )
        assert session.inventory_reads == 0
        session.current_previous = True
        blocked = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True,
        )
        assert blocked["status"] == "blocked"
        assert blocked["reason_code"] == "borrow_cycle_not_ended"
        assert len(session.dispatches) == 1
        assert service.storage.metadata_get(key) == original
        # The next click performs one fresh typed read, then reserves a new
        # generation of the very same edition/borrower claim by SQLite CAS.
        session.current_previous = False
        next_cycle = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True,
        )
        assert next_cycle["status"] == "confirmed"
        assert len(session.dispatches) == 2
        now = service.storage.loan_borrow_state(key)
        assert now["cycle_generation"] == 2
        history = "loan.borrow.history.v1:" + key.split(":")[-1] + ":1"
        assert service.storage.metadata_get(history) == original
    finally:
        service.close()


def test_ambiguous_first_post_remains_one_shot_even_with_complete_inventory(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        _prepare(session, account)
        session.failure = TimeoutError("opaque test transport")
        initial = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True,
        )
        assert initial["status"] == "verification_unavailable"
        second = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True,
        )
        assert second["status"] == "verification_unavailable"
        assert len(session.dispatches) == 1
        assert session.inventory_reads == 0
    finally:
        service.close()
