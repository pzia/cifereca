from __future__ import annotations

import json
from time import perf_counter

from recommendation_service import compute_recommendations
from recommendations import UxRecommendationsView


BOOK_COUNT = 562
BURST_SIZE = 3


def _books():
    return [
        {
            "title": f"Livre {index:03d}",
            "author": f"Auteur {index % 37:02d}",
            "description": f"Description déterministe {index % 19}",
            "metadata": {
                "subjects": [f"Sujet {index % 23}", f"Thème {index % 11}"],
                "series": [f"Série {index % 17}"],
            },
        }
        for index in range(BOOK_COUNT)
    ]


class _Metadata:
    def __init__(self):
        self.calls = 0
        self.rows = _books()

    def library_entries(self, **kwargs):
        assert kwargs == {"identity_id": "default", "scan": False}
        self.calls += 1
        return self.rows


class _Catalog:
    def searchable_providers(self):
        # This recipe isolates the cost/call-count of the library snapshot and
        # profile/query preparation from network/catalogue latency.
        return []


class _GLib:
    def __init__(self):
        self.next_id = 1
        self.sources = {}

    def timeout_add(self, _delay, callback):
        source_id = self.next_id
        self.next_id += 1
        self.sources[source_id] = callback
        return source_id

    def source_remove(self, source_id):
        self.sources.pop(source_id, None)

    def run_pending(self):
        callbacks = list(self.sources.items())
        self.sources.clear()
        for _source_id, callback in callbacks:
            callback()


def _compute(metadata):
    return compute_recommendations(
        metadata,
        _Catalog(),
        "default",
        max_queries=8,
        max_results=120,
    )


def measure_snapshot_burst():
    baseline = _Metadata()
    started = perf_counter()
    for _ in range(BURST_SIZE):
        _compute(baseline)
    baseline_ms = (perf_counter() - started) * 1000.0

    current = _Metadata()
    view = object.__new__(UxRecommendationsView)
    view._active_identity_id = "default"
    view._identity_ids = ["default"]
    view._overview_refresh_pending = False
    view._overview_refresh_source_id = 0
    view._busy = False
    view.GLib = _GLib()
    view._reload_identities = lambda: None

    def refresh():
        view._clear_overview_refresh()
        _compute(current)

    view.refresh = refresh

    started = perf_counter()
    for _ in range(BURST_SIZE):
        view._identity_overview_changed()
    scheduled_after_burst = len(view.GLib.sources)
    view.GLib.run_pending()
    coalesced_ms = (perf_counter() - started) * 1000.0

    return {
        "books": BOOK_COUNT,
        "events": BURST_SIZE,
        "baseline_snapshots": baseline.calls,
        "coalesced_snapshots": current.calls,
        "scheduled_refreshes_after_burst": scheduled_after_burst,
        "baseline_elapsed_ms": round(baseline_ms, 3),
        "coalesced_elapsed_ms": round(coalesced_ms, 3),
        "scope": "synthetic in-memory snapshot + real Recommendations profile/query preparation; no catalogue/network/UI rendering",
    }


def test_562_book_overview_burst_reduces_complete_library_snapshots_from_three_to_one(request):
    measurement = measure_snapshot_burst()
    terminal = request.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_line(
            "recommendations-benchmark "
            + json.dumps(measurement, ensure_ascii=False, sort_keys=True)
        )

    assert measurement["books"] == 562
    assert measurement["baseline_snapshots"] == 3
    assert measurement["scheduled_refreshes_after_burst"] == 1
    assert measurement["coalesced_snapshots"] == 1


if __name__ == "__main__":
    print(json.dumps(measure_snapshot_burst(), ensure_ascii=False, indent=2))
