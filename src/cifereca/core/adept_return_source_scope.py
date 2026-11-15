"""Provider-neutral, read-only provenance for historical ADEPT loan status.

A completed acquisition is not a right to return or download a loan. Never
coerce provider-owned account/loan coordinates; only exact native strings
from the original Loan may select a historical ledger row.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Loan


@dataclass(frozen=True, slots=True, repr=False)
class HistoricalLoanSource:
    provider_id: str
    credential_id: str
    account_id: str | None
    loan_id: str
    record_id: str | None

    @classmethod
    def capture(cls, loan: object) -> HistoricalLoanSource | None:
        # No arbitrary loan-like proxy, property, __str__, __hash__ or
        # subclassed string can drive the storage reader.
        if type(loan) is not Loan:
            return None
        provider, credential, account, loan_id, record = (
            loan.source_provider, loan.source_credential,
            loan.source_account_id, loan.id, loan.record_id,
        )
        if (type(provider) is not str or not provider
                or type(credential) is not str or not credential
                or type(account) not in (str, type(None))
                or type(loan_id) is not str or not loan_id
                or type(record) not in (str, type(None))):
            return None
        return cls(provider, credential, account, loan_id, record)

    @property
    def item_id(self) -> str:
        return self.record_id or self.loan_id

    @property
    def borrower_proven(self) -> bool:
        # Empty is the exact historical primary account; None is unknown.
        return self.account_id is not None

    def current(self, loan: object) -> bool:
        """Fail closed on any in-place loan edit after external callbacks."""
        if type(loan) is not Loan:
            return False
        return bool(
            type(loan.source_provider) is str
            and loan.source_provider == self.provider_id
            and type(loan.source_credential) is str
            and loan.source_credential == self.credential_id
            and type(loan.source_account_id) in (str, type(None))
            and loan.source_account_id == self.account_id
            and type(loan.id) is str and loan.id == self.loan_id
            and type(loan.record_id) in (str, type(None))
            and loan.record_id == self.record_id
        )

    def _row_source(self, row: object, *, identity_id: str) -> bool:
        return bool(
            self.borrower_proven
            and type(identity_id) is str and identity_id
            and type(row) is dict
            and type(row.get("identity_id")) is str
            and row["identity_id"] == identity_id
            and type(row.get("provider_id")) is str
            and row["provider_id"] == self.provider_id
            and type(row.get("credential_id")) is str
            and row["credential_id"] == self.credential_id
            and type(row.get("account_id")) is str
            and row["account_id"] == self.account_id
            and type(row.get("item_id")) is str
            and row["item_id"] == self.item_id
        )

    def matches_entitlement(self, row: object, *, identity_id: str) -> bool:
        return bool(
            self._row_source(row, identity_id=identity_id)
            and type(row.get("entitlement_id")) is str
            and row["entitlement_id"]
            and type(row.get("loan_id")) is str
            and row["loan_id"] == self.loan_id
        )

    def matches_acquisition(self, row: object, *, identity_id: str) -> bool:
        return self._row_source(row, identity_id=identity_id)

    def matches_loan_receipt(self, recorded_loan_id: object) -> bool:
        """A historical None needs an exact entitlement; a typed ID matches."""
        return type(recorded_loan_id) is str and recorded_loan_id == self.loan_id
