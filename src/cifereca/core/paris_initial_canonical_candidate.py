"""#3121 — consume a confirmed Paris first-offer ACSM in the canonical executor.

This module never borrows, downloads, fulfills, or publishes an Artifact.
Only a claim-bound sidecar in Core's private loan staging may supply bytes.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from .. import acsm_cache
from ..models import Account, Loan


def stage_first_paris_acsm(service, session, account: Account, source, *,
                           attempt: Path, admission_current) -> Path | None:
    """Copy one attested initial ACSM into the executor's fresh attempt.

    None means there is no first-offer receipt for this exact Paris loan.
    An existing but unusable receipt fails closed: a second content GET must
    not silently replace an uncertain first borrow token.
    """
    scope = source.scope
    if (scope.provider_id != "paris" or source.kind != "loan"
            or source.branch != "dilicom"):
        return None
    # Only the implicit primary Paris account can have a first-offer
    # receipt from the confirmed manual borrow action. Other borrowers
    # legitimately use ordinary canonical selected-right acquisition;
    # absence of this special receipt must never block their delivery.
    if type(account) is Account and account.unique_id != "":
        if (account.identity_id, account.provider_id,
                account.credential_id, account.unique_id) != (
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id):
            raise ValueError("canonical_paris_initial_scope_invalid")
        return None
    if (type(account) is not Account
            or (account.identity_id, account.provider_id,
                account.credential_id, account.unique_id) != (
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id)
            or not callable(admission_current)):
        raise ValueError("canonical_paris_initial_scope_invalid")
    if admission_current() is not True:
        raise ValueError("canonical_paris_initial_admission_revoked")

    # The service constructs these 0700 dirs with openat(O_NOFOLLOW); never
    # accept a directory provided by a plugin or derive a path from a title.
    directory = service._acquisition_work_dir(
        scope.identity_id, "paris", source.right_id)
    sidecar = directory / acsm_cache.INITIAL_MANIFEST
    try:
        sidecar_stat = os.stat(sidecar, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(sidecar_stat.st_mode)
            or sidecar_stat.st_uid != os.geteuid()
            or not 0 < sidecar_stat.st_size <= 16384):
        raise ValueError("canonical_paris_initial_receipt_invalid")

    # A fresh exact borrower inventory is descriptive and read-only; never
    # attempt another borrow, token GET, or provider-selected candidate GET.
    loans = session.loans(account)
    if type(loans) not in (list, tuple) or len(loans) > 10000:
        raise ValueError("canonical_paris_initial_inventory_unavailable")
    candidates = [
        loan for loan in loans
        if type(loan) is Loan and loan.id == source.right_id
        and loan.record_id == source.edition_id
        and loan.provider == "DILICOM" and loan.medium == "digital"
        and loan.source_provider == "paris"
        and loan.source_credential == scope.credential_id
        and loan.identity_id == scope.identity_id
        and loan.source_account_id == "primary"
    ]
    if len(candidates) != 1 or admission_current() is not True:
        raise ValueError("canonical_paris_initial_loan_unverified")
    loan = candidates[0]
    claim_state = service.storage.loan_borrow_state
    candidate = acsm_cache.reuse_initial(
        directory, loan, scope.identity_id, "primary",
        claim_state=claim_state)
    if candidate is None:
        raise ValueError("canonical_paris_initial_receipt_unverified")

    parent_fd = file_fd = attempt_fd = target_fd = None
    try:
        parent_fd = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent = os.fstat(parent_fd)
        if (not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.geteuid()):
            raise ValueError("canonical_paris_initial_parent_untrusted")
        file_fd = os.open(
            candidate.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        before = os.fstat(file_fd)
        if (not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or not 0 < before.st_size <= acsm_cache.MAX_BYTES):
            raise ValueError("canonical_paris_initial_bytes_untrusted")
        with os.fdopen(file_fd, "rb", closefd=False) as source_stream:
            payload = source_stream.read(acsm_cache.MAX_BYTES + 1)
        after = os.fstat(file_fd)
        if (len(payload) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns)):
            raise ValueError("canonical_paris_initial_bytes_changed")
        digest = hashlib.sha256(payload).hexdigest()
        # The cache's independent XML, expiry, scope and claim checks run
        # again after our own pinned read, not only before opening the file.
        if (acsm_cache._initial_digest(candidate, directory, __import__("time").time())
                != digest
                or acsm_cache.reuse_initial(
                    directory, loan, scope.identity_id, "primary",
                    claim_state=claim_state) != candidate
                or admission_current() is not True):
            raise ValueError("canonical_paris_initial_receipt_changed")
        attempt_fd = os.open(
            attempt, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        bound = os.fstat(attempt_fd)
        current = os.stat(attempt, follow_symlinks=False)
        if (bound.st_uid != os.geteuid()
                or (bound.st_dev, bound.st_ino)
                != (current.st_dev, current.st_ino)
                or not stat.S_ISDIR(current.st_mode)):
            raise ValueError("canonical_paris_initial_target_untrusted")
        target_fd = os.open(
            "first-offer.acsm",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=attempt_fd,
        )
        with os.fdopen(target_fd, "wb", closefd=False) as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        if (os.fstat(target_fd).st_size != len(payload)
                or admission_current() is not True
                or acsm_cache.reuse_initial(
                    directory, loan, scope.identity_id, "primary",
                    claim_state=claim_state) != candidate
                or os.fstat(file_fd).st_ino != before.st_ino):
            raise ValueError("canonical_paris_initial_receipt_changed")
        return attempt / "first-offer.acsm"
    except OSError:
        raise ValueError("canonical_paris_initial_copy_unavailable") from None
    finally:
        for fd in (target_fd, attempt_fd, file_fd, parent_fd):
            if fd is not None:
                os.close(fd)


__all__ = ["stage_first_paris_acsm"]
