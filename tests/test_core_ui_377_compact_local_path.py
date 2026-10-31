from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _function(name: str):
    tree = ast.parse(gui_source_text(MAIN))
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _standalone(name: str):
    functions = [_function("text_or_dash"), _function(name)]
    ns = {"Any": object, "Path": Path}
    exec(compile(ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])), str(MAIN), "exec"), ns)
    return ns[name]


def test_local_file_detail_shows_only_filename_but_keeps_full_path_for_tooltip():
    display = _standalone("detail_display_value")
    path = "/home/testuser/EBooks/example/kobo/Un titre très long.epub"

    shown, tooltip = display("Fichier local", path)

    assert shown == "Un titre très long.epub"
    assert tooltip == path


def test_non_path_detail_values_are_unchanged():
    display = _standalone("detail_display_value")

    assert display("Format", "EPUB") == ("EPUB", "EPUB")
    assert display("Fichier local", None) == ("—", "—")


def test_group_uses_compact_display_and_full_tooltip_values():
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_group")
    source = ast.unparse(method)

    assert "display_value, tooltip_value = detail_display_value(label, value)" in source
    assert "row.set_subtitle(display_value)" in source
    assert "row.set_tooltip_text(label + ' : ' + tooltip_value)" in source


def test_search_and_library_keep_using_same_fichier_local_label():
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    for method_name in ("_search_selected", "_library_selected"):
        if method_name == "_library_selected":
            owner = ast.parse(MAIN.with_name("library_detail_controller.py").read_text(encoding="utf-8"))
            method = next(node for node in owner.body if isinstance(node, ast.FunctionDef)
                          and node.name == "library_selected")
        else:
            method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                          and node.name == method_name)
        source = ast.unparse(method)
        assert "local_book_detail_sections(" in source
    helper = ast.parse(MAIN.with_name(
        "local_book_detail_sections.py"
    ).read_text(encoding="utf-8"))
    section_owner = ast.unparse(next(
        node for node in helper.body if isinstance(node, ast.FunctionDef)
        and node.name == "local_book_detail_sections"
    ))
    assert "'Fichier local'" in section_owner
    assert "item.get('local_path')" in section_owner
