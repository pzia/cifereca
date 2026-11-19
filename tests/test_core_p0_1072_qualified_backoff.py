"""Typed transient failure, durable delay, exact reattestation, no extra worker."""
from contextlib import contextmanager, nullcontext

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_work_bridge import AggregateWorkBridge, PreparedSource
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.plugin_api.provider_purchase_enrichment import PurchaseEnrichmentDeferred
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_4982_fetch_execution import broker


@pytest.mark.parametrize("mode", ("success", "revoke", "restart", "exhausted"))
def test_qualified_backoff_is_durable_and_requires_current_authority(tmp_path, mode):
    path = tmp_path / "backoff.sqlite"
    db = Storage(path)
    tasks = broker()
    now = [100]
    allowed = [True]
    execution = PurchaseIntentExecution(db, clock=lambda: now[0])
    calls = []

    @contextmanager
    def context():
        yield allowed[0]

    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)

        def execute(selected, guard, control):
            assert selected == candidate and guard()
            calls.append(now[0])
            if len(calls) == 1 or mode == "exhausted":
                raise PurchaseEnrichmentDeferred("rate_limited", 60)
            return PreparedSource(lambda sql: True, nullcontext)

        def attest():
            return execution.attest(ref, snapshot, candidate, hold_context=context,
                                    execute=execute, ttl_seconds=300)

        def bridge():
            return AggregateWorkBridge(
                db, tasks, lambda *_: pytest.fail("not aggregate"), clock=lambda: now[0],
                claim_purchase=execution.claim, prepare_purchase=execution.prepare,
            )

        assert attest()
        assert bridge().submit(ref).result(timeout=10) == "transient_failure"
        job = journal.read_job(db, ref)
        assert job.state == "pending" and job.due == 160 and job.attempts == 1
        assert ref in execution.ready_refs()
        now[0] = 159
        assert bridge().submit(ref).result(timeout=10) == "not_ready"
        assert calls == [100]
        now[0] = 160
        if mode == "revoke":
            allowed[0] = False
            assert bridge().submit(ref).result(timeout=10) == "not_ready"
            assert calls == [100]
            return
        if mode == "restart":
            execution.close()
            db.close()
            db = Storage(path)
            execution = PurchaseIntentExecution(db, clock=lambda: now[0])
            assert bridge().submit(ref).result(timeout=10) == "not_ready"
            assert attest()
        if mode == "exhausted":
            assert bridge().submit(ref).result(timeout=10) == "transient_failure"
            now[0] = 220
            assert bridge().submit(ref).result(timeout=10) == "error"
            assert journal.read_job(db, ref).reason == "exhausted"
            assert calls == [100, 160, 220]
        else:
            assert bridge().submit(ref).result(timeout=10) == "current"
            assert journal.read_job(db, ref).attempts == 2
            assert calls == [100, 160]
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        execution.close()
        tasks.close()
        db.close()


@pytest.mark.parametrize("reason, delay", [("unknown", 60), ("rate_limited", True),
                                           ("unavailable", 0), ("budget_busy", 3000000)])
def test_invalid_deferred_contract_refused(reason, delay):
    with pytest.raises(ValueError, match="invalid_purchase_enrichment_deferral"):
        PurchaseEnrichmentDeferred(reason, delay)
