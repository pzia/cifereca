from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(name: str) -> str:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_library_keeps_primary_search_and_refresh_visible():
    source = _method("_build_library_page")
    assert 'search = Gtk.Button(label="Recherche")' in source
    assert 'refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")' in source
    assert "actions.append(refresh)" in source


def test_open_folder_stays_direct_and_understandable():
    source = _method("_build_library_page")
    assert 'folder = Gtk.Button.new_from_icon_name("folder-open-symbolic")' in source
    assert "Ouvrir le dossier de la bibliothèque" in source
    assert "actions.append(folder)" in source


def test_rare_library_operations_are_grouped_in_named_menu():
    source = _method("_build_library_page")
    assert 'advanced = Gtk.MenuButton(label="Opérations…")' in source
    assert "Opérations avancées et maintenance" in source
    assert 'Gtk.Button(label="Réinspecter les couvertures")' in source
    assert 'Gtk.Button(label="Importer un ACSM…")' in source
    assert 'Gtk.Button(label="Synchroniser les comptes Kobo")' in source
    assert "advanced_box.append(covers)" in source
    assert "advanced_box.append(acsm)" in source
    assert "advanced_box.append(acquire)" in source


def test_global_kobo_operation_is_explicitly_described_as_global():
    source = _method("_build_library_page")
    assert "Opération globale" in source
    assert "tous les comptes Kobo connectés compatibles" in source


def test_underlying_business_callbacks_are_unchanged():
    source = _method("_build_library_page")
    assert "self.refresh_library(True, covers, refresh_covers=True)" in source
    assert "self.choose_acsm()" in source
    assert "self.run_kobo_backup(True, acquire)" in source
    assert "self.open_library_folder()" in source
