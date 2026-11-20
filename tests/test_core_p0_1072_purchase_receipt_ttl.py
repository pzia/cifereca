"""Long-lived descriptive receipt freshness does not delete good prior C."""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_association import (
    _DETAIL_TTL_SECONDS, read_purchase_notice_association,
    record_purchase_association_txn, reattest_current_purchase_association,
)
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_purchase_publication import publish_purchase_public_txn
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_1072_nondestructive_purchase_publication import observation


@contextmanager
def allowed():
    yield True


@pytest.mark.parametrize("mode", ("success", "failed", "changed", "pending_new_a", "failed_new_a"))
def test_expired_receipt_renews_exact_generation_but_keeps_previous_c(tmp_path, mode):
    db = Storage(tmp_path / "ttl.sqlite")
    clock = [100]
    execution = PurchaseIntentExecution(db, clock=lambda: clock[0])
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")

        def claim(ref):
            assert execution.attest(
                ref, snapshot, candidate, hold_context=allowed,
                execute=lambda *_: pytest.fail("not dispatched"), ttl_seconds=30,
            )
            return execution.claim(ref, now=clock[0], lease_seconds=30)

        def publish(ref, lease):
            spec = journal.read_job(db, ref).spec
            incoming = observation(candidate, at=clock[0])
            with db._lock, db._connect() as sql:
                sql.execute("BEGIN IMMEDIATE")
                publication = publish_purchase_public_txn(
                    sql, spec, snapshot, candidate, incoming, now=clock[0],
                )
                record_purchase_association_txn(
                    sql, lease, snapshot, candidate, publication,
                    observed_at=clock[0], now=clock[0],
                )
                journal._complete_job_txn(sql, lease, now=clock[0])
            execution.revoke(ref)
            return publication

        def read():
            return read_purchase_notice_association(db, candidate.source, identity_id="A")

        def display_state(expected):
            from cifereca.core.notice_c_purchase_library_projection import (
                project_purchase_library_notices, purchase_library_notice, purchase_library_status,
            )
            from cifereca.core.notice_c_purchase_summary import projected_purchase_counts
            from tests.test_core_p0_1072_library_live_counts import remote
            card = remote("purchase", candidate.source, state=None)
            project_purchase_library_notices(db, [card], identity_id="A")
            assert purchase_library_notice(card) is not None
            status = purchase_library_status(card)
            assert (None if status is None else status.state) == expected
            counts = projected_purchase_counts([card], identity_id="A")
            wanted = {"available": 1}
            if expected is not None:
                wanted["refresh_" + expected] = 1
            assert counts == wanted

        ref, = request(db, snapshot, candidate)
        initial = publish(ref, claim(ref))
        persist_exact_public_search_cover(db, initial.source)
        before = read()
        assert before is not None and before.observed_at == 100
        display_state(None)
        clock[0] = 100 + _DETAIL_TTL_SECONDS - 1
        assert reattest_current_purchase_association(
            db, ref, snapshot, candidate, now=clock[0], report_change=True,
        ) == (ref, False)
        assert read() == before
        clock[0] += 1
        successor, changed_receipt = reattest_current_purchase_association(
            db, ref, snapshot, candidate, now=clock[0], report_change=True,
        )
        assert changed_receipt is True
        assert successor.key == ref.key and successor.generation == ref.generation + 1
        assert request(db, snapshot, candidate, now=clock[0]) == (successor,)
        assert journal.read_job(db, successor).state == "pending"
        assert read() == before
        display_state("pending")
        if mode in ("pending_new_a", "failed_new_a"):
            if mode == "failed_new_a":
                lease = claim(successor)
                journal.fail_job(db, lease, now=clock[0], transient=False)
            old_job = journal.read_job(db, successor)
            newer = replace(snapshot.rights[0], evidence_ref="newer-positive-a",
                            observed_at="2026-11-19T15:30:00Z")
            updated = replace(snapshot, rights=(newer,))
            persist_attested_source_references(db, updated)
            changed = replace(candidate, observed_at=newer.observed_at)
            assert read() is None
            assert request(db, updated, changed, now=clock[0]) == (successor,)
            assert reattest_current_purchase_association(
                db, successor, updated, changed, now=clock[0], report_change=True,
            ) == (successor, True)
            assert reattest_current_purchase_association(
                db, successor, updated, changed, now=clock[0], report_change=True,
            ) == (successor, False)
            assert journal.read_job(db, successor) == old_job
            assert read() == before
        elif mode == "changed":
            other = replace(candidate, public_locator="https://catalogue.example.org/edition/456")
            next_ref, = request(db, snapshot, other, now=clock[0])
            assert next_ref.generation == successor.generation + 1
            assert read() is None
            replay, = request(db, snapshot, candidate, now=clock[0])
            assert replay.generation == next_ref.generation + 1
            assert read() is None
        else:
            lease = claim(successor)
            if mode == "failed":
                journal.fail_job(db, lease, now=clock[0], transient=False)
                assert read() == before
                display_state("failed")
            else:
                new = publish(successor, lease)
                # New public response does not relabel old source fields.
                assert new.source == initial.source
                assert read().observed_at == clock[0]
                assert read().document == before.document
                display_state(None)
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        execution.close()
        db.close()
