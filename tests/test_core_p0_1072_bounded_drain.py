"""Drain >32 qualified proofs through the one existing pump and bounded ticks."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_work_bridge import PreparedSource
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
from cifereca.storage import Storage
from tests.test_core_p0_1072_bounded_batch_handoff import inventory
from tests.test_core_p0_1072_durable_purchase_intent import request
from tests.test_core_p0_4982_fetch_execution import broker, setup


@pytest.mark.parametrize("mode", ("drain", "mixed", "expire"))
def test_same_pump_drains_65_attestations_with_no_second_inventory(tmp_path, mode):
    db = Storage(tmp_path / "drain.sqlite")
    tasks = broker()
    clock = [100]
    executor = PurchaseIntentExecution(db, clock=lambda: clock[0], attestation_capacity=128)
    calls = []
    refs = []

    @contextmanager
    def context():
        yield True

    def execute(selected, guard, control):
        assert guard()
        calls.append(selected.source.resource_id)
        return PreparedSource(lambda sql: True, nullcontext)

    try:
        snapshot, candidates = inventory(db)
        for i in range(0, len(candidates), 32):
            part = candidates[i:i + 32]
            proof = replace(snapshot, rights=snapshot.rights[i:i + 32],
                            status="partial", coverage="partial", pagination_complete=None)
            batch = request(db, proof, *part)
            for ref, candidate in zip(batch, part):
                assert executor.attest(ref, proof, candidate, hold_context=context,
                                       execute=execute, ttl_seconds=300)
            refs.extend(batch)
        assert len(executor.ready_refs()) == 32
        assert len(executor._live) == 65
        for n in range(90):
            spec = journal.JobSpec("fetch_source_notice", journal._PURCHASE_TARGET + f"{n:064x}",
                                   "a" * 64, journal._PURCHASE_INTENT_DIGEST)
            journal.request_job(db, spec, now=100, expected_generation=0)
        options = {}
        if mode == "mixed":
            _, _, authority, public_ref, transport, execution = setup(db, "same")
            options["fetch_execution"] = execution
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: clock[0],
                                    purchase_execution=executor, **options)
        for index in range(100):
            tick = pump.tick()
            assert tick.examined <= 32 and len(tick.handles) <= 2
            for handle in tick.handles:
                assert handle.result(timeout=10) == "current"
            if mode == "expire":
                assert 1 <= len(calls) <= 2
                clock[0] = 400
                assert pump.tick().handles == ()
                break
            if len(calls) == len(candidates) and (mode != "mixed" or
                    journal.read_job(db, public_ref).state == "current"):
                break
        if mode == "expire":
            assert sum(journal.read_job(db, r).attempts for r in refs) == len(calls)
            assert len(calls) < len(candidates)
        else:
            assert sorted(calls) == sorted(c.source.resource_id for c in candidates)
            assert all(journal.read_job(db, r).state == "current" for r in refs)
            if mode == "mixed":
                assert transport.calls == ["reserve", "execute", "release"]
            assert pump.tick().handles == ()
        assert executor.ready_refs() == ()
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        executor.close()
        tasks.close()
        db.close()
