"""#4982 opt-in worker adapter for a DECLARED one-shot fetch transport.

No concrete provider is registered here. The existing task broker must
admit the worker and claim the journal lease before calling prepare. A
transport reservation is ephemeral and may never outlive its authority.
A supplier-specific qualification must demonstrate actual start fencing,
bounded IO and the origin of the returned observation before registration.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

from . import notice_c_job_journal as journal
from .notice_c_fetch_authority import PublicFetchAuthority, PublicFetchPermit
from .notice_c_fetch_publication import prepare_public_fetch_publication
from .notice_c_work_bridge import PreparedSource, UnavailableNoticeWork
from .notice_source_observation import _id


@dataclass(frozen=True, slots=True, repr=False)
class ReservedPublicFetch:
    permit: PublicFetchPermit
    # Must check its actual provider transport/session generation at start.
    # No reconnect, discovery, implicit retry or cached DTO promotion.
    execute: Callable
    # Idempotent release of the established transport reservation, including
    # when no dispatch occurred; must not perform networking or raise.
    release: Callable
    # Pin the successful producer generation through the final transaction.
    # No I/O or Storage access; acquired after the Core authority guard.
    hold_publication: Callable


@dataclass(frozen=True, slots=True, repr=False)
class PublicFetchPort:
    provider_id: str
    transport_digest: str
    implementation_digest: str
    # Trusted adapter to the provider's existing admission. Nonblocking,
    # no HTTP, SQLite, parsing or worker wait while acquiring reservation.
    # Return ReservedPublicFetch or None (unavailable).
    reserve: Callable


class PublicFetchExecution:
    """Explicit composition of authority and qualified ports (empty by default)."""

    def __init__(self, authority, ports=()):
        if (type(authority) is not PublicFetchAuthority
                or type(ports) is not tuple or len(ports) > 4):
            raise ValueError("notice_fetch_invalid_registry")
        keys = set()
        for port in ports:
            if (type(port) is not PublicFetchPort
                    or not _id(port.provider_id)
                    or not journal._digest(port.transport_digest)
                    or not journal._digest(port.implementation_digest)
                    or not callable(port.reserve)):
                raise ValueError("notice_fetch_invalid_registry")
            key = (port.provider_id, port.transport_digest, port.implementation_digest)
            if key in keys:
                raise ValueError("notice_fetch_ambiguous_registry")
            keys.add(key)
        self._authority = authority
        self._ports = ports

    def supported(self):
        return tuple(sorted({("fetch_source_notice", p.implementation_digest)
                             for p in self._ports}))

    def prepare(self, storage, spec, lease, control, *, clock, capture_directory=None):
        """Reserve under explicit authority, execute once, prepare final CAS.

        No default transport or automatic retry on any failure. The port's
        execute method must independently fence actual transport start
        against revocation, and report a fresh admitted observation only.
        The adapter must be qualified separately; this class does not
        confer provider authorization or prove a returned row's provenance.
        """
        if (self._authority._storage is not storage
                or type(lease) is not journal.JobLease):
            raise ValueError("notice_fetch_invalid_execution")
        journal._check_spec(spec)
        if spec.kind != "fetch_source_notice" or spec.dependencies:
            raise UnavailableNoticeWork("notice_fetch_unsupported_spec")
        authority = self._authority
        reserved = None
        try:
            with authority._lock:
                grant = authority._lookup(lease.ref)
                if grant.spec != spec:
                    raise UnavailableNoticeWork("notice_fetch_generation_changed")
                matches = [p for p in self._ports
                           if p.provider_id == grant.source.observation.key.provider_id
                           and p.transport_digest == grant.transport_digest
                           and p.implementation_digest == spec.implementation_digest]
            if len(matches) != 1:
                raise UnavailableNoticeWork("notice_fetch_transport_unavailable")
            port, = matches
            control.check_cancelled()
            with authority.dispatch_admission(
                lease, transport_digest=port.transport_digest,
                implementation_digest=port.implementation_digest,
            ) as permit:
                control.check_cancelled()
                candidate = port.reserve(permit)
                if (type(candidate) is not ReservedPublicFetch
                        or candidate.permit is not permit
                        or not callable(candidate.execute)
                        or not callable(candidate.release)
                        or not callable(candidate.hold_publication)):
                    raise UnavailableNoticeWork("notice_fetch_transport_unavailable")
                reserved = candidate
            # The session's own reservation must reject a revoked start.
            # No provider or registry lock or Storage transaction held here.
            control.check_cancelled()
            try:
                result = reserved.execute(control)
            finally:
                reservation, reserved = reserved, None
                reservation.release()
            control.check_cancelled()
            with authority._lock:
                grant = authority._lookup(lease.ref)
                if grant.permit is not permit or not grant.started:
                    raise UnavailableNoticeWork("notice_fetch_transport_not_started")
            prepared = prepare_public_fetch_publication(
                storage, authority, permit, result, clock=clock,
                capture_directory=capture_directory,
            )

            @contextmanager
            def guard():
                with prepared.hold_input():
                    with reservation.hold_publication():
                        yield

            return PreparedSource(prepared.publish, guard)
        except BaseException:
            try:
                if reserved is not None:
                    reserved.release()
            finally:
                authority.revoke(lease.ref)
            raise
