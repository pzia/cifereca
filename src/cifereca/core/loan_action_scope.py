"""Core-owned snapshot guard for actions affecting one exact existing loan.

A provider's consent dialog, public capability, Loan URL or token is never a
Core authorization. This lease is a *necessary* scope check, not a permission
grant. It performs no network, storage mutation or ADEPT operation.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import Lock
from inspect import getattr_static
from typing import Any

from ..models import Account, Loan
from ..providers.base import ProviderDescriptor, ProviderSession
from ..plugin_api.loan_scope import loan_matches_account
from .source_context import source_context_epoch


def _inert_session_state(session: object) -> dict[str, Any] | None:
    """Only inspect initialized memory, never provider getters or proxies.

    A descriptor shadowing a ProviderSession scalar makes the account/session
    boundary unprovable: the plugin's own action might see another value.
    """
    # isinstance() on an ABC may consult session.__class__, which invokes
    # an overridden __getattribute__ and can perform provider I/O. Inspect
    # the real class first, without touching the session object.
    klass = type(session)
    if getattr_static(klass, "__getattribute__", None) is not object.__getattribute__:
        return None
    if not issubclass(klass, ProviderSession):
        return None
    sentinel = object()
    for key in ("connected", "provider_id", "credential_id", "_epoch", "_closed"):
        shadow = getattr_static(klass, key, sentinel)
        if shadow is not sentinel and getattr_static(type(shadow), "__get__", None) is not None:
            return None
    try:
        data = object.__getattribute__(session, "__dict__")
    except (AttributeError, TypeError):
        return None
    return data if type(data) is dict else None


def _inert_session_accounts(session: object) -> list[Account] | tuple[Account, ...] | None:
    """Read base ProviderSession's initialized accounts without plugin code.

    An override of the public property, a dynamic session registry or an
    oversized list cannot authorize a remote loan action. No getter is run.
    """
    state = _inert_session_state(session)
    if state is None:
        return None
    if getattr_static(type(session), "accounts", None) is not ProviderSession.__dict__["accounts"]:
        return None
    values = state.get("_accounts")
    return values if type(values) in (list, tuple) and len(values) <= 512 else None


def _exact_loan_fields(loan: Loan) -> tuple[object, ...]:
    """Do not retain raw loan metadata, URLs, cookies or UI labels."""
    return (
        loan.identity_id, loan.source_provider, loan.source_credential,
        loan.source_account_id, loan.id, loan.record_id, loan.medium, loan.state,
    )


class _ConfirmedStageAttempt:
    """One confirmed candidate request, even if the attempt fails."""

    def __init__(self):
        self._lock = Lock()
        self._used = False

    def claim(self):
        with self._lock:
            if self._used:
                return False
            self._used = True
            return True


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
    _descriptor: ProviderDescriptor | None = field(default=None, repr=False)
    _admitted_service: object | None = field(default=None, repr=False)
    _source_epoch: int | None = field(default=None, repr=False)
    _reader_scope: object | None = field(default=None, repr=False)
    _stage_attempt: _ConfirmedStageAttempt | None = field(default=None, repr=False, compare=False)

    def admitted_for(self, service: object, loan: Loan, identity_id: str) -> bool:
        """Only the exact loan and Core that admitted explicit consent."""
        return (self._admitted_service is service
                and self._source_epoch is not None
                and self._loan is loan
                and type(identity_id) is str
                and identity_id == self._expected_identity_id
                and self.current(service))

    def current(self, service: object) -> bool:
        """Check the original owner and epoch, even through an Alice→Bob→Alice.

        No provider method is called, no GET, write or login is triggered.
        A changed registry/session/borrower returns False without exposing
        private values in an exception.
        """
        try:
            account, session, loan = self._account, self._session, self._loan
            session_state = _inert_session_state(session)
            if (session_state is None
                    or type(account) is not Account or type(loan) is not Loan
                    or type(self._context_epoch) is not int
                    or session_state.get("_epoch") != self._provider_epoch
                    or session_state.get("_closed") is True):
                return False
            if self._source_epoch is None:
                if (type(getattr(service, "_account_context_epoch", None)) is not int
                        or service._account_context_epoch != self._context_epoch):
                    return False
            elif (self._admitted_service is not service
                  or source_context_epoch(service) != self._source_epoch):
                return False
            if self._reader_scope is not None:
                from .search_orchestration import SearchReadScope
                if (type(self._reader_scope) is not SearchReadScope
                        or self._reader_scope.identity_id != self._expected_identity_id
                        or self._reader_scope.source_epoch != self._source_epoch
                        or not self._reader_scope.current(service)):
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
            # The installed session's original typed policy is pinned with
            # the lease. No supplier-name branch or public-loan metadata can
            # add an alternate borrower to this set.
            descriptor = session_state.get("descriptor")
            if (type(descriptor) is not ProviderDescriptor
                    or descriptor is not self._descriptor
                    or descriptor.id != provider):
                return False
            if (not loan_matches_account(loan, account, descriptor)
                    or type(loan.id) is not str or not loan.id
                    or session_state.get("connected") is not True
                    or session_state.get("provider_id") != provider
                    or session_state.get("credential_id") != credential):
                return False
            if self._source_epoch is None:
                identity = getattr(service, "current_identity", None)
                if getattr(identity, "identity_id", None) != identity_id:
                    return False
            if (service.identity_by_id(identity_id) is None
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
                    or service._account_for_loan(loan) is not account):
                return False
            if self._source_epoch is None:
                return service._session_for_source(provider, credential) is session
            # The legacy source resolver asks for provider visibility using
            # the currently displayed profile. An admitted worker may only
            # consult policy for its own owner, including the persisted
            # connection switch, and the explicit account's session.
            return (
                service.storage.provider_connection_enabled(provider, credential) is True
                and service._provider_enabled_for_identity(provider, identity_id)
                and service._session_for_account(account) is session
                and source_context_epoch(service) == self._source_epoch
                and (self._reader_scope is None or self._reader_scope.current(service))
            )
        except Exception:
            return False


def capture_loan_action_scope(
    service: object, loan: Loan, account: Account, session: object, *,
    expected_identity_id: str, admitted_source_epoch: int | None = None,
    reader_scope=None,
) -> LoanActionScopeLease | None:
    """Create an exact action lease only for the still-live selected borrower.

    The caller independently owns capability, permission, explicit consent,
    at-most-once claim and action-specific state checks. A lease cannot by
    itself authorize a provider call or an ADEPT fulfillment.
    """
    if (type(loan) is not Loan or type(account) is not Account
            or type(expected_identity_id) is not str or not expected_identity_id):
        return None
    if reader_scope is not None:
        from .search_orchestration import SearchReadScope
        if (type(reader_scope) is not SearchReadScope
                or reader_scope.identity_id != expected_identity_id
                or reader_scope.source_epoch != admitted_source_epoch
                or not reader_scope.current(service)):
            return None
        epoch = 0  # not a navigation lease
    else:
        epoch = getattr(service, "_account_context_epoch", None)
        if type(epoch) is not int:
            return None
    if admitted_source_epoch is not None and (
            type(admitted_source_epoch) is not int
            or admitted_source_epoch < 0):
        return None
    session_state = _inert_session_state(session)
    if session_state is None:
        return None
    descriptor = session_state.get("descriptor")
    if type(descriptor) is not ProviderDescriptor:
        return None
    lease = LoanActionScopeLease(
        _account=account, _session=session, _loan=loan,
        _expected_identity_id=expected_identity_id,
        _account_fields=(
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        ),
        _loan_fields=_exact_loan_fields(loan),
        _context_epoch=epoch, _provider_epoch=session_state.get("_epoch"),
        _descriptor=descriptor,
        _admitted_service=service if admitted_source_epoch is not None else None,
        _source_epoch=admitted_source_epoch,
        _reader_scope=reader_scope,
    )
    return lease if lease.current(service) else None


def capture_admitted_loan_stage(service, loan: Loan, *, expected_identity_id: str):
    """Freeze a consented exact ACSM staging scope before enqueue, no I/O."""
    try:
        identity = service.current_identity
        account_epoch = service._account_context_epoch
        source_epoch = source_context_epoch(service)
        if (type(loan) is not Loan
                or loan.medium != "digital" or loan.state != "active"
                or type(expected_identity_id) is not str
                or loan.identity_id != expected_identity_id
                or getattr(identity, "identity_id", None) != expected_identity_id
                or type(loan.source_account_id) is not str
                or not loan.source_account_id):
            return None
        account = service._account_for_loan(loan)
        session = service._session_for_source(
            loan.source_provider, loan.source_credential,
        )
        # The selected-token path requires exact raw upstream UID (not alias).
        if (account.unique_id != loan.source_account_id
                or account.identity_id != expected_identity_id):
            return None
        lease = capture_loan_action_scope(
            service, loan, account, session,
            expected_identity_id=expected_identity_id,
            admitted_source_epoch=source_epoch,
        )
        if (lease is None or service.current_identity is not identity
                or service._account_context_epoch != account_epoch
                or source_context_epoch(service) != source_epoch):
            return None
        return replace(lease, _stage_attempt=_ConfirmedStageAttempt())
    except Exception:
        return None


def capture_confirmed_loan_stage(
    service, loan: Loan, *, identity_id: str, account_id: str, confirmed: bool,
) -> LoanActionScopeLease | None:
    """Explicit headless staging admission, with no offer read or file write."""
    from .search_orchestration import capture_search_read_scope
    try:
        if (confirmed is not True or type(loan) is not Loan
                or loan.medium != "digital" or loan.state != "active"
                or type(identity_id) is not str or identity_id != loan.identity_id
                or type(account_id) is not str or not account_id
                or any(c in account_id for c in "\x00\r\n")
                or account_id != loan.source_account_id):
            return None
        scope = capture_search_read_scope(service, identity_id)
        account = service._account_for_loan(loan)
        if (type(account) is not Account or account.identity_id != identity_id
                or account.unique_id != account_id):
            return None
        session = service._sessions.get(
            service._session_key(loan.source_provider, loan.source_credential),
        )
        lease = capture_loan_action_scope(
            service, loan, account, session, expected_identity_id=identity_id,
            admitted_source_epoch=scope.source_epoch, reader_scope=scope,
        )
        if lease is None:
            return None
        return replace(lease, _stage_attempt=_ConfirmedStageAttempt())
    except Exception:
        return None


__all__ = ["LoanActionScopeLease", "capture_loan_action_scope",
           "capture_admitted_loan_stage", "capture_confirmed_loan_stage"]
