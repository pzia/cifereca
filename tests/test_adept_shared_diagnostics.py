"""Installed context provenance, closed ADEPT facts, no process output copy."""
import json
import logging
import importlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.diagnostic_adept import (
    PLUGIN_ID, PACKAGE_ID, from_runtime, public_fields, public_log_fields, log_message,
)
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics
from cifereca.task_queue import PriorityExecutor
from tests.test_core037_contract import _manager, _write_package

PRIVATE = "PRIVATE https://private/?token=PRIVATE /home/PRIVATE"


def install_runtime(tmp_path, *, package_id=PACKAGE_ID):
    _write_package(tmp_path, package_id=package_id, plugin_id=PLUGIN_ID,
                   category="runtime.adapter")
    diag = Diagnostics(level="detailed")
    manager = _manager(tmp_path, diagnostics=diag)
    manager.activate()
    return manager, manager.service(PLUGIN_ID).context, diag


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


def detail(code="adept_error"):
    return dict(operation="download request", error_code=code, retryable=False,
                duration_ms=17, stdout_bytes=19, stderr_bytes=23, returncode=1,
                stdout_summary=PRIVATE, stderr_summary=PRIVATE, exception=PRIVATE,
                account=PRIVATE, task_ref="cccccccccccc")


@pytest.mark.parametrize("status,code", [
    ("error", "adept_error"), ("error", "adept_license_already_fulfilled_by_another_user"),
    ("error", "profile_missing"), ("error", "kobo_adept_missing"),
    ("ok", None),
])
def test_real_installed_context_publishes_identical_typed_facts_on_both_channels(
    tmp_path, application_log, status, code,
):
    manager, context, diag = install_runtime(tmp_path)
    pool = PriorityExecutor(max_workers=1)
    try:
        payload = {**detail(code), "returncode": 0 if status == "ok" else 1}
        pool.submit(0, lambda: context.report_diagnostic(PRIVATE, status, detail=payload)).result(timeout=3)
        report = query_diagnostic_events(diag, log_path=application_log, limit=100)
        rows = [r for r in report["events"] if r.get("detail", {}).get("operation_kind") == "kobo_adept"]
        assert {r["source"] for r in rows} == {"core_events", "app_log"}
        assert len(rows) == 2
        assert rows[0]["detail"] == rows[1]["detail"]
        assert rows[0]["message"] == rows[1]["message"]
        facts = rows[0]["detail"]
        assert facts["code"] == (code if status == "error" else "completed")
        assert facts["operation"] == "download request"
        assert facts["duration_ms"] == 17 and facts["stdout_bytes"] == 19
        assert facts["task_ref"] != "cccccccccccc"  # runtime cannot inject worker provenance
        worker = next(r for r in report["events"] if "task correlation" in r["message"])
        assert worker["task_ref"] == facts["task_ref"]
        assert "PRIVATE" not in json.dumps(report)
        assert "PRIVATE" not in diag.text("test")
        assert "PRIVATE" not in application_log.read_text()
    finally:
        pool.shutdown(wait=True)
        manager.close()


def test_unknown_code_never_attests_retryability_or_adobe_cause(tmp_path):
    manager, context, diag = install_runtime(tmp_path)
    try:
        context.report_diagnostic(PRIVATE, "error", detail={**detail(PRIVATE), "retryable": True})
        rows = query_diagnostic_events(diag, source="core_events", log_path=tmp_path / "none")["events"]
        row = next(r for r in rows if r.get("detail", {}).get("operation_kind") == "kobo_adept")
        assert row["detail"]["code"] == "code_non_disponible"
        assert "retryable" not in row["detail"] and "task_ref" not in row["detail"]
        assert PRIVATE not in diag.text("test")
    finally:
        manager.close()


def test_reserved_plugin_id_from_wrong_package_cannot_attest_or_leak_details(tmp_path):
    manager, context, diag = install_runtime(tmp_path, package_id="org.test.unverified")
    try:
        context.report_diagnostic(PRIVATE, "error", detail=detail())
        rows = query_diagnostic_events(diag, source="core_events", log_path=tmp_path / "none")["events"]
        assert not any(r.get("detail", {}).get("operation_kind") == "kobo_adept" for r in rows)
        assert "PRIVATE" not in diag.text("test")
    finally:
        manager.close()


def test_generic_add_cannot_attest_installed_runtime_fields(tmp_path):
    diag = Diagnostics()
    diag.add("ADEPT", "kobo adept", "error", detail=from_runtime(detail(), "error"))
    rows = query_diagnostic_events(diag, source="core_events", log_path=tmp_path / "none")["events"]
    assert "detail" not in rows[0]


