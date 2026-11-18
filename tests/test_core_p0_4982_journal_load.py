"""#4982 autonomous journal stress on main's actual durable primitives.

Claims/completions below are synthetic receipts, NOT execution of any of the
four producers. No HTTP, GTK, provider session, dispatcher or future port
is imported. No private task is admitted by this public-only journal.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from threading import Barrier

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.storage import Storage
from tests.test_core_p1_2385_notice_cover_persistence import PUBLIC, PRIVATE, notice, store

KINDS = ("fetch_source_notice", "reparse_cached_input",
         "relink_notices", "recompute_aggregate")


def digest(value):
    return sha256(value.encode("ascii")).hexdigest()


def work(index, kind, *, version="v1", value="A", deps=()):
    document = (canonical_cover_document_ref("edition:load:" + str(index))
                if kind in KINDS[2:] else notice_cover_document_ref(
                    replace(PUBLIC, record_id="ark:load:" + str(index)),
                ))
    return journal.public_job_spec(
        kind, document, input_digest=digest(value), implementation_digest=digest(version),
        dependencies=tuple(sorted(deps, key=lambda ref: ref.key)),
    )


def enqueue(db, spec, generation=0, now=100):
    return journal.request_job(db, spec, now=now, expected_generation=generation)


def complete(db, lease, now):
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN IMMEDIATE")
        journal._complete_job_txn(sql, lease, now=now)


def chain(db, index):
    refs = []
    for kind in KINDS:
        refs.append(enqueue(db, work(index, kind, deps=tuple(refs[-1:]))))
    return tuple(refs)


def count(db):
    with db._lock, db._connect() as sql:
        return sql.execute(
            "SELECT count(*) FROM metadata WHERE key>=? AND key<?",
            (journal._PREFIX, journal._END),
        ).fetchone()[0]


def test_four_rank_dags_under_multi_connection_contention(tmp_path):
    path = tmp_path / "stress.sqlite"
    owners = [Storage(path) for _ in range(4)]
    try:
        db = owners[0]
        # 24 independent DAGs, 96 logical keys, requests made from independent
        # Storage owners (not just threads serialized by one Python mutex).
        chains = [chain(db, i) for i in range(24)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            duplicate = list(pool.map(
                lambda pair: enqueue(owners[pair[0] % 4], work(
                    pair[1], KINDS[0])),
                [(worker, index) for worker in range(4) for index in range(24)],
            ))
            assert set(duplicate) == {refs[0] for refs in chains}
            assert count(db) == 96

            for rank in range(4):
                # Future ranks cannot claim, regardless of backlog or owner.
                later = [r for refs in chains for r in refs[rank+1:]]
                blocked = list(pool.map(
                    lambda ref: journal.claim_job(owners[1], ref, now=100+rank,
                                                  lease_seconds=30),
                    later,
                ))
                assert all(lease is None for lease in blocked)

                competing = [(owner, refs[rank]) for refs in chains for owner in owners]
                attempts = list(pool.map(
                    lambda pair: journal.claim_job(*pair, now=100+rank, lease_seconds=30),
                    competing,
                ))
                held = [lease for lease in attempts if lease is not None]
                assert len(held) == 24 and len({lease.ref for lease in held}) == 24
                # Commit each synthetic receipt on a different Storage owner.
                list(pool.map(
                    lambda pair: complete(owners[pair[0] % 4], pair[1], 100+rank),
                    enumerate(held),
                ))
                for refs in chains:
                    job = journal.read_job(db, refs[rank])
                    assert job.state == "current" and job.attempts == 1
        assert count(db) == 96
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        for owner in owners:
            owner.close()


def test_only_one_generation_wins_cross_connection_aba_and_downstream_stale(tmp_path):
    path = tmp_path / "generations.sqlite"
    first, second = Storage(path), Storage(path)
    try:
        refs = chain(first, 1)
        unrelated = chain(first, 2)
        for ref in (*refs[:3], *unrelated):
            lease = journal.claim_job(first, ref, now=100, lease_seconds=30)
            complete(first, lease, 100)
        old = journal.claim_job(second, refs[3], now=100, lease_seconds=30)
        gate = Barrier(2)

        def update(owner, version):
            gate.wait(timeout=5)
            try:
                return enqueue(owner, work(1, KINDS[0], version=version),
                               refs[0].generation, now=101)
            except ValueError as exc:
                assert "generation_changed" in str(exc)
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(update, first, "v2")
            b = pool.submit(update, second, "v3")
            winner, = [ref for ref in (a.result(timeout=15), b.result(timeout=15))
                       if ref is not None]
        back = enqueue(second, work(1, KINDS[0]), winner.generation, now=102)
        assert back.key == refs[0].key and back.generation == 3
        for ref in refs[1:]:
            assert journal.read_job(first, ref).state == "stale"
        with pytest.raises(ValueError, match="dependency_changed"):
            complete(first, old, 102)
        for ref in unrelated:
            assert journal.read_job(second, ref).state == "current"
        assert count(first) == 8
    finally:
        first.close()
        second.close()


def test_full_journal_rejects_new_keys_but_preserves_existing_generations_and_c(tmp_path, monkeypatch):
    path = tmp_path / "capacity.sqlite"
    db = Storage(path)
    monkeypatch.setattr(journal, "_MAX_JOBS", 96)
    try:
        source = store(db, notice())
        refs = [chain(db, i) for i in range(24)]
        for index, items in enumerate(refs):
            for ref in items:
                complete(db, journal.claim_job(db, ref, now=100, lease_seconds=5), 100)
        assert count(db) == 96
        with pytest.raises(ValueError, match="capacity"):
            enqueue(db, work(24, KINDS[0]))
        assert enqueue(db, work(0, KINDS[0])) == refs[0][0]
        replaced = enqueue(db, work(0, KINDS[0], value="B"),
                           refs[0][0].generation, now=101)
        assert replaced.generation == 2 and count(db) == 96
    finally:
        db.close()
    db = Storage(path)
    try:
        assert count(db) == 96
        with pytest.raises(ValueError, match="capacity"):
            enqueue(db, work(24, KINDS[0]))
        assert journal.read_job(db, replaced).state == "pending"
        assert journal.read_job(db, refs[-1][-1]).state == "current"
        assert read_notice_cover(db, source.document) == source
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_two_private_profiles_denied_independently_and_public_shared(tmp_path):
    db = Storage(tmp_path / "profiles.sqlite")
    try:
        a = store(db, notice(key=PRIVATE))
        b = store(db, notice(key=replace(
            PRIVATE, identity_id="bob", credential_id="connection-b", account_id="account-b",
        )))
        source = store(db, notice())
        spec = journal.public_job_spec(
            "reparse_cached_input", source.document, input_digest=digest("public"),
            implementation_digest=digest("v1"),
        )
        public = enqueue(db, spec)
        for private in (a, b, a):
            for kind in KINDS[:2]:
                with pytest.raises(ValueError, match="private_not_admitted"):
                    journal.public_job_spec(
                        kind, private.document, input_digest=digest("private"),
                        implementation_digest=digest("v1"),
                    )
            assert enqueue(db, spec) == public
        assert count(db) == 1
        assert read_notice_cover(db, a.document) == a
        assert read_notice_cover(db, b.document) == b
        assert read_notice_cover(db, source.document) == source
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
