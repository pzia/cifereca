from __future__ import annotations

import ast
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


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
    assert host._library_availability_label({"provider_id": "local", "state": "remote_catalog"}) == "Distant"
    assert host._library_availability_label({"provider_id": "paris", "state": "available_to_borrow"}) == "Empruntable"
    assert host._library_availability_label({"provider_id": "kobo", "state": "local"}) == "Local"


def test_library_list_does_not_add_empty_redundant_state():
    source = ast.unparse(_method("_render_library"))

    assert "state = self._library_availability_label(item)" in source
    assert "meta_parts = [state] if state else []" in source


def test_library_detail_omits_redundant_availability_row_and_badge():
    source = ast.unparse(_method("_library_selected"))

    assert "state = self._library_availability_label(item)" in source
    assert "if state:" in source
    assert "detail_rows.append(('Disponibilité', state))" in source
    assert "badges=[provider_label, state]" in source
