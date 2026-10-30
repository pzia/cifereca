"""Real Core/SQLite/worker source-sync decisions and independent public channels."""
import json
import logging
import threading
from collections import Counter
from types import SimpleNamespace as NS

import pytest

from cifereca.core import sync_coordination
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_source_sync import public_fields, public_log_fields, log_message
from cifereca.diagnostics import Diagnostics
from cifereca.locking import InterProcessLock
from cifereca.models import Account
from cifereca.plugin_api import Cancelled
from cifereca.providers import ProviderConfigurationError
from cifereca.task_queue import PriorityExecutor
from tests.test_sync_state_contract import _service


@pytest.fixture
def application_log(tmp_path):
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    previous = logger.level
    handler = logging.FileHandler(path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield path
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()


def setup_source(service, monkeypatch, tmp_path, *, registered=True):
    account = Account(display_name="PRIVATE", unique_id="PRIVATE", provider_id="provider.test",
                      credential_id="PRIVATE", identity_id="default")
    service.accounts = [account]
    # Real source Sync now validates every provider's private binding.
    # Telemetry fixtures must register the same enabled connection as a
    # configured source; bypassing it would test an unadmitted inventory.
    service.storage.provider_connection_put({
        "identity_id": account.identity_id, "provider_id": account.provider_id,
        "credential_id": account.credential_id, "enabled": True,
    })
    calls = []
    session = NS(connected=True, provider_id=account.provider_id, credential_id="PRIVATE",
                 capabilities=NS(loans=False, personal_library=True),
                 library=lambda: calls.append("inventory") or [], close=lambda: None)
    service._sessions[service._session_key(account.provider_id, account.credential_id)] = session
    monkeypatch.setattr(service.registry, "maybe_get", lambda pid:
                        NS(descriptor=NS(id=pid)) if registered and pid == "provider.test" else None)
    monkeypatch.setattr(sync_coordination, "acquisition_lock_file", lambda: tmp_path / "writer.lock")
    return account, session, calls


@pytest.mark.parametrize("outcome", ["success", "failed", "skipped", "cancelled", "busy"])
def test_actual_sync_outcome_is_shared_and_correlated_without_private_fields(
    tmp_path, monkeypatch, application_log, outcome,
):
    service = _service(tmp_path)
    pool = PriorityExecutor(max_workers=1)
    held = None
    try:
        account, session, calls = setup_source(service, monkeypatch, tmp_path)
        if outcome == "skipped":
            service.storage.metadata_set("sync.source.enabled." + service.sync_source_id(account), "0")
        if outcome in {"failed", "cancelled"}:
            def fail():
                raise (Cancelled("PRIVATE") if outcome == "cancelled" else
                       RuntimeError("PRIVATE https://private/?token=PRIVATE /home/PRIVATE"))
            session.library = fail
        if outcome == "busy":
            held = InterProcessLock(tmp_path / "writer.lock")
            held.__enter__()
        future = pool.submit(0, lambda: service.synchronize_account_source(account))
        if outcome in {"failed", "cancelled", "busy"}:
            with pytest.raises((RuntimeError, Cancelled, ProviderConfigurationError)):
                future.result(timeout=3)
        else:
            future.result(timeout=3)
        payload = query_diagnostic_events(service.diagnostics, log_path=application_log, limit=100)
        channels = {}
        for source in ("core_events", "app_log"):
            rows = [r for r in payload["events"] if r["source"] == source
                    and r.get("detail", {}).get("operation_kind") == "source_sync"]
            assert len(rows) == 2
            channels[source] = Counter((json.dumps(r["detail"], sort_keys=True), r["message"])
                                       for r in rows)
        assert channels["core_events"] == channels["app_log"]
        terminal = next(r for r in payload["events"] if r["source"] == "core_events"
                        and r.get("detail", {}).get("phase") == "completed")
        expected = "failed" if outcome == "busy" else outcome
        assert terminal["status"] == terminal["detail"]["status"] == expected
        assert terminal["detail"]["provider_id"] == "provider.test"
        assert type(terminal["detail"]["duration_ms"]) is int
        worker = next(r for r in payload["events"] if "task correlation" in r["message"])
        assert worker["task_ref"] == terminal["detail"]["task_ref"]
        if outcome in {"skipped", "busy"}:
            assert not calls
        assert "PRIVATE" not in json.dumps(payload)
        # Storage's pre-existing local path log is outside this emission.
        assert "PRIVATE" not in "\n".join(line for line in application_log.read_text().splitlines()
                                           if "TRACE source sync" in line)
    finally:
        if held:
            held.__exit__(None, None, None)
        pool.shutdown(wait=True)
        service.close()


def test_unknown_provider_is_explicit_and_unscoped_call_never_fabricates_task(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account, _, _ = setup_source(service, monkeypatch, tmp_path, registered=False)
        service.synchronize_account_source(account)
        payload = query_diagnostic_events(service.diagnostics, log_path=tmp_path / "missing", source="core_events")
        rows = [r for r in payload["events"] if r.get("detail", {}).get("operation_kind") == "source_sync"]
        assert len(rows) == 2
        assert all(r["detail"]["provider_id"] == "source_inconnue" for r in rows)
        assert all("task_ref" not in r["detail"] for r in rows)
    finally:
        service.close()


def fields():
    return dict(schema_version=1, provider_id="provider.test", operation_kind="source_sync",
                phase="completed", status="success", code="source_sync_complete")


@pytest.mark.parametrize("attack", [
    {**fields(), "provider_id": "PRIVATE@example.invalid"},
    {**fields(), "code": "PRIVATE"}, {**fields(), "credential_id": "PRIVATE"},
    {**fields(), "duration_ms": True}, {**fields(), "library": 10**30},
    {**fields(), "task_ref": "PRIVATE"}, {**fields(), "phase": "start"},
    {**fields(), "schema_version": True},
])
def test_invalid_fields_never_get_promoted(attack):
    assert public_fields(attack) == {}


def test_generic_plugin_add_cannot_attest_an_operation(tmp_path):
    diag = Diagnostics()
    diag.add("SYNC", "source sync", "success", detail=fields())
    rows = query_diagnostic_events(diag, source="core_events", log_path=tmp_path / "missing")["events"]
    assert "detail" not in rows[0]


@pytest.mark.parametrize("body", [
    log_message(fields()) + " PRIVATE",
    log_message(fields()).replace('"schema_version":1', '"schema_version":2,"schema_version":1'),
    "TRACE source sync " + "[" * 1500,
    "TRACE source sync " + json.dumps({**fields(), "token": "PRIVATE"}),
])
def test_log_requires_complete_canonical_schema(body):
    assert public_log_fields(body) == {}


def test_kobo_partial_decision_and_successful_source_sync_are_independent(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account, _, _ = setup_source(service, monkeypatch, tmp_path)
        service.diagnostics.add_core_kobo_backup_decision(dict(
            status="partial", persisted_enabled=True, connected_kobo=3,
            eligible_kobo=3, interval_minutes=60))
        service.synchronize_account_source(account)
        rows = query_diagnostic_events(service.diagnostics, source="core_events",
                                       log_path=tmp_path / "missing")["events"]
        kobo = next(r for r in rows if r["module"] == "KOBO")
        sync = next(r for r in rows if r.get("detail", {}).get("phase") == "completed")
        assert kobo["detail"]["status"] == "partial"
        assert sync["detail"]["status"] == "success"
        assert "task_ref" not in kobo["detail"]
    finally:
        service.close()


def test_concurrent_and_reused_workers_keep_distinct_explicit_references(application_log):
    diag = Diagnostics()
    pool = PriorityExecutor(max_workers=2)
    barrier = threading.Barrier(2)
    def operation():
        diag.add_core_source_sync("provider.test", phase="start", status="running", code="source_sync_started")
        barrier.wait(timeout=3)
        diag.add_core_source_sync("provider.test", phase="completed", status="success", code="source_sync_complete")
    try:
        a, b = pool.submit(0, operation), pool.submit(0, operation)
        a.result(timeout=4)
        b.result(timeout=4)
        pool.submit(0, lambda: diag.add_core_source_sync("provider.test", phase="completed",
                    status="success", code="source_sync_complete")).result(timeout=3)
        counts = Counter(r["detail"]["task_ref"] for r in diag.snapshot())
        assert sorted(counts.values()) == [1, 2, 2]
        diag.add_core_source_sync("provider.test", phase="completed", status="success", code="source_sync_complete")
        assert "task_ref" not in diag.snapshot()[-1]
    finally:
        pool.shutdown(wait=True)


def test_disabled_collector_preserves_log_and_errors_filter_is_explicit(application_log):
    diag = Diagnostics(enabled=False)
    diag.add_core_source_sync("provider.test", phase="completed", status="success", code="source_sync_complete")
    report = query_diagnostic_events(diag, log_path=application_log)
    assert diag.snapshot() == [] and report["status"] == "partial"
    assert report["events"][0]["detail"]["status"] == "success"
    filtered = Diagnostics(level="errors")
    filtered.add_core_source_sync("provider.test", phase="start", status="running", code="source_sync_started")
    filtered.add_core_source_sync("provider.test", phase="completed", status="failed", code="source_sync_failed")
    assert len(filtered.snapshot()) == 1
    assert filtered.snapshot()[0]["detail"]["status"] == "failed"


def test_optional_log_or_collector_failure_cannot_change_actual_sync(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account, _, _ = setup_source(service, monkeypatch, tmp_path)
        def broken(*_a, **_kw):
            raise RuntimeError("PRIVATE")
        monkeypatch.setattr(service.diagnostics, "add_core_source_sync", broken)
        assert service.synchronize_account_source(account)["provider"] == "provider.test"
    finally:
        service.close()


def test_optional_log_handler_failure_keeps_the_collector_event():
    class Broken(logging.Handler):
        def emit(self, _record):
            raise RuntimeError("PRIVATE")
    diag = Diagnostics()
    logger = logging.getLogger("cifereca")
    handler = Broken()
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        diag.add_core_source_sync("provider.test", phase="completed", status="success", code="source_sync_complete")
        assert diag.snapshot()[0]["detail"]["status"] == "success"
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def test_wrong_logger_or_extended_trace_never_attests_source_sync(application_log):
    body = log_message({**fields(), "task_ref": "aaaaaaaaaaaa"})
    application_log.write_text("\n".join([
        "2026-10-20 13:00:00,001 INFO cifereca " + body,
        "2026-10-20 13:00:00,001 INFO cifereca.plugin " + body,
        "2026-10-20 13:00:00,001 INFO cifereca " + body + " token=PRIVATE",
    ]) + "\n")
    rows = query_diagnostic_events(Diagnostics(), log_path=application_log, source="app_log")["events"]
    assert sum("detail" in r for r in rows) == 1
    assert "PRIVATE" not in json.dumps(rows)
