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


def test_legacy_source_aliases_use_only_unavailable_physical_journal(tmp_path, monkeypatch):
    class ForbiddenMemory:
        enabled = False

        def snapshot(self):
            raise AssertionError("broker must never consult the memory cache")

    service = SimpleNamespace(diagnostics=ForbiddenMemory())
    monkeypatch.setattr("cifereca.paths.log_file", lambda: tmp_path / "cifereca.log")
    broker = DiagnosticsBrokerImpl(service)
    assert isinstance(broker, DiagnosticsBroker)
    canonical = broker.events(source="journal")
    assert canonical["events"] == []
    assert canonical["status"] == "partial"
    assert canonical["sources"][0]["source"] == "journal"
    assert canonical["sources"][0]["reason"] == "active_log_missing"
    for source in ("all", "core_events", "app_log"):
        assert broker.events(source=source) == canonical



def test_broker_defaults_to_physical_journal_not_memory_union(tmp_path, monkeypatch):
    log_path = tmp_path / "cifereca.log"
    log_path.write_text(
        "2026-10-25 10:00:00,000 ERROR cifereca [MainThread] "
        "BNF import failure 403\n"
    )
    monkeypatch.setattr("cifereca.paths.log_file", lambda: log_path)

    class ForbiddenMemory:
        enabled = True
        def snapshot(self):
            raise AssertionError("canonical query must not access memory")

    broker = DiagnosticsBrokerImpl(SimpleNamespace(diagnostics=ForbiddenMemory()))
    default = broker.events(search="BNF import failure")
    explicit = broker.events(source="journal", search="BNF import failure")
    assert default == explicit
    assert default["total"] == 1
    assert default["events"][0]["severity"] == "error"
    assert default["sources"][0]["source"] == "journal"
    for legacy_source in ("all", "core_events", "app_log"):
        assert broker.events(source=legacy_source, search="BNF import failure") == default


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


def test_symlinked_log_ancestor_is_refused_even_for_valid_trace(tmp_path):
    # Protect every path component, not only the final log file.
    outside = tmp_path / "outside"
    outside.mkdir()
    make_log(
        outside / "cifereca.log",
        "2026-10-11 07:00:00,000 INFO cifereca [Worker] TRACE sync source status=ok",
    )
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unsupported")
    result = query_diagnostic_events(
        Diagnostics(), log_path=alias / "cifereca.log", source="app_log",
    )
    assert result["events"] == []
    assert result["sources"][0]["status"] == "unavailable"
    assert result["status"] == "unavailable"


def test_no_follow_unavailable_refuses_log_content_but_preserves_core_events(
    tmp_path, monkeypatch,
):
    import cifereca.core.diagnostic_event_feed as feed

    log = tmp_path / "cifereca.log"
    make_log(
        log,
        "2026-10-11 07:00:00,000 INFO cifereca [Worker] TRACE sync source status=ok",
    )
    diagnostics = Diagnostics()
    diagnostics.add("CORE", "sync source", "ok")
    monkeypatch.delattr(feed.os, "O_NOFOLLOW", raising=False)
    result = query_diagnostic_events(diagnostics, log_path=log)
    assert all(row["source"] == "core_events" for row in result["events"])
    assert next(row for row in result["sources"] if row["source"] == "app_log")["status"] == "unavailable"
    assert len(result["events"]) == 1


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


def test_all_unavailable_or_disabled_sources_not_reported_empty(tmp_path):
    disabled = Diagnostics(enabled=False)
    rows = query_diagnostic_events(disabled, log_path=tmp_path / "missing")
    assert rows["events"] == []
    assert rows["status"] == "unavailable"
    assert [row["status"] for row in rows["sources"]] == ["disabled", "unavailable"]


