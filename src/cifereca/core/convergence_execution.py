"""#3171: ONE Core A→B executor, for a Core-admitted snapshot under Sync lock.

This is an internal orchestration primitive, not another broker, reconciliation
tool or new permission for providers. The installed Sync must supply its
already-held acquisition lock, the account/session/epoch that admitted A,
and the prior persisted per-source acquisition consent. Provider adapters only
deliver bytes after a positive scoped offer; they never write Core SQLite.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import sqlite3
from pathlib import Path
from typing import Callable, Literal

from ..locking import InterProcessLock
from ..models import Account
from ..paths import acquisition_lock_file
from ..plugin_api.errors import Cancelled
from ..plugin_api.provider_rights_observation import (
    ProviderRightObservation, ProviderRightsSnapshot,
)
from .canonical_publication import publish_completed_acquisition
from .canonical_recovery import (
    mark_completed_delivery_published,
    record_completed_delivery_for_recovery,
    recover_completed_delivery,
)
from .convergence_verified_presence import plan_existing_verified_presence
from .source_convergence import SourceKey

_LOG = logging.getLogger("cifereca")


@dataclass(frozen=True, slots=True)
class CompletedDelivery:
    """Final bytes explicitly bound to the exact admitted right and offer.

    The receipt is issued after completed delivery, not copied from an A
    observation or reconstructed by finding a same-title local file.
    """
    source: SourceKey
    delivery_offer_id: str
    path: Path
    final_sha256: str
    receipt_ref: str
    adept_receipt_ref: str = ""


OutcomeStatus = Literal[
    "present_attested", "downloaded", "recovered", "pending", "blocked", "failed", "cancelled",
]


@dataclass(frozen=True, slots=True)
class ConvergenceOutcome:
    source: SourceKey
    status: OutcomeStatus
    reason: str
    artifact_id: str = ""


def execute_admitted_convergence(
    service,
    account: Account,
    session: object,
    snapshot: ProviderRightsSnapshot,
    *,
    admitted_epoch: int,
    shared_lock: InterProcessLock,
    library_root: Path,
    persisted_consent: frozenset[SourceKey],
    consent_still_current: Callable[[SourceKey], bool],
    deliver: Callable[[SourceKey, ProviderRightObservation], CompletedDelivery],
    cancelled: Callable[[], bool] = lambda: False,
    publication_guard: Callable[[sqlite3.Connection, SourceKey], bool] | None = None,
) -> tuple[ConvergenceOutcome, ...]:
    """Execute the authorized part of the same Sync; no second remote read.

    This method is deliberately inaccessible from plugin_api. Its Core caller
    must already have obtained the *single* typed snapshot from the selected
    session and admitted the exact offer/consent under the same lock. An offer
    in A alone is never authorization, and an uncertain local bibliographic
    candidate never vetoes an otherwise authorized acquisition.
    """
    if (
        type(account) is not Account
        or type(snapshot) is not ProviderRightsSnapshot
        or type(admitted_epoch) is not int
        or type(persisted_consent) is not frozenset
        or any(type(key) is not SourceKey for key in persisted_consent)
        or not callable(deliver) or not callable(cancelled)
        or not callable(consent_still_current)
        or (publication_guard is not None and not callable(publication_guard))
        or not isinstance(library_root, Path)
        or type(shared_lock) is not InterProcessLock
        or shared_lock._handle is None
        or shared_lock.path.resolve() != acquisition_lock_file().resolve()
    ):
        raise ValueError("convergence_core_admission_required")

    scope = snapshot.scope
    if (account.identity_id, account.provider_id, account.credential_id,
            account.unique_id) != (
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id,
            ):
        raise ValueError("convergence_source_scope_mismatch")

    try:
        registered_accounts = service.accounts
        sessions = service._sessions
        initial_storage = service.storage
    except AttributeError as exc:
        raise ValueError("convergence_core_context_required") from exc

    def current_memory() -> bool:
        """No database I/O: safe inside canonical publication's SQL lock."""
        try:
            if (service._account_context_epoch != admitted_epoch
                    or service.accounts is not registered_accounts
                    or service._sessions is not sessions
                    or service.storage is not initial_storage):
                return False
            if (account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id) != (
                        scope.identity_id, scope.provider_id,
                        scope.credential_id, scope.account_id):
                return False
            matching_accounts = [
                a for a in service.accounts if type(a) is Account
                and (a.identity_id, a.provider_id, a.credential_id, a.unique_id)
                == (scope.identity_id, scope.provider_id,
                    scope.credential_id, scope.account_id)
            ]
            if len(matching_accounts) != 1 or matching_accounts[0] is not account:
                return False
            if sum(
                candidate is session for candidate in sessions.values()
                if getattr(candidate, "provider_id", None) == scope.provider_id
                and getattr(candidate, "credential_id", None) == scope.credential_id
            ) != 1:
                return False
            return (
                service._sessions.get(service._session_key(
                    scope.provider_id, scope.credential_id,
                )) is session
                and getattr(session, "_closed", False) is False
                and getattr(session, "connected", None) is True
                and getattr(session, "provider_id", None) == scope.provider_id
                and getattr(session, "credential_id", None) == scope.credential_id
            )
        except (AttributeError, TypeError, ValueError, KeyError):
            return False

    def current() -> bool:
        """Check active SQL credential and Core source policy before I/O."""
        if not current_memory():
            return False
        try:
            if (service.storage.identity_for_connection(
                    scope.provider_id, scope.credential_id,
            ) != scope.identity_id
                    or service.sync_source_enabled(account) is not True):
                return False
            connections = service.storage.provider_connections(
                identity_id=scope.identity_id,
            )
            return sum(
                conn.get("provider_id") == scope.provider_id
                and conn.get("credential_id") == scope.credential_id
                and conn.get("enabled") is True
                for conn in connections
            ) == 1
        except (AttributeError, TypeError, ValueError, KeyError):
            return False

    def live_consent(key: SourceKey) -> bool:
        try:
            return consent_still_current(key) is True
        except Exception:
            # Revoked/malformed/unavailable consent state never grants GET.
            _LOG.exception(
                "TRACE convergence consent lookup failed provider_id=%s "
                "credential_id=%s right_id=%s",
                key.scope.provider_id, key.scope.credential_id, key.right_id,
            )
            return False

    if cancelled():
        raise Cancelled("convergence_cancelled_before_admission")
    if not current():
        raise ValueError("convergence_session_rebound")

    # Neither the Core presence probe nor a partially observed provider
    # inventory may assert local absence. Only a valid provider OriginLink
    # paired with the currently hashed target placement is satisfied.
    plan = plan_existing_verified_presence(
        initial_storage, snapshot, library_root=library_root,
    )
    plan_by_source = {decision.source: decision for decision in plan.decisions}
    results: list[ConvergenceOutcome] = []
    for row in snapshot.rights:
        key = SourceKey(
            scope, row.edition_id, row.right_id, row.kind, snapshot.branch,
        )
        decision = plan_by_source[key]
        if cancelled():
            results.append(ConvergenceOutcome(key, "cancelled", "cancelled"))
            break
        if not current():
            results.append(ConvergenceOutcome(key, "blocked", "scope_rebound"))
            break
        if decision.provider_satisfied:
            results.append(
                ConvergenceOutcome(key, "present_attested", "exact_verified_origin")
            )
            continue

        # A stale/unknown right, unsigned delivery, missing configured
        # consent or disabled source can never start a file GET or ADEPT.
        if (snapshot.status not in ("success", "partial")
                or row.state != "active" or not row.provider_attested):
            results.append(ConvergenceOutcome(key, "pending", "right_unconfirmed"))
            continue
        if row.delivery != "available" or not row.delivery_offer_id:
            results.append(ConvergenceOutcome(key, "pending", "delivery_unconfirmed"))
            continue
        if key not in persisted_consent or not live_consent(key):
            results.append(ConvergenceOutcome(key, "pending", "consent_unconfigured"))
            continue

        try:
            completed = recover_completed_delivery(
                initial_storage, source=key, offer_id=row.delivery_offer_id,
                library_root=library_root, shared_lock=shared_lock,
            )
            # A receipt copied verbatim from an inventory observation is
            # not independent evidence of a completed GET. Reject it before
            # journaling, otherwise a malformed record could be replayed
            # forever instead of permitting a genuinely new delivery.
            if completed is not None and completed.receipt_ref == row.evidence_ref:
                completed = None
            was_recovered = completed is not None
            if completed is None:
                completed = deliver(key, row)
            if (type(completed) is not CompletedDelivery
                    or completed.source != key
                    or completed.delivery_offer_id != row.delivery_offer_id
                    or type(completed.receipt_ref) is not str
                    or not completed.receipt_ref
                    or completed.receipt_ref == row.evidence_ref):
                raise ValueError("invalid_completed_delivery_binding")
            # The adapter can have replaced an account, credential, session or
            # switched identity during its GET. Never publish under its old A.
            if cancelled():
                results.append(ConvergenceOutcome(key, "cancelled", "cancelled_after_get"))
                break
            if not current():
                results.append(ConvergenceOutcome(key, "blocked", "scope_rebound_after_get"))
                break
            # Revocation during a remote GET cannot be circumvented by the
            # earlier in-memory frozen consent snapshot. The Core caller owns
            # this lookup into its persisted, exact right-level preference.
            if (key not in persisted_consent or
                    not live_consent(key)):
                results.append(ConvergenceOutcome(key, "blocked", "consent_revoked_after_get"))
                break
            # Persist a *completed* delivery receipt before any canonical B
            # mutation. If publication crashes after this durable handoff,
            # retry the same authorized Sync without re-downloading.
            if not was_recovered:
                record_completed_delivery_for_recovery(
                    initial_storage, source=key,
                    offer_id=row.delivery_offer_id, completed=completed,
                    library_root=library_root, shared_lock=shared_lock,
                )
            if cancelled():
                results.append(ConvergenceOutcome(key, "cancelled", "cancelled_after_receipt"))
                break
            if not current() or not live_consent(key):
                results.append(ConvergenceOutcome(key, "blocked", "scope_or_consent_after_receipt"))
                break
            published = publish_completed_acquisition(
                initial_storage,
                library_root=library_root,
                path=completed.path,
                source=key,
                snapshot=snapshot,
                final_sha256=completed.final_sha256,
                delivery_receipt_ref=completed.receipt_ref,
                adept_receipt_ref=completed.adept_receipt_ref,
                context_current=current_memory,
                transaction_guard=(
                    (lambda db: publication_guard(db, key))
                    if publication_guard is not None else None
                ),
            )
            try:
                mark_completed_delivery_published(
                    initial_storage, source=key, offer_id=row.delivery_offer_id,
                    artifact_id=published.artifact_id, shared_lock=shared_lock,
                )
            except Exception:
                # The provider OriginLink and verified Placement, NOT the
                # operation journal, are authoritative after commit.
                _LOG.exception(
                    "TRACE canonical intake state update failed provider_id=%s "
                    "right_id=%s", scope.provider_id, key.right_id,
                )
            results.append(ConvergenceOutcome(
                key,
                "recovered" if was_recovered else (
                    "downloaded" if published.changed else "present_attested"
                ),
                "canonical_delivery_recovered" if was_recovered
                else "canonical_delivery_committed",
                published.artifact_id,
            ))
        except Cancelled:
            results.append(ConvergenceOutcome(key, "cancelled", "delivery_cancelled"))
            break
        except Exception:
            # Journal retains the unmodified exception and traceback; the
            # result code itself is bounded and not a fabricated receipt.
            _LOG.exception(
                "TRACE convergence delivery failed provider_id=%s "
                "credential_id=%s account_id=%s right_id=%s",
                scope.provider_id, scope.credential_id, scope.account_id, key.right_id,
            )
            results.append(ConvergenceOutcome(key, "failed", "delivery_or_commit_failed"))
    return tuple(results)
