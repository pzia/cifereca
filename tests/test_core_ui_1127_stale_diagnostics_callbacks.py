"""P0 #1127: late GTK clipboard/chooser callbacks must not outlive Settings.

Execute the real nested callback functions as AST with deterministic doubles.
No actual GTK, clipboard, network or user file is required for stale tests.
"""
from __future__ import annotations

import ast
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from cifereca.gui.settings_lifetime import SettingsPageLifetime


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _settings_callback(name):
    tree = ast.parse(MAIN.with_name("settings_diagnostic_actions.py").read_text(encoding="utf-8"))
    builder = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and
                   node.name == "build_settings_diagnostic_callbacks")
    owner_name = "copy_text" if name == "verified" else "export_text"
    owner = next(node for node in ast.walk(builder)
                 if isinstance(node, ast.FunctionDef) and node.name == owner_name)
    return next(node for node in owner.body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def _callback(name, namespace):
    nested = _settings_callback(name)
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[nested], type_ignores=[])
    ), str(MAIN), "exec"), namespace)
    return namespace[name]


def test_clipboard_callback_after_close_does_not_reopen_or_report():
    lifetime = SettingsPageLifetime()
    lifetime.dispose()
    window = SimpleNamespace(diagnostics=SimpleNamespace(add=Mock()), toast=Mock())
    callback = _callback("verified", dict(
        page_lifetime=lifetime, self=window, content="expurgé",
    ))
    source = SimpleNamespace(read_text_finish=Mock(return_value="expurgé"))
    callback(source, object())
    source.read_text_finish.assert_not_called()
    window.diagnostics.add.assert_not_called()
    window.toast.assert_not_called()


def test_clipboard_callback_after_rebuild_is_ignored_but_active_one_is_valid():
    old = SettingsPageLifetime()
    old.dispose()
    new = SettingsPageLifetime()
    window = SimpleNamespace(diagnostics=SimpleNamespace(add=Mock()), toast=Mock())
    reader = SimpleNamespace(read_text_finish=Mock(return_value="copie"))
    _callback("verified", dict(page_lifetime=old, self=window, content="copie"))(
        reader, object()
    )
    assert reader.read_text_finish.call_count == 0
    _callback("verified", dict(page_lifetime=new, self=window, content="copie"))(
        reader, object()
    )
    assert reader.read_text_finish.call_count == 1
    window.toast.assert_called_once_with("Diagnostic copié dans le presse-papiers")


def test_stale_chooser_response_destroys_dialog_without_writing(tmp_path):
    lifetime = SettingsPageLifetime()
    lifetime.dispose()
    file = tmp_path / "no-export.txt"
    selected = SimpleNamespace(get_path=Mock(return_value=str(file)))
    dialog = SimpleNamespace(
        get_file=Mock(return_value=selected), destroy=Mock(),
    )
    window = SimpleNamespace(
        diagnostics=SimpleNamespace(add=Mock()), toast=Mock(), show_error=Mock(),
    )
    callback = _callback("chosen", dict(
        page_lifetime=lifetime, self=window, content="expurgé",
        Gtk=SimpleNamespace(ResponseType=SimpleNamespace(ACCEPT=1)),
        os=os, stat=stat,
    ))
    callback(dialog, 1)
    assert not file.exists()
    dialog.get_file.assert_not_called()
    dialog.destroy.assert_called_once_with()
    window.diagnostics.add.assert_not_called()
    window.toast.assert_not_called()
    window.show_error.assert_not_called()


def test_active_chooser_preserves_private_unmasked_file_export(tmp_path):
    file = tmp_path / "report.txt"
    dialog = SimpleNamespace(
        get_file=lambda: SimpleNamespace(get_path=lambda: str(file)),
        destroy=Mock(),
    )
    window = SimpleNamespace(
        diagnostics=SimpleNamespace(add=Mock()), toast=Mock(), show_error=Mock(),
    )
    _callback("chosen", dict(
        page_lifetime=SettingsPageLifetime(), self=window,
        content="rapport brut",
        Gtk=SimpleNamespace(ResponseType=SimpleNamespace(ACCEPT=1)),
        os=os, stat=stat,
    ))(dialog, 1)
    assert file.read_text(encoding="utf-8") == "rapport brut"
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    window.show_error.assert_not_called()
    window.toast.assert_called_once_with("Diagnostic exporté")
    dialog.destroy.assert_called_once_with()


def test_export_entrypoint_checks_page_lifetime_once():
    tree = ast.parse(MAIN.with_name("settings_diagnostic_actions.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "export_text")
    source = ast.unparse(fn)
    # The entrypoint has exactly one gate; the nested chooser intentionally
    # has its own gate for responses arriving after page disposal.
    guards = [node for node in fn.body
              if isinstance(node, ast.If)
              and "not page_lifetime.alive" in ast.unparse(node.test)]
    assert len(guards) == 1
    for name in ("verified", "chosen"):
        child = _settings_callback(name)
        assert "if not page_lifetime.alive:" in ast.unparse(child)
