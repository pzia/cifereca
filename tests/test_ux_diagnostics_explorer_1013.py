"""UX P0 #1013: expected Diagnostics log explorer controls.

Source-level positive regressions follow the current helper split; acceptance is
real GTK/Xvfb with anonymized, bounded Core broker events.
"""
from pathlib import Path
import ast

SOURCE = Path(__file__).resolve().parents[1] / "src/plugins/diagnostics-page/cifereca_diagnostics_page.py"


def _build():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    plugin = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DiagnosticsPagePlugin")
    return next(n for n in plugin.body if isinstance(n, ast.FunctionDef) and n.name == "_build")


def test_explorer_consumes_bounded_public_events_broker():
    # The page delegates its bounded fetch to diagnostics_explorer rather
    # than calling a Core broker method directly inside _build.
    build = ast.unparse(_build())
    assert "fetch_explorer_page(broker, **query)" in build
    helper = ast.parse(
        SOURCE.with_name("diagnostics_explorer.py").read_text(encoding="utf-8")
    )
    fetch = next(node for node in helper.body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "fetch_explorer_page")
    calls = [
        node for node in ast.walk(fetch)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "events"
    ]
    assert len(calls) == 1
    assert "PAGE_SIZE" in ast.unparse(fetch)
    assert "'limit': PAGE_SIZE" in ast.unparse(fetch)
    assert "'offset': offset" in ast.unparse(fetch)
    assert "source == 'journal'" in ast.unparse(fetch)
    assert "query['search'] = search" in ast.unparse(fetch)


def test_explorer_exposes_module_severity_period_and_search_filters():
    source = ast.get_source_segment(
        SOURCE.read_text(encoding="utf-8"), _build()
    ) or ""
    # Real controls use tooltips/dropdowns/placeholder labels rather than
    # the former static caption texts.
    for fragment in (
        '"Journaux"', '"Source des événements"',
        '"Préfixe module (Entrée pour appliquer)"',
        '"Niveau de gravité"', '"Période de la consultation"',
        '"Rechercher dans tous les événements"',
        'journals_tab.append(filters_top)',
        'filters_bottom.append(module_filter)',
        'journals_tab.append(search_filter)',
    ):
        assert fragment in source, f"Missing actual explorer control: {fragment}"


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


def test_explorer_renders_event_provenance():
    source = ast.get_source_segment(
        SOURCE.read_text(encoding="utf-8"), _build()
    ) or ""
    for fragment in (
        "local_timestamp_label(row['timestamp'])",
        "row['severity']",
        "row['source']",
        "row['module']",
        'event_id = row["event_id"]',
        "connection_provenance_label(row)",
        "linked_operation_groups(rows)",
    ):
        assert fragment in source, f"Missing explicit published provenance: {fragment}"
    helper = SOURCE.with_name("diagnostics_explorer.py").read_text(encoding="utf-8")
    for field in ('"timestamp"', '"source"', '"module"', '"severity"', '"event_id"'):
        assert field in helper
    assert "published_task_reference(raw)" in helper
