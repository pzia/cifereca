"""P0 #1129: book navigation and deferred selection are inert after close."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.plugin_api import BookRef


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(name: str):
    if name in {"_open_library_item", "_select_pending_library_item"}:
        from cifereca.gui import library_navigation
        return getattr(library_navigation, name.lstrip("_"))
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    fn = next(node for node in owner.body
              if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.fix_missing_locations(ast.Module(
        body=[
            ast.ImportFrom(module="__future__",
                           names=[ast.alias(name="annotations")], level=0),
            fn,
        ], type_ignores=[],
    ))
    from cifereca.gui import library_navigation
    namespace = {"__package__": "cifereca.gui", "library_navigation": library_navigation}
    exec(compile(module, str(MAIN), "exec"), namespace)
    return namespace[name]


def test_book_navigation_after_close_does_not_access_disposed_service():
    callback = _method("open_book_detail")
    host = NS(_closed=True)
    assert callback(host, BookRef("book")) is False


def test_book_link_after_close_does_not_queue_or_navigate():
    callback = _method("_open_library_item")
    host = NS(_closed=True)
    assert callback(host, "artifact:sha256:book") is None
    assert not hasattr(host, "_pending_library_selection_key")


def test_deferred_selection_after_close_does_not_touch_gtk_page():
    callback = _method("_select_pending_library_item")
    host = NS(_closed=True, _pending_library_selection_key="artifact:sha256:book")
    assert callback(host) is False
    assert host._pending_library_selection_key == "artifact:sha256:book"


def test_live_library_link_still_keeps_identity_and_generation_without_filters():
    callback = _method("_open_library_item")
    host = NS(
        _closed=False,
        service=NS(current_identity=NS(identity_id="alice")),
        _identity_context_generation=3,
        navigate=Mock(),
        _select_pending_library_item=Mock(return_value=False),
    )
    callback(host, "artifact:sha256:book")
    assert host._pending_library_selection_key == "artifact:sha256:book"
    assert host._pending_library_selection_identity_id == "alice"
    assert host._pending_library_selection_generation == 3
    host.navigate.assert_called_once_with("library")
    host._select_pending_library_item.assert_called_once_with()
    assert not hasattr(host, "library_controls")


def test_live_public_navigation_still_checks_attested_identity_and_key():
    callback = _method("open_book_detail")
    host = NS(
        _closed=False,
        service=NS(current_identity=NS(identity_id="alice")),
        _library_entries_cache=[{
            "key": "artifact:sha256:book", "identity_id": "alice",
            "book_id": "paris-1", "item_id": "paris-1", "provider_id": "paris",
        }],
        _open_library_item=Mock(),
    )
    ref = BookRef("paris-1", identifiers={"provider": "paris"})
    assert callback(host, ref, identity_id="alice") is True
    host._open_library_item.assert_called_once_with("artifact:sha256:book")
    assert callback(host, ref, identity_id="bob") is False
    assert callback(host, BookRef("not-attested")) is False
    host._open_library_item.assert_called_once()
