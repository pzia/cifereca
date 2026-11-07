"""Explicit per-source Kobo startup acquisition policy (#1346).

This is a *separate* opt-in from historical global kobo_backup.enabled.
Missing, malformed or legacy preferences always mean manual materialization.
No provider, file or ADEPT request is made when evaluating the policy.
"""
from __future__ import annotations

from typing import Any
import hashlib
import json
import secrets
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


# #4858 D4: an exact borrower-scoped, independent attempt journal is the
# only positive permission to RETRY an already executed automatic W2 attempt.
# Its "complete" state does NOT attest a provider right or delivered B.
_ATTEMPT_PREFIX = "sync.source.materialization.attempt.v1."
_ATTEMPT_SCHEMA = "core.kobo.startup-attempt/1"
_ATTEMPT_STATES = frozenset({"in_flight", "complete", "manual_review"})
_INVALID_ATTEMPT = object()


def attempt_key(account: Any) -> str:
    """Opaque exact-identity/credential/borrower key, never legacy v0.7 PK."""
    coords = [
        getattr(account, name, None) for name in (
            "identity_id", "provider_id", "credential_id", "unique_id",
        )
    ]
    if (coords[1] != "kobo" or any(type(v) is not str for v in coords)
            or not coords[0] or not coords[2]):
        raise ValueError("startup_attempt_scope_invalid")
    return _ATTEMPT_PREFIX + hashlib.sha256(
        json.dumps(coords, ensure_ascii=True, separators=(",", ":")).encode("ascii"),
    ).hexdigest()


