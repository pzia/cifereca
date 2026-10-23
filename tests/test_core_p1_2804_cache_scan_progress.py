"""#2804: cache purge preview counts only existing namespaces, no fake progress."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from cifereca.builtin_ui.maintenance import CacheMaintenance
from cifereca.gui.maintenance_tools import MaintenanceCancelControl


class Cache:
    def __init__(self, count):
        self.names = [
            NS(namespace_id=f"cache-{i}", entries=i % 3,
               size_bytes=i * 2, purgeable=i % 2 == 0)
            for i in range(count)
        ]
        self.calls = 0
        self.purges = 0

    def namespaces(self):
        self.calls += 1
        return list(self.names)

    def purge(self):
        self.purges += 1
        raise AssertionError("preview must not purge anything")


@pytest.mark.parametrize("count", [0, 1, 18, 600])
def test_same_plan_with_real_bounded_scan_count(count):
    cache = Cache(count)
    tool = CacheMaintenance()
    tool.cache = cache
    expected = tool.plan(NS())
    events = []
    control = MaintenanceCancelControl(
        on_progress=lambda current, total, message: events.append(
            (current, total, message),
        ),
    )
    result = tool.plan_with_progress(NS(), control)
    assert result == expected
    assert cache.calls == 2 and cache.purges == 0
    assert len(events) >= 1
    assert events[0][:2] == (None, None)
    counted = [event for event in events if event[1] is not None]
    if count:
        assert counted[0][:2] == (0, count)
        assert counted[-1][0] < count
        assert len(counted) <= count // 16 + 2
        if count == 600:
            assert len(counted) >= 25
            assert any(0 < current < count for current, total, _ in counted)
    else:
        assert counted == []
    assert all("cache-" not in event[2] for event in events)
    assert "PRIVATE_SECRET" not in json.dumps(events)


def test_cancel_check_is_inside_namespace_scan():
    cache = Cache(600)
    tool = CacheMaintenance()
    tool.cache = cache
    reports = []
    control = MaintenanceCancelControl(
        on_progress=lambda *v: reports.append(v),
    )
    class Rows:
        def __iter__(self):
            for index, row in enumerate(cache.names):
                if index == 29:
                    control.cancel()
                yield row
    cache.namespaces = lambda: Rows()
    with pytest.raises(Exception) as exc:
        tool.plan_with_progress(NS(), control)
    assert "annulée" in str(exc.value)
    assert cache.purges == 0
    assert any(item[0] == 0 for item in reports)


def test_slow_single_inventory_has_indeterminate_phase_only():
    cache = Cache(1)
    tool = CacheMaintenance()
    tool.cache = cache
    calls = []
    def inventory():
        # The enumeration has not completed, so no denominator exists yet.
        assert calls == [(None, None, "Inventaire des caches en cours")]
        return cache.names
    cache.namespaces = inventory
    control = MaintenanceCancelControl(
        on_progress=lambda *args: calls.append(args)
    )
    tool.plan_with_progress(NS(), control)
    assert len(calls) == 2 and calls[-1][:2] == (0, 1)
