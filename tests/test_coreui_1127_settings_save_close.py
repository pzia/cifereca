"""Late settings save cannot mutate state after MainWindow closes (#1127)."""

import ast
from pathlib import Path
from types import SimpleNamespace


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/settings_commit_actions.py"


def _save_callback():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    builder = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "bind_settings_commit")
    callback = next(node for node in ast.walk(builder) if isinstance(node, ast.FunctionDef) and node.name == "save_settings")
    return compile(ast.fix_missing_locations(ast.Module(body=[callback], type_ignores=[])), str(MAIN), "exec")


def test_closed_window_refuses_settings_save_before_draft_or_commit():
    scope = {"self": SimpleNamespace(_closed=True), "page_lifetime": SimpleNamespace(alive=True)}
    exec(_save_callback(), scope)
    assert scope["save_settings"]() is False
    assert scope["save_settings"](reconnect_after=True) is False


def test_disposed_settings_page_refuses_save_even_when_window_open():
    scope = {"self": SimpleNamespace(_closed=False), "page_lifetime": SimpleNamespace(alive=False)}
    exec(_save_callback(), scope)
    assert scope["save_settings"]() is False
