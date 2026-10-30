"""Numilog startup *materialization* consent, distinct from inventory (#1905).

No legacy backup switch, cache hit, schedule for another store, selected GUI
identity or source-sync grants permission.  A two-step explicit confirmation is
required for each exact source; old/malformed preferences default to manual.
"""
from __future__ import annotations

from typing import Any
from ..plugin_api import SyncResult
from .numilog_local_preview import _Binding, _identity_root, _valid_binding
from .numilog_manifest_rights import numilog_inventory_binding_valid

_PREFIX = "sync.source.numilog.materialization.consent.v1."
_ACTIVE = "on_start:v1"
_MANUAL = "manual"
_MAX_ITEMS_PER_OPENING = 8
_MIN_SECONDS_BETWEEN_STARTS = 3600


def canonical_consent_current(db, source) -> bool:
    """Adapt the existing explicit source opt-in, without copying grants.

    Called on the reader/publisher's own SQLite connection. This authorizes
    only purchases of the exact Numilog account; A and its offer are still
    independently admitted by the shared executor. OFF/rebind is immediate.
    """
    import re
    from ..models import Account
    from ..brokers import SynchronizationBrokerImpl
    scope = source.scope
    if (scope.provider_id != "numilog" or source.kind != "purchase"
            or source.branch != "meslivres"):
        return False
    sid = SynchronizationBrokerImpl._source_id(Account(
        identity_id=scope.identity_id, provider_id=scope.provider_id,
        credential_id=scope.credential_id, unique_id=scope.account_id,
    ))
    rows = dict(db.execute(
        "SELECT key,value FROM metadata WHERE key IN (?,?)",
        (policy_key(sid), "sync.source.numilog.materialization.epoch.v1." + sid),
    ).fetchall())
    epoch = rows.get("sync.source.numilog.materialization.epoch.v1." + sid)
    return (rows.get(policy_key(sid)) == _ACTIVE and type(epoch) is str
            and re.fullmatch(r"[0-9a-f]{32}", epoch) is not None)


def canonical_consent_epoch(service, account):
    """Pin the source consent generation across a canonical Sync."""
    from ..brokers import SynchronizationBrokerImpl
    if account.provider_id != "numilog":
        return None
    return service.storage.numilog_materialization_consent_revision(
        SynchronizationBrokerImpl._source_id(account))


def canonical_consent_epoch_current(db, account, expected):
    from ..brokers import SynchronizationBrokerImpl
    if account.provider_id != "numilog":
        return expected is None
    row = db.execute("SELECT value FROM metadata WHERE key=?", (
        "sync.source.numilog.materialization.epoch.v1."
        + SynchronizationBrokerImpl._source_id(account),)).fetchone()
    return (row[0] if row else None) == expected


def policy_key(source_id: str) -> str:
    return _PREFIX + source_id


def _source_accounts(broker: Any, source_id: str) -> list[Any]:
    """Isolate malformed *other* account rows before source-specific actions.

    The matched account still goes through _valid_binding and persistent
    credential/session ownership checks. Never use this as an authorization
    shortcut or infer identity from the selected GUI profile.
    """
    import re
    if (type(source_id) is not str
            or re.fullmatch(r"sync:[0-9a-f]{24}", source_id) is None):
        return []
    try:
        accounts = broker.service.accounts
        if type(accounts) not in {list, tuple}:
            return []
        # These are already loaded local account declarations, not a remote
        # provider page. Never make explicit *revocation* impossible merely
        # because many unrelated providers are also configured.
        found = []
        for account in accounts:
            try:
                if (getattr(account, "provider_id", None) == "numilog"
                        and broker._source_id(account) == source_id):
                    found.append(account)
            except Exception:
                # One corrupt unrelated row cannot withhold consent
                # revocation or safe startup eligibility from its siblings.
                continue
        return found
    except Exception:
        return []


def _binding(broker: Any, source_id: str):
    rows = _source_accounts(broker, source_id)
    if len(rows) != 1:
        return None
    account = rows[0]
    scoped = _Binding(
        account.provider_id, account.identity_id,
        account.credential_id, account.unique_id,
    )
    if not _valid_binding(broker, scoped, source_id):
        return None
    session = broker.service._sessions.get(
        broker.service._session_key("numilog", account.credential_id),
    )
    if (
        session is None
        or not numilog_inventory_binding_valid(
            broker.service, account, session=session,
        )
        or getattr(getattr(session, "capabilities", None),
                   "direct_download", None) is not True
    ):
        return None
    roots = _identity_root(broker.service, account.identity_id)
    if roots is None or len(roots) != 1:
        return None
    return account, session, roots


def _same_binding(broker: Any, source_id: str, scope: tuple, session: Any, roots: tuple) -> bool:
    """Re-admit the exact pre-preview source, session object and directory."""
    current = _binding(broker, source_id)
    if current is None:
        return False
    account, active, current_roots = current
    return (active is session
            and (source_id, account.identity_id, account.credential_id, account.unique_id) == scope
            and tuple(current_roots) == tuple(roots))


