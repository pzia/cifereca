"""#1129: a public BookRef never crosses an identity/generation boundary."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui import library_navigation
from cifereca.plugin_api import BookRef


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
OWNER = ROOT / "src/cifereca/gui/library_navigation.py"


def _row():
    return {
        "key": "artifact:sha256:alice", "identity_id": "alice",
        "provider_id": "paris", "item_id": "book-1", "book_id": "book-1",
    }


def _host(rows=None):
    return NS(
        _closed=False,
        service=NS(current_identity=NS(identity_id="alice")),
        _identity_context_generation=10,
        _library_entries_cache=[_row()] if rows is None else rows,
        _open_library_item=Mock(),
    )


def _ref():
    return BookRef("book-1", identifiers={"provider": "paris", "source_item_id": "book-1"})


def test_existing_main_window_facade_signature_and_owner_are_preserved():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    owner = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "MainWindow")
    method = next(item for item in owner.body if isinstance(item, ast.FunctionDef) and item.name == "open_book_detail")
    assert [arg.arg for arg in method.args.args] == ["self", "book"]
    assert [arg.arg for arg in method.args.kwonlyargs] == ["identity_id"]
    assert "library_navigation.open_book_detail" in ast.unparse(method)


def test_controller_has_no_gtk_or_main_window_import():
    imports = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(OWNER.read_text(encoding="utf-8")))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert not any("gi" in entry or "main_window" in entry for entry in imports)


def test_current_identity_and_attested_key_are_required():
    window = _host()
    assert library_navigation.open_book_detail(window, _ref(), identity_id="alice") is True
    window._open_library_item.assert_called_once_with("artifact:sha256:alice")
    assert library_navigation.open_book_detail(window, _ref(), identity_id="bob") is False
    assert library_navigation.open_book_detail(window, BookRef("absent")) is False
    assert library_navigation.open_book_detail(window, None) is False
    window._open_library_item.assert_called_once()


def test_no_identity_or_closed_window_is_inert():
    window = _host()
    window._closed = True
    assert library_navigation.open_book_detail(window, _ref()) is False
    window._closed = False
    window.service.current_identity = None
    assert library_navigation.open_book_detail(window, _ref()) is False
    window._open_library_item.assert_not_called()


def test_identity_switch_during_lookup_never_dispatches_alice_key_to_bob():
    window = _host()
    class SwitchingRows:
        def __iter__(self):
            window.service.current_identity = NS(identity_id="bob")
            yield _row()
    window._library_entries_cache = SwitchingRows()
    assert library_navigation.open_book_detail(window, _ref()) is False
    window._open_library_item.assert_not_called()


def test_identity_cycle_alice_bob_alice_during_lookup_invalidates_old_generation():
    window = _host()
    class SwitchingRows:
        def __iter__(self):
            window.service.current_identity = NS(identity_id="bob")
            window._identity_context_generation += 1
            window.service.current_identity = NS(identity_id="alice")
            yield _row()
    window._library_entries_cache = SwitchingRows()
    assert library_navigation.open_book_detail(window, _ref()) is False
    window._open_library_item.assert_not_called()


def test_close_during_lookup_never_navigates_or_accesses_disposed_widgets():
    window = _host()
    class ClosingRows:
        def __iter__(self):
            window._closed = True
            yield _row()
    window._library_entries_cache = ClosingRows()
    assert library_navigation.open_book_detail(window, _ref()) is False
    window._open_library_item.assert_not_called()


def test_close_during_dispatch_does_not_report_success():
    window = _host()
    def close_after_dispatch(_key):
        window._closed = True
    window._open_library_item = Mock(side_effect=close_after_dispatch)
    assert library_navigation.open_book_detail(window, _ref()) is False
    window._open_library_item.assert_called_once_with("artifact:sha256:alice")


def test_same_book_id_in_another_identity_not_a_usable_fallback():
    other = {**_row(), "identity_id": "bob", "key": "artifact:sha256:bob"}
    window = _host(rows=[other])
    assert library_navigation.open_book_detail(window, _ref()) is False
    window._open_library_item.assert_not_called()
