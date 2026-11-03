"""Closed phase telemetry on actual source synchronization, without networking."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from cifereca.core import source_sync_timing as timing, sync_coordination
from cifereca.core.diagnostic_source_sync import public_fields, public_log_fields, log_message
from cifereca.locking import InterProcessLock
from cifereca.plugin_api import Cancelled
from cifereca.providers import ProviderConfigurationError
from tests.test_source_sync_shared_diagnostics import setup_source
from tests.test_sync_state_contract import _service


def terminal(service):
    rows = service.diagnostics.snapshot()
    return next(row["detail"] for row in reversed(rows)
                if row.get("verified_source_sync") and row["detail"]["phase"] == "completed")


def test_real_sync_measures_nested_intervals_without_changing_result(tmp_path, monkeypatch):
    service = _service(tmp_path)
    now = [0]
    monkeypatch.setattr(timing, "monotonic_ns", lambda: now[0])
    try:
        account, session, _ = setup_source(service, monkeypatch, tmp_path)
        session.capabilities.loans = True

        class ClockedLock(InterProcessLock):
            def __enter__(self):
                result = super().__enter__()
                now[0] += 2_000_000
                return result

        monkeypatch.setattr(sync_coordination, "InterProcessLock", ClockedLock)

        def operation(ms, value):
            now[0] += ms * 1_000_000
            return value

        session.library = lambda: operation(7, iter(()))
        monkeypatch.setattr(service, "_loans_for_account", lambda *_a, **_kw: operation(5, []))
        monkeypatch.setattr(service, "_mirror_remote_results", lambda *_a, **_kw: operation(3, 0))
        monkeypatch.setattr(service, "catalog_scan", lambda: operation(11, {"entries": 0}))
        result = service.synchronize_account_source(account, deep=True)
        assert result == {"provider": "provider.test", "account_id": "PRIVATE",
                          "loans": 0, "status": "partial", "library": 0, "catalog_scan": {"entries": 0}}
        fields = terminal(service)
        assert {key: fields[key] for key in timing.TIMING_FIELDS if key in fields} == {
            "lock_acquire_ms": 2, "locked_work_ms": 26, "loan_refresh_ms": 5,
            "library_read_ms": 7, "catalog_mirror_ms": 3, "catalog_scan_ms": 11,
            "canonical_resources_ms": 0,
        }
        assert public_log_fields(log_message(fields)) == fields
        assert "PRIVATE" not in json.dumps(fields)
        assert timing._CURRENT.get() is None
    finally:
        service.close()


@pytest.mark.parametrize("error", [RuntimeError("PRIVATE"), Cancelled("PRIVATE")])
def test_failed_inventory_is_timed_and_original_exception_preserved(tmp_path, monkeypatch, error):
    service = _service(tmp_path)
    now = [0]
    monkeypatch.setattr(timing, "monotonic_ns", lambda: now[0])
    try:
        account, session, _ = setup_source(service, monkeypatch, tmp_path)

        def fail():
            now[0] += 9_000_000
            raise error

        session.library = fail
        with pytest.raises(type(error)) as caught:
            service.synchronize_account_source(account)
        assert caught.value is error
        fields = terminal(service)
        assert fields["library_read_ms"] == fields["locked_work_ms"] == 9
        assert "catalog_mirror_ms" not in fields
        assert fields["status"] == ("cancelled" if isinstance(error, Cancelled) else "failed")
        assert "PRIVATE" not in json.dumps(fields)
        assert timing._CURRENT.get() is None
    finally:
        service.close()


def test_busy_lock_records_attempt_without_inventory_or_fake_locked_work(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account, _, calls = setup_source(service, monkeypatch, tmp_path)
        with InterProcessLock(tmp_path / "writer.lock"):
            with pytest.raises(ProviderConfigurationError):
                service.synchronize_account_source(account)
        fields = terminal(service)
        assert fields["code"] == "operation_busy"
        assert "lock_acquire_ms" in fields
        assert not (set(fields) & (timing.TIMING_FIELDS - {"lock_acquire_ms"}))
        assert not calls
    finally:
        service.close()


def test_measurements_are_isolated_between_workers_nested_scopes_and_reuse():
    barrier = Barrier(2)

    def worker(field):
        outer, inner = {}, {}
        with timing.timing_scope(outer), timing.measure(field):
            barrier.wait(timeout=3)
            with timing.timing_scope(inner), timing.measure("catalog_scan_ms"):
                pass
        assert timing._CURRENT.get() is None
        # No scope means no measurement or shared accumulator on reuse.
        with timing.measure("rights_mirror_ms"):
            pass
        return outer, inner

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, field) for field in ("library_read_ms", "loan_refresh_ms")]
        results = [future.result(timeout=4) for future in futures]
    for field, (outer, inner) in zip(("library_read_ms", "loan_refresh_ms"), results):
        assert set(outer) == {field}
        assert set(inner) == {"catalog_scan_ms"}


@pytest.mark.parametrize("fail_at", [1, 2])
def test_broken_clock_does_not_replace_the_operation_or_its_exception(monkeypatch, fail_at):
    calls = [0]
    samples = {}
    error = RuntimeError("original provider error")

    def clock():
        calls[0] += 1
        if calls[0] == fail_at:
            raise ValueError("PRIVATE clock error")
        return 0

    monkeypatch.setattr(timing, "monotonic_ns", clock)
    with pytest.raises(RuntimeError) as caught:
        with timing.timing_scope(samples), timing.measure("library_read_ms"):
            raise error
    assert caught.value is error
    assert samples == {}
    assert timing._CURRENT.get() is None


@pytest.mark.parametrize("value", [True, -1, 86_400_001, "PRIVATE", float("nan"), {}])
def test_public_schema_rejects_malformed_timing(value):
    fields = dict(schema_version=1, provider_id="provider.test", operation_kind="source_sync",
                  phase="completed", status="success", code="source_sync_complete")
    for key in timing.TIMING_FIELDS:
        assert public_fields({**fields, key: value}) == {}
    assert public_fields({**fields, "PRIVATE_phase_ms": 1}) == {}
    assert public_fields({**fields, "phase": "start", "status": "running",
                          "code": "source_sync_started", "lock_acquire_ms": 0}) == {}


@pytest.mark.parametrize("canonical_ms", [0, 9, 73])
def test_source_sync_separates_typed_loan_and_canonical_observation_wall_time(
    tmp_path, monkeypatch, canonical_ms,
):
    """Two distinct real Core phase invocations, no extra provider read.

    The clock is entirely synthetic: these durations are not claims about
    Paris latency, BnF latency or the number of HTTP requests.
    """
    from cifereca.core import canonical_sync_stage

    service = _service(tmp_path)
    now = [0]
    monkeypatch.setattr(timing, "monotonic_ns", lambda: now[0])
    try:
        account, session, _ = setup_source(service, monkeypatch, tmp_path)
        session.capabilities.loans = True
        observed = []

        def loan_read(*_args, **_kwargs):
            observed.append("loan_inventory")
            now[0] += 5_000_000
            return []

        def canonical_read(_service, _account, *, cancelled, snapshot_sink):
            observed.append("canonical_resources")
            assert cancelled() is False
            assert callable(snapshot_sink)
            now[0] += canonical_ms * 1_000_000
            return None

        monkeypatch.setattr(service, "_loans_for_account", loan_read)
        monkeypatch.setattr(
            canonical_sync_stage, "observe_canonical_rights_in_sync",
            canonical_read,
        )
        service.synchronize_account_source(account)
        fields = terminal(service)
        assert observed == ["loan_inventory", "canonical_resources"]
        assert fields["loan_refresh_ms"] == 5
        assert fields["canonical_resources_ms"] == canonical_ms
        assert fields["locked_work_ms"] >= 5 + canonical_ms
        assert timing._CURRENT.get() is None
    finally:
        service.close()


def test_failed_canonical_observation_is_still_timed_without_new_retry(
    tmp_path, monkeypatch,
):
    from cifereca.core import canonical_sync_stage

    service = _service(tmp_path)
    now = [0]
    monkeypatch.setattr(timing, "monotonic_ns", lambda: now[0])
    try:
        account, _, _ = setup_source(service, monkeypatch, tmp_path)
        error = RuntimeError("PRIVATE SOURCE ERROR")
        calls = []

        def fail(*_args, **_kwargs):
            calls.append(1)
            now[0] += 12_000_000
            raise error

        monkeypatch.setattr(
            canonical_sync_stage, "observe_canonical_rights_in_sync", fail,
        )
        with pytest.raises(RuntimeError) as observed:
            service.synchronize_account_source(account)
        assert observed.value is error
        assert calls == [1]
        fields = terminal(service)
        assert fields["canonical_resources_ms"] == 12
        assert fields["status"] == "failed"
        assert "PRIVATE SOURCE ERROR" not in json.dumps(fields)
        assert timing._CURRENT.get() is None
    finally:
        service.close()
