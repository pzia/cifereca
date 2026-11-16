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
        if (type(identity_id) is not str or not identity_id
                or type(loan.source_provider) is not str or not loan.source_provider
                or type(loan.source_credential) is not str or not loan.source_credential
                or type(loan.source_account_id) not in (str, type(None))
                or type(loan.id) is not str or not loan.id
                or type(loan.record_id) not in (str, type(None))):
            return None
        try:
            epoch = storage.provider_connection_epoch(
                loan.source_provider, loan.source_credential, identity_id,
            )
        except Exception:
            return None
        if type(epoch) is not int or epoch < 1:
            return None
        return cls(
            provider_id=loan.source_provider,
            credential_id=loan.source_credential,
            account_id=loan.source_account_id,
            loan_id=loan.id,
            record_id=loan.record_id,
            identity_id=identity_id,
            epoch=epoch,
        )

    def current(self, storage: Any, loan: Any) -> bool:
        """Reject source drift and every A->B->A/disable/recreate since capture.

        No borrower grant or provider callback runs. Native type checks are
        performed before any equality comparison, hash or SQLite access.
        """
        if (type(self) is not SourceEpochLease
                or type(self.provider_id) is not str or not self.provider_id
                or type(self.credential_id) is not str or not self.credential_id
                or type(self.account_id) not in (str, type(None))
                or type(self.loan_id) is not str or not self.loan_id
                or type(self.record_id) not in (str, type(None))
                or type(self.identity_id) is not str or not self.identity_id
                or type(self.epoch) is not int or self.epoch < 1
                or type(loan.source_provider) is not str
                or type(loan.source_credential) is not str
                or type(loan.source_account_id) not in (str, type(None))
                or type(loan.id) is not str
                or type(loan.record_id) not in (str, type(None))):
            return False
        if (loan.source_provider != self.provider_id
                or loan.source_credential != self.credential_id
                or loan.source_account_id != self.account_id
                or loan.id != self.loan_id
                or loan.record_id != self.record_id):
            return False
        try:
            return storage.provider_connection_epoch_current(
                self.provider_id, self.credential_id, self.identity_id,
                self.epoch,
            ) is True
        except Exception:
            return False
