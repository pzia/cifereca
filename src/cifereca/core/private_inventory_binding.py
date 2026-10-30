"""Provider-neutral lease check for legacy private-library observations.

This check authorizes no acquisition and persists nothing. Exact canonical
resource admission and publication keep their independent stronger guards.
"""
from __future__ import annotations

from inspect import getattr_static

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
        return (len(bindings) == 1 and bindings[0].get("enabled") is True
                and memory_current())
    except Exception:
        return False
