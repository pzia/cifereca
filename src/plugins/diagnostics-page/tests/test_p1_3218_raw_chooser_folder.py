"""#3218: the SAVE picker starts in a readable local directory, never writes there.

No real account/log is used. A separate GTK4 E2E test covers the native picker.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from cifereca_diagnostics_page import _readable_raw_export_folder

SOURCE = Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py"


def test_choose_accessible_home_for_picker_but_never_create_export(tmp_path, monkeypatch):
    home = tmp_path / "synthetic-private-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert _readable_raw_export_folder() == str(home)
    assert list(home.iterdir()) == []


def test_missing_home_never_sets_a_nonexistent_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "not-there"))
    assert _readable_raw_export_folder() is None


def test_unreadable_directory_is_not_used_or_disclosed(tmp_path, monkeypatch):
    home = tmp_path / "private"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    def inaccessible(path):
        raise PermissionError("PRIVATE_PATH_SHOULD_NOT_BE_LOGGED")
    monkeypatch.setattr(os, "scandir", inaccessible)
    assert _readable_raw_export_folder() is None


def test_real_save_chooser_is_modal_uses_verified_folder_and_tracks_its_lifetime():
    body = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(body)
    callback = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "export_raw_journal_clicked")
    code = ast.get_source_segment(body, callback)
    assert "parent = root if isinstance(root, Gtk.Window) else None" in code
    assert "chooser.set_modal(parent is not None)" in code
    assert "_readable_raw_export_folder()" in code
    assert "chooser.set_current_folder(Gio.File.new_for_path(folder))" in code
    assert code.index("chooser.set_current_folder(") < code.index("chooser.set_current_name(")
    assert "self._raw_export_chooser = chooser" in code
    assert "self._raw_export_chooser = None" in code
    assert "dialog.destroy()" in code
    assert "broker.export_raw_journal()" in code
    assert "os.open(destination, flags, 0o600)" in code
    assert "handle.write(content)" in code
