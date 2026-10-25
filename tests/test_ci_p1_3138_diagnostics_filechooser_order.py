"""CI P1 #3138: GTK SAVE dialog must preserve exact-fixture selection."""
from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "e2e" / "diagnostics_page_probe.py"


def test_capture_export_gtk_save_sets_name_before_exact_fixture_file():
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    capture = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "capture_file_choice"
    )
    calls = {
        node.func.attr: node.lineno
        for node in ast.walk(capture)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"set_current_name", "set_file"}
    }
    assert set(calls) == {"set_current_name", "set_file"}
    assert calls["set_current_name"] < calls["set_file"]
    selected = next(
        statement for statement in capture.body
        if isinstance(statement, ast.Assert)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "set_file"
            for call in ast.walk(statement)
        )
    )
    assert isinstance(selected, ast.Assert)
    # Preserve the same real GTK file-path assertion and export completion;
    # changing the chooser ordering must not weaken the test gate.
    capture_text = ast.get_source_segment(source, capture)
    assert "exporter.dialog.get_file().get_path() == str(target)" in capture_text
    assert "seconds=5" in capture_text
    assert "CAPTURE-EXPORT-FIXTURE" in capture_text
    assert "capture_accept(exporter, target)" in capture_text