def test_available_but_empty_source_remains_empty(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(log, "non diagnostic record")
    single = query_diagnostic_events(
        Diagnostics(enabled=False), log_path=log, source="app_log",
    )
    assert single["status"] == "empty"
    assert single["sources"][0]["status"] == "empty"
    mixed = query_diagnostic_events(Diagnostics(enabled=False), log_path=log)
    assert mixed["status"] == "partial"
    assert [state["status"] for state in mixed["sources"]] == [
        "disabled", "empty",
    ]


@pytest.mark.parametrize("kwargs", [
    {"module": 123}, {"module": "x" * 91}, {"severity": 123},
    {"since": 123}, {"until": []},
])
def test_malformed_filter_types_fail_before_reading_logs(tmp_path, monkeypatch, kwargs):
    from cifereca.core import diagnostic_event_feed as feed
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unvalidated request must not read logs")
    monkeypatch.setattr(feed, "_source_logs", forbidden)
    with pytest.raises(ValueError):
        feed.query_diagnostic_events(Diagnostics(), log_path=tmp_path / "absent", **kwargs)


def test_log_namespace_must_have_exact_cifereca_boundary(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(log,
        "2026-10-11 07:00:00,000 INFO ciferecaevil [Worker] TRACE sync source status=ok",
        "2026-10-11 07:00:01,000 INFO cifereca-private [Worker] TRACE sync source status=ok",
        "2026-10-11 07:00:02,000 INFO cifereca [Worker] TRACE sync source status=ok",
        "2026-10-11 07:00:03,000 INFO cifereca.sync [Worker] TRACE sync source status=ok",
    )
    result = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert [row["module"] for row in result["events"]] == ["cifereca", "cifereca.sync"]
    assert result["total"] == 2


def test_line_limit_marks_history_partial_even_below_byte_limit(tmp_path):
    log = tmp_path / "cifereca.log"
    line = "2026-10-11 07:00:00,000 INFO cifereca.sync [Worker] TRACE sync source status=ok"
    make_log(log, *([line] * 201))
    assert log.stat().st_size < 32768
    result = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert result["total"] == 200
    assert result["sources"][0]["status"] == "partial"
    assert result["status"] == "partial"
    assert result["sources"][0]["reason"] == "log_tail_bounded"


def test_fifo_log_is_rejected_without_blocking(tmp_path):
    import stat
    import time
    fifo = tmp_path / "cifereca.log"
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("POSIX nonblocking FIFO unavailable")
    os.mkfifo(fifo)
    started = time.monotonic()
    result = query_diagnostic_events(Diagnostics(), log_path=fifo, source="app_log")
    assert time.monotonic() - started < 2.0
    assert result["events"] == []
    assert result["sources"][0]["status"] == "unavailable"


def test_trace_field_values_are_bounded_to_safe_vocabulary(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(log,
        "2026-10-11 07:00:00,000 INFO cifereca.sync [Worker] TRACE sync source phase=Alice outcome=privatecustomer status=ok count=12 operation=credentialref",
    )
    rows = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert rows["total"] == 1
    assert rows["events"][0]["message"] == "TRACE sync source status=ok count=12"
    for secret in ("Alice", "privatecustomer", "credentialref"):
        assert secret not in json.dumps(rows)


@pytest.mark.parametrize("source", [[], {}, None, 42, True])
def test_invalid_source_types_fail_before_access(tmp_path, monkeypatch, source):
    from cifereca.core import diagnostic_event_feed as feed
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid source must not read logs")
    monkeypatch.setattr(feed, "_source_logs", forbidden)
    with pytest.raises(ValueError, match="source"):
        feed.query_diagnostic_events(Diagnostics(), log_path=tmp_path / "absent", source=source)


def test_core_events_do_not_stringify_untrusted_snapshot_fields(tmp_path):
    class Hostile:
        def __str__(self):
            raise AssertionError("unexpected stringification")
    class Snapshot:
        enabled = True
        def snapshot(self):
            return [
                {"timestamp": Hostile(), "operation": Hostile()},
                {"timestamp": "2026-10-11T07:00:00+00:00",
                 "category": Hostile(), "operation": Hostile(), "status": Hostile()},
            ]
    rows = query_diagnostic_events(Snapshot(), log_path=tmp_path / "missing", source="core_events")
    assert rows["total"] == 1
    event = rows["events"][0]
    assert event["module"] == "DIAGNOSTICS"
    assert event["message"] == "[opération masquée]"
    assert event["status"] == "unknown"


@pytest.mark.parametrize("snapshot", [None, {}, "unexpected", 123, b"secret"])
def test_invalid_diagnostic_snapshot_shape_reports_unavailable(tmp_path, snapshot):
    class InvalidSnapshot:
        enabled = True
        def snapshot(self):
            return snapshot
    result = query_diagnostic_events(InvalidSnapshot(), log_path=tmp_path / "missing", source="core_events")
    assert result["events"] == []
    assert result["sources"][0] == {
        "source": "core_events", "status": "unavailable", "reason": "snapshot_unavailable"
    }


def test_invalid_trace_values_do_not_consume_safe_field_quota(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(log, "2026-10-11 07:00:00,000 INFO cifereca.sync [Worker] "
                  "TRACE sync source phase=private1 outcome=private2 "
                  "operation=private3 status=private4 status=ok count=7")
    rows = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert rows["events"][0]["message"] == "TRACE sync source status=ok count=7"


@pytest.mark.parametrize("kwargs", [{"since": "X" * 65}, {"until": "X" * 65}])
def test_oversized_date_filters_fail_before_log_access(tmp_path, monkeypatch, kwargs):
    from cifereca.core import diagnostic_event_feed as feed
    def forbidden(*args, **kw):
        raise AssertionError("invalid date should not read logs")
    monkeypatch.setattr(feed, "_source_logs", forbidden)
    with pytest.raises(ValueError, match="date filters"):
        feed.query_diagnostic_events(Diagnostics(), log_path=tmp_path / "absent", **kwargs)


def test_logger_path_never_exposes_freeform_identifiers(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(log,
        "2026-10-11 07:00:00,000 INFO cifereca.sync.private_customer [Worker] TRACE sync source status=ok",
        "2026-10-11 07:00:01,000 INFO cifereca.unknowncustomer [Worker] TRACE sync source status=ok",
        "2026-10-11 07:00:02,000 INFO cifereca.sync [Worker] TRACE sync source status=ok",
    )
    result = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert [r["module"] for r in result["events"]] == ["cifereca.sync", "cifereca", "cifereca.sync"]
    assert "private_customer" not in str(result)
    assert "unknowncustomer" not in str(result)


def test_log_event_ids_survive_rename_rotation_and_new_active_log(tmp_path):
    """Renaming an existing file must preserve the same evidence references."""
    log = tmp_path / "cifereca.log"
    lines = [
        "2026-10-11 07:00:00,000 INFO cifereca.sync [W] TRACE sync source status=ok",
        "2026-10-11 07:00:00,000 INFO cifereca.sync [W] TRACE sync source status=ok",
        "2026-10-11 07:01:00,000 WARNING cifereca.sync [W] TRACE return core status=partial",
    ]
    make_log(log, *lines)
    before = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
    )["events"]
    assert len(before) == 3
    assert len({row["event_id"] for row in before}) == 3

    log.rename(tmp_path / "cifereca.log.1")
    make_log(log, *lines[:1])  # New inode, even when the content repeats.
    after = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
    )["events"]
    assert len(after) == 4
    # A new file may carry the same timestamp: the broker sorts ties
    # by pseudonym ID, so never infer which row occupies index 3.
    original_ids = {row["event_id"] for row in before}
    after_ids = {row["event_id"] for row in after}
    assert original_ids.issubset(after_ids)
    assert len(after_ids - original_ids) == 1
    assert all(len(row["event_id"]) == 16 for row in after)
    assert all(
        "inode" not in row and "path" not in row and "byte_offset" not in row
        for row in after
    )


def test_log_id_is_stable_when_bounded_tail_start_moves(tmp_path):
    """A tail moving by bytes must not renumber still-visible records."""
    log = tmp_path / "cifereca.log"
    prefix = (
        "2026-10-11 07:00:00,000 INFO cifereca.sync [W] "
        "TRACE sync source status=ok " + "X" * 27000
    )
    anchor = (
        "2026-10-11 07:01:00,000 WARNING cifereca.sync [W] "
        "TRACE return core status=partial"
    )
    make_log(log, prefix, anchor)
    initial = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
    )
    original_id = next(
        row["event_id"] for row in initial["events"]
        if row["message"] == "TRACE return core status=partial"
    )
    with log.open("a", encoding="utf-8") as handle:
        # 125 × ~71 B, added to a 27 KiB prefix, exceeds 32 KiB and
        # moves the tail start into the original first log line.
        for i in range(125):
            handle.write(
                f"2026-10-11 07:02:{i % 60:02d},000 "
                "INFO cifereca.sync TRACE sync source status=ok\n"
            )
    later = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
    )
    assert later["sources"][0]["status"] == "partial"
    later_id = next(
        row["event_id"] for row in later["events"]
        if row["message"] == "TRACE return core status=partial"
    )
    assert later_id == original_id


def test_byte_offsets_distinguish_multibyte_and_duplicate_rows(tmp_path):
    """UTF-8 bytes, not Unicode positions, identify repeated log records."""
    log = tmp_path / "cifereca.log"
    repeated = (
        "2026-10-11 07:00:00,000 WARNING cifereca.sync [W] "
        "TRACE sync source status=partial"
    )
    make_log(
        log,
        "2026-10-11 06:59:00,000 INFO cifereca "
        "note Unicode é🙂 should be hidden",
        repeated, repeated,
    )
    first = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
    )
    ids = [row["event_id"] for row in first["events"]]
    assert len(ids) == len(set(ids)) == 3
    assert "Unicode" not in str(first)
    assert "🙂" not in str(first)
    again = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
    )
    assert ids == [row["event_id"] for row in again["events"]]


