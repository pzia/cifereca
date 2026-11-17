"""Core-only #4982 bridge to an already admitted TaskBrokerImpl.

Inert until a real, validated aggregate producer installs the preparation port.
No timer, executor, network, source access, GTK or plugin-facing API here.
The port MUST perform the full input/implementation CAS and canonical writer
on the supplied transaction. It is trusted Core code, not a plugin callback.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from threading import Lock
from typing import Callable
import sqlite3
import time

from cifereca.plugin_api import Cancelled
from ..task_runtime import TaskBrokerImpl
from ..task_queue import TaskPriority
from ..task_admission import SharedTaskAdmission
from . import notice_c_job_journal as journal
from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import _db_key, _decode

_MAX_INFLIGHT = 2
_LEASE_SECONDS = 300


class UnavailableNoticeWork(Exception):
    """Exact input or implementation is no longer available; no fallback GET."""


class RetryableNoticeWork(Exception):
    """Explicitly classified transient failure, never a persisted message."""


@dataclass(frozen=True, slots=True, repr=False)
class PreparedAggregate:
    # Return literal True only after input CAS and canonical write succeeded.
    # The caller owns BEGIN / rollback / commit; no nested transaction.
    publish: Callable[[sqlite3.Connection], bool]


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
                 allow_reparse=False):
        if (type(broker) is not TaskBrokerImpl
                or not isinstance(broker.shared_admission, SharedTaskAdmission)
                or broker.shared_admission.reserve_user_slots < 1):
            raise ValueError("notice_work_shared_admission_required")
        if not callable(prepare) or (clock is not None and not callable(clock)):
            raise ValueError("notice_work_invalid_port")
        if on_committed is not None and not callable(on_committed):
            raise ValueError("notice_work_invalid_observer")
        if type(allow_reparse) is not bool:
            raise ValueError("notice_work_invalid_port")
        self._allow_reparse = allow_reparse
        self._on_committed = on_committed
        self._storage = storage
        self._broker = broker
        self._prepare = prepare
        self._clock = clock if clock is not None else lambda: int(time.time())
        self._lock = Lock()
        self._inflight = set()

    def submit(self, ref):
        """Return existing broker handle, or None on duplicate/backpressure.

        No SQLite/claim before broker admission. Queued cancellation and
        admission refusal do not consume a durable attempt. Exact ref and
        dependencies are rechecked by the worker, not trusted from a hint.
        """
        journal._check_ref(ref)
        with self._lock:
            if ref in self._inflight or len(self._inflight) >= _MAX_INFLIGHT:
                return None
            self._inflight.add(ref)
        try:
            handle = self._broker.submit(
                lambda control: self._run(ref, control),
                priority=TaskPriority.METADATA,
                label="notice-c-aggregate", kind="maintenance",
            )
        except RuntimeError as exc:
            self._forget(ref)
            if str(exc) in {
                "executor pending queue full", "shared task admission full",
                "task broker already closed", "executor already closed",
            }:
                return None
            raise
        except BaseException:
            self._forget(ref)
            raise
        handle.add_done_callback(lambda _handle: self._forget(ref))
        return handle

    def _forget(self, ref):
        with self._lock:
            self._inflight.discard(ref)

    def _fail(self, lease, *, transient):
        try:
            journal.fail_job(
                self._storage, lease, now=self._clock(), transient=transient,
            )
        except ValueError:
            # Replaced generation, lost lease or dependency: never mutate
            # whichever newer attempt owns the coordinate now.
            return "obsolete"
        return "transient_failure" if transient else "error"

    def _run(self, ref, control):
        control.check_cancelled()
        try:
            job = journal.read_job(self._storage, ref)
            if (job.spec.kind != "recompute_aggregate"
                    and not (self._allow_reparse and job.spec.kind == "reparse_cached_input")):
                return "unsupported"
            if job.state != "pending":
                return "not_ready"
            lease = journal.claim_job(
                self._storage, ref, now=self._clock(), lease_seconds=_LEASE_SECONDS,
            )
        except ValueError:
            return "obsolete"
        if lease is None:
            return "not_ready"
        committed = False
        try:
            control.check_cancelled()
            prepared = self._prepare(job.spec, control)
            aggregate = job.spec.kind == "recompute_aggregate"
            expected = PreparedAggregate if aggregate else PreparedSource
            if (type(prepared) is not expected or not callable(prepared.publish)
                    or (not aggregate and not callable(prepared.hold_input))):
                raise TypeError("notice_work_invalid_publication")
            control.check_cancelled()
            event = None
            with (nullcontext() if aggregate else prepared.hold_input()):
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
                    if prepared.publish(db) is not True:
                        raise RetryableNoticeWork("notice_work_input_changed")
                    control.check_cancelled()
                    journal._complete_job_txn(db, lease, now=self._clock())
                    if aggregate and self._on_committed is not None:
                        after = _canonical_revision(db, document)
                        if after is None or (before is not None and after < before):
                            raise ValueError("notice_work_invalid_publication")
                        if before != after:
                            event = (document, after)
                committed = True
        except Cancelled:
            self._fail(lease, transient=True)
            raise
        except UnavailableNoticeWork:
            return "current" if committed else self._fail(lease, transient=False)
        except (RetryableNoticeWork, sqlite3.OperationalError):
            return "current" if committed else self._fail(lease, transient=True)
        except ValueError:
            # CAS/source validation refusal; no successful publication.
            return "current" if committed else self._fail(lease, transient=True)
        except Exception:
            # Only a closed reason crosses the durable boundary.
            return "current" if committed else self._fail(lease, transient=False)
        # Commit and Storage lock release have both succeeded. An observer
        # failure cannot roll back or retry an already successful job.
        if event is not None:
            try:
                self._on_committed(*event)
            except Exception:
                pass
        return "current"


def _canonical_revision(db, document):
    row = db.execute("SELECT value FROM metadata WHERE key=?",
                     (_db_key(document),)).fetchone()
    return None if row is None else _decode(row[0], document).epoch
