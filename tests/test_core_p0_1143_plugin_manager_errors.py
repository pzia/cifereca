"""P0 #1143: plugin-manager dialogs never echo plugin/ZIP/GTK exceptions.

Method bodies are evaluated without importing GTK (the production owner
imports GI); these execute the real manager error branches with fake widgets.
No ZIP installation, plugin activation, network or file outside tmp_path.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.plugin_manager_error_feedback import plugin_error_message
from cifereca.plugin_api import UserFacingError


OWNER = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"
PRIVATE = "https://private.invalid/LOAN/borrow.acsm?token=SECRET_PLUGIN_ERROR"


def _method(name):
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "PluginManagerPage")
    method = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef) and node.name == name)
    code = ast.Module(body=[method], type_ignores=[])
    namespace = {"plugin_error_message": plugin_error_message, "Path": Path}
    exec(compile(ast.fix_missing_locations(code), str(OWNER), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("method,arguments,manager_method", [
    ("_identity_toggle", ("plugin-private", "identity-private", True), "set_identity_enabled"),
    ("_apply_toggle", ("plugin-private", True), "set_enabled"),
    ("_priority", ("plugin-private", 20), "set_priority"),
    ("_rollback", ("plugin-private",), "rollback"),
    ("_remove", ("plugin-private",), "remove_package"),
    ("_install", (Path("secret-plugin.zip"),), "install"),
])
def test_each_mutating_plugin_action_uses_safe_error_boundary(
    method, arguments, manager_method,
):
    class Hostile(RuntimeError):
        def __str__(self):
            raise AssertionError("Plugin exception leaked through __str__")
        def __repr__(self):
            raise AssertionError("Plugin exception leaked through __repr__")

    dialogs = []
    manager = Mock()
    getattr(manager, manager_method).side_effect = Hostile(PRIVATE)
    host = NS(
        manager=manager,
        window=NS(show_error=lambda message, title: dialogs.append((message, title))),
        refresh=Mock(), on_changed=Mock(), _toast=Mock(),
    )
    _method(method)(host, *arguments)
    assert len(dialogs) == 1
    assert dialogs[0][0] == "Opération sur le plugin indisponible"
    assert "SECRET_PLUGIN_ERROR" not in str(dialogs)
    getattr(manager, manager_method).assert_called_once()
    if method in {"_rollback", "_remove", "_install", "_identity_toggle"}:
        host.refresh.assert_not_called()
        host.on_changed.assert_not_called()


def test_plugin_archive_inspection_never_shows_private_parser_failure(tmp_path):
    archive = tmp_path / "plugin.zip"
    archive.write_bytes(b"offline-incomplete-archive")
    manager = Mock()
    manager.inspect_archive.side_effect = OSError(PRIVATE)
    dialogs = []
    host = NS(
        manager=manager,
        window=NS(show_error=lambda message, title: dialogs.append((message, title))),
    )
    _method("_review_archive_file")(host, NS(get_path=lambda: str(archive)))
    manager.inspect_archive.assert_called_once_with(archive)
    assert dialogs == [("Opération sur le plugin indisponible", "ZIP de plugin invalide")]
    assert PRIVATE not in str(dialogs)


def test_explicit_safe_plugin_error_preserves_only_user_message():
    error = UserFacingError(
        "Une autorisation est nécessaire.",
        code="provider_access_required",
        technical_detail=PRIVATE,
    )
    assert plugin_error_message(error) == "Une autorisation est nécessaire."
    assert PRIVATE not in plugin_error_message(error)


def test_non_user_facing_error_is_not_stringified_or_reprd():
    class Hostile(BaseException):
        def __str__(self):
            raise AssertionError("no raw __str__")
        def __repr__(self):
            raise AssertionError("no raw __repr__")
    assert plugin_error_message(Hostile(PRIVATE)) == "Opération sur le plugin indisponible"


def test_all_nine_plugin_manager_exception_dialogs_use_explicit_boundary():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    callsites = []
    raw = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "show_error":
            continue
        if node.args and isinstance(node.args[0], ast.Call):
            call = node.args[0]
            if isinstance(call.func, ast.Name) and call.func.id == "plugin_error_message":
                callsites.append(node)
            if isinstance(call.func, ast.Name) and call.func.id in {"str", "repr"}:
                raw.append(node)
    assert len(callsites) == 9
    assert raw == []
