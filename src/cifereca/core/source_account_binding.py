"""Exact provider/account/identity binding before private source operations.

Read-only Core fence: no HTTP, no fallback, no permission or opt-in creation.
Callers must separately verify consent and the selected delivery offer.
"""
from __future__ import annotations

from typing import Any
from inspect import getattr_static
from types import SimpleNamespace

from ..models import Account
from ..providers.base import ProviderSession
from .loan_action_scope import _inert_session_accounts, _inert_session_state
from .legacy_source_account_id import ambiguous_legacy_source_account_id


def source_account_binding_ready(
    service: Any, account: Account, *, expected_provider_id: str,
) -> bool:
    """Reject stale session, rebound SQLite account or ambiguous legacy IDs."""
    try:
        if (type(expected_provider_id) is not str or not expected_provider_id
                or type(account) is not Account
                or account.provider_id != expected_provider_id
                or ambiguous_legacy_source_account_id(
                    expected_provider_id, account.credential_id,
                    account.unique_id,
                )
                or not service.sync_source_enabled(account)
                or service.identity_by_id(account.identity_id) is None
                or service.storage.identity_for_connection(
                    expected_provider_id, account.credential_id,
                ) != account.identity_id):
            return False
        matches = service.storage.provider_accounts(
            identity_id=account.identity_id,
        )
        if sum(
            row.get("provider_id") == expected_provider_id
            and row.get("credential_id") == account.credential_id
            and row.get("account_id") == account.unique_id
            for row in matches
        ) != 1:
            return False
        session = service._sessions.get(
            service._session_key(expected_provider_id, account.credential_id),
        )
        # Read initialized provider-session memory, not plugin properties.
        klass = type(session)
        # Built-in SimpleNamespace is an inert legacy test fixture. Its
        # own C-level __getattribute__ is not object.__getattribute__;
        # admit only that exact builtin type, never a subclass/proxy.
        if (klass is not SimpleNamespace
                and getattr_static(klass, "__getattribute__", None)
                is not object.__getattribute__):
            return False
        if issubclass(klass, ProviderSession):
            state = _inert_session_state(session)
            session_accounts = _inert_session_accounts(session)
            if (state is None or session_accounts is None
                    or state.get("provider_id") != expected_provider_id
                    or state.get("credential_id") != account.credential_id
                    or state.get("_closed") is True):
                return False
            connected = state.get("connected")
        else:
            # Inert legacy test doubles have plain initialized fields only.
            for name in ("connected", "accounts"):
                descriptor = getattr_static(klass, name, None)
                if getattr_static(type(descriptor), "__get__", None) is not None:
                    return False
            state = object.__getattribute__(session, "__dict__")
            if type(state) is not dict:
                return False
            connected = state.get("connected")
            session_accounts = state.get("accounts")
        return (
            connected is True
            and type(session_accounts) in (list, tuple)
            and len(session_accounts) <= 512
            and sum(row is account for row in session_accounts) == 1
            and sum(row is account for row in service.accounts) == 1
            and sum(
                type(row) is Account
                and row.provider_id == expected_provider_id
                and row.credential_id == account.credential_id
                for row in service.accounts
            ) == 1
        )
    except Exception:
        return False
