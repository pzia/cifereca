"""Explicit per-source Kobo startup acquisition policy (#1346).

This is a *separate* opt-in from historical global kobo_backup.enabled.
Missing, malformed or legacy preferences always mean manual materialization.
No provider, file or ADEPT request is made when evaluating the policy.
"""
from __future__ import annotations

from typing import Any
import time

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
    # Generic inventory may tolerate historical source switches. Automatic
    # downloads do not: malformed source-enable metadata must fail closed.
    raw_source_enabled = storage.metadata_get(
        "sync.source.enabled." + _account_id(account)
    )
    if raw_source_enabled is not None and raw_source_enabled not in {
        "1", "true", "yes", "on",
    }:
        return False
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
    except Exception:
        # Database or mapping failures cannot authorize automatic downloads.
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
        except Exception:
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


def on_start_due(service: Any, account: Any) -> bool:
    """Independent clock for sources without a checkpoint or download rows."""
    from .kobo_backup import _kobo_checkpoint_for
    credential = account.credential_id
    identity = account.identity_id
    if _kobo_checkpoint_for(service, identity, credential) is not None:
        return bool(service._kobo_backup_account_due(credential))
    try:
        raw = service.storage.metadata_get("sync.source.materialization.last_attempt." + _account_id(account))
        last = int(raw) if type(raw) is str and raw.isdecimal() else 0
        interval = max(15, int(service.kobo_backup_interval_minutes()))
        now = int(time.time())
        return last <= 0 or last > now + 300 or now - last >= interval * 60
    except Exception:
        # Inability to read the attempt clock is not permission to retry.
        return False


def mark_on_start_attempt(service: Any, account: Any) -> bool:
    """Persist a per-source attempt *before* dispatch; fail closed on DB errors.

    This bounds repeated empty-manifest launches without converting an attempt
    into a success or borrowing the old global success clock.
    """
    try:
        service.storage.metadata_set(
            "sync.source.materialization.last_attempt." + _account_id(account),
            str(int(time.time())),
        )
        return True
    except Exception:
        return False
