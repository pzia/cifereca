"""#4982 bounded recycling of journal rows only, with no lease ABA."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_job_retention as retention
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_4982_notice_c_job_journal import spec, enqueue, complete
from tests.test_core_p1_2385_notice_cover_persistence import PUBLIC, notice, store

NOW = 100
OLD = NOW + journal._MAX_LEASE + retention._MIN_AGE


def terminal(db, value, *, now=NOW, generation=0):
    ref = enqueue(db, value, generation, now=now)
    lease = journal.claim_job(db, ref, now=now, lease_seconds=30)
    complete(db, lease, now=now)
    return ref, lease


def recycle(db, *, now=OLD, limit=32):
    return retention.recycle_terminal_jobs(db, now=now, limit=limit)


def high(db):
    return db.metadata_get(retention._COUNTER)


def assert_gone(db, ref):
    with pytest.raises(ValueError, match="generation_changed"):
        journal.read_job(db, ref)


def test_recreate_after_restart_cannot_resurrect_old_reference_or_lease(tmp_path, monkeypatch):
    path = tmp_path / "recycle.sqlite"
    monkeypatch.setattr(journal, "_MAX_JOBS", 2)
    db = Storage(path)
    try:
        source = store(db, notice())
        a, first_lease = terminal(db, spec())
        b, _ = terminal(db, spec(value="B"), generation=a.generation)
        old, old_lease = terminal(db, spec(), generation=b.generation)
        pending = enqueue(db, spec(key=replace(PUBLIC, record_id="other")), now=NOW)
        assert old.generation == 3 and high(db) is None
        with pytest.raises(ValueError, match="capacity"):
            enqueue(db, spec(key=replace(PUBLIC, record_id="third")), now=NOW)
        assert recycle(db, now=OLD-1).recycled == 0
        result = recycle(db)
        assert (result.examined, result.recycled, result.retained) == (2, 1, 1)
        assert high(db) == "3"
        assert_gone(db, old)
        assert journal.read_job(db, pending).state == "pending"
    finally:
        db.close()
    db = Storage(path)
    try:
        new = enqueue(db, spec(), now=OLD)
        assert new.key == old.key and new.generation == 4
        assert enqueue(db, spec(), now=OLD) == new
        for lease in (first_lease, old_lease):
            with pytest.raises(ValueError, match="generation_changed"):
                complete(db, lease, now=OLD)
        with pytest.raises(ValueError, match="generation_changed"):
            enqueue(db, spec(value="B"), old.generation, now=OLD)
        assert journal.read_job(db, new).attempts == 0
        assert high(db) == "4"
        assert read_notice_cover(db, source.document) == source
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_recycle_leaves_referenced_generations_pending_and_recent_rows_intact(tmp_path):
    db = Storage(tmp_path / "deps.sqlite")
    try:
        source, _ = terminal(db, spec("fetch_source_notice"))
        parsed, _ = terminal(db, spec(deps=(source,)))
        link, _ = terminal(db, spec("relink_notices", deps=(parsed,)))
        aggregate = enqueue(db, spec("recompute_aggregate", deps=(link,)), now=NOW)
        unrelated, expired = terminal(db, spec(key=replace(PUBLIC, record_id="unrelated")))
        recent, _ = terminal(db, spec(key=replace(PUBLIC, record_id="recent")), now=OLD)
        # A pending dependent pins its complete ancestry, as does a live
        # terminal dependent; removing unrelated work does not change its DAG.
        result = recycle(db)
        assert (result.recycled, result.referenced) == (1, 3)
        assert_gone(db, unrelated)
        for ref in (source, parsed, link, recent):
            assert journal.read_job(db, ref).state == "current"
        lease = journal.claim_job(db, aggregate, now=OLD, lease_seconds=30)
        assert lease is not None
        assert recycle(db).recycled == 0
        complete(db, lease, now=OLD)
        later = OLD + journal._MAX_LEASE + retention._MIN_AGE
        result = recycle(db, now=later)
        assert result.recycled == 2
        assert_gone(db, aggregate)
        assert_gone(db, recent)
        for ref in (link, parsed, source):
            assert recycle(db, now=later, limit=1).recycled == 1
            assert_gone(db, ref)
        assert recycle(db, now=later).recycled == 0
        assert high(db) == "1"  # counter survives an empty journal
    finally:
        db.close()


def test_high_water_tracks_existing_key_updates_and_recycling_across_connections(tmp_path):
    path = tmp_path / "shared.sqlite"
    one, two = Storage(path), Storage(path)
    try:
        retired, _ = terminal(one, spec(key=replace(PUBLIC, record_id="retired")))
        value = spec()
        existing = enqueue(one, value, now=NOW)
        assert recycle(two).recycled == 1
        assert high(one) == "1"
        for i in range(2, 6):
            existing = enqueue(one, replace(value, input_digest=f"{i:064x}"),
                               existing.generation, now=OLD)
            assert existing.generation == i
        assert high(two) == "5"
        gate = Barrier(2)
        def create(owner, index):
            gate.wait(timeout=5)
            return enqueue(owner, spec(key=replace(PUBLIC, record_id="new"+str(index))), now=OLD)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(create, one, 1)
            b = pool.submit(create, two, 2)
            assert {a.result(timeout=15).generation,b.result(timeout=15).generation} == {6,7}
        assert high(one) == "7"
        recreated = enqueue(two, spec(key=replace(PUBLIC, record_id="retired")), now=OLD)
        assert recreated.key == retired.key and recreated.generation == 8
        assert_gone(one, retired)
    finally:
        one.close()
        two.close()


def test_counter_and_deletion_rollback_and_corruption_refusal(tmp_path, monkeypatch):
    db = Storage(tmp_path / "rollback.sqlite")
    try:
        a, _ = terminal(db, spec())
        b, _ = terminal(db, spec(key=replace(PUBLIC, record_id="another")))
        # Abort the second deletion: first DELETE and floor initialization
        # must roll back together in the existing metadata transaction.
        with db._lock, db._connect() as sql:
            last = max(a.key, b.key)
            sql.execute(
                "CREATE TRIGGER reject_retention_delete BEFORE DELETE ON metadata "
                "WHEN OLD.key='"+journal._PREFIX+last+"' BEGIN "
                "SELECT RAISE(ABORT,'fixture rollback'); END"
            )
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError, match="fixture rollback"):
            recycle(db)
        assert high(db) is None
        assert journal.read_job(db, a).state == journal.read_job(db, b).state == "current"
        with db._lock, db._connect() as sql:
            sql.execute("DROP TRIGGER reject_retention_delete")
            sql.execute("UPDATE metadata SET value='{}' WHERE key=?", (journal._PREFIX+b.key,))
        with pytest.raises(ValueError, match="corrupt"):
            recycle(db)
        assert high(db) is None and journal.read_job(db, a).state == "current"
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?", (journal._PREFIX+b.key,))
            sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (retention._COUNTER,"broken"))
        with pytest.raises(ValueError, match="retention_corrupt"):
            recycle(db)
        with pytest.raises(ValueError, match="retention_corrupt"):
            enqueue(db, spec(value="B"), a.generation, now=OLD)
        assert journal.read_job(db, a).state == "current"
    finally:
        db.close()


def test_terminal_error_recycles_but_pending_expired_lease_not_pruned(tmp_path):
    db = Storage(tmp_path / "errors.sqlite")
    try:
        a = enqueue(db, spec(), now=NOW)
        lease = journal.claim_job(db, a, now=NOW, lease_seconds=1)
        journal.fail_job(db, lease, now=NOW, transient=False)
        b = enqueue(db, spec(key=replace(PUBLIC, record_id="pending")), now=NOW)
        pending = journal.claim_job(db, b, now=NOW, lease_seconds=1)
        assert recycle(db, limit=1).recycled == 1
        assert_gone(db, a)
        assert recycle(db).recycled == 0
        reclaimed = journal.claim_job(db, b, now=OLD, lease_seconds=30)
        assert reclaimed.fence > pending.fence
        with pytest.raises(ValueError, match="lease_changed"):
            complete(db, pending, now=OLD)
    finally:
        db.close()
