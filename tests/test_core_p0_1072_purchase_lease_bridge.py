"""One existing broker/SQLite journal, synthetic transport + real source writer."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_work_bridge import AggregateWorkBridge, PreparedSource
from cifereca.core.notice_c_initial_publication import request_initial_publication_txn
from cifereca.core.notice_cover_projection import project_notice_cover_attribute
from cifereca.core.notice_cover_repository import _opaque_key, _persist_notice_cover_txn
from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_4982_fetch_execution import broker
from tests.test_core_p0_6366_public_c_sqlite_source_search import verified_public


@pytest.mark.parametrize("mode", [
    "no_admission", "valid", "revoke", "policy", "stale_a",
    "late_guard", "rollback", "expire",
])
def test_purchase_job_on_single_broker_with_exact_live_admission(tmp_path, mode):
    db = Storage(tmp_path / "purchase.sqlite")
    tasks = broker()
    clock = [100]
    execution = PurchaseIntentExecution(db, clock=lambda: clock[0])
    calls = []
    allowed = [True]

    @contextmanager
    def context():
        yield allowed[0]

    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        row = verified_public(title="Une fiche publique témoin")
        obs = admit_public_search_catalogue(
            row, identity_id="alice", observed_at=100,
            public_cover_url=row.thumbnail_url,
        )
        field = ("search_result", "thumbnail_url")
        attr = project_notice_cover_attribute(
            obs, field_path=field, cover_inventory_complete=True,
        )

        def execute(selected, check_authority, control):
            assert selected == candidate and check_authority()
            calls.append("transport")
            if mode == "revoke":
                allowed[0] = False
            if mode == "policy":
                set_policy(db, "false")
            if mode == "stale_a":
                newer = replace(snapshot.rights[0], evidence_ref="new-evidence")
                persist_attested_source_references(db, replace(snapshot, rights=(newer,)))
            if mode == "expire":
                clock[0] = 101

            @contextmanager
            def guard():
                if mode == "late_guard":
                    raise ValueError("synthetic producer revoked")
                yield

            def publish(sql):
                calls.append("publish")
                saved = _persist_notice_cover_txn(
                    sql, obs, attr, _opaque_key(attr.document), field, True, 0,
                )
                request_initial_publication_txn(sql, saved, now=100)
                return mode != "rollback"

            return PreparedSource(publish, guard)

        if mode != "no_admission":
            assert execution.attest(ref, snapshot, candidate, hold_context=context,
                                    execute=execute, ttl_seconds=1)
            assert not execution.attest(ref, snapshot, candidate, hold_context=context,
                                        execute=execute, ttl_seconds=1)
        bridge = AggregateWorkBridge(
            db, tasks, lambda *_: pytest.fail("not aggregate"), clock=lambda: clock[0],
            claim_purchase=execution.claim, prepare_purchase=execution.prepare,
        )
        handle = bridge.submit(ref)
        result = handle.result(timeout=10)
        job = journal.read_job(db, ref)
        if mode == "no_admission":
            assert result == "not_ready" and job.attempts == 0 and calls == []
        elif mode == "valid":
            assert result == "current" and job.state == "current"
            assert calls == ["transport", "publish"]
            assert len(search_public_notice_c(db, "publique", now=100).hits) == 1
            assert not execution.attest(ref, snapshot, candidate, hold_context=context,
                                        execute=execute, ttl_seconds=1)
        else:
            assert result == "error" and job.state == "error" and job.attempts == 1
            assert calls == (["transport", "publish"] if mode == "rollback" else ["transport"])
            assert search_public_notice_c(db, "publique", now=100).hits == ()
        if mode != "valid":
            with db._lock, db._connect() as sql:
                assert sql.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                                   (journal._PREFIX, journal._END)).fetchone() == (1,)
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        execution.close()
        tasks.close()
        db.close()


def test_pending_intent_does_not_recover_ephemeral_authority_after_close(tmp_path):
    db = Storage(tmp_path / "restart.sqlite")
    before = PurchaseIntentExecution(db, clock=lambda: 100)
    after = PurchaseIntentExecution(db, clock=lambda: 100)
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)

        @contextmanager
        def allowed():
            yield True

        assert before.attest(ref, snapshot, candidate, hold_context=allowed,
                             execute=lambda *_: pytest.fail("not started"), ttl_seconds=10)
        before.close()
        assert before.claim(ref, now=100, lease_seconds=30) is None
        assert after.claim(ref, now=100, lease_seconds=30) is None
        assert journal.read_job(db, ref).attempts == 0
        assert after.attest(ref, snapshot, candidate, hold_context=allowed,
                            execute=lambda *_: pytest.fail("not started"), ttl_seconds=10)
        lease = after.claim(ref, now=100, lease_seconds=30)
        assert lease is not None
        assert after.claim(ref, now=100, lease_seconds=30) is None
    finally:
        before.close()
        after.close()
        db.close()