@pytest.mark.parametrize("bad", [
    {"operation": PRIVATE, "error_code": PRIVATE, "retryable": 1},
    {**detail(), "duration_ms": True, "stdout_bytes": 10**40, "returncode": PRIVATE},
    {**detail(), "error_code": {"secret": PRIVATE}},
])
def test_untrusted_runtime_values_are_omitted_without_stringification(bad):
    fields = from_runtime(bad, "error")
    assert public_fields(fields) == fields
    assert "PRIVATE" not in json.dumps(fields)
    assert "stdout_summary" not in fields and "stderr_summary" not in fields


@pytest.mark.parametrize("body", [
    log_message(from_runtime(detail(), "error")) + " token=PRIVATE",
    log_message(from_runtime(detail(), "error")).replace('"schema_version":1', '"schema_version":2,"schema_version":1'),
    "TRACE adept runtime " + "[" * 1500,
    "TRACE adept runtime " + json.dumps({**from_runtime(detail(), "error"), "stdout": PRIVATE}),
])
def test_adept_log_requires_complete_canonical_closed_schema(body):
    assert public_log_fields(body) == {}


def test_log_from_other_component_cannot_attest_runtime(application_log):
    body = log_message(from_runtime(detail(), "error"))
    application_log.write_text("2026-10-20 13:00:00,001 ERROR cifereca.plugin " + body + "\n")
    rows = query_diagnostic_events(Diagnostics(), source="app_log", log_path=application_log)["events"]
    assert "detail" not in rows[0]


def test_real_runtime_reporter_crosses_installed_core_boundary_with_closed_code(tmp_path, monkeypatch):
    manager, context, diag = install_runtime(tmp_path)
    root = Path(__file__).resolve().parents[1] / "src/plugins/runtime-kobo-adept"
    monkeypatch.syspath_prepend(str(root))
    runtime_module = importlib.import_module("cifereca_kobo_adept_runtime")
    runtime = runtime_module.KoboAdeptRuntime(context)
    runtime.last_run_diagnostic = detail("adept_license_already_fulfilled_by_another_user")
    try:
        runtime._report(PRIVATE, "error", level="normal")
        rows = query_diagnostic_events(diag, source="core_events", log_path=tmp_path / "none")["events"]
        event = next(r for r in rows if r.get("detail", {}).get("operation_kind") == "kobo_adept")
        assert event["detail"]["code"] == "adept_license_already_fulfilled_by_another_user"
        assert event["detail"]["retryable"] is False
        assert "PRIVATE" not in diag.text("test")
    finally:
        manager.close()


def test_nonzero_exit_cannot_be_published_as_completed_success():
    assert from_runtime(detail(), "ok") == {}


def test_other_plugin_payload_cannot_claim_runtime_provenance(tmp_path):
    _write_package(tmp_path, package_id="org.test.other", plugin_id="runtime.other", category="runtime.adapter")
    diag = Diagnostics(level="detailed")
    manager = _manager(tmp_path, diagnostics=diag)
    manager.activate()
    try:
        manager.service("runtime.other").context.report_diagnostic("kobo adept", "error",
            detail={**detail(), "plugin_id": PLUGIN_ID, "plugin": PLUGIN_ID})
        rows = query_diagnostic_events(diag, source="core_events", log_path=tmp_path / "none")["events"]
        assert not any(r.get("detail", {}).get("operation_kind") == "kobo_adept" for r in rows)
    finally:
        manager.close()


def test_filtered_success_can_be_observed_in_log_without_claiming_collector_coverage(application_log):
    diag = Diagnostics(level="normal")
    diag.add_core_kobo_adept({**detail(None), "returncode": 0}, "ok", level="detailed")
    assert diag.snapshot() == []
    report = query_diagnostic_events(diag, log_path=application_log)
    event = next(r for r in report["events"] if r.get("detail", {}).get("operation_kind") == "kobo_adept")
    assert event["source"] == "app_log" and event["detail"]["status"] == "success"
    assert "task_ref" not in event["detail"]


def test_hostile_objects_are_never_coerced_or_introspected():
    class Hostile:
        def __str__(self):
            raise AssertionError("no raw runtime stringification")
        def __repr__(self):
            raise AssertionError("no raw runtime repr")
    fields = from_runtime({**detail(), "operation": Hostile(), "duration_ms": Hostile(),
                           "error_code": Hostile(), "retryable": Hostile()}, "error")
    assert fields["operation"] == "unknown"
    assert fields["code"] == "code_non_disponible"
    assert "duration_ms" not in fields and "retryable" not in fields


def test_broken_log_handler_never_changes_the_runtime_callback_result():
    class Broken(logging.Handler):
        def emit(self, _record):
            raise RuntimeError("PRIVATE")
    diag = Diagnostics()
    logger = logging.getLogger("cifereca")
    previous = logger.level
    logger.setLevel(logging.INFO)
    handler = Broken()
    logger.addHandler(handler)
    try:
        diag.add_core_kobo_adept(detail(), "error")
        assert diag.snapshot()[0]["detail"]["code"] == "adept_error"
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
