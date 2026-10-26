"""Core-owned snapshot guard for actions affecting one exact existing loan.

A provider's consent dialog, public capability, Loan URL or token is never a
Core authorization. This lease is a *necessary* scope check, not a permission
grant. It performs no network, storage mutation or ADEPT operation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from inspect import getattr_static
from typing import Any

from ..models import Account, Loan
from ..providers.base import ProviderSession


def _inert_session_accounts(session: object) -> list[Account] | tuple[Account, ...] | None:
    """Read base ProviderSession's initialized accounts without plugin code.

    An override of the public property, a dynamic session registry or an
    oversized list cannot authorize a remote loan action. No getter is run.
    """
    if not isinstance(session, ProviderSession):
        return None
    if getattr_static(type(session), "accounts", None) is not ProviderSession.__dict__["accounts"]:
        return None
    values = object.__getattribute__(session, "__dict__").get("_accounts")
    return values if type(values) in (list, tuple) and len(values) <= 512 else None


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
            loan_fields = _exact_loan_fields(loan)
            # Provider models remain mutable and may be malformed. Check
            # primitive field types before equality/hash on untrusted objects.
            if (any(type(value) is not str for value in exact)
                    or any(type(value) is not str for value in loan_fields)
                    or any(len(value) > 1024 or chr(0) in value
                           for value in (*exact, *loan_fields))
                    or exact != self._account_fields
                    or loan_fields != self._loan_fields):
                return False
            identity_id, provider, credential, borrower = exact
            if (not identity_id or not provider or not credential
                    or identity_id != self._expected_identity_id
                    or not loan.id or not loan.record_id):
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
            session_accounts = _inert_session_accounts(session)
            if (session_accounts is None
                    or sum(row is account for row in service.accounts) != 1
                    or sum(row is account for row in session_accounts) != 1
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
