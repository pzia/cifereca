"""#3171: canonical A→B observation inside the existing Core source Sync.

The source entrypoint already holds the sole acquisition/maintenance lock.
This phase reads the *optional* provider rights contract exactly once, records
only authenticated positive source references and verifies local presence.
It never invents a download consent, a receipt, an OriginLink or a provider GET.
Absent legacy support is a no-op, not a second reconciliation subsystem.
"""
from __future__ import annotations

from inspect import getattr_static
import logging
from pathlib import Path
from typing import Any, Callable

from ..models import Account
from ..plugin_api.errors import Cancelled
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from .canonical_publication import _stable
from .convergence_verified_presence import plan_existing_verified_presence
from .rights_snapshot_read import read_account_rights_snapshot

_LOG = logging.getLogger("cifereca")


def persist_attested_source_references(
    storage: Any, snapshot: ProviderRightsSnapshot,
    *, context_current: Callable[[], bool] | None = None,
) -> int:
    """Persist only exact positive A; never infer removal from empty snapshots.

    Provider credentials and upstream accounts must still belong to the same
    enabled Core profile at the instant of SQLite commit. One failing row
    rolls back the entire batch. No artifact/placement/origin is generated.
    """
    if type(snapshot) is not ProviderRightsSnapshot:
        raise ValueError("invalid_canonical_rights_snapshot")
    if context_current is not None and not callable(context_current):
        raise ValueError("invalid_canonical_context_guard")
    if snapshot.status not in ("success", "partial"):
        return 0
    scope = snapshot.scope
    now = __import__("time").time_ns() // 1_000_000_000
    admitted = [
        row for row in snapshot.rights
        if row.provider_attested and row.state == "active"
    ]
    if not admitted:
        return 0
    written = 0
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if context_current is not None and context_current() is not True:
            raise ValueError("canonical_rights_source_rebound")
        binding = db.execute(
            "SELECT identity_id,enabled FROM provider_connections "
            "WHERE provider_id=? AND credential_id=?",
            (scope.provider_id, scope.credential_id),
        ).fetchone()
        accounts = db.execute(
            "SELECT account_id FROM provider_accounts "
            "WHERE identity_id=? AND provider_id=? AND credential_id=?",
            (scope.identity_id, scope.provider_id, scope.credential_id),
        ).fetchall()
        profile = db.execute(
            "SELECT directory_name FROM identities WHERE identity_id=?",
            (scope.identity_id,),
        ).fetchall()
        if (binding != (scope.identity_id, 1)
                or len(profile) != 1
                or type(profile[0][0]) is not str or not profile[0][0]
                or sum(a[0] == scope.account_id for a in accounts) != 1):
            raise ValueError("canonical_rights_core_binding_changed")
        for row in admitted:
            coords = (
                scope.identity_id, scope.provider_id, scope.credential_id,
                scope.account_id, snapshot.branch, row.edition_id, row.right_id,
                row.kind,
            )
            ref = _stable("source", *coords)
            existing = db.execute(
                "SELECT identity_id,provider_id,credential_id,account_id,"
                "branch_id,edition_id,right_id,reference_kind,"
                "state,evidence_kind,evidence_ref,observed_at "
                "FROM source_references WHERE source_reference_id=?", (ref,),
            ).fetchone()
            updated = ("active", row.evidence_kind, row.evidence_ref, row.observed_at)
            if existing is None:
                db.execute(
                    "INSERT INTO source_references("
                    "source_reference_id,identity_id,provider_id,credential_id,"
                    "account_id,branch_id,edition_id,right_id,reference_kind,"
                    "state,synchronizable,evidence_kind,evidence_ref,observed_at,"
                    "created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ref, *coords, "active", 1, row.evidence_kind,
                     row.evidence_ref, row.observed_at, now, now),
                )
                written += 1
            elif existing[:8] != coords:
                raise ValueError("canonical_source_reference_collision")
            elif existing[8:] != updated:
                db.execute(
                    "UPDATE source_references SET state='active',"
                    "evidence_kind=?,evidence_ref=?,observed_at=?,updated_at=? "
                    "WHERE source_reference_id=?",
                    (*updated[1:], now, ref),
                )
                written += 1
        # An account or session may be rebound by another UI task while a
        # multi-right batch is executing. Keep the full batch uncommitted
        # until this final in-memory epoch/registry check. The callback MUST
        # NOT call Storage while storage._lock is held (it is not recursive).
        if context_current is not None and context_current() is not True:
            raise ValueError("canonical_rights_source_rebound")
    return written