def mode(broker: Any, source_id: str) -> str:
    try:
        # The configured consent must remain visible and revocable while
        # disconnected. Eligibility for I/O separately requires _binding().
        return ("on_start" if broker.service.storage.metadata_get(
            policy_key(source_id),
        ) == _ACTIVE else "manual")
    except Exception:
        return "manual"


def _receipt(status: str, *, ok: bool = False, **facts: Any) -> SyncResult:
    return SyncResult(
        ok=ok, changed=0, warnings=() if ok else (status,),
        facts={"phase": "materialize", "status": status,
               "acquisition_authorized": False, **facts},
    )


def prepare(broker: Any, source_id: str) -> SyncResult:
    """An explicit user click: local SHA preview and an expiring one-use token."""
    try:
        bound = _binding(broker, source_id)
        if bound is None:
            return _receipt("source_not_ready")
        account, session, roots = bound
        scope = (source_id, account.identity_id, account.credential_id,
                 account.unique_id)
        revision = broker.service.storage.numilog_materialization_consent_revision(
            source_id,
        )
        preview = broker.preview_local_materialization(
            source_id, verify_local_files=True,
        )
        fields = preview.facts
        if (preview.ok is not True
                or fields.get("status") != "preview_only"
                or fields.get("acquisition_authorized") is not False
                or fields.get("manifest_refreshed") is not False
                or fields.get("files_verified") is not True
                or type(fields.get("counts")) is not dict):
            return _receipt("preview_unavailable")
        counts = fields["counts"]
        if (set(counts) != {
            "owned_purchases", "already_local_linked", "already_local_verified",
            "needs_file_verification", "potentially_missing",
            "candidate_unverified", "needs_reconciliation",
            "attention_required", "not_downloadable",
            "ignored_unattested_rights",
        } or any(type(value) is not int or not 0 <= value <= 100_000
                 for value in counts.values())):
            return _receipt("preview_unavailable")
        # Exact session and library configuration are kept only in memory.
        # A revoked policy or changed credential during SHA verification
        # invalidates the proposal before it can become a confirmation token.
        if (revision != broker.service.storage.numilog_materialization_consent_revision(
                source_id,
            ) or not _same_binding(broker, source_id, scope, session, roots)):
            return _receipt("preview_changed")
        snapshot = (id(session), tuple(str(root) for root in roots),
                    tuple(sorted(counts.items())), revision)
        token = broker._numilog_consent_plans.put(scope, snapshot, (session,))
        return _receipt(
            "confirmation_required", ok=True,
            confirmation_token=token, expires_in_seconds=600,
            requires_explicit_confirmation=True,
            manifest_refreshed=False, files_verified=True,
            counts=dict(counts), schedule_available=True,
        )
    except Exception:
        return _receipt("preview_unavailable")


def confirm(broker: Any, source_id: str, token: str, *, mode: str) -> SyncResult:
    """Opt in only after consuming a matching proposal, never via old flags."""
    if type(mode) is not str or mode != "on_start":
        return _receipt("invalid_mode")
    try:
        bound = _binding(broker, source_id)
        if bound is None:
            return _receipt("source_not_ready")
        account, session, roots = bound
        scope = (source_id, account.identity_id, account.credential_id,
                 account.unique_id)
        row = broker._numilog_consent_plans.take(token, scope)
        preview = broker.preview_local_materialization(
            source_id, verify_local_files=True,
        )
        counts = preview.facts.get("counts")
        evidence = (id(session), tuple(str(root) for root in roots),
                    tuple(sorted(counts.items()))) if type(counts) is dict else None
        if (preview.ok is not True
                or preview.facts.get("files_verified") is not True
                or evidence != row[2][:3] or row[3][0] is not session
                or not _same_binding(broker, source_id, scope, session, roots)):
            return _receipt("preview_changed")
        if not broker.service.storage.numilog_materialization_change_consent(
            source_id, activate=True, expected_revision=row[2][3],
        ):
            return _receipt("consent_changed")
        broker._notify()
        return _receipt("on_start", ok=True, mode="on_start",
                        next_trigger="next_opening", schedule_available=True)
    except Exception:
        return _receipt("confirmation_unavailable")


def revoke(broker: Any, source_id: str) -> str:
    # Revocation must work even after a session disconnects or a source is
    # disabled. A source unknown to this installation cannot be fabricated.
    rows = _source_accounts(broker, source_id)
    if len(rows) != 1:
        raise ValueError("unknown_numilog_source")
    broker.service.storage.numilog_materialization_change_consent(
        source_id, activate=False,
    )
    with broker._numilog_consent_plans.lock:
        broker._numilog_consent_plans.entries.clear()
    broker._notify()
    return "manual"
