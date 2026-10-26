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


def test_explorer_distinguishes_missing_sources_from_empty_results():
    """#1013: now implemented; retain a positive regression gate, not XPASS(strict).

    This characterizes the real page's distinct *source availability* and
    *event count* presentations. A dated zero-anomaly conclusion is a separate,
    still-open product requirement tracked by #1161, not implied by this test.
    The widget states are additionally exercised by real GTK E2E D01-D03.
    """
    source = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), _build()) or ""
    for fragment in (
        "diagnostic_channel_caption(current_page)",
        "journals_sources.set_text",
        "Canaux de diagnostic : actualisation requise",
        "Aucun événement sur cette page.",
        "Données indisponibles",
        "Source indisponible",
        "journals_summary.set_text",
    ):
        assert fragment in source, f"Missing diagnostics availability/empty distinction: {fragment}"
    helper = SOURCE.with_name("diagnostics_explorer.py").read_text(encoding="utf-8")
    for fragment in (
        '"empty": "Pas d’événement enregistré"',
        '"unavailable": "Indisponible"',
        '"partial": "Journal partiel (rotation / troncation)"',
        'if not payload.get("sources"):',
    ):
        assert fragment in helper, f"Missing source availability contract: {fragment}"


def test_explorer_reads_journal_via_core_broker_only():
    build = _build()
    prohibited = {"read_text", "read_bytes", "glob", "rglob"}
    direct_reads = [
        n for n in ast.walk(build) if isinstance(n, ast.Call)
        and (isinstance(n.func, ast.Name) and n.func.id == "open"
             or isinstance(n.func, ast.Attribute) and n.func.attr in prohibited)
    ]
    assert not direct_reads, "Journal reads must go through the Core broker"
    source = ast.unparse(build)
    # os.open writes exclusively the user-approved, 0600 export destination.
    assert "os.open(destination, flags, 384)" in source
    assert "broker.export_raw_journal()" in source


@pytest.mark.xfail(strict=True, reason="#1013: journal rows missing provenance and timestamp")
def test_explorer_renders_event_provenance():
    source = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), _build()) or ""
    for attribute in ("timestamp", "source", "module", "severity", "correlation"):
        assert attribute in source, f"Missing event field: {attribute}"
