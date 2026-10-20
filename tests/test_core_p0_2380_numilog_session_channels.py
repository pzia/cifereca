"""P0 #2380: closed Numilog session facts in both bounded local channels.

These are independent observations of synthetic events. Neither a matching
timestamp nor a provider name is evidence of an authenticated connection or
a shared task; no network session or account is used.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_numilog_projection import (
    public_numilog_session_fields, public_numilog_session_log,
)
from cifereca.diagnostics import Diagnostics


PRIVATE = "https://private.invalid/LOAN/order?token=DO_NOT_PUBLISH"


def _line(reason: str, connected: bool) -> str:
    return (
        "TRACE numilog session "
        f"connected={connected} reason={reason}"
    )


def _log(path, *lines):
    path.write_text(
        "".join(
            f"2026-10-20 12:43:03,000 WARNING cifereca_numilog [MainThread] {line}\n"
            for line in lines
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("connected,reason", [
    (False, "connect_failed"),
    (True, "connect_succeeded"),
    (False, "delivery_login_form_detected"),
    (False, "library_http_error"),
])
def test_session_facts_match_across_core_and_log_without_task_correlation(
    tmp_path, connected, reason,
):
    diagnostics = Diagnostics(level="detailed")
    diagnostics.add(
        "NUMILOG", "session_state", "ok",
        detail={"connected": connected, "reason": reason,
                "cookie": PRIVATE, "account_id": "PRIVATE_CUSTOMER"},
        level="detailed",
    )
    log = tmp_path / "cifereca.log"
    _log(log, _line(reason, connected))
    report = query_diagnostic_events(diagnostics, log_path=log, limit=20)
    assert report["total"] == 2
    by_source = {row["source"]: row for row in report["events"]}
    expected = {"connected": connected, "reason": reason}
    assert by_source["core_events"]["detail"] == expected
    assert by_source["app_log"]["detail"] == expected
    assert by_source["app_log"]["message"] == _line(reason, connected)
    assert all("task_ref" not in row and "task_ref" not in row["detail"]
               for row in report["events"])
    assert by_source["core_events"]["event_id"] != by_source["app_log"]["event_id"]
    serialized = json.dumps(report)
    assert "DO_NOT_PUBLISH" not in serialized
    assert "PRIVATE_CUSTOMER" not in serialized
    assert "private.invalid" not in serialized


@pytest.mark.parametrize("attack", [
    _line("connect_failed", False) + " account=PRIVATE_CUSTOMER",
    _line("connect_failed", False) + " token=" + PRIVATE,
    "PREFIX " + _line("connect_failed", False),
    "TRACE numilog session connected=1 reason=connect_failed",
    "TRACE numilog session connected=false reason=connect_failed",
    "TRACE numilog session connected=False reason=PRIVATE_CUSTOMER",
    "TRACE numilog session connected=False reason=" + "a" * 300,
    _line("connect_failed", False) + "\n" + PRIVATE,
])
def test_invalid_numilog_session_log_cannot_publish_fake_fields(tmp_path, attack):
    assert public_numilog_session_fields(attack) == {}
    assert public_numilog_session_log(attack) is None
    log = tmp_path / "cifereca.log"
    # Each hostile candidate is confined to one physical log line.
    _log(log, attack.replace("\n", " "))
    report = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert report["total"] == 1
    row, = report["events"]
    assert "detail" not in row
    assert row["message"].startswith("[contenu libre masqué")
    assert "PRIVATE_CUSTOMER" not in json.dumps(report)
    assert "DO_NOT_PUBLISH" not in json.dumps(report)


def test_closed_session_projection_refuses_malformed_or_foreign_objects():
    class Hostile:
        def __str__(self):
            raise AssertionError("must not stringify private details")
    assert public_numilog_session_fields(Hostile()) == {}
    assert public_numilog_session_log(Hostile()) is None
    assert public_numilog_session_fields(_line("connect_failed", False)) == {
        "connected": False, "reason": "connect_failed",
    }
