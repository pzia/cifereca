"""UX P0 #1013: search scope and pagination must be transparent.

Characterization only. The broker currently returns 50 events per page and
the search is local to the page, which can produce misleading zero results.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


FILE = Path(__file__).resolve().parents[1] / "src/plugins/diagnostics-page/diagnostics_explorer.py"


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


@pytest.mark.xfail(strict=True, reason="#1013: search currently filters only the current page")
def test_search_finds_matches_beyond_first_page():
    explorer = _explorer()
    first_page = {"events": _events("ordinary event", 0), "total": 100, "offset": 0,
                  "sources": [{"source": "core_events", "status": "available"}]}
    second_page = {"events": _events("special error", 50), "total": 100, "offset": 50,
                   "sources": [{"source": "core_events", "status": "available"}]}
    assert explorer.sanitized_rows(first_page, search="special") == []
    assert len(explorer.sanitized_rows(second_page, search="special")) == 50
    # A global search must not show an empty result when a later page matches.
    assert explorer.sanitized_rows(first_page, search="special"), (
        "An empty local page is not evidence that no source event matches."
    )


def test_search_scope_warning_is_explicit_until_global_search_exists():
    explorer = _explorer()
    header = explorer.explorer_header(
        {"events": [], "total": 100, "offset": 0,
         "sources": [{"source": "core_events", "status": "available"}]},
        [], search="special",
    )
    assert "cette page uniquement" in header
