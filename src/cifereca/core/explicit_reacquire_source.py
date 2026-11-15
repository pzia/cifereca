"""Immutable, provider-neutral loan scope for one explicit reacquisition.

This is a veto/selection boundary, never permission to download, acquire an
ADEPT license or infer an identity. The existing caller keeps provider grants,
locks and all durable writes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, repr=False)
class ExplicitReacquireSource:
    provider_id: str
    credential_id: str
    account_id: str | None
    loan_id: str
    record_id: str | None
    medium: str | None

    @classmethod
    def capture(cls, loan: object) -> ExplicitReacquireSource | None:
        # Legacy facade callers may supply loan-like objects, but their
        # coordinates must be exact built-in strings, never plugin proxies.
        try:
            provider = getattr(loan, "source_provider", None)
            credential = getattr(loan, "source_credential", None)
            account = getattr(loan, "source_account_id", None)
            loan_id = getattr(loan, "id", None)
            record = getattr(loan, "record_id", None)
            medium = getattr(loan, "medium", None)
        except Exception:
            return None
        if (type(provider) is not str or not provider
                or type(credential) is not str or not credential
                or type(account) not in (str, type(None))
                or type(loan_id) is not str or not loan_id
                or type(record) not in (str, type(None))
                or type(medium) not in (str, type(None))):
            return None
        return cls(provider, credential, account, loan_id, record, medium)

    def current(self, loan: object) -> bool:
        # Re-capture reads each source field exactly once. A mutated native
        # coordinate or any hostile object invalidates the entire lease.
        return type(self) is ExplicitReacquireSource and self.capture(loan) == self

    def matches_previous(self, row: object, *, identity_id: str) -> bool:
        """Select only prior complete rows of this exact borrower and edition.

        The legacy source-account None/empty sentinel remains readable, but
        malformed rows cannot invoke __str__, __eq__ or other plugin methods.
        This selection never authorizes an entitlement or ADEPT action.
        """
        if type(row) is not dict or type(identity_id) is not str or not identity_id:
            return False
        provider = row.get("provider_id")
        credential = row.get("credential_id")
        account = row.get("account_id")
        item = row.get("item_id")
        owner = row.get("identity_id")
        status = row.get("status")
        if (type(provider) is not str or type(credential) is not str
                or type(account) not in (str, type(None))
                or type(item) is not str or type(owner) is not str
                or type(status) is not str):
            return False
        borrower = self.account_id if self.account_id is not None else ""
        return (
            owner == identity_id and provider == self.provider_id
            and credential == self.credential_id
            and (account if account is not None else "") == borrower
            and item == (self.record_id or self.loan_id)
            and status == "complete"
        )
