"""UX P0 #1013: expected Diagnostics log explorer controls.

Source-level regression characterization only; the actual acceptance gate is
real GTK/Xvfb with anonymized, bounded Core broker events.
"""
from pathlib import Path
import ast

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src/plugins/diagnostics-page/cifereca_diagnostics_page.py"


def _build():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    plugin = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DiagnosticsPagePlugin")
    return next(n for n in plugin.body if isinstance(n, ast.FunctionDef) and n.name == "_build")


@pytest.mark.xfail(strict=True, reason="#1013: page does not yet consume Core event feed")
def test_explorer_consumes_bounded_public_events_broker():
    calls = [
        n for n in ast.walk(_build()) if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute) and n.func.attr == "events"
    ]
    assert calls, "Use the Core-owned diagnostics.events(...) API; never read log files in plugin"
    assert any(any(k.arg in {"limit", "offset"} for k in call.keywords) for call in calls), (
        "The events feed must be explicitly paginated/bounded"
    )


@pytest.mark.xfail(strict=True, reason="#1013: search and filter widgets missing")
def test_explorer_exposes_module_severity_period_and_search_filters():
    source = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), _build()) or ""
    for label in ("Journaux", "Source", "Module", "Sévérité", "Période", "Rechercher"):
        assert label.casefold() in source.casefold(), f"Missing visible control: {label}"


@pytest.mark.xfail(strict=True, reason="#1013: no explicit source availability feedback")
def test_explorer_distinguishes_missing_sources_from_empty_results():
    source = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), _build()) or ""
    assert "unavailable" in source or "indisponible" in source.casefold()
    assert "empty" in source or "aucun événement" in source.casefold()
    assert "partial" in source or "partiel" in source.casefold()


def test_explorer_has_no_direct_private_log_access():
    build = _build()
    prohibited = {"open", "read_text", "read_bytes", "glob", "rglob"}
    direct = [
        n for n in ast.walk(build) if isinstance(n, ast.Call)
        and (
            isinstance(n.func, ast.Name) and n.func.id == "open"
            or isinstance(n.func, ast.Attribute) and n.func.attr in prohibited
        )
    ]
    assert not direct, "Only a public Core diagnostics broker can expose log events"


@pytest.mark.xfail(strict=True, reason="#1013: journal rows missing provenance and timestamp")
def test_explorer_renders_event_provenance():
    source = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), _build()) or ""
    for attribute in ("timestamp", "source", "module", "severity", "correlation"):
        assert attribute in source, f"Missing event field: {attribute}"