def test_structured_identical_timestamp_and_operation_have_unique_public_ids(tmp_path):
    class RepeatedDiagnostics:
        enabled = True

        def snapshot(self):
            return [
                {"timestamp": "2026-10-11T07:00:00+00:00",
                 "category": "SYNC", "operation": "return sync",
                 "status": "ok"},
                {"timestamp": "2026-10-11T07:00:00+00:00",
                 "category": "SYNC", "operation": "return sync",
                 "status": "error"},
                {"timestamp": "2026-10-11T07:00:00+00:00",
                 "category": "SYNC", "operation": "return sync",
                 "status": "ok"},
            ]

    diag = RepeatedDiagnostics()
    feed = query_diagnostic_events(
        diag, log_path=tmp_path/"missing", source="core_events",
    )
    assert feed["total"] == 3
    event_ids = [row["event_id"] for row in feed["events"]]
    assert len(set(event_ids)) == 3
    assert all(len(event_id) == 16 for event_id in event_ids)
    assert {row["status"] for row in feed["events"]} == {"ok", "error"}
    again = query_diagnostic_events(
        diag, log_path=tmp_path/"missing", source="core_events",
    )
    assert event_ids == [row["event_id"] for row in again["events"]]
    assert "operation" not in str(feed["sources"])


def test_structured_duplicate_ids_remain_stable_across_broker_pages(tmp_path):
    class RepeatedDiagnostics:
        enabled = True

        def snapshot(self):
            return [
                {"timestamp": "2026-10-11T07:00:00Z",
                 "category": "SYNC", "operation": "sync",
                 "status": "ok"} for _ in range(4)
            ]

    diag = RepeatedDiagnostics()
    full = query_diagnostic_events(
        diag, log_path=tmp_path/"missing", source="core_events",
    )
    visited = []
    for offset in range(4):
        page = query_diagnostic_events(
            diag, log_path=tmp_path/"missing", source="core_events",
            limit=1, offset=offset,
        )
        visited.extend(row["event_id"] for row in page["events"])
    assert len(set(visited)) == 4
    assert visited == [row["event_id"] for row in full["events"]]


