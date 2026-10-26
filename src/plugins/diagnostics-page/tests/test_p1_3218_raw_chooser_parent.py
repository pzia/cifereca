"""Regression for #3218: native GTK chooser never receives the _UiHost adapter."""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py"


def _export_callback():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "export_raw_journal_clicked")


def test_raw_journal_save_chooser_uses_verified_gtk_toplevel():
    callback = _export_callback()
    chooser_calls = [
        node for node in ast.walk(callback)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "new"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "FileChooserNative"
    ]
    assert len(chooser_calls) == 1
    call = chooser_calls[0]
    assert isinstance(call.args[1], ast.Name) and call.args[1].id == "parent"
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_root"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_button"
        for node in ast.walk(callback)
    )
    assignments = [
        node for node in ast.walk(callback)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "parent"
                for target in node.targets)
    ]
    assert len(assignments) == 1
    conditional = assignments[0].value
    assert isinstance(conditional, ast.IfExp)
    assert isinstance(conditional.test, ast.Call)
    assert isinstance(conditional.test.func, ast.Name)
    assert conditional.test.func.id == "isinstance"
    assert isinstance(conditional.orelse, ast.Constant)
    assert conditional.orelse.value is None
    assert ast.unparse(conditional.body) == "root"
    assert ast.unparse(conditional.test.args[1]) == "Gtk.Window"


def test_raw_export_keeps_confirmation_file_permissions_and_unmodified_bytes():
    callback = _export_callback()
    body = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), callback)
    assert "raw_export_armed" in body
    assert "Confirmer l’export brut non masqué" in body
    assert "broker.export_raw_journal()" in body
    assert "Gtk.FileChooserAction.SAVE" in body
    assert "Gtk.ResponseType.ACCEPT" in body
    assert 'getattr(os, "O_NOFOLLOW", 0)' in body
    assert "os.open(destination, flags, 0o600)" in body
    assert "os.fchmod(handle.fileno(), 0o600)" in body
    assert "handle.write(content)" in body
