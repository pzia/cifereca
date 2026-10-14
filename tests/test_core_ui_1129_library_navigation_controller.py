"""P0 #1129 — Library pending navigation: real owner, stable facade, no GTK."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui import library_navigation


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "library_navigation.py"


def _window(identity="alice", generation=3):
    return NS(
        _closed=False,
        service=NS(current_identity=NS(identity_id=identity) if identity else None),
        _identity_context_generation=generation,
        _library_entries_cache=[],
        library_page=NS(select_key=Mock(return_value=False)),
        toast=Mock(),
        navigate=Mock(),
        _select_pending_library_item=Mock(return_value=False),
    )


def test_headless_owner_and_unchanged_main_window_facades():
    source = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(source)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(x == "import gi" or x.startswith("from gi ") or "main_window" in x
                   for x in imports)
    owner = next(n for n in ast.parse(MAIN.read_text(encoding="utf-8")).body
                 if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    methods = {m.name: m for m in owner.body if isinstance(m, ast.FunctionDef)}
    for name, target, signature in (
        ("_open_library_item", "open_library_item", ["self", "key"]),
        ("_select_pending_library_item", "select_pending_library_item", ["self"]),
    ):
        method = methods[name]
        assert [a.arg for a in method.args.args] == signature
        assert len(method.body) == 1 and isinstance(method.body[0], ast.Return)
        assert ast.unparse(method.body[0].value.func) == "library_navigation." + target


def test_no_identity_request_remains_scoped_to_empty_identity():
    window = _window(identity=None, generation=8)
    library_navigation.open_library_item(window, "artifact:sha256:shared")
    assert window._pending_library_selection_identity_id == ""
    assert window._pending_library_selection_generation == 8
    window.navigate.assert_called_once_with("library")
    assert window._pending_library_selection_key == "artifact:sha256:shared"


def test_no_page_yet_keeps_request_until_library_is_built():
    window = _window()
    library_navigation.open_library_item(window, "artifact:sha256:shared")
    del window.library_page
    assert library_navigation.select_pending_library_item(window) is False
    assert window._pending_library_selection_key == "artifact:sha256:shared"
    window.toast.assert_not_called()


def test_missing_key_after_nonempty_refresh_does_not_reset_filters():
    window = _window()
    library_navigation.open_library_item(window, "artifact:sha256:missing")
    window._library_entries_cache = [{"key": "artifact:sha256:other"}]
    window.library_controls = NS(set_state=Mock())
    assert library_navigation.select_pending_library_item(window) is False
    assert "n’est plus présent" in window.toast.call_args.args[0]
    window.library_controls.set_state.assert_not_called()
    assert window._pending_library_selection_key is None
    assert window._pending_library_selection_generation is None


def test_closed_window_keeps_pending_selection_and_never_accesses_service():
    window = NS(_closed=True, _pending_library_selection_key="book")
    assert library_navigation.select_pending_library_item(window) is False
    assert library_navigation.open_library_item(window, "another") is None
    assert window._pending_library_selection_key == "book"
