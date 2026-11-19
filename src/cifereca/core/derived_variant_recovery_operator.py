"""#4520: private review then one explicit recovery confirmation.

No startup, plugin or GTK entry point. The caller must already hold the
standard acquisition lock on its worker when executing the confirmation.
Receipt identifiers remain private; no automatic relink or deletion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from .derived_variant_journal import DerivedReservation
from .derived_variant_review import review_pending_derived_publication
from .derived_variant_supervised_recovery import recover_linked_derived_publication
from .library_audit_roots import configured_identity_roots
from .source_context import source_context_epoch
from .local_origin_suggestion_operator import _root, _navigation_epoch


@dataclass(frozen=True, slots=True, repr=False)
class _Scope:
    service: object
    owner: object
    identity_id: str
    directory: str
    storage: object
    database: Path
    config: object
    root: Path
    roots: tuple[Path, ...]
    epoch: int
    navigation: int

    def current(self, db=None):
        try:
            s = self.service
            if not (
                s.current_identity is self.owner
                and s.identity_by_id(self.identity_id) is self.owner
                and self.owner.identity_id == self.identity_id
                and self.owner.directory_name == self.directory
                and s.storage is self.storage and Path(s.storage.path) == self.database
                and s.config is self.config and _root(s.config) == self.root
                and configured_identity_roots(s.config, self.identity_id)[self.identity_id] == self.roots
                and source_context_epoch(s) == self.epoch
                and _navigation_epoch(s) == self.navigation
            ):
                return False
            if db is None:
                return self.storage.exact_profile_directory_binding(self.identity_id) == (self.directory, True)
            # The recovery writer already holds Storage._lock. Never call
            # an ordinary Storage method from this transaction callback.
            return (
                db.in_transaction
                and db.execute("SELECT directory_name FROM identities WHERE identity_id=? LIMIT 2",
                               (self.identity_id,)).fetchall() == [(self.directory,)]
                and db.execute("SELECT identity_id FROM identities WHERE directory_name=? LIMIT 2",
                               (self.directory,)).fetchall() == [(self.identity_id,)]
            )
        except Exception:
            return False


def _capture(service, identity_id):
    epoch, navigation = source_context_epoch(service), _navigation_epoch(service)
    owner = service.current_identity
    if owner is None or owner.identity_id != identity_id:
        return None
    root = _root(service.config)
    directory = owner.directory_name
    roots = configured_identity_roots(service.config, identity_id)[identity_id]
    if type(directory) is not str or not directory or root / directory not in roots:
        return None
    scope = _Scope(service, owner, identity_id, directory, service.storage,
                   Path(service.storage.path), service.config, root, roots, epoch, navigation)
    return scope if scope.current() else None


@dataclass(frozen=True, slots=True, repr=False)
class RecoveryReview:
    scope: _Scope
    reservation: DerivedReservation
    _guard: object = field(default_factory=Lock, compare=False, repr=False)


def review_linked_recovery(service, *, identity_id, reservation, explicitly_requested=False):
    """Return a private review object only for a freshly verified linked intent."""
    if (explicitly_requested is not True or type(identity_id) is not str
            or not 0 < len(identity_id) <= 256 or type(reservation) is not DerivedReservation):
        return None
    try:
        scope = _capture(service, identity_id)
        if scope is None:
            return None
        observation = review_pending_derived_publication(
            scope.storage, library_root=scope.root, identity_id=identity_id,
            reservation=reservation,
        )
        if observation.get("status") != "linked_pending" or not scope.current():
            return None
        return RecoveryReview(scope, reservation)
    except Exception:
        return None


def _refusal():
    return {"status": "review_required", "destructive_cleanup_allowed": False}


def _claim(service, review, confirmed):
    return (confirmed is True and type(review) is RecoveryReview
            and review.scope.service is service
            and review._guard.acquire(blocking=False))


def execute_confirmed_linked_recovery(service, review, *, confirmed=False, shared_lock=None):
    """Consume the private review once; exact receipt and bytes rechecked by Core.

    On any refusal a new review is required. A failed/ambiguous commit may
    already have changed durable state: do not label it as rolled back.
    """
    if not _claim(service, review, confirmed):
        return _refusal()
    return _execute_claimed(review, shared_lock)


def _execute_claimed(review, shared_lock):
    try:
        scope = review.scope
        if not scope.current():
            return _refusal()
        return recover_linked_derived_publication(
            scope.storage, library_root=scope.root, identity_id=scope.identity_id,
            reservation=review.reservation,
            approved_receipt_sha256=review.reservation.receipt_sha256,
            shared_lock=shared_lock, scope_current=scope.current,
        )
    except Exception:
        return _refusal()


def run_confirmed_linked_recovery(service, review, *, confirmed=False):
    """Worker entry point: consume confirmation and acquire existing lock.

    Never create the state directory or truncate a lock file. Missing or
    unsafe lock, contention and revocation require a new operator review.
    POSIX only; this is not a new application-wide lock policy.
    """
    if not _claim(service, review, confirmed):
        return _refusal()
    try:
        if not review.scope.current():
            return _refusal()
        with InterProcessLock(acquisition_lock_file(create=False),
                              existing_nofollow=True) as shared_lock:
            return _execute_claimed(review, shared_lock)
    except Exception:
        return _refusal()
