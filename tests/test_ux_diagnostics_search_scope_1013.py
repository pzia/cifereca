"""UX P0 #1013: search scope and pagination must be transparent.

The journal broker searches before pagination. Legacy sources remain
explicitly page-local and must not be conflated with the canonical journal.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

FILE = Path(__file__).resolve().parents[1] / "src/plugins/diagnostics-page/diagnostics_explorer.py"


@pytest.fixture(autouse=True)
def _isolated_diagnostics_imports(monkeypatch):
    # Import from the real package location in every pytest worker. Relying
    # on other tests to mutate sys.path works only under sequential ordering.
    monkeypatch.syspath_prepend(str(FILE.parent))


def _explorer():
    spec = importlib.util.spec_from_file_location("ux_explorer_search", FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _events(term: str, first: int):
    return [{
        "timestamp": f"2026-10-11T08:{i:02d}:00+00:00",
        "event_id": str(first + i),
        "source": "core_events",
        "module": "sync",
        "severity": "error",
        "message": term,
        "status": "failed",
    } for i in range(50)]


def test_search_finds_matches_beyond_first_page():
    explorer = _explorer()
    all_events = _events("ordinary event", 0) + _events("special error", 50)
    for event in all_events:
        event["source"] = "journal"

    class JournalBroker:
        def __init__(self):
            self.calls = []

        def events(self, **kwargs):
            self.calls.append(kwargs)
            # Simulate Core's GLOBAL search before applying pagination.
            filtered = [event for event in all_events
                        if kwargs["search"].casefold() in event["message"].casefold()]
            offset = kwargs["offset"]
            return {"events": filtered[offset:offset + kwargs["limit"]],
                    "total": len(filtered), "offset": offset,
                    "sources": [{"source": "journal", "status": "available"}]}

    broker = JournalBroker()
    page = explorer.fetch_explorer_page(
        broker, source="journal", period="all", search="special", offset=0,
    )
    assert broker.calls[0]["search"] == "special"
    assert broker.calls[0]["limit"] == explorer.PAGE_SIZE
    assert broker.calls[0]["offset"] == 0
    assert page["total"] == 50
    assert len(page["events"]) == 50
    # The GTK journal view must NOT re-filter the already searched,
    # redacted display projection and must not claim page-local search.
    assert len(explorer.sanitized_rows(page, search="")) == 50
    assert "cette page uniquement" not in explorer.explorer_header(
        page, explorer.sanitized_rows(page), search=""
    )


def test_search_scope_warning_is_explicit_until_global_search_exists():
    explorer = _explorer()
    header = explorer.explorer_header(
        {"events": [], "total": 100, "offset": 0,
         "sources": [{"source": "core_events", "status": "available"}]},
        [], search="special",
    )
    assert "cette page uniquement" in header
