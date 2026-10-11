"""UX P0 #1011: diagnostics presentation contracts.

These tests deliberately document current UX deficits with strict xfail.
They become ordinary passing regressions once the Plugins/CoreUI owner
implements the behavior. Source-level tests complement, but NEVER replace,
the real GTK/Xvfb D01-D16 recipe.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


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


@pytest.mark.xfail(strict=True, reason="#1011 D05: report needs independent scroll")
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


@pytest.mark.xfail(strict=True, reason="#1011 D01/D03: unavailable must not look healthy")
def test_1011_runtime_status_is_never_hardcoded_ok():
    build = _build_statements()
    render = next(
        node for node in ast.walk(build)
        if isinstance(node, ast.FunctionDef) and node.name == "render"
    )
    hardcoded_healthy = [
        node for node in ast.walk(render)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant) and key.value == "status"
        and isinstance(value, ast.Constant) and value.value == "ok"
    ]
    assert not hardcoded_healthy, (
        "A runtime diagnostic's presence does not attest operational health"
    )


@pytest.mark.xfail(strict=True, reason="#1011 D01: empty snapshots need a visible state")
def test_1011_empty_diagnostic_snapshot_has_visible_feedback():
    build = _build_statements()
    render = next(
        node for node in ast.walk(build)
        if isinstance(node, ast.FunctionDef) and node.name == "render"
    )
    source = ast.get_source_segment(PLUGIN.read_text(encoding="utf-8"), render) or ""
    assert any(
        phrase in source.casefold()
        for phrase in ("non alimenté", "aucun événement", "aucune donnée")
    ), "An empty snapshot should not silently render a blank diagnostic panel"
