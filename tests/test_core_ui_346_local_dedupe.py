from __future__ import annotations

import ast
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
LIBRARY_ROWS = MAIN.with_name("library_rows.py")


def _method(name: str):
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _standalone_pair():
    state = _method("_library_state_label")
    availability = _method("_library_availability_label")
    ns = {"Any": object}
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[state, availability], type_ignores=[])), str(MAIN), "exec"),
        ns,
    )
    class Host:
        _library_state_label = staticmethod(ns["_library_state_label"])
        _library_availability_label = classmethod(ns["_library_availability_label"])
    return Host


def test_local_local_availability_is_suppressed_only_as_duplicate():
    host = _standalone_pair()

    assert host._library_availability_label({"provider_id": "local", "state": "local"}) == ""
    assert host._library_state_label({"provider_id": "local", "state": "local"}) == "Local"


def test_non_trivial_states_remain_visible():
    host = _standalone_pair()

    assert host._library_availability_label({"provider_id": "local", "state": "missing"}) == "Fichier manquant"
    assert host._library_availability_label({"provider_id": "local", "state": "remote_catalog"}) == "Notice de catalogue · sans droit attesté"
    assert host._library_availability_label({"provider_id": "paris", "state": "available_to_borrow"}) == "Empruntable"
    assert host._library_availability_label({"provider_id": "kobo", "state": "local"}) == "Local"


def test_library_list_does_not_add_empty_redundant_state():
    # Inspect the real owner of the projection, not a stale window facade.
    module = ast.parse(LIBRARY_ROWS.read_text(encoding="utf-8"))
    source = ast.unparse(next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "project_library_rows"
    ))
    assert "state = availability_label(item)" in source
    assert "meta_parts = [state] if state else []" in source
    owner = MAIN.parent / "library_render_controller.py"
    module = ast.parse(owner.read_text(encoding="utf-8"))
    facade = ast.unparse(next(node for node in module.body
                              if isinstance(node, ast.FunctionDef) and node.name == "render_library"))
    assert "project_library_rows(" in facade
    assert "availability_label=window._library_availability_label" in facade


def test_library_detail_omits_redundant_availability_row_and_badge():
    owner = ast.parse(MAIN.with_name("library_detail_controller.py").read_text(encoding="utf-8"))
    source = ast.unparse(next(node for node in owner.body
                              if isinstance(node, ast.FunctionDef) and node.name == "library_selected"))

    assert "availability_label=window._library_availability_label" in source
    assert "Disponible hors ligne" not in source
    assert "offline_if_local=True" not in source
    assert "badges=[]" in source
    helper = ast.parse(MAIN.with_name(
        "local_book_detail_sections.py"
    ).read_text(encoding="utf-8"))
    projection = ast.unparse(next(node for node in helper.body
                                  if isinstance(node, ast.FunctionDef)
                                  and node.name == "local_book_detail_sections"))
    assert "('Disponibilité', availability)" in projection
    assert "offline_if_local" in projection and "not availability" in projection
    assert "'Disponible hors ligne'" in projection

