"""Integrated explicit pump: real broker, journal and both canonical writers."""
import time
from threading import Event

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_exact_recompute as exact
from cifereca.core import notice_c_origin_recompute as origin
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump, _SUPPORTED
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_queue import TaskPriority
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_4982_notice_c_job_journal import enqueue, spec
from tests.test_core_p0_4982_exact_public_recompute import source
from tests.test_core_p0_4982_origin_public_recompute import (
    setup_group, refresh, NEW_URL, A_URL,
)
from tests.test_core_p1_2385_notice_cover_persistence import PUBLIC
from dataclasses import replace


def test_real_publication_both_ports_with_bounded_scan_and_backpressure(
    tmp_path, monkeypatch,
):
    path = tmp_path / "work.sqlite"
    db = Storage(path)
    try:
        own = persist_exact_public_search_cover(db, source(db, "Ancien", 10))
        source(db, "Nouveau", 11)
        group = setup_group(db, tmp_path, monkeypatch)
        refresh(db)
        single = exact.request_exact_public_recompute(
            db, own.document, now=100, expected_generation=0,
        )
        multi = origin.request_origin_public_recompute(
            db, group.document, now=100, expected_generation=0,
        )
        unknown = [enqueue(db, spec(key=replace(PUBLIC, record_id=f"unknown-{i}")))
                   for i in range(65)]
        unsupported = enqueue(db, spec("recompute_aggregate", value="unknown-contract"))
    finally:
        db.close()

    db = Storage(path)
    budget = SharedTaskAdmission(max_inflight=2, reserve_user_slots=1)
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=budget)
    pump = PublicNoticeWorkPump(db, broker, clock=lambda: 100)
    reservation = budget.acquire(priority=int(TaskPriority.METADATA))
    try:
        assert reservation is not None
        # Bounded class slices, even if all scanned jobs are unsupported.
        saw_deferral = False
        for _ in range(8):
            tick = pump.tick()
            assert tick.examined <= 32 and tick.handles == ()
            if tick.deferred:
                saw_deferral = True
                break
        assert saw_deferral
        assert journal.read_job(db, single).attempts == 0
        assert journal.read_job(db, multi).attempts == 0
        user = budget.acquire(priority=int(TaskPriority.USER_ACTION))
        assert user is not None
        user.release()
        reservation.release()
        # Completion callbacks race result(); wait only in the test, never
        # in the pump. A fixed clock is sufficient for initial work.
        deadline = time.monotonic() + 10
        outcomes = []
        while time.monotonic() < deadline:
            tick = pump.tick()
            assert tick.examined <= 32 and len(tick.handles) <= 2
            outcomes.extend(h.result(timeout=5) for h in tick.handles)
            if all(journal.read_job(db, ref).state == "current"
                   for ref in (single, multi)):
                break
            Event().wait(.01)
        assert outcomes == ["current", "current"]
        assert journal.read_job(db, single).state == "current"
        assert journal.read_job(db, multi).state == "current"
        new_own = read_consolidated_notice_cover(db, own.document)
        new_group = read_consolidated_notice_cover(db, group.document)
        assert new_own.epoch == own.epoch + 1
        assert new_own.sources[0].public_bibliography.title == "Nouveau"
        assert new_group.epoch == group.epoch + 1
        assert {c.url for c in new_group.candidates} == {A_URL, NEW_URL}
        assert all(journal.read_job(db, ref).attempts == 0
                   for ref in (*unknown, unsupported))
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        reservation.release()
        broker.close()
        db.close()


def test_scan_filter_and_reentrant_tick(tmp_path):
    storage = Storage(tmp_path / "work.sqlite")
    budget = SharedTaskAdmission(max_inflight=3, reserve_user_slots=1)
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=budget)
    try:
        unknown = enqueue(storage, spec())
        for value in ((), [], (("other", "0" * 64),),
                      (("recompute_aggregate", "secret"),),
                      (_SUPPORTED[0], _SUPPORTED[0])):
            with pytest.raises(ValueError, match="scan_filter"):
                journal.read_ready_page(storage, now=100, supported=value)
        page = journal.read_ready_page(storage, now=100, supported=_SUPPORTED)
        assert page.refs == () and page.examined == 1
        assert journal.read_ready_page(storage, now=100).refs == (unknown,)
        pump = PublicNoticeWorkPump(storage, broker, clock=lambda: 100)
        with pump._tick_lock:
            tick = pump.tick()
            assert tick.busy and tick.examined == 0 and tick.handles == ()
        tick = pump.tick()
        assert not tick.busy and not tick.deferred and 1 <= tick.examined <= 32
        assert journal.read_job(storage, unknown).attempts == 0
    finally:
        broker.close()
        storage.close()


def test_real_journal_rotation_offers_both_enabled_classes(tmp_path, monkeypatch):
    from cifereca.core.notice_c_dispatch_rotation import RotationOffer
    from cifereca.core.notice_c_cover_index import canonical_cover_document_ref

    db = Storage(tmp_path / "rotation.sqlite")
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                            SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
    try:
        pump = PublicNoticeWorkPump(db, broker, clock=lambda: 100)
        refs = {}
        # Select distinct work classes, not implementation table offsets.
        from cifereca.core import notice_c_origin_relink as relink
        ports = (("recompute_aggregate", exact.IMPLEMENTATION_DIGEST),
                 ("relink_notices", relink.IMPLEMENTATION_DIGEST))
        assert all(port in _SUPPORTED for port in ports)
        for (kind, implementation), suffix in zip(ports, ("a", "b")):
            value = journal.public_job_spec(
                kind, canonical_cover_document_ref("edition:rotation-" + suffix),
                input_digest="a" * 64, implementation_digest=implementation,
            )
            refs[kind] = enqueue(db, value)
        visited = []
        def offer(ref, *, circuit):
            assert circuit == "local"
            visited.append(ref)
            return RotationOffer("accepted", ref)
        monkeypatch.setattr(pump._bridge, "offer", offer)
        tick = pump.tick()
        assert tick.examined <= 32 and len(tick.handles) == 2
        assert set(visited) == set(refs.values())
        assert all(journal.read_job(db, ref).attempts == 0 for ref in visited)
    finally:
        broker.close()
        db.close()
