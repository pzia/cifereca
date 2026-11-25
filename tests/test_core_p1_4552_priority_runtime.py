"""Q11: the existing runtime forwards a replaceable owner snapshot."""
from contextlib import closing
from threading import Event
from time import monotonic

import pytest

from cifereca.core.notice_c_work_runtime import NoticeWorkRuntime
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_4982_dispatch_rotation import row, KINDS


def test_runtime_forwards_latest_hints_without_creating_work(tmp_path, monkeypatch):
    with closing(Storage(tmp_path / "priority.sqlite")) as storage:
        budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
        broker = TaskBrokerImpl(object(), max_workers=3, shared_admission=budget)
        runtime = NoticeWorkRuntime(storage, broker, clock=lambda: 100)
        _, first = row(KINDS[3], 1)
        _, second = row(KINDS[3], 2)
        seen = []
        tick = runtime._pump.tick

        def observe(*, control=None, priority_hints=()):
            seen.append(priority_hints)
            return tick(control=control, priority_hints=priority_hints)

        monkeypatch.setattr(runtime._pump, "tick", observe)

        def scan():
            handle = runtime.request_tick()
            assert handle is not None
            outcome = handle.result(timeout=10)
            assert outcome.handles == ()
            deadline = monotonic() + 10
            while (runtime._scan is not None or budget.snapshot()["inflight"]) and monotonic() < deadline:
                Event().wait(.005)
            assert runtime._scan is None and not budget.snapshot()["inflight"]

        try:
            assert runtime.replace_priority_hints(((first, "interactive_visible"),))
            assert runtime.replace_priority_hints(((second, "recent_acquisition"),))
            # Rejected snapshots leave the last valid one intact.
            with pytest.raises(ValueError, match="invalid_priority_hints"):
                runtime.replace_priority_hints(((first, "invalid"),))
            assert runtime.progress_snapshot().scans_completed == 0
            scan()
            assert seen == [((second, "recent_acquisition"),)]
            assert runtime.replace_priority_hints(())
            scan()
            assert seen[-1] == ()
            assert storage.entitlements() == storage.acquisitions() == []
            runtime.close()
            assert not runtime.replace_priority_hints(((first, "interactive_visible"),))
        finally:
            pool = broker._executor
            runtime.close()
            broker.close()
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)


def test_desktop_context_resolves_exact_ready_generation_without_extra_scan(tmp_path):
    from types import SimpleNamespace as NS
    from cifereca import app
    from cifereca.core import notice_c_job_journal as journal
    from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
    from cifereca.core.notice_c_exact_recompute import IMPLEMENTATION_DIGEST
    from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
    from cifereca.core.notice_c_dispatch_rotation import RotationOffer

    with closing(Storage(tmp_path / "context.sqlite")) as storage:
        docs = [canonical_cover_document_ref("edition:" + str(i)) for i in range(3)]
        jobs = [journal.request_job(
            storage, journal.public_job_spec(
                "recompute_aggregate", doc, input_digest="1"*64,
                implementation_digest=IMPLEMENTATION_DIGEST,
            ), now=100, expected_generation=0,
        ) for doc in docs]
        # Choose the lexically last ready key as the visible selection:
        # ordinary key order would not promote it first.
        order = sorted(range(3), key=lambda i: jobs[i].key)
        visible, recent = docs[order[-1]], docs[order[1]]
        recent_row = NS(_cover_document_ref=recent, _cover_public_c_link=NS(document=recent))
        selected = [NS(_cover_document_ref=visible)]
        mapped = [True]
        snapshots = []
        runtime = NS(bind_committed_observer=lambda _: True,
                     replace_priority_documents=lambda value: snapshots.append(value),
                     request_tick=lambda: None)
        window = NS(_closed=False, _identity_context_generation=1, _recent_loaded_generation=1,
                    document_covers=NS(refresh_committed_source_in_worker=lambda *_: None),
                    search_page=NS(get_mapped=lambda: mapped[0],
                                   selection=NS(get_selected_item=lambda: selected[0])),
                    recent_page=NS(get_mapped=lambda: False, store=NS(
                        get_n_items=lambda: 1, get_item=lambda _: recent_row)))
        app._bind_desktop_notice_work(NS(notice_work=runtime), window)
        callbacks = []
        app._start_notice_work_wakeup(window, timeout_add_seconds=lambda _, cb: callbacks.append(cb) or 1,
                                     source_remove=lambda _: None)
        assert callbacks[0]() is True
        assert snapshots[-1] == ((visible, "interactive_visible"), (recent, "recent_acquisition"))
        budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
        broker = TaskBrokerImpl(object(), max_workers=3, shared_admission=budget)
        owner = NoticeWorkRuntime(storage, broker, clock=lambda: 100)
        try:
            assert owner.replace_priority_documents(snapshots[-1])
            pump = PublicNoticeWorkPump(storage, broker, clock=lambda: 100)
            pump._bridge.offer = lambda ref, **_: RotationOffer("accepted", ref)
            outcome = pump.tick(priority_targets=owner._priority_targets)
            assert outcome.handles[0] == jobs[order[-1]]
            assert outcome.examined <= 32
            mapped[0] = False
            window._identity_context_generation = 2
            assert callbacks[0]() is True
            assert snapshots[-1] == ()
            assert owner.replace_priority_documents(snapshots[-1])
            assert owner._priority_targets == ()
        finally:
            owner.close()
            broker.close()