def _unique_fields(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError("duplicate_startup_attempt_field")
        row[key] = value
    return row


def _attempt(service: Any, account: Any):
    key = attempt_key(account)
    raw = service.storage.metadata_get(key)
    if raw is None:
        return None
    if type(raw) is not str or len(raw) > 4096:
        return _INVALID_ATTEMPT
    try:
        row = json.loads(raw, object_pairs_hook=_unique_fields)
        expected = [
            account.identity_id, "kobo",
            account.credential_id, account.unique_id,
        ]
        if (type(row) is not dict
                or set(row) != {"schema", "scope", "status", "updated_at", "generation"}
                or row.get("schema") != _ATTEMPT_SCHEMA
                or row.get("scope") != expected
                or row.get("status") not in _ATTEMPT_STATES
                or type(row.get("updated_at")) is not int
                or not 0 < row["updated_at"] <= 4_102_444_800
                or type(row.get("generation")) is not str
                or len(row["generation"]) != 32
                or any(c not in "0123456789abcdef" for c in row["generation"])):
            return _INVALID_ATTEMPT
        return row
    except (ValueError, TypeError, KeyError, RecursionError):
        return _INVALID_ATTEMPT


def finish_on_start_attempt(service: Any, account: Any, report: Any) -> bool:
    """After W2 finishes, record only a verified *terminal batch state*.

    Crash, callback failure, scope switch, ambiguous/empty supplier evidence,
    cancellation, partial GET, pending or conflict are not permission to
    schedule another background GET. Those need an explicit manual review.
    """
    try:
        current = _attempt(service, account)
        if type(current) is not dict or current["status"] != "in_flight":
            return False
        # A changed live borrower after a provider GET must never finalize
        # the old owner's journal as automatically retryable.
        if hasattr(service, "accounts") and not _configured_binding(service, account):
            return False
        rows = report.get("sources") if type(report) is dict else None
        source = rows[0] if type(rows) is list and len(rows) == 1 else None
        complete = (
            type(report) is dict
            and report.get("status") == "complete"
            and report.get("sessions") == 1
            and report.get("manifests_fetched") == 1
            and report.get("errors") == 0
            and report.get("pending") == 0
            and report.get("attention_required") == 0
            and report.get("remaining") == 0
            and report.get("aborted") is False
            and report.get("cancelled") is False
            and type(source) is dict
            and source.get("identity_id") == account.identity_id
            and source.get("credential_id") == account.credential_id
            and source.get("status") == "complete"
            and source.get("errors") == 0
            and source.get("pending") == 0
            and source.get("remaining") == 0
        )
        next_status = "complete" if complete else "manual_review"
        updated = {
            **current, "status": next_status,
            "updated_at": int(time.time()),
        }
        service.storage.metadata_set(
            attempt_key(account),
            json.dumps(updated, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        )
        return True
    except Exception:
        # Leaving in_flight is fail-closed, even if a supplier GET succeeded
        # but the local terminal checkpoint itself could not be persisted.
        return False


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
    # #3196 D4: the legacy on_start preference is account-scoped, whereas
    # _sync_kobo_backup_account_locked executes by *credential*. A reconnect
    # may replace the upstream borrower while retaining that credential and
    # its old ON bit. Never dispatch based only on the connected session key:
    # the single cached provider account must match the consented identity,
    # credential and upstream account EXACTLY. Unknown/ambiguous session
    # account inventory is manual-only (no provider I/O is performed here).
    if (not getattr(session, "connected", False)
            or not getattr(getattr(session, "capabilities", None), "backup", False)):
        return False
    bound_accounts = getattr(session, "accounts", None)
    if (not isinstance(bound_accounts, (list, tuple))
            or len(bound_accounts) != 1):
        return False
    bound = bound_accounts[0]
    # The historical Kobo Account model defaults identity_id="default"
    # even when SQLite's credential owner is a named Core identity. That
    # label is not ownership evidence; above we independently checked the
    # exact enabled connection and owner. Permit ONLY that legacy default,
    # never an unrelated explicit profile or borrower identifier.
    if (getattr(bound, "provider_id", None) != "kobo"
            or getattr(bound, "credential_id", None) != credential
            or getattr(bound, "identity_id", None) not in {identity, "default"}
            or getattr(bound, "unique_id", None) != upstream_id):
        return False
    # #3196: on_start is strictly the historical Kobo backup_items writer.
    # A complete Core A->B source already has a single canonical executor.
    # Keep the preference persisted for audit/rollback, but never schedule
    # the independent legacy acquisition or advance its attempt clock.
    from .canonical_delivery_abi import has_complete_delivery_abi
    return not has_complete_delivery_abi(session)


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
    """Retry only a fresh source or a fully completed exact-source attempt.

    The historical W2 checkpoint is a one-time negative quarantine for
    installations without this journal. It can NEVER authorize another GET.
    """
    from .kobo_backup import _kobo_checkpoint_for, _kobo_checkpoint_key
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
        attempt = _attempt(service, account)
        if attempt is _INVALID_ATTEMPT:
            return False  # corrupt/mismatched exact-source journal
        if attempt is not None:
            # A crash can strand 'in_flight'; auto-retrying even after the
            # cooldown risks a duplicate Kobo GET. Only a fully processed,
            # scoped terminal observation allows the NEXT scheduled pass.
            return (attempt["status"] == "complete"
                    and attempt["updated_at"] <= now
                    and now - attempt["updated_at"] >= interval * 60)
        if last > 0:
            # Old on_start clocks have no independent completion proof.
            # Do not bless them with a second GET during the cutover.
            return False
        # Historical checkpoint means an earlier 0.7 attempt, not proof of
        # its completion. It is a VETO only; a new exact-source journal is
        # never seeded as 'complete' from that record.
        if _kobo_checkpoint_for(service, identity, credential) is not None:
            return False
        if service.storage.metadata_get(_kobo_checkpoint_key(identity, credential)) is not None:
            return False
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
        prior = _attempt(service, account)
        if prior is _INVALID_ATTEMPT or (
            prior is not None and prior["status"] != "complete"
        ):
            return False
        if prior is None:
            from .kobo_backup import _kobo_checkpoint_for, _kobo_checkpoint_key
            # Direct caller of mark() must not bypass on_start_due's retired
            # checkpoint quarantine. Legacy incomplete/unknown work is NOT
            # a positive retry admission even with an unchanged ON switch.
            if service.storage.metadata_get(
                "sync.source.materialization.last_attempt." + source_id,
            ) is not None:
                return False
            if (_kobo_checkpoint_for(
                    service, account.identity_id, account.credential_id,
            ) is not None or service.storage.metadata_get(
                _kobo_checkpoint_key(account.identity_id, account.credential_id),
            ) is not None):
                return False
        # This is written FIRST. Even if a later retry-clock write fails,
        # the in-flight fence survives and forbids a second automatic GET.
        # A terminal completion is never guessed from W2 backup_items.
        journal = {
            "schema": _ATTEMPT_SCHEMA,
            "scope": [
                account.identity_id, "kobo",
                account.credential_id, account.unique_id,
            ],
            "status": "in_flight",
            "updated_at": int(time.time()),
            "generation": secrets.token_hex(16),
        }
        service.storage.metadata_set(
            attempt_key(account),
            json.dumps(journal, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        )
        if service.storage.metadata_get(interval_key) is None:
            service.storage.metadata_set(interval_key, str(interval))
        service.storage.metadata_set(
            "sync.source.materialization.last_attempt." + source_id,
            str(int(time.time())),
        )
        return True
    except Exception:
        return False
