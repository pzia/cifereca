"""Exact borrower loan cache decoding and navigation-free display hints."""
from __future__ import annotations

import hashlib
import json
from inspect import getattr_static

from ..models import Account, Loan
from ..providers import ProviderConfigurationError
from .source_context import source_context_epoch

def _loan_cache_key(account: Account) -> str:
    """Opaque, unambiguous identity/provider/credential/borrower cache scope.

    The legacy `provider:credential:uid:loans` alias omitted identity, and
    `:` can occur inside a provider credential or borrower identifier. Do
    not read old aliases: they cannot be safely attributed retroactively.
    """
    if type(account) is not Account:
        raise ProviderConfigurationError("loan_cache_scope_invalid")
    parts = (
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )
    if any(type(value) is not str or len(value) > 1024
           or chr(0) in value for value in parts):
        raise ProviderConfigurationError("loan_cache_scope_invalid")
    serialized = json.dumps(parts, ensure_ascii=True, separators=(',', ':'))
    return "loans.v2:" + hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _cached_loans_for_exact_account(
    value: object, account: Account, *, descriptor=None,
) -> list[Loan] | None:
    """Treat cached rows as untrusted: never rewrite their source/identity.

    A cache hit is only usable if every row still identifies the same
    provider, credential, account and identity. Corrupt/legacy rows trigger
    an ordinary fresh provider read, never a cross-account display.
    """
    if type(value) is not list or len(value) > 10_000:
        return None
    from ..plugin_api.loan_scope import loan_matches_account
    # Exact account IDs need no alias convention. A different observed ID
    # requires an attestation from this installed provider descriptor, not
    # an implicit Paris/default-primary fallback encoded in Core.
    loans = []
    for value_row in value:
        if type(value_row) is not dict:
            return None
        try:
            row = Loan(**value_row)
        except (TypeError, ValueError):
            return None
        if not (
            type(row) is Loan
            and type(row.id) is str and bool(row.id)
            and type(row.source_provider) is str
            and row.source_provider == account.provider_id
            and type(row.source_credential) is str
            and row.source_credential == account.credential_id
            and type(row.source_account_id) is str
            and (row.source_account_id == account.unique_id
                 or loan_matches_account(row, account, descriptor))
            and type(row.identity_id) is str
            and row.identity_id == account.identity_id
            and type(row.medium) is str
            and row.medium in ('digital', 'physical', 'unknown')
        ):
            return None
        loans.append(row)
    return loans


def loan_display_rows(service, identity_id: str) -> tuple[Loan, ...]:
    """Read only qualified cache rows; no refresh, navigation, or rights grant.

    Cache hints are optional. A disconnected/rebound/ambiguous source does not
    contribute a hint. The caller still requires a matching OriginLink or
    loan entitlement before attaching these descriptive fields to a book.
    """
    try:
        if type(identity_id) is not str or not identity_id:
            return ()
        identity = service.identity_by_id(identity_id)
        if identity is None:
            return ()
        epoch = source_context_epoch(service)
        accounts = tuple(service.accounts_for_identity(identity_id))
    except Exception:
        return ()

    def reader_current():
        return (service.identity_by_id(identity_id) is identity
                and source_context_epoch(service) == epoch)

    rows = []
    for account in accounts:
        try:
            if type(account) is not Account or account.identity_id != identity_id:
                continue
            provider, credential, borrower = (
                account.provider_id, account.credential_id, account.unique_id,
            )
            coords = (identity_id, provider, credential, borrower)
            if (not provider or not credential
                    or sum(type(a) is Account and (
                        a.identity_id, a.provider_id, a.credential_id, a.unique_id,
                    ) == coords for a in accounts) != 1):
                continue
            key = service._session_key(provider, credential)
            session = service._sessions.get(key)
            if session is None:
                continue
            conn_epoch = service.storage.provider_connection_epoch(
                provider, credential, identity_id,
            )
            if type(conn_epoch) is not int or conn_epoch < 1:
                continue

            def binding_current():
                return (
                    reader_current()
                    and (account.identity_id, account.provider_id,
                         account.credential_id, account.unique_id) == coords
                    and service._sessions.get(key) is session
                    and getattr_static(session, "connected", False) is True
                    and service._provider_enabled_for_identity(provider, identity_id) is True
                    and service.storage.provider_scope_account_bound(
                        identity_id=identity_id, provider_id=provider,
                        credential_id=credential, account_id=borrower,
                    ) is True
                    and service.storage.provider_connection_epoch_current(
                        provider, credential, identity_id, conn_epoch,
                    ) is True
                )

            if not binding_current():
                continue
            cached = service.storage.cache_get("account", _loan_cache_key(account))
            decoded = _cached_loans_for_exact_account(
                cached, account, descriptor=getattr_static(session, "descriptor", None),
            )
            if decoded is not None and binding_current():
                rows.extend(decoded)
        except Exception:
            continue
    try:
        return tuple(rows) if reader_current() else ()
    except Exception:
        return ()
