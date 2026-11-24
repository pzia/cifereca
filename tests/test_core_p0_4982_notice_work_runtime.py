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


def _await_progress(runtime, predicate):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        snapshot = runtime.progress_snapshot()
        if predicate(snapshot):
            return snapshot
        Event().wait(.005)
    pytest.fail("documentary progress callback did not settle")


def test_documentary_progress_counts_real_work_and_admission_not_acquisitions(tmp_path):
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
    with open_core(tmp_path, notice_work_admission=budget) as core:
        runtime = core.notice_work
        initial = runtime.progress_snapshot()
        assert initial.workers_submitted == initial.documentary_revision == 0
        assert initial.scan_active is initial.hint_pending is False
        first = persist_exact_public_search_cover(
            core.storage, source(core.storage, "Ancien", 10),
        )
        source(core.storage, "Nouveau", 11)
        ref = request_exact_public_recompute(core.storage, first.document, now=100,
                                             expected_generation=0)
        leases = [budget.acquire(priority=int(TaskPriority.METADATA))
                  for _ in range(3)]
        try:
            assert all(lease is not None for lease in leases)
            assert runtime.request_tick() is None
            refused = runtime.progress_snapshot()
            assert refused.admission_refusals == 1
            assert refused.hint_pending is True and refused.scan_active is False
            assert refused.workers_submitted == 0
            assert journal.read_job(core.storage, ref).attempts == 0
        finally:
            for lease in leases:
                if lease is not None:
                    lease.release()

        scan = runtime.request_tick()
        assert scan is not None
        outcome = scan.result(timeout=10)
        worker, = outcome.handles
        assert worker.result(timeout=10) == "current"
        progress = _await_progress(runtime, lambda p: not p.scan_active
                                   and p.workers_current == 1)
        assert progress.scans_completed == 1
        assert progress.rows_examined == outcome.examined
        assert progress.corrupt_rows == outcome.corrupt
        assert progress.deferred_scans == int(outcome.deferred)
        assert progress.workers_submitted == 1
        assert progress.workers_running_or_queued == 0
        assert progress.workers_retry == progress.workers_error == 0
        assert progress.documentary_revision == 1
        assert journal.read_job(core.storage, ref).state == "current"
        assert core.storage.acquisitions() == [] and core.storage.entitlements() == []
        assert initial.workers_submitted == 0  # previous immutable snapshot
    assert runtime.progress_snapshot() is None

    with open_core(tmp_path, notice_work_admission=SharedTaskAdmission(
        max_inflight=4, reserve_user_slots=1,
    )) as core:
        # Counters are scoped to one runtime, not a durable backlog total.
        assert core.notice_work.progress_snapshot().workers_submitted == 0
        assert journal.read_job(core.storage, ref).state == "current"


def test_dispatch_scan_failure_does_not_invent_worker_or_document_progress(tmp_path, monkeypatch):
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
    with open_core(tmp_path, notice_work_admission=budget) as core:
        runtime = core.notice_work
        def broken(*, control=None):
            raise ValueError("synthetic scan interruption")
        original = runtime._pump.tick
        monkeypatch.setattr(runtime._pump, "tick", broken)
        scan = runtime.request_tick()
        assert scan is not None
        with pytest.raises(ValueError, match="synthetic scan interruption"):
            scan.result(timeout=10)
        failed = _await_progress(runtime, lambda p: not p.scan_active and p.scans_failed == 1)
        assert failed.scans_completed == failed.workers_submitted == 0
        assert failed.documentary_revision == 0
        monkeypatch.setattr(runtime._pump, "tick", original)
        scan = runtime.request_tick()
        assert scan is not None
        assert scan.result(timeout=10).handles == ()
        recovered = _await_progress(runtime, lambda p: not p.scan_active
                                    and p.scans_completed == 1)
        assert recovered.scans_failed == 1
        assert recovered.workers_submitted == recovered.documentary_revision == 0


