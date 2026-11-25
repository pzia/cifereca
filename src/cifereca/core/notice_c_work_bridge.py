"""Core-only #4982 bridge to an already admitted TaskBrokerImpl.

Inert until a real, validated aggregate producer installs the preparation port.
No timer, executor, network, source access, GTK or plugin-facing API here.
The port MUST perform the full input/implementation CAS and canonical writer
on the supplied transaction. It is trusted Core code, not a plugin callback.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from threading import Lock
from typing import Callable
import sqlite3
import time

from cifereca.plugin_api import Cancelled
from ..plugin_api.provider_purchase_enrichment import PurchaseEnrichmentDeferred
from ..task_runtime import TaskBrokerImpl
from ..task_queue import TaskPriority
from ..task_admission import SharedTaskAdmission
from . import notice_c_job_journal as journal
from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import _db_key, _decode
from .notice_c_dispatch_rotation import RotationOffer
from .notice_c_initial_group_publication import (
    InitialGroupSuperseded, mark_initial_group_superseded,
)

_MAX_INFLIGHT = 2
_LEASE_SECONDS = 300


class UnavailableNoticeWork(Exception):
    """Exact input or implementation is no longer available; no fallback GET."""


@dataclass(frozen=True, slots=True, repr=False)
class WitnessRejectedPublication:
    """Closed result: diagnostic persisted, no documentary write performed."""

    after_commit: Callable[[], object] | None = None
    diagnostic_id: str | None = None


class RetryableNoticeWork(Exception):
    """Explicitly classified transient failure, never a persisted message."""


@dataclass(frozen=True, slots=True, repr=False)
class PreparedAggregate:
    # Return literal True only after input CAS and canonical write succeeded.
    # The caller owns BEGIN / rollback / commit; no nested transaction.
    publish: Callable[[sqlite3.Connection], bool]


@dataclass(frozen=True, slots=True, repr=False)
class PreparedDescriptive:
    # Trusted Core proposal and transaction-local reader, not a plugin callback.
    row: object
    expected_execution_key: str | None
    capture_current_txn: Callable
    schema: object
    hold_input: Callable | None = None


@dataclass(frozen=True, slots=True, repr=False)
class PreparedSource:
    publish: Callable[[sqlite3.Connection], bool]
    hold_input: Callable


class AggregateWorkBridge:
    """Bound queued + running hints, claim only AFTER broker admission.

    Construct once per Core owner, using its existing broker. No submission
    from GTK: caller is the Core scheduling coordinator. Local dedup/limit
    is not a cross-process budget; the durable claim fences cross-process
    execution/publication. Public recompute is the default; reparse is
    admitted only with an explicitly installed Core port.
    """

    def __init__(self, storage, broker, prepare, *, clock=None, on_committed=None,
                 allow_reparse=False, allow_relink=False, prepare_fetch=None,
                 claim_purchase=None, prepare_purchase=None, on_purchase_committed=None,
                 finish_purchase=None, on_witness_rejected=None,
                 prepare_descriptive=None, on_descriptive_committed=None):
        if (type(broker) is not TaskBrokerImpl
                or not isinstance(broker.shared_admission, SharedTaskAdmission)
                or broker.shared_admission.reserve_user_slots < 1):
            raise ValueError("notice_work_shared_admission_required")
        if not callable(prepare) or (clock is not None and not callable(clock)):
            raise ValueError("notice_work_invalid_port")
        if on_committed is not None and not callable(on_committed):
            raise ValueError("notice_work_invalid_observer")
        if type(allow_reparse) is not bool or type(allow_relink) is not bool:
            raise ValueError("notice_work_invalid_port")
        if prepare_fetch is not None and not callable(prepare_fetch):
            raise ValueError("notice_work_invalid_port")
        if ((claim_purchase is None) != (prepare_purchase is None)
                or (claim_purchase is not None and (
                    not callable(claim_purchase) or not callable(prepare_purchase)))):
            raise ValueError("notice_work_invalid_purchase_port")
        if on_purchase_committed is not None and not callable(on_purchase_committed):
            raise ValueError("notice_work_invalid_observer")
        if on_witness_rejected is not None and not callable(on_witness_rejected):
            raise ValueError("notice_work_invalid_observer")
        self._on_witness_rejected = on_witness_rejected
        self._on_purchase_committed = on_purchase_committed
        if finish_purchase is not None and (claim_purchase is None or not callable(finish_purchase)):
            raise ValueError("notice_work_invalid_purchase_port")
        self._finish_purchase = finish_purchase
        self._claim_purchase = claim_purchase
        self._prepare_purchase = prepare_purchase
        if prepare_descriptive is not None and not callable(prepare_descriptive):
            raise ValueError("notice_work_invalid_port")
        if on_descriptive_committed is not None and not callable(on_descriptive_committed):
            raise ValueError("notice_work_invalid_observer")
        self._on_descriptive_committed = on_descriptive_committed
        self._prepare_descriptive = prepare_descriptive
        self._prepare_fetch = prepare_fetch
        self._allow_reparse = allow_reparse
        self._allow_relink = allow_relink
        self._on_committed = on_committed
        self._storage = storage
        self._broker = broker
        self._prepare = prepare
        self._clock = clock if clock is not None else lambda: int(time.time())
        self._lock = Lock()
        self._inflight = set()
        self._network_inflight = set()

    def submit(self, ref):
        """Return existing broker handle, or None on duplicate/backpressure.

        No SQLite/claim before broker admission. Queued cancellation and
        admission refusal do not consume a durable attempt. Exact ref and
        dependencies are rechecked by the worker, not trusted from a hint.
        """
        return self.offer(ref).handle

    def offer(self, ref, *, circuit=None):
        """Class rotation distinguishes an exact duplicate from saturation."""
        journal._check_ref(ref)
        if circuit not in (None, "network", "local"):
            raise ValueError("notice_work_invalid_circuit")
        with self._lock:
            if ref in self._inflight:
                return RotationOffer("duplicate")
            if circuit == "network" and self._network_inflight:
                return RotationOffer("circuit_blocked")
            if len(self._inflight) >= _MAX_INFLIGHT:
                return RotationOffer("blocked")
            self._inflight.add(ref)
            if circuit == "network":
                self._network_inflight.add(ref)
        try:
            handle = self._broker.submit(
                lambda control: self._run(ref, control, circuit=circuit),
                priority=TaskPriority.METADATA,
                label="notice-c-aggregate", kind="maintenance",
            )
        except RuntimeError as exc:
            self._forget(ref)
            if str(exc) in {
                "executor pending queue full", "shared task admission full",
                "task broker already closed", "executor already closed",
            }:
                return RotationOffer("blocked")
            raise
        except BaseException:
            self._forget(ref)
            raise
        handle.add_done_callback(lambda _handle: self._forget(ref))
        return RotationOffer("accepted", handle)

    def _forget(self, ref):
        with self._lock:
            self._inflight.discard(ref)
            self._network_inflight.discard(ref)

    def _purchase_transition(self):
        # Invalidation is advisory, not part of durable success. No GUI
        # callback, payload or second work queue on this worker.
        if self._on_purchase_committed is not None:
            try:
                self._on_purchase_committed()
            except Exception:
                pass

    def _fail(self, lease, *, transient, retry_after_seconds=None):
        try:
            receipt = journal.fail_job(
                self._storage, lease, now=self._clock(), transient=transient,
                **({"retry_after_seconds": retry_after_seconds} if retry_after_seconds is not None else {}),
            )
        except ValueError:
            # Replaced generation, lost lease or dependency: never mutate
            # whichever newer attempt owns the coordinate now.
            return "obsolete"
        # A transient cause can exhaust the attempt budget. Use the
        # committed receipt, not the cause or a second racy journal read.
        if receipt.spec.implementation_digest == journal._PURCHASE_INTENT_DIGEST:
            self._purchase_transition()
        return "transient_failure" if receipt.state == "pending" else "error"

    def _run(self, ref, control, *, circuit=None):
        control.check_cancelled()
        try:
            job = journal.read_job(self._storage, ref)
            purchase = job.spec.implementation_digest == journal._PURCHASE_INTENT_DIGEST
            actual = "network" if job.spec.kind == "fetch_source_notice" else "local"
            if circuit is not None and circuit != actual:
                return "unsupported"
            if purchase and self._claim_purchase is None:
                return "unsupported"
            if (not purchase and job.spec.kind != "recompute_aggregate"
                    and not (self._allow_reparse and not purchase and job.spec.kind in ("reparse_cached_input", "fetch_source_notice"))
                    and not (self._allow_relink and job.spec.kind == "relink_notices")
                    and not (self._prepare_fetch is not None
                             and job.spec.kind == "fetch_source_notice")
                    and not (self._prepare_descriptive is not None
                             and job.spec.kind == "consolidate_descriptive")):
                return "unsupported"
            if job.state != "pending":
                return "not_ready"
            lease = (self._claim_purchase(ref, now=self._clock(), lease_seconds=_LEASE_SECONDS)
                     if purchase else journal.claim_job(
                         self._storage, ref, now=self._clock(), lease_seconds=_LEASE_SECONDS,
                     ))
        except ValueError:
            return "obsolete"
        if lease is None:
            return "not_ready"
        try:
            return self._run_claimed(job, lease, purchase, control)
        finally:
            if purchase and self._finish_purchase is not None:
                self._finish_purchase(lease)

    def _run_claimed(self, job, lease, purchase, control):
        if purchase:
            self._purchase_transition()
        committed = False
        rejected = False
        fetch = job.spec.kind == "fetch_source_notice"
        descriptive = job.spec.kind == "consolidate_descriptive"
        try:
            control.check_cancelled()
            if purchase:
                prepared = self._prepare_purchase(job.spec, lease, control)
            elif descriptive:
                prepared = self._prepare_descriptive(job.spec, control)
            else:
                prepared = (self._prepare_fetch(job.spec, lease, control) if fetch
                            else self._prepare(job.spec, control))
            aggregate = job.spec.kind in ("recompute_aggregate", "relink_notices")
            if descriptive:
                if (type(prepared) is not PreparedDescriptive
                        or not callable(prepared.capture_current_txn)
                        or (prepared.hold_input is not None and not callable(prepared.hold_input))):
                    raise TypeError("notice_work_invalid_publication")
            else:
                expected = PreparedAggregate if aggregate else PreparedSource
                if (type(prepared) is not expected or not callable(prepared.publish)
                        or (not aggregate and not callable(prepared.hold_input))):
                    raise TypeError("notice_work_invalid_publication")
            control.check_cancelled()
            event = None
            guard = (nullcontext() if aggregate or (descriptive and prepared.hold_input is None)
                     else prepared.hold_input())
            with guard:
                with self._storage._lock, self._storage._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    # Check before any canonical write, then again before commit.
                    journal._held(db, lease, self._clock())
                    control.check_cancelled()
                    if aggregate and self._on_committed is not None:
                        document = CoverDocumentScope(
                            kind="public_catalogue", identity_id=None,
                            provider_id="cifereca.c", credential_id="__public__",
                            source_key=job.spec.target,
                        )
                        before = _canonical_revision(db, document)
                    if descriptive:
                        from .notice_descriptive_materialization_store import publish_descriptive_materialization_txn
                        publish_descriptive_materialization_txn(
                            db, prepared.row, expected_execution_key=prepared.expected_execution_key,
                            capture_current_txn=prepared.capture_current_txn,
                            journal_lease=lease, journal_now=self._clock(),
                            journal_clock=self._clock, schema=prepared.schema,
                        )
                        outcome = True
                    else:
                        outcome = prepared.publish(db)
                    rejected = (type(outcome) is WitnessRejectedPublication
                                and job.spec.kind in ("reparse_cached_input", "fetch_source_notice"))
                    if rejected:
                        control.check_cancelled()
                        current = journal._held(db, lease, self._clock())
                        journal._save(db, replace(current, state="error", lease_until=0,
                                                  reason="permanent"))
                    else:
                        if outcome is not True:
                            raise RetryableNoticeWork("notice_work_input_changed")
                        control.check_cancelled()
                        if not descriptive:
                            journal._complete_job_txn(db, lease, now=self._clock())
                        if aggregate and self._on_committed is not None:
                            after = _canonical_revision(db, document)
                            if after is None or (before is not None and after < before):
                                raise ValueError("notice_work_invalid_publication")
                            if before != after:
                                event = (document, after)
                committed = True
        except Cancelled:
            if committed:
                return "error" if rejected else "current"
            self._fail(lease, transient=not fetch)
            raise
        except PurchaseEnrichmentDeferred as exc:
            if committed:
                return "error" if rejected else "current"
            if purchase and type(exc) is PurchaseEnrichmentDeferred:
                # Reconstruct to validate even a mutated plugin exception.
                try:
                    deferred = PurchaseEnrichmentDeferred(exc.reason, exc.retry_after_seconds)
                except (AttributeError, ValueError):
                    return self._fail(lease, transient=False)
                return self._fail(lease, transient=True, retry_after_seconds=deferred.retry_after_seconds)
            return self._fail(lease, transient=False)
        except UnavailableNoticeWork:
            return ("error" if rejected else "current") if committed else self._fail(lease, transient=False)
        except (RetryableNoticeWork, sqlite3.OperationalError):
            return ("error" if rejected else "current") if committed else self._fail(lease, transient=not fetch)
        except InitialGroupSuperseded:
            if committed:
                return "error" if rejected else "current"
            try:
                if mark_initial_group_superseded(self._storage, lease, now=self._clock()):
                    return "stale"
            except ValueError:
                return "obsolete"
            # Successor disappeared between preparation and final CAS.
            return self._fail(lease, transient=True)
        except ValueError:
            # CAS/source validation refusal; no successful publication.
            return ("error" if rejected else "current") if committed else self._fail(lease, transient=not fetch)
        except Exception:
            # Only a closed reason crosses the durable boundary.
            return ("error" if rejected else "current") if committed else self._fail(lease, transient=False)
        if not committed:
            # A broken guard must not silently suppress a failed transaction.
            return self._fail(lease, transient=False)
        if rejected:
            # The provider guard and SQLite transaction are both released.
            # A failed file export never changes the committed job outcome.
            capture_state = "capacity_refused" if outcome.diagnostic_id is None else "pending"
            if outcome.after_commit is not None:
                try:
                    capture_state = outcome.after_commit() or "metadata_unavailable"
                except Exception:
                    pass  # durable capture intention remains for maintenance
            if self._on_witness_rejected is not None:
                try:
                    self._on_witness_rejected(outcome.diagnostic_id, capture_state)
                except Exception:
                    pass  # diagnostic delivery cannot change the committed outcome
            return "error"
        # Commit and Storage lock release have both succeeded. An observer
        # failure cannot roll back or retry an already successful job.
        if event is not None:
            try:
                self._on_committed(*event)
            except Exception:
                pass
        if descriptive and self._on_descriptive_committed is not None:
            try:
                self._on_descriptive_committed()
            except Exception:
                pass
        if purchase:
            self._purchase_transition()
        return "current"


def _canonical_revision(db, document):
    row = db.execute("SELECT value FROM metadata WHERE key=?",
                     (_db_key(document),)).fetchone()
    return None if row is None else _decode(row[0], document).epoch
