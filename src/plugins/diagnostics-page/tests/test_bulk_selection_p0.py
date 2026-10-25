"""P0 #2140: deterministic multi-page diagnostics selection without raw logs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from diagnostics_selection import (
    MAX_SCAN_PAGES, collect_filtered_selection, page_selection_ids,
)


@pytest.fixture(autouse=True)
def _fixed_selection_clock(monkeypatch):
    """#2140: test relative periods against one frozen date, not wall time.

    Fixtures use 2026-10-18. Without this clock, their default 'day'
    selection fails as soon as the CI date rolls to October 19.
    Only the selection module's clock is frozen; production code is unchanged.
    """
    import diagnostics_selection

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 18, 15, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(diagnostics_selection, "datetime", FixedDateTime)


class FakeBroker:
    def __init__(self, rows, *, source_status="available"):
        self.rows = rows
        self.calls = []
        self.source_status = source_status

    def events(self, **filters):
        self.calls.append(filters.copy())
        severity = filters["severity"]
        rows = [row for row in self.rows
                if not severity or row["severity"] == severity]
        offset = filters["offset"]
        limit = filters["limit"]
        page = rows[offset:offset + limit]
        following = offset + len(page)
        return {
            "events": list(page), "total": len(rows), "offset": offset,
            "next_offset": following if following < len(rows) else None,
            "sources": [{"source": "app_log", "status": self.source_status}],
        }


def event(number, severity="info", message="TRACE public code=ok"):
    return {
        "event_id": f"diag-{number}", "source": "app_log",
        "module": "cifereca.sync", "severity": severity,
        "timestamp": "2026-10-18T12:00:00+00:00",
        "message": message, "status": "ok",
    }


def test_errors_include_later_pages_and_critical_not_warnings():
    rows = [event(i, "error", "no match") for i in range(52)]
    rows[2]["message"] = "public operation failed"
    rows[51]["message"] = "public operation failed"
    rows += [event(60, "critical", "public operation failed"),
             event(61, "warning", "public operation failed")]
    broker = FakeBroker(rows)
    selection = collect_filtered_selection(
        broker, source="app_log", module="cifereca", period="week",
        severity="warning", search="operation failed", mode="errors",
    )
    exported = json.loads(selection.export_json)
    assert selection.event_ids == {"diag-2", "diag-51", "diag-60"}
    assert selection.count == 3 and selection.inspected == 53
    assert exported["selection"]["event_count"] == 3
    assert exported["selection"]["scope"] == "all_accessible_filtered_pages"
    assert exported["selection"]["severity_filter"] == "error_and_critical"
    assert exported["selection"]["local_text_search_applied"] is True
    assert exported["selection"]["pagination_consistency"] == "first_page_rechecked_non_atomic"
    assert all(row["severity"] in ("error", "critical") for row in exported["events"])
    assert all(call["limit"] == 50 and call["until"] is None for call in broker.calls)
    assert len({call["since"] for call in broker.calls}) == 1  # fixed time boundary
    assert [call["offset"] for call in broker.calls if call["severity"] == "error"] == [0, 50, 0]
    assert not any("cookie" in row for row in exported["events"])


def test_select_all_respects_severity_and_scans_zero_and_one_page():
    rows = [event(1, "warning"), event(2, "error")]
    all_broker = FakeBroker(rows)
    batch = collect_filtered_selection(all_broker, severity="warning", mode="all")
    assert batch.event_ids == {"diag-1"}
    assert {call["severity"] for call in all_broker.calls} == {"warning"}
    assert collect_filtered_selection(FakeBroker([]), mode="errors").count == 0


def test_selection_over_two_hundred_does_not_export_partial_result():
    broker = FakeBroker([event(i, "error") for i in range(201)])
    with pytest.raises(ValueError, match="200 events"):
        collect_filtered_selection(broker, mode="errors")
    assert [call["offset"] for call in broker.calls] == [0, 50, 100, 150, 200]


def test_more_than_eight_pages_is_a_refusal_not_a_false_zero():
    broker = FakeBroker([event(i, "info") for i in range(450)])
    with pytest.raises(ValueError, match="safe page limit"):
        collect_filtered_selection(broker, mode="all", search="not-found")
    assert len(broker.calls) == MAX_SCAN_PAGES


def test_large_utf8_selection_exceeding_96k_is_refused():
    broker = FakeBroker([event(i, "error", "é" * 490) for i in range(200)])
    with pytest.raises(ValueError, match="96 KB"):
        collect_filtered_selection(broker, mode="errors")


@pytest.mark.parametrize("fault", ["duplicate", "missing", "changed", "incomplete", "invalid_cursor"])
def test_cross_page_faults_never_export_subset(fault):
    rows = [event(i, "error", "not selected") for i in range(51)]
    rows[50]["message"] = "include this"
    broker = FakeBroker(rows)
    original = broker.events

    def faulty(**filters):
        result = original(**filters)
        if fault == "duplicate" and filters["offset"] == 50:
            result["events"][0] = event(0, "error", "include this")
        if fault == "missing" and filters["offset"] == 50:
            result["events"][0] = {**result["events"][0], "event_id": ""}
        if fault == "changed" and filters["offset"] == 0 and len(broker.calls) > 2:
            result["events"][0] = event(900, "error", "not selected")
        if fault == "incomplete" and filters["offset"] == 50:
            result["total"] = 52
        if fault == "invalid_cursor" and filters["offset"] == 0:
            result["next_offset"] = 100
        return result

    broker.events = faulty
    with pytest.raises(ValueError, match="selection"):
        collect_filtered_selection(broker, mode="errors", search="include this")


def test_partial_source_is_signalled_in_export_even_if_rows_are_available():
    selection = collect_filtered_selection(
        FakeBroker([event(1, "error")], source_status="partial"), mode="errors",
    )
    assert selection.count == 1 and selection.partial_sources
    assert json.loads(selection.export_json)["selection"]["sources_partial_or_unavailable"] is True


def test_cancel_does_not_return_an_incomplete_selection():
    broker = FakeBroker([event(1, "error")])
    with pytest.raises(ValueError, match="cancelled"):
        collect_filtered_selection(broker, cancelled=lambda: True)
    assert broker.calls == []


def test_page_only_actions_filter_duplicates_and_never_claim_other_pages():
    rows = [
        {"event_id": "one", "severity": "error"},
        {"event_id": "two", "severity": "critical"},
        {"event_id": "three", "severity": "warning"},
        {"event_id": "duplicate", "severity": "error"},
        {"event_id": "duplicate", "severity": "error"},
        {"event_id": "", "severity": "error"},
    ]
    assert page_selection_ids(rows, set(), "errors") == {"one", "two"}
    assert page_selection_ids(rows, set(), "all") == {"one", "two", "three"}
    assert page_selection_ids(rows, {"one"}, "invert") == {"two", "three"}
    assert page_selection_ids(rows, {"one"}, "clear") == set()


def test_invalid_requests_fail_before_network():
    for inputs in ({"source": "foreign"}, {"mode": "raw"},
                   {"severity": "password"}, {"search": "x" * 501},
                   {"max_pages": 999}):
        broker = FakeBroker([event(1)])
        with pytest.raises(ValueError):
            collect_filtered_selection(broker, **inputs)
        assert broker.calls == []


def test_gtk_has_wrapped_actions_preview_async_and_stale_response_guard():
    src = (Path(__file__).parents[1] / "cifereca_diagnostics_page.py").read_text()
    assert "journals_select_actions = Gtk.FlowBox()" in src
    assert 'journals_select_errors.connect("clicked", lambda *_: select_filtered("errors"))' in src
    assert 'journals_select_all.connect("clicked", lambda *_: select_filtered("all"))' in src
    assert 'journals_invert.connect("clicked", lambda *_: select_on_page("invert"))' in src
    assert 'journals_clear.connect("clicked"' in src
    assert "selection_json = selected.export_json" in src
    assert "page_token != page_generation" in src
    assert "token_result != selection_generation" in src
    assert 'self.tasks.submit(' in src
    assert "GLib.idle_add(finish, token, start_generation, selected, None)" in src
    assert "selection_json is not None" in src
    assert "selected_event_ids.clear()" in src
    assert "journals_preview_text.set_editable(False)" in src
    assert "journals_select_actions.set_max_children_per_line(4)" in src


def test_fifty_one_and_two_hundred_errors_export_every_row_across_pages():
    for count in (51, 200):
        rows = [event(i, "error", "Err public") for i in range(count)]
        rows.append(event(count + 1, "warning", "Ignored warning"))
        broker = FakeBroker(rows)
        selected = collect_filtered_selection(broker, mode="errors", source="app_log")
        exported = json.loads(selected.export_json)
        assert selected.count == count
        assert len(selected.event_ids) == count
        assert selected.inspected == count
        assert len(exported["events"]) == count
        assert exported["selection"]["event_count"] == count
        assert exported["selection"]["pagination_consistency"] == "first_page_rechecked_non_atomic"
        assert len({row["event_id"] for row in exported["events"]}) == count
        assert {row["severity"] for row in exported["events"]} == {"error"}
        assert [x["offset"] for x in broker.calls if x["severity"] == "error"] == (
            list(range(0, count, 50)) + [0]
        )
        assert len(selected.export_json.encode("utf-8")) <= 96_000


def test_bulk_export_redacts_secrets_even_across_the_fifty_row_boundary():
    rows = [event(i, "error") for i in range(55)]
    rows[50]["message"] = (
        "book_title: Le livre secret\n"
        "Bearer SAMPLESECRET tok=privacy "
        "https://example.invalid/fulfillment?token=SECRET "
        "token=PRIVATE_TOKEN alice@example.com /home/secret/mybook"
    )
    selection = collect_filtered_selection(FakeBroker(rows), mode="errors")
    exported = selection.export_json
    for value in ("Le livre secret", "SAMPLESECRET", "PRIVATE_TOKEN",
                  "alice@example.com", "/home/secret/mybook",
                  "https://example.invalid/fulfillment"):
        assert value in exported
    assert '"event_count": 55' in exported


def test_filtered_batch_limits_do_not_raise_page_export_limits():
    from diagnostics_explorer import export_selected_events
    rows = [event(i, "error") for i in range(51)]
    ids = {row["event_id"] for row in rows}
    with pytest.raises(ValueError, match="too many"):
        export_selected_events(rows, ids)
    multi = json.loads(export_selected_events(
        rows, ids, max_events=200, max_bytes=96_000,
    ))
    assert len(multi["events"]) == 51
    for extra in (
        {"max_events": 201, "max_bytes": 96_000},
        {"max_events": 200, "max_bytes": 96_001},
        {"max_events": 200, "max_bytes": True},
        {"max_events": True, "max_bytes": 96_000},
    ):
        with pytest.raises(ValueError, match="budget"):
            export_selected_events(rows, ids, **extra)


def test_gtk_label_and_failure_handling_disclose_boundaries():
    src = (Path(__file__).parents[1] / "cifereca_diagnostics_page.py").read_text()
    assert "200 événements / 96 Ko maximum" in src
    assert "50 événements / 24 Ko" in src
    assert '"diagnostic selection exceeds 200 events"' in src
    assert '"diagnostic selection exceeds 96 KB"' in src
