"""#4982: inert durable PUBLIC Notice C work journal, not an executor.

Only trusted Core producers may request work. A digest is a coordinate,
not an access grant. No provider calls, parser, GTK, timer or worker here.
Use the existing Storage transaction for the eventual canonical publication.
Private/session-bound work is deliberately not admitted in this first slice.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import re

from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import is_canonical_cover_document
from .notice_cover_repository import _opaque_key

_PREFIX = "notice.c.work.v1:"
_END = _PREFIX + "\uffff"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX = 2**63 - 1
_MAX_JOBS = 2048
_MAX_DEPS = 8
_MAX_ATTEMPTS = 3
_MAX_LEASE = 300
_KINDS = {
    "fetch_source_notice": 0,
    "reparse_cached_input": 1,
    "relink_notices": 2,
    "recompute_aggregate": 3,
}
_STATES = {"pending", "current", "stale", "error"}
_REASONS = {"none", "dependency_changed", "exhausted", "transient", "permanent"}


@dataclass(frozen=True, slots=True, repr=False)
class JobRef:
    key: str
    generation: int


@dataclass(frozen=True, slots=True, repr=False)
class JobSpec:
    kind: str
    target: str
    input_digest: str
    implementation_digest: str
    dependencies: tuple[JobRef, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class Job:
    ref: JobRef
    spec: JobSpec
    state: str
    fence: int
    attempts: int
    due: int
    lease_until: int
    reason: str


@dataclass(frozen=True, slots=True, repr=False)
class JobLease:
    ref: JobRef
    fence: int
    lease_until: int


def _integer(value, *, minimum=0):
    return type(value) is int and minimum <= value <= _MAX


def _digest(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _check_ref(ref):
    if (type(ref) is not JobRef or not _digest(ref.key)
            or not _integer(ref.generation, minimum=1)):
        raise ValueError("notice_work_invalid_ref")


def _document_target(document):
    if (type(document) is not CoverDocumentScope
            or document.kind != "public_catalogue"
            or document.identity_id is not None):
        raise ValueError("notice_work_private_not_admitted")
    if is_canonical_cover_document(document):
        return document.source_key
    if _opaque_key(document) is None:
        raise ValueError("notice_work_invalid_target")
    return document.source_key


def public_job_spec(kind, document, *, input_digest, implementation_digest,
                    dependencies=()):
    """Build exact public coordinates; caller still owns actual admission."""
    spec = JobSpec(kind, _document_target(document), input_digest,
                   implementation_digest, dependencies)
    _check_spec(spec)
    return spec


def _check_spec(spec):
    if (type(spec) is not JobSpec or type(spec.kind) is not str
            or spec.kind not in _KINDS or type(spec.target) is not str
            or not _digest(spec.input_digest)
            or not _digest(spec.implementation_digest)
            or type(spec.dependencies) is not tuple
            or len(spec.dependencies) > _MAX_DEPS):
        raise ValueError("notice_work_invalid_spec")
    prefix = ("notice-c:canonical-cover:v1:"
              if spec.kind in ("relink_notices", "recompute_aggregate")
              else "notice-c:v1:")
    if not spec.target.startswith(prefix) or not _digest(spec.target[len(prefix):]):
        raise ValueError("notice_work_invalid_target")
    for ref in spec.dependencies:
        _check_ref(ref)
    keys = tuple(ref.key for ref in spec.dependencies)
    if keys != tuple(sorted(set(keys))):
        raise ValueError("notice_work_invalid_dependencies")


def _key(spec):
    data = json.dumps(["notice.c.work.v1", spec.kind, spec.target],
                      ensure_ascii=True, separators=(",", ":"))
    return sha256(data.encode("ascii")).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("notice_work_corrupt")
        result[key] = value
    return result


def _load(db, key):
    if not _digest(key):
        raise ValueError("notice_work_invalid_ref")
    row = db.execute("SELECT value FROM metadata WHERE key=?",
                     (_PREFIX + key,)).fetchone()
    if row is None:
        return None
    try:
        raw = row[0]
        if type(raw) is not str or not 1 <= len(raw) <= 4096:
            raise ValueError
        value = json.loads(raw, object_pairs_hook=_unique)
        if type(value) is not dict or set(value) != {
            "ref", "spec", "state", "fence", "attempts", "due",
            "lease_until", "reason",
        }:
            raise ValueError
        spec = value["spec"]
        if type(spec) is not dict or type(spec.get("dependencies")) is not list:
            raise ValueError
        job = Job(
            ref=JobRef(**value["ref"]),
            spec=JobSpec(**{**spec, "dependencies": tuple(
                JobRef(**dep) for dep in spec["dependencies"]
            )}),
            state=value["state"], fence=value["fence"],
            attempts=value["attempts"], due=value["due"],
            lease_until=value["lease_until"], reason=value["reason"],
        )
        _check_ref(job.ref)
        _check_spec(job.spec)
        if (job.ref.key != key or _key(job.spec) != key
                or type(job.state) is not str or job.state not in _STATES
                or type(job.reason) is not str or job.reason not in _REASONS
                or not _integer(job.fence) or not _integer(job.attempts)
                or not 0 <= job.attempts <= _MAX_ATTEMPTS
                or job.fence < job.attempts
                or not _integer(job.due) or not _integer(job.lease_until)
                or (job.lease_until and (
                    job.state != "pending" or not job.attempts
                    or job.reason != "none" or job.lease_until <= job.due
                ))
                or (job.state == "current" and (
                    job.reason != "none" or not job.attempts
                ))
                or (job.state == "stale" and job.reason != "dependency_changed")
                or (job.state == "error" and job.reason not in
                    ("permanent", "exhausted"))
                or (job.state == "pending" and job.reason not in
                    ("none", "transient"))):
            raise ValueError
    except (TypeError, ValueError, KeyError, AttributeError,
            RecursionError, OverflowError) as exc:
        raise ValueError("notice_work_corrupt") from exc
    return job


def _save(db, job):
    db.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_PREFIX + job.ref.key,
         json.dumps(asdict(job), ensure_ascii=True, sort_keys=True,
                    separators=(",", ":"))),
    )


def _dependency_status(db, spec, *, memo=None):
    """DAG strictly decreases rank; inspect exact generations transitively."""
    memo = {} if memo is None else memo
    waiting = False
    for ref in spec.dependencies:
        if ref not in memo:
            memo[ref] = _load(db, ref.key)
        dep = memo[ref]
        if (dep is None or dep.ref != ref
                or _KINDS[dep.spec.kind] >= _KINDS[spec.kind]
                or dep.state in ("stale", "error")):
            return "stale"
        # Input-fetch/reparse work cannot depend on a different source.
        if (_KINDS[spec.kind] <= 1 and dep.spec.target != spec.target):
            return "stale"
        status = _dependency_status(db, dep.spec, memo=memo)
        if status == "stale":
            return "stale"
        waiting |= dep.state != "current" or status == "waiting"
    return "waiting" if waiting else "ready"


def _get(db, ref):
    _check_ref(ref)
    job = _load(db, ref.key)
    if job is None or job.ref != ref:
        raise ValueError("notice_work_generation_changed")
    return job


def request_job(storage, spec, *, now, expected_generation):
    """CAS on logical work, never overwrite an unrelated newer request.

    Same exact currently stored spec deduplicates, including retry state.
    Changed spec requires its current generation. Missing = generation zero.
    No pruning in this tranche: a full journal rejects new logical keys
    rather than resetting old generations or growing without bound.
    """
    _check_spec(spec)
    if not _integer(now) or not _integer(expected_generation):
        raise ValueError("notice_work_invalid_clock_or_generation")
    key = _key(spec)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = _load(db, key)
        if previous is not None and previous.spec == spec:
            return previous.ref
        generation = previous.ref.generation if previous else 0
        if expected_generation != generation:
            raise ValueError("notice_work_generation_changed")
        if generation == _MAX:
            raise ValueError("notice_work_generation_overflow")
        if _dependency_status(db, spec) == "stale":
            raise ValueError("notice_work_invalid_dependencies")
        if previous is None:
            count = db.execute(
                "SELECT count(*) FROM (SELECT key FROM metadata "
                "WHERE key>=? AND key<? LIMIT ?)",
                (_PREFIX, _END, _MAX_JOBS),
            ).fetchone()[0]
            if count >= _MAX_JOBS:
                raise ValueError("notice_work_capacity")
        ref = JobRef(key, generation + 1)
        _save(db, Job(ref, spec, "pending", 0, 0, now, 0, "none"))
        return ref


def read_job(storage, ref):
    """A replaced dependency makes even a previously current job stale."""
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        job = _get(db, ref)
        if _dependency_status(db, job.spec) == "stale":
            return replace(job, state="stale", reason="dependency_changed",
                           lease_until=0)
        return job


def claim_job(storage, ref, *, now, lease_seconds):
    """Claim one exact ready generation; expired leases are fenced on reclaim."""
    if (not _integer(now) or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= _MAX_LEASE
            or now > _MAX - lease_seconds):
        raise ValueError("notice_work_invalid_lease")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = _get(db, ref)
        status = _dependency_status(db, job.spec)
        if status == "stale":
            _save(db, replace(job, state="stale", reason="dependency_changed",
                              lease_until=0))
            return None
        if (job.state != "pending" or status != "ready" or job.due > now
                or job.lease_until > now):
            return None
        if job.attempts >= _MAX_ATTEMPTS or job.fence == _MAX:
            _save(db, replace(job, state="error", lease_until=0,
                              reason="exhausted"))
            return None
        claimed = replace(job, fence=job.fence + 1, attempts=job.attempts + 1,
                          due=now, lease_until=now + lease_seconds, reason="none")
        _save(db, claimed)
        return JobLease(ref, claimed.fence, claimed.lease_until)


def _held(db, lease, now):
    if (type(lease) is not JobLease or not _integer(now)
            or not _integer(lease.fence, minimum=1)
            or not _integer(lease.lease_until, minimum=1)):
        raise ValueError("notice_work_invalid_lease")
    job = _get(db, lease.ref)
    if (job.state != "pending" or job.fence != lease.fence
            or job.lease_until != lease.lease_until
            or not job.due <= now < job.lease_until):
        raise ValueError("notice_work_lease_changed")
    if _dependency_status(db, job.spec) != "ready":
        raise ValueError("notice_work_dependency_changed")
    return job


def release_unsubmitted_job(storage, lease, *, now):
    """Only call after broker refusal, before execution; no retry consumed."""
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = _held(db, lease, now)
        _save(db, replace(job, attempts=job.attempts - 1,
                          lease_until=0, due=now, reason="none"))


def fail_job(storage, lease, *, now, transient):
    if type(transient) is not bool:
        raise ValueError("notice_work_invalid_failure")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = _held(db, lease, now)
        if transient and job.attempts < _MAX_ATTEMPTS:
            delay = (1, 4, 16)[job.attempts - 1]
            if now > _MAX - delay:
                raise ValueError("notice_work_clock_overflow")
            updated = replace(job, lease_until=0, due=now + delay,
                              reason="transient")
        else:
            updated = replace(job, state="error", lease_until=0,
                              reason="exhausted" if transient else "permanent")
        _save(db, updated)


def _complete_job_txn(db, lease, *, now):
    """Internal final CAS, to be composed with the real writer on SAME txn.

    This does not write documentary facts and is not an execution API.
    Caller must establish its input/source/implementation CAS and write
    through the canonical writer in the transaction; on any refusal roll
    back the entire transaction. Not routed to any production caller yet.
    """
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
    job = _held(db, lease, now)
    _save(db, replace(job, state="current", lease_until=0, reason="none"))


_MAX_SCAN = 32


@dataclass(frozen=True, slots=True, repr=False)
class ReadyPage:
    """Snapshot hints, not reservations or authority to execute."""
    refs: tuple[JobRef, ...]
    next_after: str | None
    examined: int
    corrupt: int


def read_ready_page(storage, *, now, after=None, limit=_MAX_SCAN):
    """Bounded, read-only keyset scan of the existing durable public journal.

    Keep next_after across ticks, restarting at None at the end of a sweep.
    Empty pages may have a continuation (leases, backoff, dependencies).
    New keys behind the cursor are seen on the next sweep, not lost.
    The caller MUST claim each exact ref and obtain broker admission before
    any execution; a concurrent replacement can invalidate any hint.
    Expired final attempts are included so claim_job can terminalize them.
    No worker, timer, reservation, transport or source access here.
    """
    if (not _integer(now) or type(limit) is not int
            or not 1 <= limit <= _MAX_SCAN
            or (after is not None and not _digest(after))):
        raise ValueError("notice_work_invalid_scan")
    lower = _PREFIX + after if after is not None else _PREFIX
    refs = []
    corrupt = 0
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        # Only actual opaque logical keys can be returned as cursor tokens.
        # A malformed metadata key cannot become a source/account identifier.
        keys = db.execute(
            "SELECT key FROM metadata WHERE key>? AND key<? "
            "AND length(key)=? AND substr(key,?) NOT GLOB '*[^0-9a-f]*' "
            "ORDER BY key LIMIT ?",
            (lower, _END, len(_PREFIX) + 64, len(_PREFIX) + 1, limit),
        ).fetchall()
        memo = {}
        for row in keys:
            key = row[0][len(_PREFIX):]
            try:
                job = _load(db, key)
                if job is None:
                    continue
                if (job.state == "pending" and job.due <= now
                        and job.lease_until <= now
                        and _dependency_status(db, job.spec, memo=memo) == "ready"):
                    refs.append(job.ref)
            except ValueError:
                # Never expose the invalid payload or make it executable.
                # A damaged entry/dependency must not block unrelated jobs.
                corrupt += 1
    next_after = keys[-1][0][len(_PREFIX):] if len(keys) == limit else None
    return ReadyPage(tuple(refs), next_after, len(keys), corrupt)
