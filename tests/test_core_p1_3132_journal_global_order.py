"""P1 #3132: chronological order is applied across the ENTIRE journal before paging.

Synthetic rotated local log only; no GTK, HTTP, provider or Core mutation.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.diagnostic_journal import query_journal


def _line(n):
    instant = datetime(2026, 10, 26, 9, 0) + timedelta(seconds=n)
    return (
        instant.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
        + f" ERROR cifereca [Worker] BNF ACSM HTTP 403 item-{n:03d}\n"
    )


def test_newest_first_is_server_wide_not_reversed_oldest_50(tmp_path, monkeypatch):
    path = tmp_path / "cifereca.log"
    (tmp_path / "cifereca.log.1").write_text(
        "".join(_line(i) for i in range(70)), encoding="utf-8",
    )
    path.write_text("".join(_line(i) for i in range(70, 135)), encoding="utf-8")
    assert query_journal(path)["total"] == 135
    assert query_journal(path, limit=1)["events"][0]["message"].endswith("item-000")
    descending = query_journal(path, search="BNF ACSM", sort_order="desc", limit=50)
    assert descending["total"] == 135
    assert descending["has_more"] is True
    assert [e["message"].split("item-")[1] for e in descending["events"]] == [
        f"{i:03d}" for i in range(134, 84, -1)
    ]
    second = query_journal(path, search="BNF ACSM", sort_order="desc",
                           offset=50, limit=50)
    third = query_journal(path, search="BNF ACSM", sort_order="desc",
                          offset=100, limit=50)
    assert [e["message"].split("item-")[1] for e in second["events"]] == [
        f"{i:03d}" for i in range(84, 34, -1)
    ]
    assert [e["message"].split("item-")[1] for e in third["events"]] == [
        f"{i:03d}" for i in range(34, -1, -1)
    ]
    assert third["has_more"] is False and third["next_offset"] is None
    assert len({
        e["event_id"]
        for page in (descending, second, third)
        for e in page["events"]
    }) == 135

    # Public Core broker uses exactly the same global physical source.
    monkeypatch.setattr("cifereca.paths.log_file", lambda: path)
    broker = DiagnosticsBrokerImpl(SimpleNamespace(diagnostics=None))
    latest = broker.events(source="journal", search="BNF", sort_order="desc", limit=1)
    assert latest["total"] == 135
    assert latest["events"][0]["event_id"] == descending["events"][0]["event_id"]
    assert broker.events(source="journal", search="BNF", limit=1)["events"][0]["message"].endswith("item-000")


def test_equal_timestamps_preserve_physical_tie_order_or_exact_reverse(tmp_path):
    line = "2026-10-26 09:00:00,123 ERROR cifereca [Worker] "
    path = tmp_path / "cifereca.log"
    (tmp_path / "cifereca.log.1").write_text(
        line + "oldest\n" + line + "second\n", encoding="utf-8",
    )
    path.write_text(line + "third\n" + line + "newest\n", encoding="utf-8")
    oldest = query_journal(path, limit=10)["events"]
    latest = query_journal(path, sort_order="desc", limit=10)["events"]
    assert [e["message"] for e in oldest] == ["oldest", "second", "third", "newest"]
    assert [e["message"] for e in latest] == ["newest", "third", "second", "oldest"]
    assert [e["event_id"] for e in latest] == [e["event_id"] for e in reversed(oldest)]


@pytest.mark.parametrize("bad", ("reversed", "", None, True, 3, ["desc"]))
def test_bad_global_order_fails_closed_without_accessing_log(tmp_path, monkeypatch, bad):
    import cifereca.core.diagnostic_journal as journal
    monkeypatch.setattr(
        journal, "_events",
        lambda _path: pytest.fail("Bad order must be rejected before journal I/O"),
    )
    with pytest.raises(ValueError, match="invalid journal sort order"):
        query_journal(tmp_path / "cifereca.log", sort_order=bad)
