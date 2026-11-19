"""Explicit retry preserves CAS, final Retry-After and descriptive C."""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_purchase_retry import request_explicit_purchase_retry
from cifereca.core.notice_c_purchase_association import (
    _DETAIL_TTL_SECONDS, read_purchase_notice_association,
    record_purchase_association_txn, reattest_current_purchase_association,
)
from cifereca.core.notice_c_purchase_publication import publish_purchase_public_txn
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_1072_nondestructive_purchase_publication import observation


@contextmanager
def allowed():
    yield True


@pytest.mark.parametrize("mode", ("plain", "refresh", "permanent"))
def test_explicit_retry_only_after_exhaustion_and_not_before_due(tmp_path, mode):
    db = Storage(tmp_path / "retry.sqlite")
    clock = [100]
    execution = PurchaseIntentExecution(db, clock=lambda: clock[0])
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")

        def claim(ref):
            assert execution.attest(
                ref, snapshot, candidate, hold_context=allowed,
                execute=lambda *_: pytest.fail("no transport"), ttl_seconds=30,
            )
            return execution.claim(ref, now=clock[0], lease_seconds=30)

        def read():
            return read_purchase_notice_association(db, candidate.source, identity_id="A")

        ref, = request(db, snapshot, candidate)
        prior = None
        if mode == "refresh":
            lease = claim(ref)
            spec = journal.read_job(db, ref).spec
            with db._lock, db._connect() as sql:
                sql.execute("BEGIN IMMEDIATE")
                publication = publish_purchase_public_txn(
                    sql, spec, snapshot, candidate, observation(candidate, at=clock[0]),
                    now=clock[0],
                )
                record_purchase_association_txn(
                    sql, lease, snapshot, candidate, publication,
                    observed_at=clock[0], now=clock[0],
                )
                journal._complete_job_txn(sql, lease, now=clock[0])
            execution.revoke(ref)
            persist_exact_public_search_cover(db, publication.source)
            prior = read()
            assert prior is not None
            clock[0] += _DETAIL_TTL_SECONDS
            ref = reattest_current_purchase_association(
                db, ref, snapshot, candidate, now=clock[0],
            )
        for n in range(1 if mode == "permanent" else 3):
            lease = claim(ref)
            job = journal.fail_job(
                db, lease, now=clock[0], transient=mode != "permanent",
                **({} if mode == "permanent" else {"retry_after_seconds": 60}),
            )
            execution.revoke(ref)
            if mode != "permanent":
                assert job.due == clock[0] + 60
                clock[0] += 60
        assert job.state == "error"
        assert read() == prior

        def retry(*, at=None, guard=lambda: True, item=None, expected=None):
            return request_explicit_purchase_retry(
                db, expected or ref, snapshot, item or candidate,
                now=clock[0] if at is None else at, context_current=guard,
            )

        if mode == "permanent":
            with pytest.raises(ValueError, match="invalid_purchase_retry"):
                retry()
            assert journal.read_job(db, ref) == job
            return

        with pytest.raises(ValueError, match="invalid_purchase_retry"):
            retry(at=clock[0] - 1)
        set_policy(db, "false")
        with pytest.raises(ValueError, match="retry_context"):
            retry()
        set_policy(db, "true")
        with pytest.raises(ValueError, match="retry_changed"):
            retry(item=replace(candidate, public_locator="https://catalogue.example.org/edition/456"))
        states = iter((True, False))
        with pytest.raises(ValueError, match="retry_context"):
            retry(guard=lambda: next(states))
        assert journal.read_job(db, ref) == job and read() == prior
        successor = retry()
        assert successor.key == ref.key and successor.generation == ref.generation + 1
        fresh = journal.read_job(db, successor)
        assert (fresh.state, fresh.attempts, fresh.lease_until) == ("pending", 0, 0)
        assert request(db, snapshot, candidate, now=clock[0]) == (successor,)
        with pytest.raises(ValueError, match="generation_changed"):
            retry()
        assert execution.ready_refs() == ()
        assert journal.claim_job(db, successor, now=clock[0], lease_seconds=10) is None
        assert read() == prior
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        execution.close()
        db.close()
