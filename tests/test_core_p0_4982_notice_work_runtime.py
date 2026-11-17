"""#4982: opt-in Core composition and admitted, single-flight heartbeat."""
from threading import Event
from time import monotonic

import pytest

from cifereca.core.runtime import CiferecaCore
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_exact_recompute import request_exact_public_recompute
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_queue import TaskPriority
from tests.test_core_p0_4982_exact_public_recompute import source


def open_core(tmp_path, **options):
    return CiferecaCore.open(
        config_path=tmp_path / "config.json",
        database_path=tmp_path / "state.sqlite",
        builtin_ui=False, no_plugins=True, activate_plugins=False,
        diagnostics_enabled=False, **options,
    )


def test_default_composition_inert_and_invalid_budget_rejected(tmp_path):
    with open_core(tmp_path) as core:
        assert core.notice_work is None
        assert core.brokers["tasks"].shared_admission is None
    for budget in (
        SharedTaskAdmission(max_inflight=2, reserve_user_slots=1),
        SharedTaskAdmission(max_inflight=3, reserve_user_slots=0),
        SharedTaskAdmission(max_inflight=4, reserve_user_slots=1, max_per_scope=2),
    ):
        with pytest.raises(ValueError, match="invalid_runtime_admission"):
            open_core(tmp_path, notice_work_admission=budget)


def test_real_core_heartbeat_durable_work_and_shutdown(tmp_path):
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
    with open_core(tmp_path, notice_work_admission=budget) as core:
        runtime = core.notice_work
        assert runtime is not None
        broker = core.brokers["tasks"]
        assert broker.shared_admission is budget and broker._executor is None
        assert "notice_work" not in core.brokers
        first = persist_exact_public_search_cover(core.storage, source(core.storage, "Ancien", 10))
        source(core.storage, "Nouveau", 11)
        ref = request_exact_public_recompute(
            core.storage, first.document, now=100, expected_generation=0,
        )
        assert journal.read_job(core.storage, ref).attempts == 0
        assert broker._executor is None  # no implicit startup dispatch
        events = []
        assert runtime.bind_committed_observer(lambda doc, epoch: events.append((doc, epoch)))
        # Exhaust background admission independently of the broker.
        held = [budget.acquire(priority=int(TaskPriority.METADATA)) for _ in range(3)]
        try:
            assert all(lease is not None for lease in held)
            assert runtime.request_tick() is None
            assert journal.read_job(core.storage, ref).attempts == 0
            assert broker.submit(lambda control: "interactive",
                                 priority=TaskPriority.USER_ACTION).result(timeout=10) == "interactive"
        finally:
            for lease in held:
                if lease is not None:
                    lease.release()
        # Worker may release its admission after the Future becomes done.
        until = monotonic() + 10
        while budget.snapshot()["inflight"] and monotonic() < until:
            Event().wait(.005)
        scan = runtime.request_tick()
        assert scan is not None
        result = scan.result(timeout=10)
        assert result.examined <= 32
        aggregate, = result.handles
        assert aggregate.result(timeout=10) == "current"
        assert events == [(first.document, first.epoch + 1)]
        assert journal.read_job(core.storage, ref).state == "current"
        assert read_consolidated_notice_cover(core.storage, first.document).sources[0].public_bibliography.title == "Nouveau"
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []
    assert runtime.request_tick() is None
    assert runtime.bind_committed_observer(lambda *args: None) is False


def test_coordinator_coalesces_ticks_and_detaches_observer(tmp_path, monkeypatch):
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
    entered, release = Event(), Event()
    with open_core(tmp_path, notice_work_admission=budget) as core:
        runtime = core.notice_work
        def scan():
            entered.set()
            assert release.wait(10)
            return "scanned"
        monkeypatch.setattr(runtime._pump, "tick", scan)
        try:
            handle = runtime.request_tick()
            assert handle is not None and entered.wait(10)
            assert runtime.request_tick() is None
            seen = []
            runtime.bind_committed_observer(lambda *args: seen.append(args))
            runtime.bind_committed_observer(None)
            runtime._notify(None, 1)
            assert seen == []
            runtime.close()
            assert runtime.request_tick() is None
        finally:
            release.set()
        assert handle.result(timeout=10) == "scanned"