def test_overwriting_same_log_inode_offset_changes_id_for_severity(tmp_path):
    """The event hash must change if a record is rewritten in-place."""
    log = tmp_path / "cifereca.log"
    initial = (
        "2026-10-11 07:00:00,000 INFO cifereca.sync [Worker] "
        "TRACE sync source status=ok"
    )
    revised = initial.replace(" INFO ", " ERROR ")
    make_log(log, initial)
    inode = log.stat().st_ino
    first = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
    )["events"][0]

    make_log(log, revised)
    assert log.stat().st_ino == inode
    second = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
    )["events"][0]
    assert first["timestamp"] == second["timestamp"]
    assert first["message"] == second["message"]
    assert first["module"] == second["module"]
    assert first["severity"] == "info"
    assert second["severity"] == "error"
    assert first["event_id"] != second["event_id"]


def test_overwriting_same_log_inode_offset_changes_id_for_module(tmp_path):
    """Different public modules are not the same evidence at one byte offset."""
    log = tmp_path / "cifereca.log"
    before_line = (
        "2026-10-11 07:00:00,000 INFO cifereca.sync [Worker] "
        "TRACE sync source status=ok"
    )
    after_line = before_line.replace("cifereca.sync", "cifereca.core")
    make_log(log, before_line)
    first = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
    )["events"][0]
    inode = log.stat().st_ino
    make_log(log, after_line)
    assert log.stat().st_ino == inode
    second = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
    )["events"][0]
    assert first["timestamp"] == second["timestamp"]
    assert first["message"] == second["message"]
    assert first["severity"] == second["severity"]
    assert first["module"] == "cifereca.sync"
    assert second["module"] == "cifereca.core"
    assert first["event_id"] != second["event_id"]


