"""Explicit per-source Kobo startup acquisition policy (#1346).

This is a *separate* opt-in from historical global kobo_backup.enabled.
Missing, malformed or legacy preferences always mean manual materialization.
No provider, file or ADEPT request is made when evaluating the policy.
"""
from __future__ import annotations

from typing import Any

_PREFIX = "sync.source.materialization.on_start."


def policy_key(source_id: str) -> str:
    """Source IDs are opaque digests from the existing Sync broker."""
    return _PREFIX + source_id


def _account_id(account: Any) -> str:
    from ..brokers import SynchronizationBrokerImpl
    return SynchronizationBrokerImpl._source_id(account)


def _configured_binding(service: Any, account: Any) -> bool:
    """Fail closed on ambiguous credentials, stale bindings or disabled sources."""
    if getattr(account, "provider_id", None) != "kobo":
        return False
    credential = getattr(account, "credential_id", None)
    identity = getattr(account, "identity_id", None)
    if type(credential) is not str or not credential or type(identity) is not str or not identity:
        return False
    # Kobo acquisition is credential-scoped. Two account records sharing the
    # credential cannot safely be materialized as one identity/account.
    if sum(
        1 for row in getattr(service, "accounts", ())
        if getattr(row, "provider_id", None) == "kobo"
        and getattr(row, "credential_id", None) == credential
    ) != 1:
        return False
    if not service.sync_source_enabled(account):
        return False
    storage = service.storage
    if storage.identity_for_connection("kobo", credential) != identity:
        return False
    connections = [
        row for row in storage.provider_connections(identity_id=identity)
        if row.get("provider_id") == "kobo" and row.get("credential_id") == credential
        and row.get("enabled") is True
    ]
    if len(connections) != 1:
        return False
    sessions = [
        session for session in getattr(service, "_sessions", {}).values()
        if getattr(session, "provider_id", None) == "kobo"
        and getattr(session, "credential_id", None) == credential
    ]
    if len(sessions) != 1:
        return False
    session = sessions[0]
    return bool(
        getattr(session, "connected", False)
        and getattr(getattr(session, "capabilities", None), "backup", False)
    )


def can_enable_kobo_on_start(service: Any, account: Any) -> bool:
    """A policy can be enabled only for an unambiguous connected source."""
    try:
        return _configured_binding(service, account)
    except (AttributeError, TypeError, ValueError, KeyError):
        return False


def kobo_on_start_sessions(service: Any) -> tuple[Any, ...]:
    """Return *only* explicitly approved and still eligible Kobo sessions.

    Never infer authorization from the legacy global bool, cache age, an
    interrupted checkpoint, a previous download or a deep inventory scan.
    """
    ready = []
    for account in getattr(service, "accounts", ()):
        if not can_enable_kobo_on_start(service, account):
            continue
        source_id = _account_id(account)
        try:
            enabled = service.storage.metadata_get(policy_key(source_id)) == "1"
        except (AttributeError, TypeError, ValueError, KeyError):
            enabled = False
        if not enabled:
            continue
        credential = account.credential_id
        ready.extend(
            session for session in service._sessions.values()
            if getattr(session, "provider_id", None) == "kobo"
            and getattr(session, "credential_id", None) == credential
        )
    return tuple(ready)
