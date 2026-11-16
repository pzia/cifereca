"""Pinned, source-neutral SQLite connection generation for one digital loan.

A lease is a *necessary veto*, never loan, entitlement, ADEPT borrower/profile,
provider permission or a network authorization. The durable epoch is generated
by Storage's SQLite triggers, not by the current UI identity or a plugin.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True, repr=False)
class SourceEpochLease:
    provider_id: str
    credential_id: str
    account_id: str | None
    loan_id: str
    record_id: str | None
    identity_id: str
    epoch: int

    @classmethod
    def capture(cls, storage: Any, loan: Any, identity_id: str) -> SourceEpochLease | None:
        """Pin the connection epoch before calling any identity/borrower hook."""
        # Legacy public reacquisition accepts loan-like sources that omit
        # optional account/edition. Read each plugin-owned field once and
        # never coerce a foreign object to str or reuse an unstable getter.
        try:
            provider = getattr(loan, "source_provider", None)
            credential = getattr(loan, "source_credential", None)
            account = getattr(loan, "source_account_id", None)
            loan_id = getattr(loan, "id", None)
            record = getattr(loan, "record_id", None)
        except Exception:
            return None
        if (type(identity_id) is not str or not identity_id
                or type(provider) is not str or not provider
                or type(credential) is not str or not credential
                or type(account) not in (str, type(None))
                or type(loan_id) is not str or not loan_id
                or type(record) not in (str, type(None))):
            return None
        try:
            epoch = storage.provider_connection_epoch(
                provider, credential, identity_id,
            )
        except Exception:
            return None
        if type(epoch) is not int or epoch < 1:
            return None
        return cls(
            provider_id=provider, credential_id=credential, account_id=account,
            loan_id=loan_id, record_id=record, identity_id=identity_id,
            epoch=epoch,
        )

    def current(self, storage: Any, loan: Any) -> bool:
        """Reject source drift and every A->B->A/disable/recreate since capture.

        No borrower grant or provider callback runs. Native type checks are
        performed before any equality comparison, hash or SQLite access.
        """
        try:
            provider = getattr(loan, "source_provider", None)
            credential = getattr(loan, "source_credential", None)
            account = getattr(loan, "source_account_id", None)
            loan_id = getattr(loan, "id", None)
            record = getattr(loan, "record_id", None)
        except Exception:
            return False
        if (type(self) is not SourceEpochLease
                or type(self.provider_id) is not str or not self.provider_id
                or type(self.credential_id) is not str or not self.credential_id
                or type(self.account_id) not in (str, type(None))
                or type(self.loan_id) is not str or not self.loan_id
                or type(self.record_id) not in (str, type(None))
                or type(self.identity_id) is not str or not self.identity_id
                or type(self.epoch) is not int or self.epoch < 1
                or type(provider) is not str
                or type(credential) is not str
                or type(account) not in (str, type(None))
                or type(loan_id) is not str
                or type(record) not in (str, type(None))):
            return False
        if (provider != self.provider_id
                or credential != self.credential_id
                or account != self.account_id
                or loan_id != self.loan_id
                or record != self.record_id):
            return False
        try:
            return storage.provider_connection_epoch_current(
                self.provider_id, self.credential_id, self.identity_id,
                self.epoch,
            ) is True
        except Exception:
            return False


# Keep LibraryService's public acquisition method signatures unchanged.
# The explicit recovery and nested automatic loan acquire are synchronous in
# one logical Python execution context. A ContextVar is task/thread-isolated,
# reset in finally, and cannot act as a permission by itself.
_INHERITED_EXPLICIT_LEASE: ContextVar[SourceEpochLease | None] = ContextVar(
    "cifereca_inherited_explicit_source_epoch", default=None,
)


def inherited_explicit_source_epoch() -> SourceEpochLease | None:
    """Read the currently pinned recovery lease, not GUI/current identity."""
    return _INHERITED_EXPLICIT_LEASE.get()


@contextmanager
def pin_explicit_source_epoch(lease: SourceEpochLease):
    """Transmit one immutable recovery epoch without changing facade ABI.

    Refuse a nested call trying to replace a different transaction's epoch,
    and unconditionally restore the prior context even if provider/ADEPT fails.
    The caller must still check lease.current() at every sensitive boundary.
    """
    if type(lease) is not SourceEpochLease:
        raise ValueError("invalid_explicit_source_epoch")
    existing = _INHERITED_EXPLICIT_LEASE.get()
    if existing is not None and existing is not lease:
        raise ValueError("explicit_source_epoch_conflict")
    token = _INHERITED_EXPLICIT_LEASE.set(lease)
    try:
        yield
    finally:
        _INHERITED_EXPLICIT_LEASE.reset(token)
