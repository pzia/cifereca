"""UX P0 #1011: diagnostics presentation contracts.

Positive regressions match the actual runtime and journal-page renderers.
Source-level tests complement, but NEVER replace,
the real GTK/Xvfb D01-D16 recipe.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "src/plugins/diagnostics-page/cifereca_diagnostics_page.py"


def _build_statements():
    tree = ast.parse(PLUGIN.read_text(encoding="utf-8"))
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DiagnosticsPagePlugin"
    )
    build = next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build"
    )
    return build


def _calls(method):
    return [
        node for node in ast.walk(method)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]


def test_1011_report_view_is_embedded_in_independent_scroller():
    build = _build_statements()
    assignments = [
        node for node in ast.walk(build)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "export_view" for t in node.targets)
    ]
    assert assignments, "Report TextView must remain identifiable"
    scroll_names = {
        node.targets[0].id
        for node in ast.walk(build)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "ScrolledWindow"
    }
    wrapped = any(
        isinstance(call.func.value, ast.Name)
        and call.func.value.id in scroll_names
        and call.func.attr == "set_child"
        and any(isinstance(arg, ast.Name) and arg.id == "export_view" for arg in call.args)
        for call in _calls(build)
    )
    assert wrapped, "Report must have a dedicated Gtk.ScrolledWindow"


def test_1011_runtime_status_is_never_hardcoded_ok():
    build = _build_statements()
    render = next(
        node for node in ast.walk(build)
        if isinstance(node, ast.FunctionDef) and node.name == "render"
    )
    source = ast.get_source_segment(PLUGIN.read_text(encoding="utf-8"), render) or ""
    assert '"status": runtime_diagnostic_status(row)' in source
    from sys import path
    plugin_dir = str(PLUGIN.parent)
    if plugin_dir not in path:
        path.insert(0, plugin_dir)
    from cifereca_diagnostics_page import runtime_diagnostic_status
    assert runtime_diagnostic_status({}) == "unknown"
    assert runtime_diagnostic_status({"status": True}) == "unknown"
    assert runtime_diagnostic_status({"status": "unavailable"}) == "error"
    assert runtime_diagnostic_status({"status": "blocked"}) == "error"
    assert runtime_diagnostic_status({"state": "degraded"}) == "warning"
    assert runtime_diagnostic_status({"status": "ready"}) == "ok"


def test_1011_empty_diagnostic_snapshot_has_visible_feedback():
    build = _build_statements()
    display = next(
        node for node in ast.walk(build)
        if isinstance(node, ast.FunctionDef) and node.name == "display_events"
    )
    source = ast.get_source_segment(
        PLUGIN.read_text(encoding="utf-8"), display
    ) or ""
    # Empty is a journal-page state, not a runtime-status rendering state.
    assert "if not rows:" in source
    assert "empty_observation(" in source
    assert "Aucun événement sur cette page." in source
    assert "Vérifier la période, les filtres" in source
    assert "Données indisponibles" in PLUGIN.read_text(encoding="utf-8")
