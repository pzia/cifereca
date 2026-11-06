"""#3121: first Paris ACSM offer, obtained by the same explicitly claimed click.

This is a *staging* operation, never ADEPT fulfillment, loan admission or
proof of canonical delivery. A failed token GET must not replay ASSARedirect
or transform a confirmed loan into a failed/second borrowing attempt.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from .. import acsm_cache
from ..artifact_inspection import inspect_artifact
from ..models import Account, Loan


def stage_paris_initial_acsm(service, session, account: Account, loan: Loan,
                             *, scope_current, claim_key: str,
                             operation_id: str) -> bool:
    """Attempt one GET of a known first-offer URL; persist only staged bytes.

    False means no verified local candidate, not that the loan was not made.
    The caller must have a confirmed exact loan and a spent one-shot claim.
    This method cannot initiate another borrow or invoke Adobe fulfillment.
    """
    if (type(account) is not Account or type(loan) is not Loan
            or not callable(scope_current)
            or loan.source_provider != "paris" or loan.provider != "DILICOM"
            or loan.medium != "digital"
            or account.provider_id != "paris"
            or account.credential_id != loan.source_credential
            or account.identity_id != loan.identity_id
            or account.unique_id != ""
            or loan.source_account_id != "primary"
            or type(loan.ebook_url) is not str
            or not loan.ebook_url):
        return False
    # No network activity is permitted without an exact signed ACSM URL from
    # the first borrow attempt AND its confirmed durable Core checkpoint.
    # A loan model returned by a provider is never sufficient authority.
    def confirmed_claim_current():
        try:
            receipt = service.storage.loan_borrow_state(claim_key)
            return (type(receipt) is dict
                    and receipt.get("status") == "confirmed"
                    and receipt.get("request_accepted") is True
                    and receipt.get("verification") == "positive_active_loan"
                    and receipt.get("operation_id") == operation_id
                    and receipt.get("verified_loan_id") == loan.id)
        except Exception:
            return False

    try:
        if (type(claim_key) is not str or type(operation_id) is not str
                or not confirmed_claim_current()):
            return False
        parts = urlsplit(loan.ebook_url)
        if (parts.scheme != "https" or not parts.hostname
                or not parts.path.casefold().endswith(".acsm")
                or parts.username is not None or parts.password is not None
                or scope_current() is not True):
            return False
        directory = service._acquisition_work_dir(
            account.identity_id, "paris", loan.id)
        if scope_current() is not True:
            return False
        candidate, _url = session.download_digital(
            loan, account, directory, admission_current=scope_current,
            initial_offer_only=True)
        if (scope_current() is not True or not isinstance(candidate, Path)
                or directory.is_symlink() or candidate.is_symlink()
                or candidate.parent != directory
                or candidate.suffix.casefold() != ".acsm"
                or not candidate.is_file()):
            return False
        inspection = inspect_artifact(candidate, expected_format="acsm")
        if inspection.valid is not True or inspection.media_format != "acsm":
            return False
        if scope_current() is not True or not confirmed_claim_current():
            return False
        return acsm_cache.remember_initial(
            candidate, directory, loan, account.identity_id, "primary",
            borrow_claim=(claim_key, operation_id))
    except Exception:
        # The lender may have successfully created the loan already. Never
        # let a token retrieval failure trigger a retry of the mutating GET.
        return False


__all__ = ["stage_paris_initial_acsm"]
