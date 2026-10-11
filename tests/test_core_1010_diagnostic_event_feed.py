"""P0 #1010 — offline, bounded and redacted Diagnostics broker feed."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics
from cifereca.plugin_api.brokers import DiagnosticsBroker


def make_log(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_missing_sources_are_explicit_and_offline(tmp_path, monkeypatch):
    diag = Diagnostics(enabled=False)
    def forbidden():
        raise AssertionError("no provider or network operations in Diagnostics")
    service = SimpleNamespace(diagnostics=diag, connect_configured=forbidden)
    monkeypatch.setattr("cifereca.paths.log_file", lambda: tmp_path / "cifereca.log")
    broker = DiagnosticsBrokerImpl(service)
    assert isinstance(broker, DiagnosticsBroker)
    result = broker.events()
    assert result["events"] == []
    assert result["status"] == "empty"
    assert [(x["source"], x["status"]) for x in result["sources"]] == [
        ("core_events", "disabled"), ("app_log", "unavailable"),
    ]
    assert result["read_only"] and result["bounded"]


def test_structured_events_do_not_expose_personal_info_or_details(tmp_path):
    diag = Diagnostics(level="trace")
    diag.add(
        "ADEPT", "return verification for Alexandre /home/alex/secret.txt",
        "warning", detail={"credential_id": "private", "loan_id": "isbn-secret"},
    )
    rows = query_diagnostic_events(diag, log_path=tmp_path/"absent", source="core_events")
    assert rows["total"] == 1
    item = rows["events"][0]
    assert item["module"] == "ADEPT"
    assert item["severity"] == "warning"
    assert "return verification" in item["message"]
    output = json.dumps(rows)
    for forbidden in ("Alexandre", "/home/alex", "isbn-secret", "private"):
        assert forbidden not in output


def test_log_reader_allowlists_machine_trace_never_raw_details(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(
        log,
        '2026-10-11 07:01:02,111 INFO cifereca.sync [MainThread] TRACE return core phase=adobe_return_confirmed ref=ABCSECRET token=supersecret /home/alex/dev.json',
        '2026-10-11 07:01:03,121 ERROR cifereca [MainThread] user Alice alice@example.com bearer ABCDEF password=topsecret https://library.tld/LOAN/secret?token=hello',
        '2026-10-11 07:01:03,231 INFO external.runtime [MainThread] TRACE return core phase=hidden',
    )
    rows = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert rows["total"] == 2
    assert rows["events"][0]["module"] == "cifereca.sync"
    assert rows["events"][0]["message"] == "TRACE return core phase=adobe_return_confirmed"
    assert rows["events"][1]["message"].startswith("[contenu libre masqué")
    assert rows["sources"][0]["status"] == "available"
    blob = json.dumps(rows)
    for forbidden in ("ABCSECRET", "supersecret", "alex@example.com", "Alice", "/home/alex", "ABCDEF", "topsecret", "/LOAN/secret"):
        assert forbidden not in blob


def test_filters_severity_module_time_and_page(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(log,
        "2026-10-11 07:00:00,100 WARNING cifereca.sync [MainThread] TRACE sync source status=partial",
        "2026-10-11 07:00:00,200 INFO cifereca.sync [MainThread] TRACE sync source status=ok",
        "2026-10-11 07:00:00,300 WARNING cifereca.core [MainThread] TRACE return core status=failed",
    )
    rows = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
        module="cifereca.sync", severity="warning", limit=1, offset=0,
        since="2026-10-11T00:00:00+00:00", until="2026-10-12T00:00:00+00:00",
    )
    assert rows["total"] == 1
    assert rows["events"][0]["severity"] == "warning"
    all_rows = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log", limit=2)
    assert len(all_rows["events"]) == 2
    assert all_rows["next_offset"] == 2
    last = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log", limit=2, offset=2)
    assert len(last["events"]) == 1
    assert last["next_offset"] is None


def test_rotation_and_bounded_tail_skips_partial_first_line(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(
        tmp_path / "cifereca.log.1",
        "2026-10-11 06:00:00,000 ERROR cifereca [Worker] TRACE adept check status=failed",
    )
    long_secret = "X" * 40000
    make_log(
        log,
        "2026-10-11 06:30:00,000 INFO cifereca [Worker] TRACE sync source status=ok " + long_secret,
        "2026-10-11 07:00:00,000 WARNING cifereca [Worker] TRACE return core status=partial",
    )
    rows = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert len(rows["events"]) >= 2
    assert rows["sources"][0]["status"] == "partial"
    assert long_secret[:100] not in json.dumps(rows)


def test_symlink_is_rejected_before_any_file_content_access(tmp_path):
    secret = tmp_path / "private.log"
    secret.write_text("2026-10-11 07:00:00,000 ERROR cifereca [Worker] SECRET", encoding="utf-8")
    link = tmp_path / "cifereca.log"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks unsupported")
    result = query_diagnostic_events(Diagnostics(), log_path=link, source="app_log")
    assert result["events"] == []
    assert result["sources"][0]["status"] == "unavailable"


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1},
    {"offset": 10001}, {"source": "/etc/passwd"}, {"severity": "CRITICAL-SECRETS"},
    {"since": "yesterday"}, {"since": "2026-10-11T00:00:00"},
    {"since": "2026-10-12T00:00:00Z", "until": "2026-10-11T00:00:00Z"},
])
def test_invalid_queries_fail_closed(tmp_path, kwargs):
    with pytest.raises(ValueError):
        query_diagnostic_events(Diagnostics(), log_path=tmp_path/"missing", **kwargs)


def test_mixed_sources_have_stable_pseudonym_ids_and_no_raw_message(tmp_path):
    diag = Diagnostics()
    diag.add("CORE", "sync source", "ok")
    log = tmp_path / "cifereca.log"
    make_log(log, "2026-10-11 07:00:00,000 WARNING cifereca [Worker] TRACE sync source status=partial")
    page = query_diagnostic_events(diag, log_path=log)
    assert page["total"] == 2
    assert len({r["event_id"] for r in page["events"]}) == 2
    assert all(len(r["event_id"]) == 16 for r in page["events"])
    assert [s["source"] for s in page["sources"]] == ["core_events", "app_log"]


def test_truncated_log_without_matching_records_is_partial(tmp_path):
    log = tmp_path / "cifereca.log"
    # A truncated tail may contain no parseable entries: it is not proof
    # that the source is empty or that the history is complete.
    log.write_text("unparseable private content\\n" * 3000, encoding="utf-8")
    rows = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert rows["events"] == []
    assert rows["sources"][0]["status"] == "partial"
    assert rows["sources"][0]["reason"] == "log_tail_bounded"
    assert rows["status"] == "partial"


def test_identical_log_lines_have_distinct_event_ids(tmp_path):
    log = tmp_path / "cifereca.log"
    line = "2026-10-11 07:00:00,000 WARNING cifereca.sync [Worker] TRACE sync source status=partial"
    make_log(log, line, line)
    make_log(tmp_path / "cifereca.log.1", line)
    result = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert result["total"] == 3
    assert len({event["event_id"] for event in result["events"]}) == 3
    assert len({event["message"] for event in result["events"]}) == 1


def test_unknown_trace_tokens_are_never_exported(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(log,
        "2026-10-11 07:00:00,000 INFO cifereca.sync [Worker] TRACE secretcustomer sessionid phase=ok",
        "2026-10-11 07:00:01,000 INFO cifereca.sync [Worker] TRACE sync source phase=ok",
    )
    rows = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert rows["total"] == 2
    assert rows["events"][0]["message"].startswith("[contenu libre masqué")
    assert rows["events"][1]["message"] == "TRACE sync source phase=ok"
    assert "secretcustomer" not in json.dumps(rows)
    assert "sessionid" not in json.dumps(rows)


def test_trace_field_values_are_bounded_to_safe_vocabulary(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(log,
        "2026-10-11 07:00:00,000 INFO cifereca.sync [Worker] TRACE sync source phase=Alice outcome=privatecustomer operation=credentialref status=ok count=12",
    )
    rows = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert rows["total"] == 1
    assert rows["events"][0]["message"] == "TRACE sync source status=ok count=12"
    for secret in ("Alice", "privatecustomer", "credentialref"):
        assert secret not in json.dumps(rows)