def test_broker_source_last_event_is_latest_published_with_timezone_and_rotation(tmp_path):
    from datetime import datetime, timezone

    class StaticDiagnostics:
        enabled = True

        def snapshot(self):
            return [
                {"timestamp": "2026-10-11T10:00:00+02:00",
                 "category": "SYNC", "operation": "sync", "status": "ok"},
                {"timestamp": "2026-10-11T09:00:00+00:00",
                 "category": "SYNC", "operation": "sync", "status": "ok"},
            ]

    log = tmp_path / "cifereca.log"
    make_log(
        tmp_path / "cifereca.log.1",
        "2026-10-11 06:30:00,000 INFO cifereca.sync TRACE sync source status=ok",
    )
    make_log(
        log,
        "2026-10-11 07:30:00,000 INFO cifereca.sync TRACE sync source status=ok",
    )
    feed = query_diagnostic_events(
        StaticDiagnostics(), log_path=log, source="all", limit=1,
        module="no.match", offset=0,
    )
    assert feed["events"] == [] and feed["total"] == 0
    assert [row["source"] for row in feed["sources"]] == ["core_events", "app_log"]
    core_last = datetime.fromisoformat(feed["sources"][0]["last_event"])
    log_last = datetime.fromisoformat(feed["sources"][1]["last_event"])
    assert core_last == datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
    published_log_rows = query_diagnostic_events(
        StaticDiagnostics(), log_path=log, source="app_log",
    )["events"]
    assert log_last == max(
        datetime.fromisoformat(row["timestamp"]) for row in published_log_rows
    )
    assert core_last.utcoffset().total_seconds() == 0
    assert log_last.utcoffset().total_seconds() == 0


def test_source_last_event_stays_stable_across_public_pagination(tmp_path):
    from datetime import datetime
    log = tmp_path / "cifereca.log"
    make_log(
        log,
        "2026-10-11 07:00:00,000 INFO cifereca.sync TRACE sync source status=ok",
        "2026-10-11 08:00:00,000 INFO cifereca.sync TRACE sync source status=ok",
        "2026-10-11 09:00:00,000 INFO cifereca.sync TRACE sync source status=ok",
    )
    first = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log", limit=1,
    )
    last = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log", limit=1, offset=2,
    )
    assert first["total"] == last["total"] == 3
    assert first["sources"][0]["last_event"] == last["sources"][0]["last_event"]
    parsed = datetime.fromisoformat(first["sources"][0]["last_event"])
    newest = max(datetime.fromisoformat(row["timestamp"]) for row in
                 query_diagnostic_events(Diagnostics(), log_path=log,
                                         source="app_log")["events"])
    assert parsed == newest


def test_source_without_public_events_has_no_misleading_last_timestamp(tmp_path):
    empty_log = tmp_path / "cifereca.log"
    make_log(empty_log, "record not published by broker")
    feed = query_diagnostic_events(
        Diagnostics(enabled=False), log_path=empty_log,
    )
    assert [row["status"] for row in feed["sources"]] == ["disabled", "empty"]
    assert all("last_event" not in row for row in feed["sources"])

    missing = query_diagnostic_events(
        Diagnostics(enabled=False), log_path=tmp_path / "no-log",
    )
    assert all("last_event" not in row for row in missing["sources"])


