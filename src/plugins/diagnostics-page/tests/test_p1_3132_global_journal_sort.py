"""#3132: GTK asks Core for a globally ordered journal, not a reversed 50-row slice.

Read-only synthetic source; no GTK instance, credentials, provider I/O or sockets.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cifereca.core.diagnostic_journal import query_journal
from diagnostics_explorer import fetch_explorer_page, sanitized_rows


class PhysicalJournalBroker:
    def __init__(self, path):
        self.path = path
        self.calls = []

    def events(self, **filters):
        self.calls.append(dict(filters))
        return query_journal(
            self.path, search=filters.get("search", ""),
            module=filters.get("module", ""),
            severity=filters.get("severity", ""),
            provider_id=filters.get("provider_id", ""),
            limit=filters["limit"], offset=filters["offset"],
            since=filters.get("since"), until=filters.get("until"),
            sort_order=filters.get("sort_order", "asc"),
        )


def _write(path, n):
    base = datetime(2026, 10, 26, 10, 0)
    lines = []
    for index in range(n):
        stamp = (base + timedelta(seconds=index)).strftime(
            "%Y-%m-%d %H:%M:%S,%f"
        )[:-3]
        lines.append(
            f"{stamp} ERROR cifereca [Worker] BNF ACSM code-{index:03d}\n"
        )
    path.write_text("".join(lines), encoding="utf-8")


def test_journal_toggle_requests_full_retention_order_before_pagination(tmp_path):
    path = tmp_path / "cifereca.log"
    _write(path, 75)
    broker = PhysicalJournalBroker(path)

    first = fetch_explorer_page(
        broker, source="journal", period="all", search="BNF",
        sort_order="desc", offset=0,
    )
    assert first["total"] == 75
    assert len(first["events"]) == 50
    assert broker.calls[0]["sort_order"] == "desc"
    assert [r["message"] for r in sanitized_rows(first, newest_first=True)[:2]] == [
        "BNF ACSM code-074", "BNF ACSM code-073",
    ]

    second = fetch_explorer_page(
        broker, source="journal", period="all", search="BNF",
        sort_order="desc", offset=50,
    )
    assert [r["message"] for r in sanitized_rows(second, newest_first=True)[:2]] == [
        "BNF ACSM code-024", "BNF ACSM code-023",
    ]
    assert second["next_offset"] is None

    oldest = fetch_explorer_page(
        broker, source="journal", period="all", sort_order="asc", offset=0,
    )
    assert broker.calls[2].get("sort_order") is None
    assert [r["message"] for r in sanitized_rows(oldest, newest_first=False)[:2]] == [
        "BNF ACSM code-000", "BNF ACSM code-001",
    ]


def test_journal_same_timestamp_does_not_sort_by_random_event_hash(tmp_path):
    path = tmp_path / "cifereca.log"
    line = "2026-10-26 10:00:00,123 ERROR cifereca [Worker] "
    path.write_text(
        line + "first emitted\n" + line + "second emitted\n"
        + line + "third emitted\n",
        encoding="utf-8",
    )
    broker = PhysicalJournalBroker(path)
    page = fetch_explorer_page(
        broker, source="journal", period="all", sort_order="desc",
    )
    raw_order = [r["event_id"] for r in page["events"]]
    visible_order = [r["event_id"] for r in sanitized_rows(page, newest_first=True)]
    assert raw_order == visible_order
    assert [r["message"] for r in sanitized_rows(page, newest_first=True)] == [
        "third emitted", "second emitted", "first emitted",
    ]


@pytest.mark.parametrize("value", ("descending", "", None, True, 7))
def test_invalid_sort_does_not_call_broker(value):
    class NoRequest:
        def events(self, **_kwargs):
            pytest.fail("Unsupported ordering must be rejected before broker I/O")

    with pytest.raises(ValueError, match="sort order"):
        fetch_explorer_page(NoRequest(), source="journal", period="all",
                            sort_order=value)


def test_legacy_sort_can_only_reorder_current_page_and_never_claim_global():
    class NoRequest:
        def events(self, **_kwargs):
            pytest.fail("Legacy source cannot claim global sort")

    with pytest.raises(ValueError, match="global ordering"):
        fetch_explorer_page(NoRequest(), source="app_log", period="all",
                            sort_order="desc")
