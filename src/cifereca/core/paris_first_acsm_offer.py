"""#3121 — qualify a *confirmed* first Paris ACSM when ListLoans lost EbookUrl.

This is a descriptive Core-only offer: no borrow POST, content GET, ADEPT,
Artifact or right is created. The selected executor re-verifies the receipt.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from .. import acsm_cache
from ..models import Account, Loan
from ..plugin_api.provider_rights_observation import ProviderRightObservation
from .source_convergence import SourceKey


def qualify_claimed_paris_offer(
    service, account: Account, session, source: SourceKey,
    right: ProviderRightObservation, *, current,
) -> ProviderRightObservation | None:
    """Return a Core-only offer after an exact loan and initial receipt.

    A provider snapshot saying 'unknown delivery' must not become 'available'
    based on a cache file alone. The source is already provider-attested, the
    user has opted in, and the original loan.borrow claim is confirmed.
    """
    scope = source.scope
    if (type(account) is not Account or type(source) is not SourceKey
            or type(right) is not ProviderRightObservation
            or not right.provider_attested or right.delivery != "unknown"
            or scope.provider_id != "paris" or source.kind != "loan"
            or source.branch != "dilicom" or account.unique_id != ""
            or (right.edition_id, right.right_id, right.kind) != (
                source.edition_id, source.right_id, source.kind)
            or (account.identity_id, account.provider_id,
                account.credential_id, account.unique_id) != (
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id)
            or not callable(current) or current() is not True):
        return None

    # No cache = no additional provider read. This branch is exclusively
    # for the one-shot manual-first token; ordinary selected delivery keeps
    # its pre-existing offer semantics.
    try:
        directory = service._acquisition_work_dir(
            scope.identity_id, "paris", source.right_id,
        )
        if not isinstance(directory, Path):
            return None
        info = os.stat(
            directory / acsm_cache.INITIAL_MANIFEST,
            follow_symlinks=False,
        )
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or not 0 < info.st_size <= 16384 or current() is not True):
            return None
    except (OSError, AttributeError, TypeError, ValueError):
        return None

    # Read-only real lender inventory: the right must STILL be exactly one
    # loan for the same credential/borrower/edition, not merely a local file.
    try:
        loans = session.loans(account)
        if type(loans) not in (list, tuple) or len(loans) > 10000:
            return None
        matches = [
            loan for loan in loans
            if type(loan) is Loan and loan.id == source.right_id
            and loan.record_id == source.edition_id
            and loan.provider == "DILICOM" and loan.medium == "digital"
            and loan.source_provider == "paris"
            and loan.source_credential == scope.credential_id
            and loan.source_account_id == "primary"
            and loan.identity_id == scope.identity_id
        ]
        if len(matches) != 1 or current() is not True:
            return None
        candidate = acsm_cache.reuse_initial(
            directory, matches[0], scope.identity_id, "primary",
            claim_state=service.storage.loan_borrow_state,
        )
        if candidate is None or current() is not True:
            return None
    except Exception:
        # The exception can contain signed borrower data. Fail closed, never
        # log it and never ask the plugin to redo the content GET here.
        return None

    # This opaque value identifies only the checked Core offer admission;
    # the actual bytes, claim and local inode are checked again by the
    # canonical executor before ADEPT. No private path or signed URL stored.
    encoded = json.dumps(
        [scope.identity_id, scope.provider_id, scope.credential_id,
         scope.account_id, source.branch, source.edition_id, source.right_id,
         source.kind],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    offer_id = "core.paris.firstacsm:" + sha256(encoded).hexdigest()
    return replace(right, delivery="available", delivery_offer_id=offer_id)


__all__ = ["qualify_claimed_paris_offer"]
