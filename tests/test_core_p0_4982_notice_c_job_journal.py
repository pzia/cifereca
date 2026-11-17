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


def sweep(db, now, limit=32):
    refs = []
    after = None
    examined = corrupt = 0
    while True:
        page = journal.read_ready_page(db, now=now, after=after, limit=limit)
        assert page.examined <= limit
        refs.extend(page.refs)
        examined += page.examined
        corrupt += page.corrupt
        if page.next_after is None:
            return tuple(refs), examined, corrupt
        assert after is None or page.next_after > after
        after = page.next_after


def test_bounded_ready_sweep_restarts_and_never_claims(tmp_path):
    path = tmp_path / "scan.sqlite"
    db = Storage(path)
    refs = [enqueue(db, spec(key=replace(PUBLIC, record_id=f"scan-{i}")))
            for i in range(70)]
    order = sorted(refs, key=lambda ref: ref.key)
    first = journal.claim_job(db, order[0], now=1, lease_seconds=10)
    later = journal.claim_job(db, order[1], now=1, lease_seconds=10)
    journal.fail_job(db, later, now=2, transient=True)
    complete(db, journal.claim_job(db, order[2], now=1, lease_seconds=10))
    page = journal.read_ready_page(db, now=2, limit=3)
    assert page.refs == () and page.examined == 3
    assert page.next_after == order[2].key
    db.close()
    db = Storage(path)
    try:
        second = journal.read_ready_page(db, now=2,
                                         after=page.next_after, limit=3)
        assert second.refs == tuple(order[3:6])
        ready, examined, corrupt = sweep(db, now=2)
        assert ready == tuple(order[3:]) and examined == 70 and corrupt == 0
        assert all(journal.read_job(db, ref).attempts == 0 for ref in ready)
        assert journal.read_job(db, first.ref).fence == first.fence
        assert sweep(db, now=3)[0] == tuple(order[1:2] + order[3:])
        assert sweep(db, now=11)[0] == tuple(order[:2] + order[3:])
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_ready_hints_require_exact_claim_and_skip_bad_dependencies(tmp_path):
    db = Storage(tmp_path / "scan.sqlite")
    try:
        source = enqueue(db, spec("fetch_source_notice"))
        child = enqueue(db, spec(deps=(source,)))
        link = enqueue(db, spec("relink_notices", deps=(child,)))
        other = enqueue(db, spec(key=replace(PUBLIC, record_id="unrelated")))
        assert set(sweep(db, now=1)[0]) == {source, other}
        complete(db, journal.claim_job(db, source, now=1, lease_seconds=10))
        assert set(sweep(db, now=2)[0]) == {child, other}
        newer = enqueue(db, spec("fetch_source_notice", value="B"),
                        source.generation, now=2)
        assert set(sweep(db, now=2)[0]) == {newer, other}
        assert journal.claim_job(db, child, now=2, lease_seconds=10) is None
        assert journal.read_job(db, link).state == "stale"
        old = other
        other = enqueue(db, spec(value="B",
                            key=replace(PUBLIC, record_id="unrelated")),
                        old.generation, now=2)
        with pytest.raises(ValueError, match="generation_changed"):
            journal.claim_job(db, old, now=2, lease_seconds=10)
        dependant = enqueue(db, spec(value="B", deps=(newer,)),
                            child.generation, now=2)
        with db._lock, db._connect() as conn:
            conn.execute("UPDATE metadata SET value=? WHERE key=?",
                         ("bad", journal._PREFIX + newer.key))
            conn.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                         (journal._PREFIX + "bad-key", "ignored"))
        ready, examined, corrupt = sweep(db, now=2, limit=1)
        assert ready == (other,) and examined == 4
        assert corrupt == 2  # bad row and its pending dependant
        assert journal.read_job(db, other).attempts == 0
        with pytest.raises(ValueError, match="corrupt"):
            journal.claim_job(db, dependant, now=2, lease_seconds=10)
    finally:
        db.close()


def test_ready_scan_exhaustion_and_invalid_cursor(tmp_path):
    db = Storage(tmp_path / "scan.sqlite")
    try:
        ref = enqueue(db, spec())
        for now in (1, 2, 3):
            lease = journal.claim_job(db, ref, now=now, lease_seconds=1)
            assert lease is not None
        assert sweep(db, now=3)[0] == ()
        assert sweep(db, now=4)[0] == (ref,)
        assert journal.claim_job(db, ref, now=4, lease_seconds=1) is None
        assert journal.read_job(db, ref).reason == "exhausted"
        assert sweep(db, now=4)[0] == ()
        for kwargs in ({"now": True}, {"now": -1}, {"now": 1, "limit": 33},
                       {"now": 1, "limit": False},
                       {"now": 1, "after": "some-secret"}):
            with pytest.raises(ValueError, match="invalid_scan"):
                journal.read_ready_page(db, **kwargs)
    finally:
        db.close()
