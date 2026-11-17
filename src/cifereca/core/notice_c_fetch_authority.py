"""#4982: one-shot public fetch authorization, NOT a transport implementation.

A durable fetch job is never itself consent. An explicitly admitted Core
producer must supply its actual current policy/session authority and an
exact, declared transport implementation. No provider discovery, endpoint,
URL, private scope, network call, timer or automatic dispatcher here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import RLock
import time
from typing import Callable
from uuid import uuid4

from . import notice_c_job_journal as journal
from .notice_c_raw_input_receipt import _source_state
from .notice_cover_repository import PersistedNoticeCover

_MAX_GRANTS = 32
_MAX_TTL = 60


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class PublicFetchPermit:
    """Process-local one-use dispatch reservation, not a transport capability."""
    lease: journal.JobLease
    source: PersistedNoticeCover
    transport_digest: str
    implementation_digest: str
    admitted_at: int


@dataclass(slots=True, repr=False)
class _Grant:
    ref: journal.JobRef
    spec: journal.JobSpec
    source: PersistedNoticeCover
    source_digest: str
    transport_digest: str
    deadline: int
    hold_authority: Callable
    permit: PublicFetchPermit | None = None


class PublicFetchAuthority:
    """Bounded ephemeral authority owned by a trusted Core composition.

    The provider's explicit authorization guard must pin the actual current
    consent/policy, connected anonymous session and declared transport.
    A boolean supplied by a plugin/result is not authority. The guard is
    called OUTSIDE Storage, in lock order authority -> registry -> Storage.
    It must not access Storage, perform HTTP, parse, or wait on workers.
    The owner must revoke its guard on account/config/session/policy changes.

    No authority survives restart. Pending durable rows cannot resurrect
    grants, nor may retries repeat a consumed transport reservation.
    """
    def __init__(self, storage, *, clock=None):
        if clock is not None and not callable(clock):
            raise ValueError("notice_fetch_invalid_clock")
        self._storage = storage
        self._clock = clock if clock is not None else lambda: int(time.time())
        self._lock = RLock()
        self._grants = {}
        self._closed = False

    def _now(self):
        now = self._clock()
        if not journal._integer(now, minimum=1):
            raise ValueError("notice_fetch_invalid_clock")
        return now

    def _prune(self, now):
        for ref in tuple(self._grants):
            if self._grants[ref].deadline <= now:
                self._grants.pop(ref)

    def request(self, source, *, transport_digest, implementation_digest,
                hold_authority, ttl_seconds, expected_generation):
        """Explicit trusted producer action, not a scheduler or GTK hint.

        Only an already admitted, complete public source qualifies in this
        slice. The selected exact transport must already exist; an unknown
        source, cache MISS, or missing URL never manufactures permission.
        A distinct explicit request gets a fresh nonce and supersedes its
        old generation. No private authority or nonce is serialized.
        """
        if (type(source) is not PersistedNoticeCover
                or source.observation.key.visibility != "public"
                or source.observation.parser_id != "core.search.public-cover"
                or source.observation.parser_version != "2"
                or not journal._digest(transport_digest)
                or not journal._digest(implementation_digest)
                or not callable(hold_authority)
                or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL):
            raise ValueError("notice_fetch_invalid_authority")
        with hold_authority() as allowed:
            if allowed is not True:
                raise ValueError("notice_fetch_authority_revoked")
            with self._lock:
                now = self._now()
                self._prune(now)
                if self._closed or now > journal._MAX - ttl_seconds:
                    raise ValueError("notice_fetch_authority_unavailable")
                replaced = [ref for ref, grant in self._grants.items()
                            if grant.source.document == source.document]
                if len(self._grants) - len(replaced) >= _MAX_GRANTS:
                    raise ValueError("notice_fetch_authority_capacity")
                with self._storage._lock, self._storage._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    current, state = _source_state(db, source.document)
                    if current != source or state is None:
                        raise ValueError("notice_fetch_source_changed")
                    wire = json.dumps(
                        ["cifereca.notice-c.fetch-grant.v1", source.document.source_key,
                         state, transport_digest, implementation_digest, uuid4().hex],
                        ensure_ascii=True, separators=(",", ":"),
                    ).encode("ascii")
                    spec = journal.public_job_spec(
                        "fetch_source_notice", source.document,
                        input_digest=sha256(wire).hexdigest(),
                        implementation_digest=implementation_digest,
                    )
                    ref = journal._request_job_txn(
                        db, spec, now=now, expected_generation=expected_generation,
                    )
                for old in replaced:
                    self._grants.pop(old, None)
                self._grants[ref] = _Grant(
                    ref, spec, source, state, transport_digest, now + ttl_seconds,
                    hold_authority,
                )
                return ref

    def _lookup(self, ref):
        now = self._now()
        self._prune(now)
        grant = self._grants.get(ref)
        if self._closed or grant is None:
            raise ValueError("notice_fetch_authority_unavailable")
        return grant

    def _verify_txn(self, db, grant, lease, now):
        if not db.in_transaction:
            raise ValueError("notice_fetch_transaction_required")
        if now >= grant.deadline:
            raise ValueError("notice_fetch_authority_unavailable")
        job = journal._held(db, lease, now)
        if job.ref != grant.ref or job.spec != grant.spec:
            raise ValueError("notice_fetch_generation_changed")
        current, state = _source_state(db, grant.source.document)
        if current != grant.source or state != grant.source_digest:
            raise ValueError("notice_fetch_source_changed")
        return current

    @contextmanager
    def dispatch_admission(self, lease, *, transport_digest, implementation_digest):
        """Linearize ONE transport admission against current live authority.

        A worker must already hold its admitted durable job lease. The
        eventual provider adapter must reserve its existing transport while
        inside this guard; execute HTTP outside the guard and Storage.
        No network dispatch happens in this method. In particular a permit
        retained after the guard is NOT an ongoing grant to start a GET.
        Actual transport admission must honor the provider's own generation.
        """
        if type(lease) is not journal.JobLease:
            raise ValueError("notice_fetch_invalid_lease")
        with self._lock:
            grant = self._lookup(lease.ref)
        with grant.hold_authority() as allowed:
            if allowed is not True:
                raise ValueError("notice_fetch_authority_revoked")
            with self._lock:
                if self._lookup(lease.ref) is not grant or grant.permit is not None:
                    raise ValueError("notice_fetch_already_consumed")
                if (transport_digest != grant.transport_digest
                        or implementation_digest != grant.spec.implementation_digest):
                    raise ValueError("notice_fetch_transport_changed")
                now = self._now()
                with self._storage._lock, self._storage._connect() as db:
                    db.execute("BEGIN")
                    source = self._verify_txn(db, grant, lease, now)
                permit = PublicFetchPermit(
                    lease, source, transport_digest, implementation_digest, now,
                )
                # Consume before granting transport admission. On any later
                # failure, including ambiguous dispatch, never retry the GET
                # under this authorization. A fresh explicit action is needed.
                grant.permit = permit
                yield permit

    @contextmanager
    def publication_guard(self, permit):
        """Pin the same authority until the caller's transaction commits.

        Yields a verifier to invoke BEFORE any source write in the existing
        canonical publication transaction. The caller must also complete
        the exact journal lease in that same transaction. No writer or
        transport is supplied here. Consumed grant is removed on exit even
        when the write fails; no implicit retry of supplier I/O.
        """
        if type(permit) is not PublicFetchPermit:
            raise ValueError("notice_fetch_invalid_permit")
        with self._lock:
            grant = self._lookup(permit.lease.ref)
            if grant.permit is not permit:
                raise ValueError("notice_fetch_invalid_permit")
        try:
            with grant.hold_authority() as allowed:
                if allowed is not True:
                    raise ValueError("notice_fetch_authority_revoked")
                with self._lock:
                    if (self._lookup(permit.lease.ref) is not grant
                            or grant.permit is not permit):
                        raise ValueError("notice_fetch_invalid_permit")
                    def verify(db):
                        return self._verify_txn(db, grant, permit.lease, self._now())
                    yield verify
        finally:
            with self._lock:
                if self._grants.get(permit.lease.ref) is grant:
                    self._grants.pop(permit.lease.ref)

    def revoke(self, ref):
        journal._check_ref(ref)
        with self._lock:
            return self._grants.pop(ref, None) is not None

    def close(self):
        with self._lock:
            self._closed = True
            self._grants.clear()