def test_configured_capture_destination_used_by_maintenance(tmp_path, monkeypatch):
    import json
    from hashlib import sha256
    from cifereca.core.notice_parse_witness import ParseWitnessReview
    from cifereca.core.notice_regression_capture_intent import enqueue_capture_txn, capture_status

    destination = tmp_path / "chosen"
    (tmp_path / "config.json").write_text(json.dumps({
        "diagnostics": {"regression_capture_directory": str(destination)}
    }), encoding="utf-8")
    monkeypatch.setattr("cifereca.core.notice_regression_capture.downloads_dir",
                        lambda: tmp_path)
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
    with open_core(tmp_path, notice_work_admission=budget) as core:
        runtime = core.notice_work
        assert runtime._pump._capture_directory == destination
        with core.storage._lock, core.storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            diagnostic = enqueue_capture_txn(
                db, scope="a"*64, generation=1,
                review=ParseWitnessReview("parser_drift_suspected", ("critical_field_1",)),
                input_digest=sha256(b"body").hexdigest())
        assert not destination.exists()
        scan = runtime.request_tick()
        assert scan is not None
        scan.result(timeout=10)
        assert capture_status(core.storage, diagnostic) == {"capture_state": "partial"}
        assert len(list(destination.glob("cifereca-regression-*.zip"))) == 1
        assert not list(tmp_path.glob("cifereca-regression-*.zip"))


@pytest.mark.parametrize("configured", (None, "absolute", "relative", "", 123, "/bad\x00path"))
def test_capture_destination_configuration_without_optional_pump(tmp_path, configured):
    import json

    value = str(tmp_path / "chosen") if configured == "absolute" else configured
    (tmp_path / "config.json").write_text(json.dumps({
        "diagnostics": {"regression_capture_directory": value}
    }), encoding="utf-8")
    if configured not in (None, "absolute"):
        with pytest.raises(ValueError, match="notice_capture_invalid_directory"):
            open_core(tmp_path)
        assert not (tmp_path / "state.sqlite").exists()
    else:
        with open_core(tmp_path) as core:
            assert core.notice_work is None
            assert core.library._notice_regression_capture_directory == (
                tmp_path / "chosen" if configured == "absolute" else None)
            assert not (tmp_path / "chosen").exists()


def test_replay_handoff_does_not_replace_live_bridge(tmp_path, monkeypatch):
    from cifereca.core.notice_c_reparse_execution import PublicReplayRegistry

    budget = SharedTaskAdmission(max_inflight=5, reserve_user_slots=1)
    entered, release = Event(), Event()
    with open_core(tmp_path, notice_work_admission=budget) as core:
        runtime = core.notice_work
        old_pump = runtime._pump
        first = persist_exact_public_search_cover(
            core.storage, source(core.storage, "Ancien", 10),
        )
        source(core.storage, "Nouveau", 11)
        ref = request_exact_public_recompute(
            core.storage, first.document, now=100, expected_generation=0,
        )
        prepare = old_pump._bridge._prepare

        def hold(spec, control):
            entered.set()
            assert release.wait(10)
            return prepare(spec, control)

        monkeypatch.setattr(old_pump._bridge, "_prepare", hold)
        try:
            first_scan = runtime.request_tick()
            assert first_scan is not None
            worker, = first_scan.result(timeout=10).handles
            assert entered.wait(10)
            _await_progress(runtime, lambda p: not p.scan_active and p.workers_running_or_queued == 1)
            staged = PublicReplayRegistry()
            with runtime._lock:
                runtime._pending_replay_registry = staged
            scan = runtime.request_tick()
            assert scan is not None
            outcome = scan.result(timeout=10)
            assert outcome.deferred and outcome.handles == ()
            assert runtime._pump is old_pump
            assert runtime._pending_replay_registry is staged
            assert journal.read_job(core.storage, ref).attempts == 1
        finally:
            release.set()
        assert worker.result(timeout=10) == "current"
        _await_progress(runtime, lambda p: not p.scan_active and p.workers_running_or_queued == 0)
        # If the completion wake hit admission pressure, an ordinary later
        # lifecycle tick must still install the staged registry.
        if runtime._pending_replay_registry is not None:
            scan = runtime.request_tick()
            if scan is not None:
                scan.result(timeout=10)
        _await_progress(runtime, lambda p: not p.scan_active)
        assert runtime._pump is not old_pump
        assert runtime._replay_registry is staged
        assert runtime._pending_replay_registry is None
        assert journal.read_job(core.storage, ref).attempts == 1