def observe_canonical_rights_in_sync(
    service: Any, account: Account, *,
    cancelled: Callable[[], bool] = lambda: False,
    snapshot_sink: Callable[[ProviderRightsSnapshot], None] | None = None,
) -> dict[str, Any] | None:
    """Opt-in rights phase of the *existing* source Sync, no additional loop.

    No adapter can make a v1 catalogue/loan list into exact A. In this
    incremental step all delivery offers remain proposals; automatic
    execution is wired only when Core has a proven delivery & consent route.
    """
    if type(account) is not Account or not callable(cancelled):
        raise ValueError("invalid_canonical_rights_request")
    # Inspect only the already registered session. Resolving through the
    # legacy service method may throw for synthetic/local sources without a
    # remote session; the optional v14 observer must not break their Sync.
    try:
        session = service._sessions.get(
            service._session_key(account.provider_id, account.credential_id)
        )
    except (AttributeError, KeyError, ValueError, TypeError):
        return None
    if session is None or getattr_static(session, "rights_observation_snapshot", None) is None:
        return None
    if service.sync_source_enabled(account) is not True:
        raise ValueError("canonical_rights_source_disabled")
    before_epoch = service._account_context_epoch
    registered_accounts = service.accounts
    registered_sessions = service._sessions
    original_storage = service.storage
    snapshot = read_account_rights_snapshot(service, account, cancelled=cancelled)
    if snapshot.status == "cancelled" or cancelled():
        raise Cancelled("canonical_rights_sync_cancelled")

    def current_context() -> bool:
        """Pure in-memory registry check: safe under the SQLite storage lock."""
        try:
            scope = snapshot.scope
            matches = [
                candidate for candidate in service.accounts
                if type(candidate) is Account and (
                    candidate.identity_id, candidate.provider_id,
                    candidate.credential_id, candidate.unique_id,
                ) == (
                    scope.identity_id, scope.provider_id,
                    scope.credential_id, scope.account_id,
                )
            ]
            return (
                service._account_context_epoch == before_epoch
                and service.accounts is registered_accounts
                and service._sessions is registered_sessions
                and service.storage is original_storage
                and len(matches) == 1 and matches[0] is account
                and service._sessions.get(
                    service._session_key(scope.provider_id, scope.credential_id)
                ) is session
                and getattr(session, "connected", None) is True
                and getattr(session, "_closed", False) is False
                and (
                    account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id,
                ) == (
                    scope.identity_id, scope.provider_id,
                    scope.credential_id, scope.account_id,
                )
            )
        except (AttributeError, TypeError, ValueError, KeyError):
            return False

    if (not current_context()
            or service.sync_source_enabled(account) is not True
            or service.storage.identity_for_connection(
                account.provider_id, account.credential_id,
            ) != account.identity_id):
        raise ValueError("canonical_rights_source_rebound")
    if snapshot.status == "failed":
        return {
            "status": "failed", "observed": 0, "source_references_changed": 0,
            "present_attested": 0, "read_only_plan": True,
            "acquisition_authorized": False,
        }
    if service.storage.schema_version() < 14:
        # No v13 fallback write. Legacy stores remain untouched.
        return {
            "status": "schema_unavailable", "observed": len(snapshot.rights),
            "source_references_changed": 0, "present_attested": 0,
            "read_only_plan": True, "acquisition_authorized": False,
        }
    # Do not promote historical receipts or unknown rights into provider A.
    admitted = sum(
        row.provider_attested and row.state == "active"
        for row in snapshot.rights
    )
    try:
        changed = persist_attested_source_references(
            service.storage, snapshot, context_current=current_context,
        )
    except ValueError as exc:
        if str(exc) != "canonical_rights_core_binding_changed":
            raise
        # Legacy sessions can expose a typed snapshot without having the
        # complete v14 provider_accounts binding. Reject publication of A,
        # but never cancel their independent loan/materialization results.
        _LOG.warning(
            "TRACE canonical rights skipped unbound source provider_id=%s "
            "credential_id=%s account_id=%s",
            account.provider_id, account.credential_id, account.unique_id,
        )
        return {
            "status": "failed", "observed": len(snapshot.rights),
            "attested_active": 0, "source_references_changed": 0,
            "present_attested": 0, "pending": len(snapshot.rights),
            "read_only_plan": True, "acquisition_authorized": False,
        }
    # Every physical presence must be freshly hashed in this profile and
    # linked to the exact already committed origin. Missing coverage is
    # deliberately not converted to an absent file or a GET permission.
    if not current_context():
        raise ValueError("canonical_rights_source_rebound")
    plan = plan_existing_verified_presence(
        service.storage, snapshot, library_root=Path(service.library_dir()),
    )
    if not current_context():
        raise ValueError("canonical_rights_source_rebound")
    if snapshot_sink is not None:
        if not callable(snapshot_sink):
            raise ValueError("invalid_canonical_snapshot_sink")
        # Only Core's existing source Sync receives this in-memory lease.
        # No raw A payload or private delivery URL is stored in Sync facts.
        snapshot_sink(snapshot)
    return {
        "status": snapshot.status, "observed": len(snapshot.rights),
        "attested_active": admitted,
        "source_references_changed": changed,
        "present_attested": sum(d.provider_satisfied for d in plan.decisions),
        "pending": sum(not d.provider_satisfied for d in plan.decisions),
        "read_only_plan": True, "acquisition_authorized": False,
    }


__all__ = [
    "persist_attested_source_references", "observe_canonical_rights_in_sync",
]
