"""Pinned, source-neutral SQLite connection generation for one digital loan.

A lease is a *necessary veto*, never loan, entitlement, ADEPT borrower/profile,
provider permission or a network authorization. The durable epoch is generated
by Storage's SQLite triggers, not by the current UI identity or a plugin.
"""
from __future__ import annotations

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
        # Snapshot every native transaction coordinate BEFORE the storage
        # callback: it can be a plugin shim and may mutate the caller's Loan.
        # Never combine epoch(A) with source(B), even if both happen to have
        # the same numeric SQLite epoch and identity owner.
        if type(identity_id) is not str or not identity_id:
            return None
        try:
            provider = loan.source_provider
            credential = loan.source_credential
            account = loan.source_account_id
            loan_id = loan.id
            record = loan.record_id
        except Exception:
            return None
        if (type(provider) is not str or not provider
                or type(credential) is not str or not credential
                or type(account) not in (str, type(None))
                or type(loan_id) is not str or not loan_id
                or type(record) not in (str, type(None))):
            return None
        try:
            epoch = storage.provider_connection_epoch(
                provider, credential, identity_id,
            )
            if type(epoch) is not int or epoch < 1:
                return None
            # Never rebind the lease to post-callback coordinates. Even a
            # mutation to a foreign source with the SAME epoch fails closed.
            if (type(loan.source_provider) is not str
                    or loan.source_provider != provider
                    or type(loan.source_credential) is not str
                    or loan.source_credential != credential
                    or type(loan.source_account_id) not in (str, type(None))
                    or loan.source_account_id != account
                    or type(loan.id) is not str or loan.id != loan_id
                    or type(loan.record_id) not in (str, type(None))
                    or loan.record_id != record):
                return None
        except Exception:
            return None
        return cls(
            provider_id=provider,
            credential_id=credential,
            account_id=account,
            loan_id=loan_id,
            record_id=record,
            identity_id=identity_id,
            epoch=epoch,
        )

    def current(self, storage: Any, loan: Any) -> bool:
        """Reject source drift and every A->B->A/disable/recreate since capture.

        A Loan may be a plugin-supplied object with getters that mutate or
        raise. Read each native coordinate exactly once, then validate the
        pinned tuple before consulting SQLite. A failing getter is a veto.
        No borrower grant or provider callback runs here.
        """
        if (type(self) is not SourceEpochLease
                or type(self.provider_id) is not str or not self.provider_id
                or type(self.credential_id) is not str or not self.credential_id
                or type(self.account_id) not in (str, type(None))
                or type(self.loan_id) is not str or not self.loan_id
                or type(self.record_id) not in (str, type(None))
                or type(self.identity_id) is not str or not self.identity_id
                or type(self.epoch) is not int or self.epoch < 1):
            return False
        try:
            provider = loan.source_provider
            credential = loan.source_credential
            account = loan.source_account_id
            loan_id = loan.id
            record = loan.record_id
            if (type(provider) is not str or provider != self.provider_id
                    or type(credential) is not str
                    or credential != self.credential_id
                    or type(account) not in (str, type(None))
                    or account != self.account_id
                    or type(loan_id) is not str or loan_id != self.loan_id
                    or type(record) not in (str, type(None))
                    or record != self.record_id):
                return False
            # Recheck the loan after the SQLite callback: an adapter may
            # mutate the source while returning an otherwise valid epoch.
            if storage.provider_connection_epoch_current(
                self.provider_id, self.credential_id, self.identity_id,
                self.epoch,
            ) is not True:
                return False
            return (type(loan.source_provider) is str
                    and loan.source_provider == provider
                    and type(loan.source_credential) is str
                    and loan.source_credential == credential
                    and type(loan.source_account_id) in (str, type(None))
                    and loan.source_account_id == account
                    and type(loan.id) is str and loan.id == loan_id
                    and type(loan.record_id) in (str, type(None))
                    and loan.record_id == record)
        except Exception:
            return False
