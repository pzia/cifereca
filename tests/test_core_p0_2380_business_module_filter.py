"""P0 #2380: a typed business module filter covers both Core and log channels.

The physical logger is preserved. No free-text TRACE, timestamp adjacency or
plugin-supplied category can attest a Kobo/Numilog/ADEPT business operation.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_adept import from_runtime, log_message
from cifereca.core.public_provider_diagnostics import public_connection_log_message
from cifereca.diagnostics import Diagnostics

KOBO = {
    "status": "partial", "persisted_enabled": True,
    "connected_kobo": 3, "eligible_kobo": 3, "interval_minutes": 60,
}
KOBO_LOG = (
    "TRACE kobo backup decision status=partial "
    "enabled=True connected=3 eligible=3 interval=60"
)
PRIVATE = "https://private.invalid/book.acsm?token=VERY_PRIVATE"


def _log(path, *lines):
    path.write_text(
        "".join(
            f"2026-10-20 19:55:29,379 WARNING {logger} [MainThread] {body}\n"
            for logger, body in lines
        ),
        encoding="utf-8",
    )


def test_kobo_business_filter_includes_closed_core_and_log_without_guessing_link(
    tmp_path,
):
    diagnostics = Diagnostics()
    diagnostics.add_core_kobo_backup_decision(KOBO)
    log = tmp_path / "cifereca.log"
    _log(log, ("cifereca", KOBO_LOG))

    result = query_diagnostic_events(
        diagnostics, source="all", log_path=log, module="KOBO",
    )
    assert result["total"] == 2
    by_source = {row["source"]: row for row in result["events"]}
    assert set(by_source) == {"core_events", "app_log"}
    assert by_source["core_events"]["module"] == "KOBO"
    assert by_source["app_log"]["module"] == "cifereca"
    assert by_source["app_log"]["business_module"] == "KOBO"
    assert "business_module" not in by_source["core_events"]
    assert by_source["core_events"]["detail"] == by_source["app_log"]["detail"]
    assert by_source["core_events"]["event_id"] != by_source["app_log"]["event_id"]
    assert all("task_ref" not in row for row in result["events"])
    assert PRIVATE not in json.dumps(result)
    assert result["status"] in {"available", "partial"}

    # Logger queries remain valid, and neither channel changes its identity.
    logger_only = query_diagnostic_events(
        diagnostics, source="all", log_path=log, module="cifereca",
    )
    assert logger_only["total"] == 1
    assert logger_only["events"][0]["source"] == "app_log"


def test_numilog_session_filter_keeps_plugin_observation_distinct_from_core(
    tmp_path,
):
    diagnostics = Diagnostics(level="detailed")
    diagnostics.add(
        "NUMILOG", "session_state", "ok", level="detailed",
        detail={"connected": False, "reason": "connect_failed",
                "account_id": "PRIVATE_USER", "token": PRIVATE},
    )
    log = tmp_path / "cifereca.log"
    _log(log, ("cifereca_numilog",
               "TRACE numilog session connected=False reason=connect_failed"))
    report = query_diagnostic_events(
        diagnostics, source="all", log_path=log, module="NUMILOG",
    )
    assert report["total"] == 2
    assert {x["source"] for x in report["events"]} == {"core_events", "app_log"}
    log_event = next(x for x in report["events"] if x["source"] == "app_log")
    assert log_event["module"] == "cifereca_numilog"
    assert log_event["business_module"] == "NUMILOG"
    assert log_event["detail"] == {"connected": False, "reason": "connect_failed"}
    assert all("task_ref" not in event for event in report["events"])
    assert "PRIVATE_USER" not in json.dumps(report)
    assert "VERY_PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("module,logger,body", [
    (
        "ADEPT", "cifereca",
        log_message(from_runtime(
            {"operation": "loans list", "error_code": "kobo_adept_invalid_response"},
            "error",
        )),
    ),
    (
        "PLUGIN", "cifereca",
        public_connection_log_message({
            "provider_id": "numilog", "code": "provider_connect_failed",
        }),
    ),
])
def test_other_closed_machine_logs_have_business_module_without_core_row(
    tmp_path, module, logger, body,
):
    log = tmp_path / "cifereca.log"
    _log(log, (logger, body))
    report = query_diagnostic_events(
        Diagnostics(), source="app_log", log_path=log, module=module,
    )
    assert report["total"] == 1
    row, = report["events"]
    assert row["module"] == "cifereca"
    assert row["business_module"] == module
    assert "task_ref" not in row


@pytest.mark.parametrize("logger,body", [
    ("cifereca", KOBO_LOG + " token=" + PRIVATE),
    ("cifereca.plugin", KOBO_LOG),
    ("cifereca_numilog",
     "TRACE numilog session connected=False reason=connect_failed"
     + " account=PRIVATE_CUSTOMER"),
    ("cifereca", "TRACE kobo backup decision status=partial account=PRIVATE"),
    ("cifereca", "TRACE source sync " + '{"status":"failed","token":"PRIVATE"}'),
    ("cifereca", "TRACE adept runtime " + '{"code":"kobo_adept_invalid_response","user":"PRIVATE"}'),
])
def test_untrusted_trace_never_promotes_business_module(tmp_path, logger, body):
    log = tmp_path / "cifereca.log"
    _log(log, (logger, body))
    all_rows = query_diagnostic_events(
        Diagnostics(), source="app_log", log_path=log,
    )["events"]
    assert len(all_rows) == 1
    assert "business_module" not in all_rows[0]
    for guessed in ("KOBO", "NUMILOG", "ADEPT", "SYNC", "PLUGIN"):
        selected = query_diagnostic_events(
            Diagnostics(), source="app_log", log_path=log, module=guessed,
        )
        assert selected["total"] == 0
        assert selected["events"] == []
    assert "VERY_PRIVATE" not in json.dumps(all_rows)
    assert "PRIVATE_CUSTOMER" not in json.dumps(all_rows)
