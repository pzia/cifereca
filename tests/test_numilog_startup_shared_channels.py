"""One real Core startup emission, two independently filtered observations."""
import json
import logging
from collections import Counter
from types import SimpleNamespace

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.numilog_materialization_startup import execute
from cifereca.core.numilog_startup_diagnostics import StartupTrace, public_log_fields
from cifereca.diagnostics import Diagnostics
from cifereca.task_queue import PriorityExecutor
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row
from tests.test_core_p0_numilog_startup_account_isolation import _safe_mock_downloads


@pytest.fixture
def application_log(tmp_path):
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    handler = logging.FileHandler(path)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"))
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield path
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()


@pytest.mark.parametrize("failure", [False, True])
def test_actual_startup_decisions_have_identical_facts_and_task_reference(
    tmp_path, monkeypatch, application_log, failure,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    pool = PriorityExecutor(max_workers=1)
    try:
        service.diagnostics = Diagnostics()
        _authorize(broker, sid)
        session.library = lambda: [_row(account.credential_id, 0)]
        _safe_mock_downloads(monkeypatch, service)
        if failure:
            def fail(*_a, **_kw):
                raise RuntimeError("PRIVATE https://private/?token=PRIVATE /home/PRIVATE")
            monkeypatch.setattr(service, "acquire_direct_item", fail)
        result = pool.submit(0, lambda: execute(broker, account.identity_id, "startup")).result(timeout=5)
        assert result.ok is not failure
        report = query_diagnostic_events(service.diagnostics, log_path=application_log, limit=100)
        core = [r for r in report["events"] if r["source"] == "core_events"
                and r.get("detail", {}).get("task_kind") == "source-materialize"]
        log = [r for r in report["events"] if r["source"] == "app_log"
               and r.get("detail", {}).get("task_kind") == "source-materialize"]
        assert core and len(core) == len(log)
        # Millisecond timestamps can tie; event hashes are source-local and
        # are not an ordering/correlation proof. Preserve duplicate counts.
        def facts(rows):
            return Counter((json.dumps(r["detail"], sort_keys=True), r["message"]) for r in rows)
        assert facts(core) == facts(log)
        terminal = next(r["detail"] for r in core if r["detail"]["phase"] == "completed")
        assert terminal["phase"] == "completed"
        assert terminal["status"] == ("failed" if failure else "complete")
        assert terminal["code"] == ("acquisition_failed" if failure else "complete")
        worker = next(r for r in report["events"] if "task correlation" in r["message"])
        assert worker["task_ref"] == terminal["task_ref"]
        # A successful worker return can carry a failed business result.
        assert "status=success" in worker["message"]
        assert "PRIVATE" not in application_log.read_text()
        assert "PRIVATE" not in json.dumps(report)
        assert calls == []
    finally:
        pool.shutdown(wait=True)
        service.close()


def _fields():
    return dict(phase="completed", cause_phase="acquisition", code="acquisition_failed",
                status="failed", task_kind="source-materialize", attempted=1,
                downloaded=0, skipped=0, failed=1)


def _body(fields):
    return "TRACE numilog startup_materialization " + json.dumps(
        fields, separators=(",", ":"), sort_keys=True)


@pytest.mark.parametrize("body", [
    _body({**_fields(), "token": "PRIVATE"}),
    _body({**_fields(), "task_ref": "PRIVATE"}),
    _body({**_fields(), "code": "PRIVATE"}),
    _body({**_fields(), "attempted": True}),
    _body({**_fields(), "attempted": 10**50}),
    _body(_fields()) + " token=PRIVATE",
    _body(_fields()).replace('"failed":1', '"failed":0,"failed":1'),
    "TRACE numilog startup_materialization " + "[" * 1500,
    "TRACE numilog startup_materialization " + "x" * 2048,
    None, {},
])
def test_invalid_or_extended_log_never_promotes_untrusted_fields(body):
    assert public_log_fields(body) == {}


def test_log_requires_exact_core_logger_and_retains_absent_correlation(application_log):
    fields = _fields()
    with application_log.open("w") as handle:
        for logger in ("cifereca", "cifereca.plugin", "cifereca_numilog"):
            handle.write(f"2026-10-20 13:00:00,001 INFO {logger} {_body(fields)}\n")
    report = query_diagnostic_events(Diagnostics(), log_path=application_log, source="app_log")
    promoted = [r for r in report["events"] if r.get("detail")]
    assert len(promoted) == 1
    assert promoted[0]["detail"] == fields
    assert "task_ref" not in promoted[0]["detail"]


def test_disabled_collector_does_not_disable_existing_log_channel(application_log):
    service = SimpleNamespace(diagnostics=Diagnostics(enabled=False),
                              storage=SimpleNamespace(metadata_get=lambda _k: None))
    trace = StartupTrace(service, "PRIVATE")
    expected = trace.emit("admission", "started")
    assert expected and service.diagnostics.snapshot() == []
    report = query_diagnostic_events(service.diagnostics, log_path=application_log)
    assert report["status"] == "partial"
    assert report["events"][0]["detail"] == expected
    assert "task_ref" not in expected
    assert "PRIVATE" not in application_log.read_text()


def test_broken_log_handler_cannot_drop_diagnostic_or_change_outcome():
    class Broken(logging.Handler):
        def emit(self, _record):
            raise RuntimeError("PRIVATE")
    logger = logging.getLogger("cifereca")
    previous = logger.level
    logger.setLevel(logging.INFO)
    handler = Broken()
    logger.addHandler(handler)
    diag = Diagnostics()
    service = SimpleNamespace(diagnostics=diag,
                              storage=SimpleNamespace(metadata_get=lambda _k: None))
    try:
        fields = StartupTrace(service, "PRIVATE").emit("admission", "started")
        assert diag.snapshot()[0]["detail"] == fields
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
