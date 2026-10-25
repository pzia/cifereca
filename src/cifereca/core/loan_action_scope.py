"""Core-owned snapshot guard for actions affecting one exact existing loan.

A provider's consent dialog, public capability, Loan URL or token is never a
Core authorization. This lease is a *necessary* scope check, not a permission
grant. It performs no network, storage mutation or ADEPT operation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import Account, Loan


def _exact_loan_fields(loan: Loan) -> tuple[object, ...]:
    """Do not retain raw loan metadata, URLs, cookies or UI labels."""
    return (
        loan.identity_id, loan.source_provider, loan.source_credential,
        loan.source_account_id, loan.id, loan.record_id, loan.medium, loan.state,
    )


@dataclass(frozen=True, slots=True, repr=False)
class LoanActionScopeLease:
    """Capture a Core action's account/session identity and generation.

    The account, session and Loan object references are checked by identity.
    Their original scalar scope is independently checked on each call; a
    mutable Python object cannot retain authority by remaining the same object.
    """
    _account: Account = field(repr=False)
    _session: Any = field(repr=False)
    _loan: Loan = field(repr=False)
    _expected_identity_id: str
    _account_fields: tuple[str, str, str, str]
    _loan_fields: tuple[object, ...]
    _context_epoch: int
    _provider_epoch: object = field(default=None, repr=False)

    def current(self, service: object) -> bool:
        """Check the original owner and epoch, even through an Alice→Bob→Alice.

        No provider method is called, no GET, write or login is triggered.
        A changed registry/session/borrower returns False without exposing
        private values in an exception.
        """
        try:
            account, session, loan = self._account, self._session, self._loan
            if (type(account) is not Account or type(loan) is not Loan
                    or type(self._context_epoch) is not int
                    or type(getattr(service, "_account_context_epoch", None)) is not int
                    or service._account_context_epoch != self._context_epoch
                    or getattr(session, "_epoch", None) != self._provider_epoch
                    or getattr(session, "_closed", False) is True):
                return False
            exact = (
                account.identity_id, account.provider_id,
                account.credential_id, account.unique_id,
            )
            if exact != self._account_fields or _exact_loan_fields(loan) != self._loan_fields:
                return False
            identity_id, provider, credential, borrower = exact
            if (type(identity_id) is not str or not identity_id
                    or identity_id != self._expected_identity_id
                    or type(provider) is not str or not provider
                    or type(credential) is not str or not credential
                    or type(borrower) is not str):
                return False
            allowed_borrowers = {borrower}
            if provider == "paris" and account.is_primary is True and borrower == "":
                allowed_borrowers.add("primary")
            if (loan.identity_id != identity_id
                    or loan.source_provider != provider
                    or loan.source_credential != credential
                    or loan.source_account_id not in allowed_borrowers
                    or type(loan.id) is not str or not loan.id
                    or getattr(session, "connected", None) is not True
                    or getattr(session, "provider_id", None) != provider
                    or getattr(session, "credential_id", None) != credential):
                return False
            identity = getattr(service, "current_identity", None)
            if (getattr(identity, "identity_id", None) != identity_id
                    or service.identity_by_id(identity_id) is None
                    or service.storage.identity_for_connection(
                        provider, credential,
                    ) != identity_id
                    or service._sessions.get(
                        service._session_key(provider, credential),
                    ) is not session):
                return False
            # Object identity matters: two reader accounts with the same
            # display name or an ABA reconnect are not interchangeable.
            if (sum(row is account for row in service.accounts) != 1
                    or sum(row is account for row in session.accounts) != 1
                    or service._account_for_loan(loan) is not account
                    or service._session_for_source(provider, credential) is not session):
                return False
            return True
        except Exception:
            return False


def capture_loan_action_scope(
    service: object, loan: Loan, account: Account, session: object, *,
    expected_identity_id: str,
) -> LoanActionScopeLease | None:
    """Create an exact action lease only for the still-live selected borrower.

    The caller independently owns capability, permission, explicit consent,
    at-most-once claim and action-specific state checks. A lease cannot by
    itself authorize a provider call or an ADEPT fulfillment.
    """
    if (type(loan) is not Loan or type(account) is not Account
            or type(expected_identity_id) is not str or not expected_identity_id):
        return None
    epoch = getattr(service, "_account_context_epoch", None)
    if type(epoch) is not int:
        return None
    lease = LoanActionScopeLease(
        _account=account, _session=session, _loan=loan,
        _expected_identity_id=expected_identity_id,
        _account_fields=(
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        ),
        _loan_fields=_exact_loan_fields(loan),
        _context_epoch=epoch, _provider_epoch=getattr(session, "_epoch", None),
    )
    return lease if lease.current(service) else None


__all__ = ["LoanActionScopeLease", "capture_loan_action_scope"]
