"""#4982 explicit bounded terminal journal recycling, not automatic GC.

The existing canonical metadata table holds one high-water counter OUTSIDE
the job key range. No per-key tombstone table or documentary data. Before
the first deletion, initialize the counter above/equal to every existing
generation in the same SQLite transaction. After that, every new generation
advances or preserves the high water; a recreated absent key receives a
generation strictly above it. Existing key increments stay compatible.

Only the SINGLE upgraded writer may write journal rows. Downgrading to a
writer that ignores the counter after recycling is unsupported. Storage
restore to an earlier database image is not a crash recovery guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from . import notice_c_job_journal as journal

_COUNTER = "notice.c.work-retention.v1:high-water"
_COUNT = re.compile(r"[1-9][0-9]{0,18}\Z")
_MAX_BATCH = 32
_MIN_AGE = 24 * 3600


@dataclass(frozen=True, slots=True)
class RecycleResult:
    examined: int
    recycled: int
    referenced: int
    retained: int


def _high_water(db):
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
    row = db.execute("SELECT value FROM metadata WHERE key=?", (_COUNTER,)).fetchone()
    if row is None:
        return None
    value = row[0]
    if (type(value) is not str or _COUNT.fullmatch(value) is None
            or not journal._integer(int(value), minimum=1)):
        raise ValueError("notice_work_retention_corrupt")
    return int(value)


def _set_high_water(db, value):
    if not db.in_transaction or not journal._integer(value, minimum=1):
        raise ValueError("notice_work_retention_corrupt")
    db.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_COUNTER, str(value)),
    )


def _allocate_generation_txn(db, previous):
    """Part of request's existing BEGIN IMMEDIATE, with no separate commit.

    Before first recycle, legacy key-local generations remain unchanged.
    After recycle, absent/new keys use the common high-water counter; an
    existing key still advances by exactly one. The floor records *all*
    issued generations, including key-local changes after migration.
    Missing = expected_generation zero remains the request CAS contract,
    but the newly allocated generation need not be one.
    """
    high = _high_water(db)
    old = previous.ref.generation if previous is not None else 0
    if high is not None and old > high:
        raise ValueError("notice_work_retention_corrupt")
    generation = (high if previous is None and high is not None else old) + 1
    if not journal._integer(generation, minimum=1):
        raise ValueError("notice_work_generation_overflow")
    if high is not None and generation > high:
        _set_high_water(db, generation)
    return generation


def recycle_terminal_jobs(storage, *, now, min_age=_MIN_AGE, limit=_MAX_BATCH):
    """Explicit maintenance entry point, never called by request or rendering.

    Examine the bounded journal in ONE write snapshot. Any corruption,
    overflow or unsupported row refuses the entire operation. Preserve all
    pending rows, even expired/exhausted leases; claiming/recovery remains
    responsible for their state transition. Preserve every exact generation
    referenced by ANY surviving job (including terminal dependents). Leaf
    removal may free its dependencies on a subsequent explicit pass.

    The due timestamp of a terminal row is the most recent claim time,
    not a completion timestamp. Add the maximum lease duration before
    the retention age, so a slow but valid completion cannot be recycled
    prematurely. No new age field, timer, network, C/source/cache deletion
    or implicit retry. Limit caps deletions, not the bounded reverse scan.
    """
    if (not journal._integer(now, minimum=1)
            or not journal._integer(min_age, minimum=_MIN_AGE)
            or type(limit) is not int or not 1 <= limit <= _MAX_BATCH):
        raise ValueError("notice_work_invalid_retention")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        high = _high_water(db)
        keys = db.execute(
            "SELECT key FROM metadata WHERE key>=? AND key<? ORDER BY key LIMIT ?",
            (journal._PREFIX, journal._END, journal._MAX_JOBS + 1),
        ).fetchall()
        if len(keys) > journal._MAX_JOBS:
            raise ValueError("notice_work_retention_capacity")
        jobs = []
        for (key,) in keys:
            job = journal._load(db, key[len(journal._PREFIX):])
            if job is None:
                raise ValueError("notice_work_retention_corrupt")
            jobs.append(job)
        highest = max((j.ref.generation for j in jobs), default=0)
        if high is not None and highest > high:
            raise ValueError("notice_work_retention_corrupt")
        refs = {dep for job in jobs for dep in job.spec.dependencies}
        pinned = sum(job.ref in refs for job in jobs)
        # Subtraction avoids overflow at the maximum SQLite clock.
        cutoff = now - journal._MAX_LEASE - min_age
        eligible = sorted(
            (job for job in jobs if job.state in ("current", "stale", "error")
             and job.lease_until == 0 and job.ref not in refs
             and job.due <= cutoff),
            key=lambda job: (job.due, job.ref.key),
        )[:limit]
        if not eligible:
            return RecycleResult(len(jobs), 0, pinned, len(jobs))
        floor = max(high or 0, highest)
        if floor >= journal._MAX:
            raise ValueError("notice_work_generation_overflow")
        # First initialization + deletion atomic. No job row is ever
        # deleted without preserving its issued generation globally.
        if high is None:
            _set_high_water(db, floor)
        for job in eligible:
            cursor = db.execute("DELETE FROM metadata WHERE key=?",
                                (journal._PREFIX + job.ref.key,))
            if cursor.rowcount != 1:
                raise ValueError("notice_work_retention_corrupt")
        return RecycleResult(len(jobs), len(eligible), pinned, len(jobs)-len(eligible))
