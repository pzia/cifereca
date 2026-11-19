"""#4520: bounded, private read-only Maintenance overview."""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from .derived_variant_recovery_operator import _capture
from .derived_variant_review_queue import pending_derived_review_page
from .derived_variant_review import review_pending_derived_publication
from .source_context import source_context_epoch

_LIMIT = 4
_SECONDS = 3


@dataclass(frozen=True, slots=True, repr=False)
class MaintenanceReviewRequest:
    service: object
    owner: object
    identity_id: str
    storage: object
    source_epoch: int
    account_epoch: int
    config: object

    def current(self):
        """In-memory guard suitable for GTK delivery; no file or SQL access."""
        try:
            s = self.service
            return (
                s.current_identity is self.owner
                and self.owner.identity_id == self.identity_id
                and s.storage is self.storage and s.config is self.config
                and source_context_epoch(s) == self.source_epoch
                and s._account_context_epoch == self.account_epoch
            )
        except Exception:
            return False


def admit_maintenance_review(service):
    try:
        owner = service.current_identity
        identity = owner.identity_id
        epoch = service._account_context_epoch
        if type(identity) is not str or not identity or type(epoch) is not int or epoch < 0:
            return None
        request = MaintenanceReviewRequest(service, owner, identity, service.storage,
                                           source_context_epoch(service), epoch, service.config)
        return request if request.current() else None
    except Exception:
        return None


def read_maintenance_review(service, request):
    """Inspect at most four current pending receipts, on a worker only.

    Deadline is cooperative between receipts; not a hard bound on I/O.
    No authority or opaque handle is returned to the display.
    """
    if (type(request) is not MaintenanceReviewRequest or request.service is not service
            or not request.current()):
        return None
    try:
        scope = _capture(service, request.identity_id)
        if scope is None or not request.current():
            return None
        page = pending_derived_review_page(scope.storage, identity_id=scope.identity_id,
                                          limit=_LIMIT)
        counts = dict.fromkeys(("linked_pending", "staged_only", "manual_review"), 0)
        deadline = monotonic() + _SECONDS
        checked = 0
        for reservation in page["reservations"]:
            if monotonic() >= deadline:
                break
            if not request.current() or not scope.current():
                return None
            outcome = review_pending_derived_publication(
                scope.storage, library_root=scope.root, identity_id=scope.identity_id,
                reservation=reservation,
            )
            category = outcome.get("status")
            counts[category if category in counts else "manual_review"] += 1
            checked += 1
        # Compare the exact receipt page again. This is not a global snapshot
        # of the filesystem; each observation refers to its review instant.
        repeated = pending_derived_review_page(scope.storage, identity_id=scope.identity_id,
                                               limit=_LIMIT)
        if (repeated != page or not scope.current() or not request.current()):
            return None
        return {
            "status": "observed",
            "counts": counts, "checked": checked,
            "complete_pending_page": page["complete"] and checked == len(page["reservations"]),
            "physical_inventory_complete": False, "recovery_authorized": False,
            "destructive_cleanup_allowed": False,
        }
    except Exception:
        return None
