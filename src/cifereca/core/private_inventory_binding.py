"""Provider-neutral lease check for legacy private-library observations.

This check authorizes no acquisition and persists nothing. Exact canonical
resource admission and publication keep their independent stronger guards.
"""
from __future__ import annotations

from inspect import getattr_static
from typing import Any

from .passive_source_session import passive_private_library_session

from .source_context import source_context_epoch


def _coordinates(account):
    return (account.identity_id, account.provider_id,
            account.credential_id, account.unique_id)


def private_inventory_binding_current(service, account, session, *, scope, epoch):
    """Revalidate the pinned account/session without interpreting provider IDs.

    Empty upstream account IDs are exact primary IDs, never wildcards. A v1
    session without an accounts projection remains usable only when its
    credential has exactly one registered account. Multiple upstream accounts
    require an explicit local projection containing the requested account once.
    """
    try:
        if (type(scope) is not tuple or len(scope) != 4
                or any(type(value) is not str or "\x00" in value for value in scope)
                or not all(scope[:3]) or type(epoch) is not int or epoch < 0):
            return False
        identity, provider, credential, _account_id = scope

        def memory_current():
            if (_coordinates(account) != scope
                    or source_context_epoch(service) != epoch
                    or getattr(session, "provider_id", None) != provider
                    or getattr(session, "credential_id", None) != credential
                    or getattr(session, "connected", None) is not True
                    or getattr(session, "_closed", False) is not False):
                return False
            accounts = [row for row in service.accounts
                        if row.provider_id == provider and row.credential_id == credential]
            matching = [row for row in accounts if _coordinates(row) == scope]
            if (len(matching) != 1 or matching[0] is not account
                    or any(row.identity_id != identity for row in accounts)):
                return False
            registered = [row for row in service._sessions.values()
                          if getattr(row, "provider_id", None) == provider
                          and getattr(row, "credential_id", None) == credential]
            if len(registered) != 1 or registered[0] is not session:
                return False
            if service._sessions.get(service._session_key(provider, credential)) is not session:
                return False
            if getattr_static(session, "accounts", None) is None:
                return len(accounts) == 1
            upstream = session.accounts
            return (isinstance(upstream, (tuple, list))
                    and sum(_coordinates(row) == scope for row in upstream) == 1)

        if (not memory_current() or service.identity_by_id(identity) is None
                or service.sync_source_enabled(account) is not True
                or service.storage.identity_for_connection(provider, credential) != identity):
            return False
        bindings = [row for row in service.storage.provider_connections(identity_id=identity)
                    if row.get("provider_id") == provider
                    and row.get("credential_id") == credential]
        # The connection only attests an identity. An upstream borrower can
        # be removed or reassigned independently while that credential stays
        # connected. Require the exact persisted account-to-connection join
        # before AND after every private library read; no legacy fallback
        # based on a sole in-memory account or on the selected UI profile.
        return (
            len(bindings) == 1
            and bindings[0].get("enabled") is True
            and service.storage.provider_scope_account_bound(
                identity_id=identity, provider_id=provider,
                credential_id=credential, account_id=scope[3],
            ) is True
            and memory_current()
        )
    except Exception:
        return False


def exact_private_inventory_binding_valid(
    service: Any, account: Any, *, session: Any, provider_id: str,
) -> bool:
    """Validate a pinned private inventory source without granting any right.

    Match the exact in-memory account, SQLite borrower binding and passive
    session coordinates. Never authorize a GET or infer a purchase.
    """
    try:
        if type(provider_id) is not str or not provider_id:
            return False
        if getattr(account, "provider_id", None) != provider_id:
            return False
        identity = getattr(account, "identity_id", None)
        credential = getattr(account, "credential_id", None)
        account_id = getattr(account, "unique_id", None)
        if not all(type(x) is str and x and "\x00" not in x
                   for x in (identity, credential, account_id)):
            return False
        matches = [
            item for item in service.accounts
            if getattr(item, "provider_id", None) == provider_id
            and getattr(item, "credential_id", None) == credential
        ]
        if (len(matches) != 1
                or matches[0].identity_id != identity
                or matches[0].unique_id != account_id
                or not service.sync_source_enabled(account)
                or service.identity_by_id(identity) is None):
            return False
        # Pin the exact SQLite borrower/connection join. An identity-level
        # connection alone cannot attest a detached account.
        if service.storage.provider_scope_account_bound(
            identity_id=identity, provider_id=provider_id,
            credential_id=credential, account_id=account_id,
        ) is not True:
            return False
        # A login/reconnect can replace a session while library() is
        # retrieving pages. Never accept an old in-flight session as the
        # current authority, even if its credential and connected flag match.
        current_sessions = [
            active for active in getattr(service, "_sessions", {}).values()
            if (coordinates := passive_private_library_session(active)) is not None
            and coordinates[0] == account.provider_id
            and coordinates[1] == credential
        ]
        if len(current_sessions) != 1 or current_sessions[0] is not session:
            return False
        coordinates = passive_private_library_session(session)
        return (
            coordinates is not None
            and coordinates[0] == account.provider_id
            and coordinates[1] == credential
            and coordinates[2] is True
            and coordinates[3] is True
        )
    except Exception:
        # A corrupt binding is never permission for a private provider call.
        return False

