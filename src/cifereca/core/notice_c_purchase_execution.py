"""Core-only, process-local admission for durable purchase intent.

The existing work broker owns execution and the journal owns the only queue.
An intent alone is never a GET grant. A trusted composition supplies an exact
same-read candidate, live context guard and qualified execution port. No
transport, supplier discovery, automatic registration or timer here.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock, get_ident

from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from ..plugin_api.provider_purchase_enrichment import PurchaseEnrichmentCandidate
from . import notice_c_job_journal as journal
from .notice_c_purchase_intent import _allowed_txn, _spec_txn
from .notice_c_work_bridge import PreparedSource, UnavailableNoticeWork

_MAX_LIVE = 32
_MAX_TTL = 300


@dataclass(slots=True, repr=False)
class _Admission:
    snapshot: object
    candidate: object
    spec: journal.JobSpec
    hold_context: object
    execute: object
    deadline: int
    lease: journal.JobLease | None = None
    preparing: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class PreparedPurchaseSource:
    """Trusted Core writer receives the exact lease, never a reconstructed one."""
    publish: object
    hold_input: object


class PurchaseIntentExecution:
    """Short-lived reattestation, not a second job store.

    hold_context pins policy/session/config generation (no IO, Storage or
    waiting on workers); the guard order is context -> registry -> Storage.
    execute is a trusted Core composition, NOT a plugin callback. It receives
    a checked candidate and a guard to invoke immediately before any GET
    and after response, and returns a canonical PreparedSource. It must
    arrange its own qualified transport start fence and never retry blindly.
    """

    def __init__(self, storage, *, clock):
        if not callable(clock):
            raise ValueError("notice_purchase_invalid_clock")
        self._storage = storage
        self._clock = clock
        self._lock = RLock()
        self._live = {}
        self._closed = False

    def _now(self):
        now = self._clock()
        if not journal._integer(now, minimum=1):
            raise ValueError("notice_purchase_invalid_clock")
        return now

    def _prune(self, now):
        for ref in tuple(self._live):
            if self._live[ref].deadline <= now:
                self._live.pop(ref)

    def _lookup(self, ref, now):
        self._prune(now)
        if self._closed or ref not in self._live:
            raise ValueError("notice_purchase_admission_unavailable")
        return self._live[ref]

    def _verify(self, db, job, entry):
        if (job.spec != entry.spec
                or not _allowed_txn(db, entry.snapshot.scope)
                or _spec_txn(db, entry.snapshot, entry.candidate) != entry.spec):
            raise ValueError("notice_purchase_admission_changed")

    def attest(self, ref, snapshot, candidate, *, hold_context, execute, ttl_seconds):
        """Bind a pending generation to current A; no lease or task submission."""
        journal._check_ref(ref)
        if (type(snapshot) is not ProviderRightsSnapshot
                or snapshot.status not in ("success", "partial") or snapshot.resume_cursor
                or type(candidate) is not PurchaseEnrichmentCandidate
                or not callable(hold_context) or not callable(execute)
                or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL):
            raise ValueError("notice_purchase_invalid_admission")
        snapshot, candidate = deepcopy(snapshot), deepcopy(candidate)
        with hold_context() as allowed:
            if allowed is not True:
                raise ValueError("notice_purchase_admission_changed")
            with self._lock:
                now = self._now()
                self._prune(now)
                if self._closed or now > journal._MAX - ttl_seconds:
                    raise ValueError("notice_purchase_admission_unavailable")
                if ref in self._live:
                    # A live admission is never replaced/extended in place.
                    return False
                if len(self._live) >= _MAX_LIVE:
                    raise ValueError("notice_purchase_admission_capacity")
                with self._storage._lock, self._storage._connect() as db:
                    db.execute("BEGIN")
                    job = journal._get(db, ref)
                    if job.state != "pending" or job.lease_until > now:
                        return False
                    if job.spec.implementation_digest != journal._PURCHASE_INTENT_DIGEST:
                        raise ValueError("notice_purchase_invalid_admission")
                    entry = _Admission(snapshot, candidate, job.spec, hold_context,
                                       execute, now + ttl_seconds)
                    self._verify(db, job, entry)
                self._live[ref] = entry
                return True

    def ready_refs(self):
        """Opaque, bounded scheduling hints only; never a lease or callback."""
        with self._lock:
            self._prune(self._now())
            if self._closed:
                return ()
            return tuple(sorted(
                (ref for ref, entry in self._live.items() if entry.lease is None),
                key=lambda ref: (ref.key, ref.generation),
            ))

    def revoke(self, ref):
        with self._lock:
            self._live.pop(ref, None)

    def close(self):
        with self._lock:
            self._closed = True
            self._live.clear()

    def claim(self, ref, *, now, lease_seconds):
        """Only after admission by the existing Core broker."""
        with self._lock:
            try:
                entry = self._lookup(ref, self._now())
            except ValueError:
                return None
        with entry.hold_context() as allowed:
            if allowed is not True:
                self.revoke(ref)
                return None
            with self._lock:
                actual = self._now()
                if self._lookup(ref, actual) is not entry or entry.lease is not None:
                    return None
                if not journal._integer(now, minimum=1) or now > actual:
                    raise ValueError("notice_purchase_invalid_clock")
                with self._storage._lock, self._storage._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    job = journal._get(db, ref)
                    self._verify(db, job, entry)
                    lease = journal._claim_ready_txn(
                        db, job, now=actual, lease_seconds=lease_seconds,
                    )
                if lease is not None:
                    entry.lease = lease
                return lease

    def prepare(self, spec, lease, control):
        """Recheck before producer work and on the same final writer transaction."""
        with self._lock:
            entry = self._lookup(lease.ref, self._now())
            if entry.lease != lease or entry.spec != spec or entry.preparing:
                raise UnavailableNoticeWork("notice_purchase_admission_changed")
            entry.preparing = True

        def verify_held(db):
            now = self._now()
            if self._lookup(lease.ref, now) is not entry:
                raise ValueError("notice_purchase_admission_changed")
            self._verify(db, journal._held(db, lease, now), entry)

        def check_authority():
            control.check_cancelled()
            try:
                with entry.hold_context() as allowed:
                    if allowed is not True:
                        return False
                    with self._lock, self._storage._lock, self._storage._connect() as db:
                        db.execute("BEGIN")
                        verify_held(db)
                return True
            except ValueError:
                return False

        try:
            if not check_authority():
                raise UnavailableNoticeWork("notice_purchase_admission_changed")
            prepared = entry.execute(deepcopy(entry.candidate), check_authority, control)
            if (type(prepared) not in (PreparedSource, PreparedPurchaseSource)
                    or not callable(prepared.publish)
                    or not callable(prepared.hold_input) or not check_authority()):
                raise UnavailableNoticeWork("notice_purchase_invalid_publication")
        except BaseException:
            self.revoke(lease.ref)
            raise

        owner = None
        used = False

        @contextmanager
        def hold_input():
            nonlocal owner, used
            if used:
                raise UnavailableNoticeWork("notice_purchase_publication_consumed")
            used = True
            try:
                with entry.hold_context() as allowed:
                    if allowed is not True:
                        raise UnavailableNoticeWork("notice_purchase_admission_changed")
                    with self._lock, prepared.hold_input():
                        if self._lookup(lease.ref, self._now()) is not entry:
                            raise UnavailableNoticeWork("notice_purchase_admission_changed")
                        owner = get_ident()
                        yield
            finally:
                owner = None
                self.revoke(lease.ref)

        def publish(db):
            if owner != get_ident() or not db.in_transaction:
                raise UnavailableNoticeWork("notice_purchase_publication_guard_required")
            verify_held(db)
            result = (prepared.publish(db, lease) if type(prepared) is PreparedPurchaseSource
                      else prepared.publish(db))
            if result is not True:
                return False
            verify_held(db)
            return True

        return PreparedSource(publish, hold_input)
