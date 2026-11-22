"""#7093 isolated broker, not yet registered in the host.

Only projections of the existing Core read/review and dry-run paths.
A fresh instance is required after host revocation.
"""
from secrets import token_hex
from threading import Lock

from ..plugin_api.derived_maintenance import (
    DerivedPendingOverview, DerivedCapacityObservation, DerivedReceiptChoice,
    DerivedReceiptPage,
)
from .derived_variant_maintenance_review import admit_maintenance_review, read_maintenance_review
from .derived_variant_capacity_review import read_capacity_review
from .derived_variant_recovery_selection import (
    recovery_choices, review_recovery_choice, preview_confirmed_recovery_choice,
)
from .library_sqlite_freshness import sqlite_source_stamp
from .derived_variant_recovery_operator import _capture


def _counts(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("invalid_counts")
    if any(type(value[k]) is not int or not 0 <= value[k] <= 2**74 for k in keys):
        raise ValueError("invalid_counts")
    return tuple((k, value[k]) for k in keys)


def _capacity(value):
    if (type(value) is not dict or value["status"] not in ("observed", "partial")
            or any(value[k] is not False for k in (
                "admission_authorized", "recovery_authorized", "quota_refund_authorized",
                "destructive_cleanup_allowed"))):
        return None
    counts = _counts(value["counts"], ("pending", "committed", "unreadable", "unbound",
                                     "active_pending", "active_committed", "other_bound"))
    cap = _counts(value["capacity"], ("records", "payload_bytes", "output_bytes"))
    keys = ("record_count", "payload_bytes", "known_output_bytes",
            "active_payload_bytes", "active_output_bytes")
    numeric = [value[k] for k in keys]
    if any(type(n) is not int or not 0 <= n <= 2**74 for n in numeric):
        return None
    if (type(value["output_bytes_complete"]) is not bool
            or value["saturation"] not in ("below_limits", "at_limit", "unknown")):
        return None
    stock, region, compact = value["stock"], value["region"], value["compact"]
    logical = allocated = None
    if stock["status"] in ("journal_entries_observed", "partial"):
        logical, allocated = (stock["observed_unique_inode_logical_bytes"],
                              stock["observed_unique_inode_allocated_bytes"])
        if any(type(n) is not int or not 0 <= n <= 2**74 for n in (logical, allocated)):
            return None
    region_counts = ()
    if region["status"] == "observed":
        region_counts = _counts(region["counts"], (
            "pending_named", "committed_named", "unrecorded_regular",
            "ambiguous_entries", "unverified_regions", "unobserved_receipts"))
    compact_counts = ()
    if compact["status"] in ("observed", "partial"):
        compact_counts = _counts({k: compact[k] for k in (
            "count", "payload_bytes", "matched", "unpaired", "retired")},
            ("count", "payload_bytes", "matched", "unpaired", "retired"))
        if not 0 <= compact["retired"] <= compact["matched"] <= compact["count"]:
            return None
    return DerivedCapacityObservation(
        value["status"], counts, *numeric[:3], value["output_bytes_complete"],
        *numeric[3:], cap, value["saturation"],
        stock["status"], logical, allocated, region["status"], region_counts,
        compact["status"], compact_counts,
        _counts(compact["capacity"], ("records", "payload_bytes")), compact["saturation"],
    )


class DerivedMaintenanceReadBrokerImpl:
    def __init__(self, service):
        self._service = service
        self._lock = Lock()
        self._generation = 0
        self._revoked = False
        self._active = None
        self._reviewed = None

    def _begin(self):
        with self._lock:
            if self._revoked:
                return None
            self._generation += 1
            self._active = self._reviewed = None
            return self._generation

    def _live(self, generation):
        with self._lock:
            return not self._revoked and generation == self._generation

    def _request(self, identity_id):
        request = admit_maintenance_review(self._service)
        if request is None or request.identity_id != identity_id:
            return None
        return request

    def _observe(self, identity_id, confirmed, reader, projector):
        if confirmed is not True or type(identity_id) is not str:
            return None
        with self._lock:
            if self._revoked:
                return None
            generation = self._generation
        try:
            request = self._request(identity_id)
            if request is None or not self._live(generation):
                return None
            result = projector(reader(self._service, request))
            return result if request.current() and self._live(generation) else None
        except Exception:
            return None

    def overview(self, *, identity_id, confirmed=False):
        def project(value):
            if (type(value) is not dict or value["status"] != "observed"
                    or any(value[k] is not False for k in (
                        "physical_inventory_complete", "recovery_authorized",
                        "destructive_cleanup_allowed"))):
                return None
            counts = dict(_counts(value["counts"], ("linked_pending", "staged_only", "manual_review")))
            if sum(counts.values()) != value["checked"] or not 0 <= value["checked"] <= 4:
                return None
            return DerivedPendingOverview(value["checked"], **counts,
                                          complete_pending_page=value["complete_pending_page"])
        return self._observe(identity_id, confirmed, read_maintenance_review, project)

    def capacity(self, *, identity_id, confirmed=False):
        return self._observe(identity_id, confirmed, read_capacity_review, _capacity)

    def _page(self, identity, previous, scope, stamp, generation):
        if not self._live(generation) or not scope.current():
            return None
        page = recovery_choices(self._service, identity_id=identity,
                                explicitly_requested=True, previous=previous)
        if (page is None or page.scope != scope or not scope.current()
                or sqlite_source_stamp(scope.database) != stamp):
            return None
        public = DerivedReceiptPage(token_hex(32), tuple(
            DerivedReceiptChoice(token_hex(32), choice.parent_relative,
                                 choice.target_relative, choice.output_size, choice.media_format)
            for choice in page.choices), page.next_cursor is not None, page.complete)
        with self._lock:
            if self._revoked or self._generation != generation:
                return None
            self._active = (page, public, stamp)
        return public

    def list_receipts(self, *, identity_id, confirmed=False):
        if confirmed is not True or type(identity_id) is not str:
            return None
        generation = self._begin()
        if generation is None:
            return None
        try:
            scope = _capture(self._service, identity_id)
            if scope is None:
                return None
            stamp = sqlite_source_stamp(scope.database)
            return self._page(identity_id, None, scope, stamp, generation) if stamp else None
        except Exception:
            return None

    def next_page(self, *, token, confirmed=False):
        if confirmed is not True or type(token) is not str:
            return None
        with self._lock:
            if self._revoked or self._active is None:
                return None
            page, public, stamp = self._active
            if public.token != token or not public.has_more:
                return None
            self._generation += 1
            generation = self._generation
            self._active = self._reviewed = None
        try:
            if sqlite_source_stamp(page.scope.database) != stamp:
                return None
            return self._page(page.scope.identity_id, page, page.scope, stamp, generation)
        except Exception:
            return None

    def verify(self, *, token, confirmed=False):
        if confirmed is not True or type(token) is not str:
            return None
        with self._lock:
            if self._revoked or self._active is None:
                return None
            page, public, stamp = self._active
            choice = next((c for c, p in zip(page.choices, public.choices) if p.token == token), None)
            if choice is None:
                return None
            self._generation += 1
            generation = self._generation
            self._reviewed = None
        try:
            if not page.scope.current() or sqlite_source_stamp(page.scope.database) != stamp:
                return None
            result = review_recovery_choice(self._service, page, choice, explicitly_requested=True)
            if result is None or not page.scope.current() or sqlite_source_stamp(page.scope.database) != stamp:
                return None
            handle = token_hex(32)
            with self._lock:
                if self._revoked or generation != self._generation:
                    return None
                self._reviewed = (handle, result)
                return handle
        except Exception:
            return None

    def simulate(self, *, token, confirmed=False):
        if confirmed is not True or type(token) is not str:
            return "review_required"
        with self._lock:
            if (self._revoked or self._active is None or self._reviewed is None
                    or self._reviewed[0] != token):
                return "review_required"
            result = self._reviewed[1]
            page, _, stamp = self._active
            self._reviewed = None
            generation = self._generation
        try:
            if not page.scope.current() or sqlite_source_stamp(page.scope.database) != stamp:
                return "review_required"
            verdict = preview_confirmed_recovery_choice(self._service, page, result, confirmed=True)
            if (verdict == "verified_no_write" and page.scope.current()
                    and sqlite_source_stamp(page.scope.database) == stamp and self._live(generation)):
                return verdict
        except Exception:
            pass
        return "review_required"

    def cancel(self, *, token):
        with self._lock:
            if type(token) is str and (
                    (self._active is not None and self._active[1].token == token)
                    or (self._reviewed is not None and self._reviewed[0] == token)):
                self._generation += 1
                self._active = self._reviewed = None

    def revoke(self):
        with self._lock:
            self._revoked = True
            self._generation += 1
            self._active = self._reviewed = None