def test_core_disabled_with_app_log_events_means_partial(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(log, "2026-10-11 07:00:00,000 INFO cifereca.sync "
                  "[Worker] TRACE sync source status=ok")
    page = query_diagnostic_events(
        Diagnostics(enabled=False), log_path=log, source="all",
    )
    assert page["total"] == 1 and page["status"] == "partial"
    assert [state["status"] for state in page["sources"]] == [
        "disabled", "available",
    ]
    assert page["events"][0]["source"] == "app_log"


def test_missing_log_with_core_event_means_partial(tmp_path):
    diag = Diagnostics()
    diag.add("SYNC", "sync source", "ok")
    page = query_diagnostic_events(
        diag, log_path=tmp_path / "log-missing", source="all",
    )
    assert page["total"] == 1 and page["status"] == "partial"
    assert [state["status"] for state in page["sources"]] == [
        "available", "unavailable",
    ]
    assert page["events"][0]["source"] == "core_events"


def test_unavailable_source_alone_remains_unavailable(tmp_path):
    absent = query_diagnostic_events(
        Diagnostics(), log_path=tmp_path / "absent", source="app_log",
    )
    assert absent["total"] == 0 and absent["status"] == "unavailable"
    disabled = query_diagnostic_events(
        Diagnostics(enabled=False), log_path=tmp_path / "absent",
        source="core_events",
    )
    assert disabled["status"] == "unavailable"


def test_all_sources_present_remains_available(tmp_path):
    diag = Diagnostics()
    diag.add("SYNC", "sync source", "ok")
    log = tmp_path / "cifereca.log"
    make_log(log, "2026-10-11 07:00:00,000 INFO cifereca.sync "
                  "[Worker] TRACE sync source status=ok")
    result = query_diagnostic_events(diag, log_path=log)
    assert result["status"] == "available" and result["total"] == 2
    assert {x["status"] for x in result["sources"]} == {"available"}


def test_unreadable_rotation_does_not_appear_as_complete_log_coverage(tmp_path):
    """No partial evidence can be silently upgraded by a healthy active log."""
    log = tmp_path / "cifereca.log"
    make_log(log,
        "2026-10-11 07:00:00,000 INFO cifereca.sync [W] TRACE sync source status=ok",
    )
    private = tmp_path / "private.log"
    make_log(private,
        "2026-10-11 06:00:00,000 ERROR cifereca.sync [W] user SecretPerson token=SecretToken",
    )
    try:
        (tmp_path / "cifereca.log.1").symlink_to(private)
    except OSError:
        pytest.skip("symlinks unsupported")
    page = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert page["total"] == 1
    assert page["status"] == "partial"
    assert page["sources"][0]["status"] == "partial"
    assert page["sources"][0]["reason"] == "log_source_incomplete"
    assert "SecretPerson" not in json.dumps(page)
    assert "SecretToken" not in json.dumps(page)
    assert str(private) not in json.dumps(page)


def test_archived_log_without_active_file_is_not_claimed_complete(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(tmp_path / "cifereca.log.1",
        "2026-10-11 06:00:00,000 WARNING cifereca.sync [W] TRACE sync source status=partial",
    )
    page = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert page["total"] == 1
    assert page["status"] == "partial"
    assert page["sources"][0]["reason"] == "log_source_incomplete"
    assert "last_event" in page["sources"][0]


def test_unused_missing_rotation_slots_do_not_mark_active_log_partial(tmp_path):
    log = tmp_path / "cifereca.log"
    make_log(log,
        "2026-10-11 07:00:00,000 INFO cifereca.sync [W] TRACE sync source status=ok",
    )
    page = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert page["total"] == 1
    assert page["status"] == "available"
    assert page["sources"][0]["reason"] == ""


@pytest.mark.parametrize("failure", [
    RuntimeError("private borrower Camille /home/alex/private"),
    PermissionError("private config path /home/alex/profiles"),
    OSError("token=PERSONAL-SECRET"),
])
def test_broken_diagnostics_snapshot_keeps_safe_app_log_available(tmp_path, failure):
    """One failing Core source cannot hide a healthy second source."""
    class BrokenDiagnostics:
        enabled = True

        def snapshot(self):
            raise failure

    log = tmp_path / "cifereca.log"
    make_log(log,
        "2026-10-11 07:00:00,000 INFO cifereca.sync [W] TRACE sync source status=ok",
    )
    feed = query_diagnostic_events(BrokenDiagnostics(), log_path=log)
    assert feed["status"] == "partial"
    assert feed["total"] == 1
    assert feed["events"][0]["source"] == "app_log"
    assert [(s["source"], s["status"]) for s in feed["sources"]] == [
        ("core_events", "unavailable"), ("app_log", "available"),
    ]
    assert feed["sources"][0]["reason"] == "snapshot_unavailable"
    serialized = json.dumps(feed)
    for private in ("Camille", "/home/alex", "PERSONAL-SECRET", "private borrower"):
        assert private not in serialized


def test_broken_diagnostics_snapshot_without_logs_is_unavailable_not_empty(tmp_path):
    class BrokenDiagnostics:
        enabled = True

        def snapshot(self):
            raise RuntimeError("private unexpected backend error")

    feed = query_diagnostic_events(
        BrokenDiagnostics(), log_path=tmp_path / "absent",
    )
    assert feed["status"] == "unavailable"
    assert feed["total"] == 0
    assert feed["events"] == []
    assert [row["status"] for row in feed["sources"]] == [
        "unavailable", "unavailable",
    ]
    assert "private unexpected backend error" not in json.dumps(feed)


def test_diagnostics_keyboard_interrupt_is_not_misreported_as_source_failure(tmp_path):
    class Interrupted:
        enabled = True

        def snapshot(self):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        query_diagnostic_events(Interrupted(), log_path=tmp_path / "absent")


def test_snapshot_with_bad_row_preserves_valid_events_but_reports_partial(tmp_path):
    class PartlyMalformed:
        enabled = True

        def snapshot(self):
            return [
                {"timestamp": "2026-10-11T09:00:00+00:00",
                 "category": "SYNC", "operation": "sync", "status": "ok"},
                {"timestamp": "Camille /home/borrower/private", "operation": "secret"},
                {"timestamp": None, "operation": "private token"},
            ]

    feed = query_diagnostic_events(
        PartlyMalformed(), log_path=tmp_path / "absent", source="core_events",
    )
    assert feed["total"] == 1
    assert feed["status"] == "partial"
    assert feed["sources"][0] == {
        "source": "core_events", "status": "partial",
        "reason": "snapshot_records_incomplete",
        "last_event": "2026-10-11T09:00:00+00:00",
    }
    assert "Camille" not in json.dumps(feed)
    assert "/home/borrower" not in json.dumps(feed)


def test_snapshot_with_only_invalid_rows_is_not_an_empty_healthy_source(tmp_path):
    class Malformed:
        enabled = True

        def snapshot(self):
            return ["not a row", {"timestamp": "no timestamp", "operation": "secret"}]

    feed = query_diagnostic_events(
        Malformed(), log_path=tmp_path / "absent", source="core_events",
    )
    assert feed["events"] == []
    assert feed["status"] == "partial"
    assert feed["sources"][0]["reason"] == "snapshot_records_incomplete"


def test_snapshot_history_beyond_500_records_reports_bounded_partial_coverage(tmp_path):
    class LongSnapshot:
        enabled = True

        def snapshot(self):
            return [
                {"timestamp": f"2026-10-11T09:00:{i % 60:02d}+00:00",
                 "category": "SYNC", "operation": "sync", "status": "ok"}
                for i in range(501)
            ]

    feed = query_diagnostic_events(
        LongSnapshot(), log_path=tmp_path / "absent",
        source="core_events", limit=100,
    )
    assert feed["status"] == "partial"
    assert feed["total"] == 500
    assert len(feed["events"]) == 100
    assert feed["sources"][0]["reason"] == "snapshot_records_incomplete"


def test_clean_empty_snapshot_retains_explicit_no_recorded_events(tmp_path):
    class EmptySnapshot:
        enabled = True

        def snapshot(self):
            return []

    feed = query_diagnostic_events(
        EmptySnapshot(), log_path=tmp_path / "absent", source="core_events",
    )
    assert feed["status"] == "empty"
    assert feed["sources"][0]["reason"] == "no_recorded_events"
