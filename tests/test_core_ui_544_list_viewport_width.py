from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
BOOK_WIDGETS = ROOT / "src/cifereca/gui/book_widgets.py"


def _method(path: Path, class_name: str, method_name: str) -> str:
    tree = ast.parse(gui_source_text(path))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_master_detail_list_viewport_owns_horizontal_allocation():
    source = _method(MAIN, "MasterDetailPage", "__init__")
    assert "add_css_class('boxed-list')" not in source
    assert "set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)" in source
    assert "set_propagate_natural_width(False)" in source
    assert "set_min_content_width(0)" in source
    assert "self.scroll.set_hexpand(True)" in source
    assert "self.listview.set_hexpand(True)" in source
    assert "self.listview.set_halign(Gtk.Align.FILL)" in source


def test_search_and_library_share_the_fixed_master_detail_viewport():
    # Library's GTK assembly now belongs to its extracted controller; the
    # MainWindow facade injects the very same MasterDetailPage as Search.
    source = gui_source_text(MAIN)
    library_owner = MAIN.with_name("library_page_controller.py").read_text(encoding="utf-8")
    assert 'MasterDetailPage(self, "Recherche"' in source
    assert 'MasterDetailPage(window, "Bibliothèque"' in library_owner
    facade = _method(MAIN, "MainWindow", "_build_library_page")
    assert "library_page_controller.build_library_page(" in facade
    assert "MasterDetailPage=MasterDetailPage" in facade


def test_list_rows_fill_viewport_instead_of_requesting_global_width():
    source = _method(BOOK_WIDGETS, "BookListCell", "__init__")
    assert "self.set_hexpand(True)" in source
    assert "self.set_halign(Gtk.Align.FILL)" in source
    assert "labels.set_hexpand(True)" in source
    assert "labels.set_halign(Gtk.Align.FILL)" in source
