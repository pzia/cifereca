"""P0 #2140: multi-page bulk actions never mislabel out-of-scope log events.

All rows are synthetic broker summaries. No files, identity, credentials, raw
provider logs, or external requests. The selection is always fail-closed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from diagnostics_selection import collect_filtered_selection


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


def event(number, *, severity="error", source="app_log", module="cifereca.sync",
          message="Safe diagnostic", status="failed"):
    return {
        "event_id": f"evt-{number}", "source": source, "module": module,
        "severity": severity, "timestamp": "2026-10-18T12:00:00+00:00",
        "message": message, "status": status,
    }


class FakeBroker:
    def __init__(self, rows, *, mutate=None):
        self.rows = rows
        self.calls = []
        self.mutate = mutate

    def events(self, **filters):
        self.calls.append(filters.copy())
        # A real broker filters server-side; mutation below simulates an
        # incorrectly scoped or changing response crossing that contract.
        filtered = [
            r for r in self.rows if
            (not filters["severity"] or r["severity"] == filters["severity"])
            and (filters["source"] == "all" or r["source"] == filters["source"])
            and (
                r["module"].casefold().startswith(filters["module"].casefold())
                or (type(r.get("business_module")) is str
                    and r["business_module"].casefold().startswith(filters["module"].casefold()))
            )
        ]
        offset, limit = filters["offset"], filters["limit"]
        batch = [r.copy() for r in filtered[offset:offset + limit]]
        result = {
            "events": batch, "total": len(filtered), "offset": offset,
            "next_offset": offset + len(batch) if offset + len(batch) < len(filtered) else None,
            "sources": [{"source": "app_log", "status": "available"}],
        }
        if self.mutate:
            self.mutate(self, filters, result)
        return result


@pytest.mark.parametrize(("wrong_field", "value", "filters"), [
    ("severity", "warning", {"mode": "errors"}),
    ("severity", "info", {"mode": "all", "severity": "error"}),
    ("severity", "fake-level", {"mode": "all"}),
    ("source", "core_events", {"source": "app_log", "mode": "all"}),
    ("source", "private-source", {"mode": "all"}),
    ("module", "other.component", {"module": "cifereca", "mode": "all"}),
])
def test_broker_must_not_return_out_of_scope_summary(wrong_field, value, filters):
    def mutate(_broker, _filters, response):
        if response["events"]:
            response["events"][0][wrong_field] = value
    broker = FakeBroker([event(1)], mutate=mutate)
    with pytest.raises(ValueError, match="out-of-scope") as ex:
        collect_filtered_selection(broker, **filters)
    assert "private-source" not in str(ex.value)


@pytest.mark.parametrize(("field", "replacement"), [
    ("message", "New visible body token=PRIVATE_NOVEMBER"),
    ("status", "attention_required"),
    ("timestamp", "2026-10-18T13:00:00+00:00"),
    ("module", "cifereca.changed"),
    ("source", "core_events"),
])
def test_same_event_id_changed_in_recheck_is_not_a_stable_export(field, replacement):
    def mutate(broker, filters, response):
        # 51 rows requires offset=0, offset=50 and final recheck at 0.
        # A changed field on the first event must defeat the recheck.
        if len(broker.calls) == 3 and filters["offset"] == 0:
            response["events"][0][field] = replacement
    broker = FakeBroker([event(i) for i in range(51)], mutate=mutate)
    with pytest.raises(ValueError, match="(changed during pagination|out-of-scope)") as exc:
        collect_filtered_selection(
            broker, severity="error", mode="all", period="all",
        )
    assert "PRIVATE_NOVEMBER" not in str(exc.value)
    assert [c["offset"] for c in broker.calls] == [0, 50, 0]


def test_scope_mismatch_on_last_page_is_not_silently_discarded():
    def mutate(_broker, filters, response):
        if filters["offset"] == 50:
            response["events"][0]["severity"] = "warning"
    broker = FakeBroker([event(i) for i in range(51)], mutate=mutate)
    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(broker, mode="errors")
    assert [c["offset"] for c in broker.calls] == [0, 50]


def test_casefolded_module_prefix_and_fixed_source_allow_valid_selection():
    broker = FakeBroker([
        event(1, module="Cifereca.Sync", severity="error"),
        event(2, module="cifereca.plugin", severity="critical"),
        event(3, module="cifereca.misc", severity="warning"),
    ])
    batch = collect_filtered_selection(
        broker, source="all", module="CIFERECA",
        severity="warning", mode="errors", period="all",
    )
    exported = json.loads(batch.export_json)
    assert batch.count == 2
    assert {r["event_id"] for r in exported["events"]} == {"evt-1", "evt-2"}
    assert {r["severity"] for r in exported["events"]} == {"error", "critical"}
    assert exported["selection"]["severity_filter"] == "error_and_critical"
    assert exported["selection"]["pagination_consistency"] == "first_page_rechecked_non_atomic"
    assert all(call["module"] == "CIFERECA" for call in broker.calls)




@pytest.mark.parametrize(("mode", "expected"), [
    ("all", {"evt-1", "evt-2", "evt-3"}),
    ("errors", {"evt-1", "evt-2"}),
])
def test_business_domain_selection_matches_attested_app_logs_and_core_events(mode, expected):
    # #2569: app_log physical logger stays cifereca, while Core attests
    # the NUMILOG business domain using a strictly parsed canonical TRACE.
    broker = FakeBroker([
        {**event(1, module="cifereca", severity="error"), "business_module": "NUMILOG"},
        {**event(2, module="cifereca", severity="critical"), "business_module": "NUMILOG"},
        event(3, source="core_events", module="NUMILOG", severity="info"),
        {**event(4, module="cifereca", severity="warning"), "business_module": "KOBO"},
    ])
    selected = collect_filtered_selection(
        broker, module="numilog", period="all", mode=mode,
    )
    public = json.loads(selected.export_json)
    assert {row["event_id"] for row in public["events"]} == expected
    assert selected.count == len(expected)
    assert all("business_module" not in row for row in public["events"])
    assert all(call["module"] == "numilog" for call in broker.calls)


@pytest.mark.parametrize(("source", "business_domain"), [
    ("app_log", "NUMILOG-fake-secret"),
    ("app_log", "numilog"),
    ("core_events", "NUMILOG"),
])
def test_unattested_or_wrong_source_business_domain_is_not_in_bulk_scope(source, business_domain):
    # A broken/malicious broker that returns an invalid domain or attaches
    # a domain to core_events must not bypass the explicit module filter.
    row = {
        **event(1, source=source, module="cifereca"),
        "business_module": business_domain,
    }
    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(
            FakeBroker([row]), module="NUMILOG", period="all", mode="all",
        )


def test_business_domain_change_with_same_event_id_invalidates_bulk_recheck():
    broker = FakeBroker([
        {**event(1, module="NUMILOG.logger"), "business_module": "KOBO"},
    ], mutate=lambda broker, filters, response: (
        response["events"][0].__setitem__("business_module", "NUMILOG")
        if len(broker.calls) == 2 else None
    ))
    with pytest.raises(ValueError, match="changed during pagination"):
        collect_filtered_selection(broker, module="NUMILOG", period="all", mode="all")
    assert len(broker.calls) == 2

def test_empty_response_and_unfiltered_all_mode_remain_available():
    empty = collect_filtered_selection(FakeBroker([]), mode="errors")
    assert empty.count == 0
    all_rows = FakeBroker([
        event(1), event(2, severity="critical"),
        event(3, severity="warning"),
    ])
    result = collect_filtered_selection(all_rows, severity="", mode="all")
    assert result.count == 3
    assert not result.partial_sources
    assert len(json.loads(result.export_json)["events"]) == 3


def test_even_same_id_and_same_severity_cannot_hide_changed_public_message():
    calls = []
    def mutate(broker, filters, response):
        calls.append(filters.copy())
        if len(calls) == 2:
            # Same ID, same count, same severity: content-only change.
            response["events"][0]["message"] = "other published summary"
    broker = FakeBroker([event(1)], mutate=mutate)
    with pytest.raises(ValueError, match="changed during pagination"):
        collect_filtered_selection(broker, severity="error", mode="all")
    assert len(calls) == 2
