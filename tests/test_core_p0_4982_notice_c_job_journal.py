"""Inert journal acceptance. No scheduler/transport or actual C publication."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.storage import Storage
from tests.test_core_p1_2385_notice_cover_persistence import PUBLIC, PRIVATE


def digest(value):
    return sha256(value.encode()).hexdigest()


def spec(kind="reparse_cached_input", value="A", deps=(), key=PUBLIC):
    document = (canonical_cover_document_ref("edition:journal") if kind in
                ("relink_notices", "recompute_aggregate")
                else notice_cover_document_ref(key))
    return journal.public_job_spec(
        kind, document, input_digest=digest(value),
        implementation_digest=digest("parser-v2"), dependencies=deps,
    )


def enqueue(db, value, generation=0, now=1):
    return journal.request_job(db, value, now=now, expected_generation=generation)


def complete(db, lease, now=2):
    with db._lock, db._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        journal._complete_job_txn(conn, lease, now=now)


def test_concurrent_dedup_and_aba_generation(tmp_path):
    db = Storage(tmp_path / "work.sqlite")
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            refs = list(pool.map(lambda _: enqueue(db, spec()), range(100)))
        assert len(set(refs)) == 1
        a = refs[0]
        with ThreadPoolExecutor(max_workers=8) as pool:
            leases = list(pool.map(lambda _: journal.claim_job(
                db, a, now=1, lease_seconds=10), range(100)))
        held, = [x for x in leases if x is not None]
        b = enqueue(db, spec(value="B"), a.generation)
        c = enqueue(db, spec(), b.generation)
        assert a.key == b.key == c.key and c.generation == 3
        with pytest.raises(ValueError, match="generation_changed"):
            enqueue(db, spec(value="B"), a.generation)
        with pytest.raises(ValueError, match="generation_changed"):
            complete(db, held)
        assert journal.read_job(db, c).state == "pending"
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_restart_reclaim_retries_and_admission_refusal(tmp_path):
    path = tmp_path / "work.sqlite"
    db = Storage(path)
    ref = enqueue(db, spec())
    first = journal.claim_job(db, ref, now=1, lease_seconds=5)
    journal.release_unsubmitted_job(db, first, now=2)
    assert journal.read_job(db, ref).attempts == 0
    lease = journal.claim_job(db, ref, now=2, lease_seconds=5)
    assert lease.fence > first.fence
    db.close()
    db = Storage(path)
    try:
        assert journal.claim_job(db, ref, now=6, lease_seconds=5) is None
        second = journal.claim_job(db, ref, now=7, lease_seconds=5)
        assert second.fence > lease.fence
        with pytest.raises(ValueError, match="lease_changed"):
            complete(db, lease, now=8)
        journal.fail_job(db, second, now=8, transient=True)
        assert journal.read_job(db, ref).due == 12
        assert journal.claim_job(db, ref, now=11, lease_seconds=5) is None
        third = journal.claim_job(db, ref, now=12, lease_seconds=5)
        journal.fail_job(db, third, now=13, transient=True)
        job = journal.read_job(db, ref)
        assert (job.state, job.reason, job.attempts) == ("error", "exhausted", 3)
        assert journal.claim_job(db, ref, now=999, lease_seconds=5) is None
    finally:
        db.close()


def test_dependency_fencing_and_transaction_rollback(tmp_path):
    db = Storage(tmp_path / "work.sqlite")
    try:
        source = enqueue(db, spec())
        link = enqueue(db, spec("relink_notices", deps=(source,)))
        aggregate = enqueue(db, spec("recompute_aggregate", deps=(link,)))
        assert journal.claim_job(db, link, now=1, lease_seconds=5) is None
        lease = journal.claim_job(db, source, now=1, lease_seconds=5)
        with pytest.raises(RuntimeError, match="rollback"):
            with db._lock, db._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                             ("fixture-publication", "synthetic"))
                journal._complete_job_txn(conn, lease, now=2)
                raise RuntimeError("rollback")
        assert db.metadata_get("fixture-publication") is None
        assert journal.read_job(db, source).state == "pending"
        complete(db, lease)
        complete(db, journal.claim_job(db, link, now=2, lease_seconds=5), now=3)
        held = journal.claim_job(db, aggregate, now=3, lease_seconds=5)
        enqueue(db, spec(value="B"), source.generation, now=4)
        assert journal.read_job(db, link).state == "stale"
        assert journal.read_job(db, aggregate).state == "stale"
        with pytest.raises(ValueError, match="dependency_changed"):
            complete(db, held, now=4)
        assert journal.claim_job(db, aggregate, now=4, lease_seconds=5) is None
    finally:
        db.close()


def test_bound_corruption_private_and_wrong_dependency(tmp_path, monkeypatch):
    db = Storage(tmp_path / "work.sqlite")
    try:
        with pytest.raises(ValueError, match="private_not_admitted"):
            spec(key=PRIVATE)
        source = enqueue(db, spec("fetch_source_notice"))
        other = replace(PUBLIC, record_id="other")
        with pytest.raises(ValueError, match="invalid_dependencies"):
            enqueue(db, spec(deps=(source,), key=other))
        with pytest.raises(ValueError, match="invalid_dependencies"):
            enqueue(db, spec("fetch_source_notice", value="B", deps=(source,)),
                    source.generation)
        monkeypatch.setattr(journal, "_MAX_JOBS", 1)
        assert enqueue(db, spec("fetch_source_notice")) == source
        with pytest.raises(ValueError, match="capacity"):
            enqueue(db, spec())
        with db._lock, db._connect() as conn:
            conn.execute("UPDATE metadata SET value=? WHERE key=?",
                         ('{"ref":{},"ref":{}}', journal._PREFIX + source.key))
        with pytest.raises(ValueError, match="corrupt"):
            journal.read_job(db, source)
        with pytest.raises(ValueError, match="corrupt"):
            enqueue(db, spec("fetch_source_notice"))
    finally:
        db.close()
