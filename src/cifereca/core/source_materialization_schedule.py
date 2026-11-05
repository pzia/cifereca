"""Explicit per-source Kobo startup acquisition policy (#1346).

This is a *separate* opt-in from historical global kobo_backup.enabled.
Missing, malformed or legacy preferences always mean manual materialization.
No provider, file or ADEPT request is made when evaluating the policy.
"""
from __future__ import annotations

from typing import Any
import time

_PREFIX = "sync.source.materialization.on_start."
_RETRY_PREFIX = "sync.source.materialization.retry_minutes."
_DEFAULT_RETRY_MINUTES = 60  # Never shorten the baseline startup retry window.
_MAX_RETRY_MINUTES = 30 * 24 * 60


def retry_interval_key(source_id: str) -> str:
    """Independent, exact-source retry clock policy; never acquisition consent."""
    return _RETRY_PREFIX + source_id


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
    upstream_id = getattr(account, "unique_id", None)
    # The legacy source key normalizes a blank primary account to
    # "kobo:<credential>:primary" and strips nonblank IDs. An account whose
    # actual upstream ID equals that fallback (or requires stripping) must
    # never inherit an ON from a previous borrower after reconnect/rebind.
    # Exact per-account V2 CAS consent is a separate, safer path.
    if (type(upstream_id) is not str
            or (upstream_id and (
                upstream_id != upstream_id.strip()
                or upstream_id == f"kobo:{credential}:primary"
            ))):
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
    # #3196: on_start is strictly the historical Kobo backup_items writer.
    # A complete Core A->B source already has a single canonical executor.
    # Keep the preference persisted for audit/rollback, but never schedule
    # the independent legacy acquisition or advance its attempt clock.
    from .canonical_delivery_abi import has_complete_delivery_abi
    if has_complete_delivery_abi(session):
        return False
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


def _source_retry_minutes(service: Any, account: Any) -> int | None:
    """One-way bounded retirement of the old GLOBAL retry cadence (#3198).

    A saved exact-source interval wins. Until the first explicitly approved
    startup attempt, preserve a *conservative* legacy retry window; never
    import historical enabled/allow_unlinked flags as source permission.
    mark_on_start_attempt pins the selected window to this exact source.
    """
    raw = service.storage.metadata_get(retry_interval_key(_account_id(account)))
    if raw is not None:
        if (
            type(raw) is not str or not raw.isascii()
            or not raw.isdecimal() or len(raw) > 5
        ):
            return None
        minutes = int(raw)
    else:
        old = service.kobo_backup_interval_minutes()
        if type(old) is not int:
            return None
        # An old value below the safe 60-minute baseline cannot shorten it.
        minutes = max(_DEFAULT_RETRY_MINUTES, old)
    return minutes if _DEFAULT_RETRY_MINUTES <= minutes <= _MAX_RETRY_MINUTES else None


def on_start_due(service: Any, account: Any) -> bool:
    """Independent clock for sources without a checkpoint or download rows."""
    from .kobo_backup import _kobo_checkpoint_for
    credential = account.credential_id
    identity = account.identity_id
    try:
        raw = service.storage.metadata_get("sync.source.materialization.last_attempt." + _account_id(account))
        if raw is None:
            last = 0  # first explicit on_start consent: no prior attempt
        elif type(raw) is str and raw.isdecimal() and len(raw) <= 16:
            last = int(raw)
        else:
            return False  # corrupt clock is not permission to retry
        interval = _source_retry_minutes(service, account)
        if interval is None:
            return False  # malformed exact-source cadence cannot authorize retry
        now = int(time.time())
        if last > now + 300:
            return False  # clock rollback/future metadata: require manual action
        # A checkpoint must not bypass the per-source retry throttle. A
        # failed/partial run can leave a checkpoint and must not trigger a
        # download attempt on every application startup.
        if last > 0 and now - last < interval * 60:
            return False
        if _kobo_checkpoint_for(service, identity, credential) is not None:
            return bool(service._kobo_backup_account_due(
                credential, interval_minutes=interval,
            ))
        return True
    except Exception:
        # Inability to read the attempt clock is not permission to retry.
        return False


def mark_on_start_attempt(service: Any, account: Any) -> bool:
    """Freeze source cadence and attempt clock *before* any authorized dispatch.

    If either SQLite write fails, the caller must not enter acquisition.
    An orphaned retry interval alone cannot grant ON or advance the attempt
    clock; retries still require the exact-source consent and live binding.
    """
    try:
        source_id = _account_id(account)
        interval_key = retry_interval_key(source_id)
        interval = _source_retry_minutes(service, account)
        if interval is None:
            return False
        if service.storage.metadata_get(interval_key) is None:
            service.storage.metadata_set(interval_key, str(interval))
        service.storage.metadata_set(
            "sync.source.materialization.last_attempt." + source_id,
            str(int(time.time())),
        )
        return True
    except Exception:
        return False
