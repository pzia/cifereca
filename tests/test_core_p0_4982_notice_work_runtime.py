"""#4982: opt-in Core composition and admitted, single-flight heartbeat."""
from contextlib import nullcontext
from threading import Event
from time import monotonic

import pytest

from cifereca.core.runtime import CiferecaCore
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_exact_recompute import request_exact_public_recompute
from cifereca.core.notice_c_fetch_authority import PublicFetchAuthority
from cifereca.core.notice_c_fetch_execution import PublicFetchExecution, PublicFetchPort
from cifereca.storage import Storage
from cifereca.core.notice_c_public_search import search_public_notice_c
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
        def scan(*, control=None):
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


def test_real_core_restart_reclaims_expired_lease_but_not_fetch_authority(tmp_path):
    """Model a persisted interrupted attempt, not an OS-level process kill."""
    def admission():
        return SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)

    with open_core(tmp_path, notice_work_admission=admission()) as core:
        old = persist_exact_public_search_cover(
            core.storage, source(core.storage, "Ancien", 10),
        )
        changed = source(core.storage, "Nouveau", 11)
        ref = request_exact_public_recompute(
            core.storage, old.document, now=100, expected_generation=0,
        )
        abandoned = journal.claim_job(core.storage, ref, now=100, lease_seconds=30)
        assert abandoned is not None
        assert journal.read_job(core.storage, ref).attempts == 1
        # A live explicit grant is process-local. Only the pending hint is
        # durable, and the next runtime must not infer a transport from it.
        authority = PublicFetchAuthority(core.storage, clock=lambda: 100)
        try:
            fetch = authority.request(
                changed, transport_digest="a" * 64,
                implementation_digest="b" * 64,
                hold_authority=lambda: nullcontext(True),
                ttl_seconds=30, expected_generation=0,
            )
            assert journal.read_job(core.storage, fetch).attempts == 0
        finally:
            authority.close()
        assert core.brokers["tasks"]._executor is None

    with open_core(tmp_path, notice_work_admission=admission()) as core:
        runtime = core.notice_work
        assert core.brokers["tasks"]._executor is None
        assert read_consolidated_notice_cover(core.storage, old.document) == old
        assert journal.read_job(core.storage, ref).lease_until == abandoned.lease_until
        assert journal.read_job(core.storage, fetch).state == "pending"
        events = []

        def committed(doc, epoch):
            # Observer sees the *committed* C and journal, not prepared data.
            events.append((
                doc, epoch, journal.read_job(core.storage, ref).state,
                read_consolidated_notice_cover(core.storage, doc).epoch,
            ))

        assert runtime.bind_committed_observer(committed)
        scan = runtime.request_tick()
        assert scan is not None
        handles = scan.result(timeout=10).handles
        worker, = handles
        assert worker.result(timeout=10) == "current"
        receipt = journal.read_job(core.storage, ref)
        assert receipt.state == "current" and receipt.attempts == 2
        assert receipt.fence == abandoned.fence + 1 and receipt.lease_until == 0
        assert events == [(old.document, old.epoch + 1, "current", old.epoch + 1)]
        with pytest.raises(ValueError, match="lease_changed"):
            journal.fail_job(core.storage, abandoned, now=101, transient=True)
        assert journal.read_job(core.storage, ref) == receipt
        latest = read_consolidated_notice_cover(core.storage, old.document)
        assert latest.sources[0].source_epoch == changed.epoch
        assert latest.sources[0].public_bibliography.title == "Nouveau"
        assert journal.read_job(core.storage, fetch).attempts == 0
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []

    with open_core(tmp_path, notice_work_admission=admission()) as core:
        # Reopening never re-executes the completed generation or adopts a
        # fetch hint whose live consent/session/transport was not installed.
        assert read_consolidated_notice_cover(core.storage, old.document) == latest
        assert search_public_notice_c(core.storage, "nouveau", now=131).hits[0].document == old.document
        scan = core.notice_work.request_tick()
        assert scan is not None
        assert scan.result(timeout=10).handles == ()
        assert journal.read_job(core.storage, ref) == receipt
        assert journal.read_job(core.storage, fetch).attempts == 0
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_fetch_install_requires_same_storage_and_precedes_any_heartbeat(tmp_path):
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
    other = Storage(tmp_path / "other.sqlite")
    foreign = PublicFetchAuthority(other)
    port = PublicFetchPort("supplier", "a" * 64, "b" * 64,
                           lambda permit: pytest.fail("unexpected transport"))
    try:
        with open_core(tmp_path, notice_work_admission=budget) as core:
            runtime = core.notice_work
            authority = PublicFetchAuthority(core.storage)
            try:
                with pytest.raises(ValueError, match="invalid_registry"):
                    runtime.install_fetch_execution(PublicFetchExecution(foreign, (port,)))
                with pytest.raises(ValueError, match="invalid_registry"):
                    runtime.install_fetch_execution(PublicFetchExecution(authority))
                assert core.brokers["tasks"]._executor is None
                scan = runtime.request_tick()
                assert scan is not None and scan.result(timeout=10).handles == ()
                assert runtime.install_fetch_execution(PublicFetchExecution(authority, (port,))) is False
            finally:
                authority.close()
        assert not foreign._closed  # refused installation transfers no ownership
    finally:
        foreign.close()
        other.close()



def test_late_hints_drain_new_durable_work_without_another_external_tick(tmp_path, monkeypatch):
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
    scanned, release, committed = Event(), Event(), Event()
    calls = []
    with open_core(tmp_path, notice_work_admission=budget) as core:
        runtime = core.notice_work
        first = persist_exact_public_search_cover(
            core.storage, source(core.storage, "Ancien", 10),
        )
        tick = runtime._pump.tick

        def hold_first(*, control=None):
            calls.append("scan")
            outcome = tick(control=control)
            if len(calls) == 1:
                assert outcome.handles == ()
                scanned.set()
                assert release.wait(10)
            return outcome

        monkeypatch.setattr(runtime._pump, "tick", hold_first)
        assert runtime.bind_committed_observer(lambda *_: committed.set())
        scan = runtime.request_tick()
        try:
            assert scan is not None and scanned.wait(10)
            source(core.storage, "Nouveau", 11)
            ref = request_exact_public_recompute(
                core.storage, first.document, now=100, expected_generation=0,
            )
            # All three hints arrive after the first cursor was read.
            assert runtime.request_tick() is None
            assert runtime.request_tick() is None
            assert runtime.request_tick() is None
            assert journal.read_job(core.storage, ref).attempts == 0
        finally:
            release.set()
        assert scan.result(timeout=10).handles == ()
        assert committed.wait(10)
        assert calls == ["scan", "scan"]
        assert journal.read_job(core.storage, ref).state == "current"
        assert read_consolidated_notice_cover(
            core.storage, first.document,
        ).sources[0].public_bibliography.title == "Nouveau"
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []
    assert runtime.request_tick() is None
