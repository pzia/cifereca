"""#3121: exact first Paris ACSM location survives the UI return only in memory.

The sole mutating ASSARedirect is simulated. No live Paris, token, ADEPT or
actual ACSM download is performed by these tests.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.manual_paris_first_acsm import (
    take_first_acsm_loan, remember_confirmed_first_acsm,
)
from cifereca.models import Loan
from tests.test_paris_manual_borrow_dispatch import setup, attempt


FIRST = "https://bibliotheques.paris.fr/numerique/synthetic.acsm"


def _fresh_loan(account, *, book="edition", loan_id="loan-exact"):
    return Loan(
        id=loan_id, record_id=book, provider="DILICOM", medium="digital",
        title="Synthetic", state="active", identity_id=account.identity_id,
        source_provider="paris", source_credential=account.credential_id,
        source_account_id="primary", ebook_url=None,
    )


def test_first_acsm_from_confirmed_borrow_is_one_shot_and_not_public(tmp_path):
    service, session, account, item, state = setup(tmp_path)
    try:
        original = attempt(service, account, item)
        assert original == {
            "status": "confirmed", "reason_code": "borrow_active_loan_observed",
            "provider_verified": True, "retry": "verification_only",
        }
        assert FIRST not in str(original)
        assert state["mutations"] == 1
        key = borrow_claim_key(account, item)
        assert service.storage.loan_borrow_state(key)["status"] == "confirmed"
        assert FIRST not in str(service.storage.loan_borrow_state(key))

        seen = _fresh_loan(account)
        restored = take_first_acsm_loan(service, seen, account)
        assert restored is not None and restored.ebook_url == FIRST
        assert seen.ebook_url is None  # mutable caller/source projection untouched
        assert take_first_acsm_loan(service, seen, account) is None
        assert state["mutations"] == 1  # never a second ASSARedirect
    finally:
        service.close()


@pytest.mark.parametrize("result", ["no-acsm", "timeout-unconfirmed"])
def test_no_verified_initial_acsm_offer_is_never_invented(tmp_path, result):
    service, session, account, item, state = setup(tmp_path, result=result)
    try:
        outcome = attempt(service, account, item)
        if result == "no-acsm":
            assert outcome["status"] == "confirmed"
        else:
            assert outcome["status"] == "verification_unavailable"
        assert take_first_acsm_loan(service, _fresh_loan(account), account) is None
        assert state["mutations"] == 1
    finally:
        service.close()


@pytest.mark.parametrize("wrong", ["loan", "edition", "identity", "credential", "epoch"])
def test_first_offer_never_rebinds_account_edition_or_epoch(tmp_path, wrong):
    service, session, account, item, state = setup(tmp_path)
    try:
        assert attempt(service, account, item)["status"] == "confirmed"
        row = _fresh_loan(account)
        if wrong == "loan":
            row = replace(row, id="foreign-loan")
        elif wrong == "edition":
            row = replace(row, record_id="foreign-edition")
        elif wrong == "identity":
            row = replace(row, identity_id="another-profile")
        elif wrong == "credential":
            row = replace(row, source_credential="foreign-card")
        else:
            service._account_context_epoch += 1
        assert take_first_acsm_loan(service, row, account) is None
        assert state["mutations"] == 1
        if wrong == "epoch":
            assert take_first_acsm_loan(
                service, _fresh_loan(account), account,
            ) is None
        else:
            assert take_first_acsm_loan(
                service, _fresh_loan(account), account,
            ).ebook_url == FIRST
    finally:
        service.close()


def test_restart_does_not_synthesize_old_signed_url_or_resubmit_borrow(tmp_path):
    service, session, account, item, state = setup(tmp_path)
    try:
        assert attempt(service, account, item)["status"] == "confirmed"
        assert state["mutations"] == 1
    finally:
        service.close()
    restarted, session, account, item, state = setup(tmp_path)
    try:
        assert take_first_acsm_loan(
            restarted, _fresh_loan(account), account,
        ) is None
        assert attempt(restarted, account, item)["reason_code"] == "borrow_previous_attempt"
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        restarted.close()


def test_claim_changed_after_confirmation_invalidates_initial_offer(tmp_path):
    service, session, account, item, state = setup(tmp_path)
    try:
        assert attempt(service, account, item)["status"] == "confirmed"
        key = borrow_claim_key(account, item)
        row = service.storage.loan_borrow_state(key)
        row["status"] = "verification_unavailable"
        import json
        service.storage.metadata_put(key, json.dumps(row))
        assert take_first_acsm_loan(
            service, _fresh_loan(account), account,
        ) is None
    finally:
        service.close()


def test_direct_provider_stage_receives_first_offer_without_second_borrow(tmp_path):
    service, session, account, item, state = setup(tmp_path)
    try:
        assert attempt(service, account, item)["status"] == "confirmed"
        later_loan = _fresh_loan(account)  # fresh ListLoans omitted EbookUrl
        observed = []

        def stop_before_network(loan, borrower, _target):
            observed.append((loan, borrower))
            raise RuntimeError("synthetic_stop_before_download")

        session.download_digital = stop_before_network
        with pytest.raises(RuntimeError, match="synthetic_stop_before_download"):
            service.acquire_digital_loan(later_loan)
        assert len(observed) == 1
        assert observed[0][0].ebook_url == FIRST
        assert observed[0][1] is account
        assert later_loan.ebook_url is None
        assert state["mutations"] == 1
        assert take_first_acsm_loan(service, later_loan, account) is None
    finally:
        service.close()
