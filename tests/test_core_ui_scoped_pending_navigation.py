from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _main_method(name):
    module = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _methods():
    # Execute the actual headless implementation; MainWindow keeps only facades.
    from cifereca.gui import library_navigation
    return {
        "_open_library_item": library_navigation.open_library_item,
        "_select_pending_library_item": library_navigation.select_pending_library_item,
    }


def _host():
    methods = _methods()
    host = NS(
        service=NS(current_identity=NS(identity_id="alice")),
        _identity_context_generation=7,
        library_page=NS(select_key=Mock(return_value=False)),
        _library_entries_cache=[],
        toast=Mock(),
        navigate=Mock(),
    )
    host._select_pending_library_item = lambda: methods["_select_pending_library_item"](host)
    host._open_library_item = lambda key: methods["_open_library_item"](host, key)
    return host


def test_pending_selection_records_identity_and_survives_initial_empty_render():
    host = _host()
    host._open_library_item("artifact:sha256:shared")
    host.navigate.assert_called_once_with("library")
    assert host._pending_library_selection_key == "artifact:sha256:shared"
    assert host._pending_library_selection_identity_id == "alice"
    assert host._pending_library_selection_generation == 7
    host.library_page.select_key.assert_called_once_with("artifact:sha256:shared")
    host._library_entries_cache = []
    assert host._select_pending_library_item() is False
    assert host._pending_library_selection_key == "artifact:sha256:shared"
    host.toast.assert_not_called()


def test_selection_is_cancelled_before_accessing_bob_even_with_identical_sha():
    host = _host()
    host._open_library_item("artifact:sha256:shared")
    host.service.current_identity = NS(identity_id="bob")
    host._identity_context_generation += 1
    host._library_entries_cache = [{"key": "artifact:sha256:shared", "identity_id": "bob"}]
    host.library_page.select_key.reset_mock()
    host.library_page.select_key.return_value = True
    assert host._select_pending_library_item() is False
    host.library_page.select_key.assert_not_called()
    assert host._pending_library_selection_key is None
    assert host._pending_library_selection_identity_id is None
    assert host._pending_library_selection_generation is None
    host.toast.assert_not_called()


def test_a_b_a_cycle_cannot_restore_old_request_after_identity_reselection():
    host = _host()
    host._open_library_item("artifact:sha256:shared")
    host._identity_context_generation += 2
    host._library_entries_cache = [{"key": "artifact:sha256:shared", "identity_id": "alice"}]
    host.library_page.select_key.reset_mock()
    host.library_page.select_key.return_value = True
    assert host._select_pending_library_item() is False
    host.library_page.select_key.assert_not_called()
    assert host._pending_library_selection_key is None


def test_new_request_after_identity_switch_can_select_bob_book():
    host = _host()
    host._open_library_item("artifact:sha256:shared")
    host.service.current_identity = NS(identity_id="bob")
    host._identity_context_generation += 1
    host._select_pending_library_item()
    host._library_entries_cache = [{"key": "artifact:sha256:bob", "identity_id": "bob"}]
    host.library_page.select_key.return_value = True
    host._open_library_item("artifact:sha256:bob")
    assert host._pending_library_selection_key is None
    assert host._pending_library_selection_identity_id is None
    host.library_page.select_key.assert_called_with("artifact:sha256:bob")


def test_identity_activation_clears_pending_request_before_new_library_render():
    owner = ast.parse(MAIN.with_name("identity_activation_controller.py").read_text(encoding="utf-8"))
    source = ast.unparse(next(n for n in owner.body
                              if isinstance(n, ast.FunctionDef) and n.name == "activate_identity"))
    key_reset = source.index("window._pending_library_selection_key = None")
    generation_advance = source.index("window._identity_context_generation += 1")
    render_new_identity = source.index("window._render_library([])")
    assert generation_advance < key_reset < render_new_identity
    assert "window._pending_library_selection_identity_id = None" in source
    assert "window._pending_library_selection_generation = None" in source
